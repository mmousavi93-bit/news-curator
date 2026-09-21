"""Signal extraction -- the Phase 7 prompt, now a callable, measurable unit.

ARCHITECTURE.md §v1.5: UNDERSTAND emits summary, entities, signals and claim
status in ONE call per cluster. This module is the signals half of that
contract, built FIRST as an offline-measurable unit so the accuracy gate
(measure extraction precision/recall BEFORE paying any adjudicator) has
something to run over historical clusters.

DELIBERATELY NOT WIRED into build_stages yet: the same-call-vs-separate-call
decision is deferred until measured accuracy + token cost are known. A
separate call per cluster doubles LLM load and breaks the ~40-call budget
(constraint 2). This module makes exactly one router call per cluster and
returns engine.SignalEvent objects; it persists nothing and scores nothing.

The prompt lives in config/prompts/signal_extraction.txt (migrated verbatim
from analysis/AGENT_PROMPT.md, its Phase 7 instruction).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Mapping

from agent.config import ConfigError
from agent.pipeline.batch import render_prompt
from agent.pipeline.cluster import Cluster
from agent.pipeline.contract import extract_json
from agent.risk.engine import SignalEvent


def signal_catalog(weights_raw: Mapping[str, object]) -> frozenset[str]:
    """Valid news-extraction signal ids: risk_weights.yaml `signals` keys,
    minus C1 (a deadline countdown, not a discrete signal -- the prompt's own
    C1 rule routes it to `countdowns`) and minus `covered_by_markets_fetcher`
    (G1/G2 arrive from the markets fetcher, never from news). The engine's
    catalog skips C1 too, so emitting it would KeyError downstream."""
    signals = weights_raw.get("signals")
    if not isinstance(signals, dict) or not signals:
        raise ConfigError("risk_weights.yaml: expected a non-empty 'signals' mapping")
    market_only = set(weights_raw.get("covered_by_markets_fetcher") or ())
    return frozenset(signals) - {"C1"} - market_only


@dataclass(frozen=True)
class ExtractedSignal:
    """One signal the model extracted, exactly as the prompt schema defines it
    (countdowns/scheduled_events/soothing_statements/economic_events are
    consumed by a later layer, not here)."""

    signal_id: str
    claim: str
    actor: str
    event_date: date
    sources: tuple[str, ...]
    quote: str
    confidence_notes: str | None
    state_update: bool


@dataclass(frozen=True)
class ExtractionResult:
    """Outcome of extracting one cluster. `status` is "ok" | "none" |
    "unparseable" | the LlmResult status (REFUSED_CAP/UNAVAILABLE/FATAL)."""

    signals: tuple[ExtractedSignal, ...]
    status: str
    provider: str | None
    none: bool  # the model answered none:true
    dropped_ids: tuple[str, ...]  # signal_ids emitted but not in the catalog


def parse_signals(payload: dict, catalog: frozenset[str]) -> tuple[list[dict], list[str]]:
    """Validate the parsed JSON object's `signals` list against the catalog.
    Returns (valid signal dicts, dropped signal ids). Non-dict elements are
    silently skipped; unknown signal ids are dropped and reported."""
    raw_signals = payload.get("signals")
    if not isinstance(raw_signals, list):
        return [], []
    valid: list[dict] = []
    dropped: list[str] = []
    for element in raw_signals:
        if not isinstance(element, dict):
            continue
        signal_id = element.get("signal_id")
        if not isinstance(signal_id, str) or signal_id not in catalog:
            dropped.append(signal_id if isinstance(signal_id, str) else "<missing>")
            continue
        valid.append(element)
    return valid, dropped


def _parse_event_date(raw: object, fallback: date) -> date:
    """ISO date (or ISO datetime) -> date; anything unparseable falls back to
    the cluster's latest publish date. Timing integrity is the whole product
    (prompt Rule 7), so a broken date must not silently become 'today'."""
    if isinstance(raw, str):
        text = raw.strip()
        try:
            return datetime.fromisoformat(text).date()
        except ValueError:
            try:
                return date.fromisoformat(text)
            except ValueError:
                pass
    return fallback


def _cluster_source_tier(cluster: Cluster, credibility: Mapping[str, object]) -> int:
    """Best (lowest numeric) credibility tier among the cluster's members.
    Mirrors independence.py: only tiers 1/2 corroborate; tier 3 and `lead`
    never do. Returns 1 if any tier-1 member, else 2 -- a rumour-only cluster
    (no corroborating member) is gated by Step 1 regardless of this value."""
    best = 2
    for member in cluster.members:
        entry = credibility.get(member.source_id)
        tier = getattr(entry, "tier", 3) if entry is not None else 3
        if isinstance(tier, int) and tier in (1, 2) and tier < best:
            best = tier
    return best


def attribute_signal_event(
    cluster: Cluster, credibility: Mapping[str, object], signal: ExtractedSignal
) -> SignalEvent:
    """Bridge an extracted signal to the engine's scoring input.

    PROXIES (documented, refined later): `independent_source_count` is the
    cluster's corroborating-group count -- an UPPER bound on the signal's own
    corroboration (per-signal source attribution from `sources` is a
    downstream storage-layer concern); `source_tier` is the cluster's best
    corroborating tier; `state_end_date` is always None (state END is
    computed by later runs, not extraction)."""
    return SignalEvent(
        signal_id=signal.signal_id,
        event_date=signal.event_date,
        source_tier=_cluster_source_tier(cluster, credibility),
        independent_source_count=cluster.corroborating_count(credibility),
    )


def _to_extracted(raw: dict, cluster: Cluster) -> ExtractedSignal:
    sources = raw.get("sources")
    notes = raw.get("confidence_notes")
    return ExtractedSignal(
        signal_id=raw["signal_id"],
        claim=str(raw.get("claim") or ""),
        actor=str(raw.get("actor") or ""),
        event_date=_parse_event_date(raw.get("event_date"), cluster.latest().date()),
        sources=tuple(str(s) for s in sources) if isinstance(sources, list) else (),
        quote=str(raw.get("quote") or ""),
        confidence_notes=notes if isinstance(notes, str) and notes else None,
        state_update=raw.get("state_update") is True,
    )


def extract(
    router,
    cluster: Cluster,
    *,
    template: str,
    body_chars: int,
    catalog: frozenset[str],
    credibility: Mapping[str, object],
    logger,
) -> ExtractionResult:
    """One cluster -> one router call -> ExtractedSignals.

    `router` exposes `complete(prompt, *, stage=...) -> LlmResult` (the same
    shape UnderstandStage uses); `catalog` is signal_catalog(weights_raw),
    computed once per run, not per cluster.
    """
    prompt = render_prompt(template, cluster, body_chars)
    result = router.complete(prompt, stage="signals")
    if not result.ok:
        return ExtractionResult((), result.status, result.provider, False, ())
    try:
        parsed = extract_json(result.text)
    except ValueError:
        logger.error("signals: cluster %s response unparseable -- skipped", cluster.key)
        return ExtractionResult((), "unparseable", result.provider, False, ())
    if parsed.get("none") is True:
        return ExtractionResult((), "none", result.provider, True, ())
    valid, dropped = parse_signals(parsed, catalog)
    if dropped:
        logger.warning(
            "signals: cluster %s emitted unknown signal ids %s -- dropped",
            cluster.key,
            sorted(set(dropped)),
        )
    signals = tuple(_to_extracted(signal, cluster) for signal in valid)
    return ExtractionResult(signals, "ok", result.provider, False, tuple(dropped))
