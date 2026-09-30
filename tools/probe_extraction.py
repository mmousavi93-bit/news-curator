"""Extraction-accuracy probe: cluster a frozen items snapshot, then run
signals.extract() against EACH provider in isolation and dump the raw
results so precision/recall can be computed offline against hand labels.

This is the measurement half of the Phase 11 accuracy gate (CLAUDE.md
line 612: "measure Gemini extraction precision/recall BEFORE paying any
adjudicator"). It does NOT score, wire, or persist anything -- it answers
one question: which provider extracts the signal catalog accurately enough
to trust behind a deterministic risk score.

The engine (risk/engine.py) is deterministic and gate-verified, so 100% of
the error budget is the extraction. The engine AMPLIFIES extraction errors:
event_date errors change full-vs-decayed weight, the rumour gate (Step 1)
drops any signal with <2 corroborating groups, and a confidently wrong
0-100 is worse than no score. That is what this probe measures.

Run (CI runner only -- needs sentence-transformers + provider keys):
  python tools/probe_extraction.py \
      --items-csv fixtures/extraction_eval/items_20260928.csv \
      --out-dir reports/extraction

Determinism: the sample is fixed -- clusters sorted by corroborating_count
desc, top ~2/3 (the multi-source stories that would actually fire signals)
plus the bottom ~1/3 (rumour-only, to test the Step-1 gate). No RNG.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
from datetime import datetime
from pathlib import Path

from agent.collectors.base import Item
from agent.config import load_all, load_yaml
from agent.llm.providers import (
    GeminiAdapter,
    GroqAdapter,
    Groq2Adapter,
    MistralAdapter,
)
from agent.llm.router import Router
from agent.pipeline.cluster import cluster_items
from agent.pipeline.embed import MiniLmEmbedder
from agent.pipeline.signals import extract, signal_catalog
from agent.util.logging import get_logger

ADAPTERS = {
    "gemini": GeminiAdapter,
    "groq": GroqAdapter,
    "groq2": Groq2Adapter,
    "mistral": MistralAdapter,
}
KEY_ENV = {
    "gemini": "GEMINI_API_KEY",
    "groq": "GROQ_API_KEY",
    "groq2": "GROQ_API_KEY_2",
    "mistral": "MISTRAL_API_KEY",
}

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = REPO_ROOT / "config"


def load_items(path: Path) -> list[Item]:
    items: list[Item] = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            published = None
            if row.get("published_at_utc"):
                try:
                    published = datetime.fromisoformat(row["published_at_utc"])
                except ValueError:
                    published = None
            items.append(
                Item(
                    source_id=row["source_id"],
                    url=row["url"],
                    title=row["title"],
                    body=row["body"],
                    published_at=published,
                    lang=row.get("lang") or "en",
                    raw_hash="",
                    date_only=row.get("date_only") in ("1", "true", "True"),
                )
            )
    return items


def build_router(name: str, model: str, api_key: str, max_calls: int, logger) -> Router:
    """One adapter, one router -- measures the provider in isolation, never
    through the cascade. Budget/breaker thresholds are raised so the probe
    records raw per-cluster outcomes instead of masking them."""
    adapter = ADAPTERS[name](model, api_key)
    return Router(
        [adapter],
        max_calls=max_calls,
        max_retries=1,
        breaker_threshold=max_calls + 1,
        logger=logger,
    )


def pick_sample(clusters, cred, n: int) -> list:
    """Top ~2/3 highest-corroborated + bottom ~1/3 lowest, deterministic."""
    ordered = sorted(
        clusters,
        key=lambda c: (c.corroborating_count(cred), len(c.members), c.key),
        reverse=True,
    )
    n_low = min(n // 3, max(1, len(ordered) // 3))
    n_high = n - n_low
    sample = list(ordered[:n_high])
    have = {c.key for c in sample}
    for c in reversed(ordered[-n_low:]):
        if c.key not in have and len(sample) < n:
            sample.append(c)
            have.add(c.key)
    return sample


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="probe_extraction")
    ap.add_argument("--items-csv", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--clusters", type=int, default=12)
    ap.add_argument("--providers", default="gemini,groq,groq2,mistral")
    args = ap.parse_args(argv)

    logger = get_logger("agent.probe_extraction")
    config = load_all(base=CONFIG_DIR)
    weights_raw = load_yaml("risk_weights.yaml", base=CONFIG_DIR)
    catalog = signal_catalog(weights_raw)
    template = (CONFIG_DIR / "prompts" / "signal_extraction.txt").read_text(
        encoding="utf-8"
    )
    body_chars = config.settings.pipeline.item_body_chars
    threshold = config.settings.pipeline.cluster_similarity_threshold

    items = load_items(Path(args.items_csv))
    logger.info("loaded %d items", len(items))

    embedder = MiniLmEmbedder(config.settings.pipeline.embed_model)
    texts = [f"{item.title}\n{item.body}" for item in items]
    vectors = embedder.embed(texts)
    clusters = cluster_items(items, vectors, threshold)
    logger.info(
        "clustered %d items -> %d clusters (threshold %.2f)",
        len(items), len(clusters), threshold,
    )

    cred = config.credibility
    sample = pick_sample(clusters, cred, args.clusters)
    logger.info("sample: %d clusters", len(sample))

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    # 1. fixture -- member content for offline hand-labeling.
    fixture = []
    for c in sample:
        fixture.append({
            "cluster_key": c.key,
            "corroborating_count": c.corroborating_count(cred),
            "n_members": len(c.members),
            "members": [
                {
                    "source_id": m.source_id,
                    "title": m.title,
                    "body": m.body,
                    "url": m.url,
                    "published_at": m.published_at.isoformat() if m.published_at else None,
                    "lang": m.lang,
                }
                for m in c.members
            ],
        })
    (out / "extraction_fixture.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in fixture),
        encoding="utf-8",
    )

    # 2. per-provider extraction.
    provider_names = [p for p in args.providers.split(",") if p]
    predictions = []
    for name in provider_names:
        cfg = config.settings.llm.providers.get(name)
        if cfg is None or not cfg.model:
            logger.warning("provider %s not configured -- skipped", name)
            continue
        api_key = os.environ.get(KEY_ENV[name], "")
        if not api_key:
            logger.warning("provider %s: no %s in env -- skipped", name, KEY_ENV[name])
            continue
        router = build_router(name, cfg.model, api_key, len(sample) * 4 + 10, logger)
        for c in sample:
            res = extract(
                router, c, template=template, body_chars=body_chars,
                catalog=catalog, credibility=cred, logger=logger,
            )
            predictions.append({
                "cluster_key": c.key,
                "provider": name,
                "status": res.status,
                "none": res.none,
                "dropped_ids": list(res.dropped_ids),
                "signals": [
                    {
                        "signal_id": s.signal_id,
                        "event_date": s.event_date.isoformat(),
                        "claim": s.claim,
                        "actor": s.actor,
                        "sources": list(s.sources),
                        "quote": s.quote,
                        "state_update": s.state_update,
                        "confidence_notes": s.confidence_notes,
                    }
                    for s in res.signals
                ],
            })
            logger.info(
                "%s %s -> %s (%d signals)", c.key[:8], name, res.status, len(res.signals),
            )

    (out / "extraction_predictions.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in predictions),
        encoding="utf-8",
    )

    ok = sum(1 for p in predictions if p["status"] == "ok")
    by_provider = {}
    for p in predictions:
        by_provider.setdefault(p["provider"], {"ok": 0, "none": 0, "other": 0})
        if p["status"] == "ok":
            by_provider[p["provider"]]["ok"] += 1
        elif p["status"] == "none":
            by_provider[p["provider"]]["none"] += 1
        else:
            by_provider[p["provider"]]["other"] += 1
    print(f"extraction: {len(sample)} clusters x {len(provider_names)} providers "
          f"= {len(predictions)} calls, {ok} ok")
    for name, counts in by_provider.items():
        print(f"  {name}: {counts['ok']} ok / {counts['none']} none / {counts['other']} other")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
