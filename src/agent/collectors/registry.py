"""Reads config/sources.yaml, validates the credibility join, dispatches
each ENABLED source to the right collector by `type`, and aggregates the
results into a CollectReport. Formatting that report as the table
agent.run's --collect-only prints, or as the JSON
.github/workflows/collect-test.yml asserts against, lives in report.py --
split out to keep this file under the ~200-line cap (CLAUDE.md
constraint #12), same shape as Phase 2's telegram.py -> credentials.py +
transport.py split.

Per-host serialisation, cross-host parallelism: tools/check_feeds.py proved
that probing two paths on one host concurrently self-inflicts a 429.
Several ids in this project's source set share a host (all three BBC feeds,
every t.me channel), so the same discipline applies here.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit

from agent.collectors import fetch, rss, telegram_web
from agent.collectors.base import SourceResult, SourceSpec
from agent.config import SourceCredibility, load_yaml
from agent.settings import Settings

# Whole-collect-stage ceiling, independent of any single request's own wall
# deadline (fetch.py). Not a settings.yaml key: settings_schema.py is
# strict/closed and a schema change is out of this phase's file list
# (PHASE_3_BRIEF.md's Files table only authorises editing
# collection.user_agent). 180s covers the 10 enabled sources -- and the 51
# staged ones, once Phase 8 flips flags -- serialised across ~40 distinct
# hosts at up to a 10s wall deadline each, well inside GitHub's 6h kill.
STAGE_DEADLINE_SECONDS = 180

_DISPATCH = {"rss": rss.collect, "telegram": telegram_web.collect}


class SourcesError(Exception):
    """Raised for a sources.yaml problem: bad shape, a plaintext URL, or a
    credibility-join miss. Same fail-fast shape as ConfigError -- every
    offender is collected before raising once."""


def load_sources(*, base: Path | None = None) -> list[SourceSpec]:
    raw = load_yaml("sources.yaml", base=base)
    rows = raw.get("sources", [])
    if not isinstance(rows, list):
        raise SourcesError("sources.yaml: 'sources' must be a list")

    errors: list[str] = []
    specs: list[SourceSpec] = []
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            errors.append(f"sources[{i}]: expected a mapping, got {type(row).__name__}")
            continue
        source_id = row.get("id")
        url = str(row.get("url", ""))
        if not source_id:
            errors.append(f"sources[{i}]: missing 'id'")
            continue
        if not url.startswith("https://"):
            # Checked for every entry, enabled or not -- `mee` was plaintext
            # http:// through all four probe rounds while `enabled: false`.
            errors.append(f"sources[{i}] ({source_id}): url is not https:// -- {url!r}")
        specs.append(SourceSpec(
            id=str(source_id),
            name=str(row.get("name", source_id)),
            url=url,
            type=str(row.get("type", "")),
            lang=str(row.get("lang", "")),
            enabled=bool(row.get("enabled", False)),
            max_items=row.get("max_items"),
            topic_gate=bool(row.get("topic_gate", False)),
            signals_covered=tuple(row.get("signals_covered") or ()),
        ))
    if errors:
        raise SourcesError("; ".join(errors))
    return specs


def validate_join(sources: list[SourceSpec], credibility: Mapping[str, SourceCredibility]) -> None:
    """Every id in sources.yaml -- enabled AND disabled -- must already be
    a credibility.yaml key. A missing key does not error on its own; it
    silently degrades to `defaults: tier 3, group unlisted` (the
    2026-08-17 incident: 13 sources including Reuters/AP/BBC ME/Haaretz
    were live in that state at once, and nobody noticed by reading the
    file). Checks all 51, not just the 10 enabled -- requirement 1 is
    explicit that enabled-only checking lets a typo in a staged entry
    survive until Phase 8's flag-flip and crash every run after."""
    missing = sorted(s.id for s in sources if s.id not in credibility)
    if missing:
        raise SourcesError(
            "sources.yaml ids missing from credibility.yaml: " + ", ".join(missing)
        )


@dataclass(frozen=True, slots=True)
class CollectReport:
    generated_at: datetime
    results: Mapping[str, SourceResult]
    total_kept: int
    sources_with_items: int
    sources_enabled: int


def _collect_one(spec: SourceSpec, settings: Settings) -> SourceResult:
    collector = _DISPATCH.get(spec.type)
    if collector is None:
        return SourceResult(spec.id, raw_entries=0, parsed=0, kept=0, items=[],
                             error=f"unknown source type '{spec.type}'")

    max_items = spec.max_items or settings.collection.max_items_per_source
    try:
        fetched = fetch.fetch(
            spec.url,
            user_agent=settings.collection.user_agent,
            timeout_seconds=float(settings.collection.per_source_timeout_seconds),
        )
    except fetch.FetchError as exc:
        # A 403/429 is a definitive refusal per DECISION 2b -- recorded, and
        # the run continues. Never retried with different headers, a
        # different path, a proxy, or an archive mirror.
        return SourceResult(spec.id, raw_entries=0, parsed=0, kept=0, items=[], error=str(exc))

    try:
        return collector(spec, fetched.body, fetched.content_type, max_items)
    except Exception as exc:  # noqa: BLE001 -- a parse failure must never abort the run
        return SourceResult(spec.id, raw_entries=0, parsed=0, kept=0, items=[],
                             error=f"{type(exc).__name__}: {exc}")


def collect_all(sources: list[SourceSpec], settings: Settings, now: datetime) -> CollectReport:
    """Fetch + parse every ENABLED source. One source's failure is recorded
    on its own SourceResult and never aborts the stage -- the try/except
    boundary in _collect_one is per source."""
    enabled = [s for s in sources if s.enabled]
    buckets: dict[str, list[SourceSpec]] = {}
    for spec in enabled:
        buckets.setdefault(urlsplit(spec.url).netloc.lower(), []).append(spec)

    deadline = time.monotonic() + STAGE_DEADLINE_SECONDS

    def run_host_bucket(bucket: list[SourceSpec]) -> list[SourceResult]:
        out = []
        for spec in bucket:  # serial within a host, on purpose
            if time.monotonic() > deadline:
                out.append(SourceResult(spec.id, raw_entries=0, parsed=0, kept=0, items=[],
                                         error="collect stage deadline exceeded before this source ran"))
                continue
            out.append(_collect_one(spec, settings))
        return out

    results: dict[str, SourceResult] = {}
    workers = max(1, min(settings.collection.concurrency, len(buckets) or 1))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for bucket_results in pool.map(run_host_bucket, buckets.values()):
            for res in bucket_results:
                results[res.source_id] = res

    total_kept = sum(r.kept for r in results.values())
    sources_with_items = sum(1 for r in results.values() if r.kept > 0)
    return CollectReport(now, results, total_kept, sources_with_items, len(enabled))
