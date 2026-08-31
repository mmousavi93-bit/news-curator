"""The deterministic matcher — zero LLM calls (constraint 3).

Identical input always produces identical matches, which is what makes
the alert channel's behavior testable and tunable. Matching rules:

- normalize: NFC, strip ZWNJ (نیم‌فاصله), Arabic->Persian codepoints
  (ي ى -> ی, ك -> ک, أ إ -> ا, ة -> ه), Latin lowercase. The langgate.py
  marker set, reused for MATCHING instead of rejection.
- term match: substring over normalized title + first `window_chars` of
  body (buckets must sit in the lead, not in a recap tail).
- location match: whole-token equality for single words («ری» never
  matches «خبری»); substring for multi-word phrases («میدان آزادی»).
- exclusions: substring over the FULL text; any hit kills the fire.
- freshness gate: only items published within `freshness_minutes` of now
  can fire; date-only items (day, no time) and undated items are dropped
  — a re-shared 2020 video with today's caption is the one class this
  gate cannot catch, and the «شایعه» label is its mitigation.
- class precedence: classes are checked in config order; the first class
  whose buckets both match wins.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from agent.flash.config import AlertClass, FlashConfig
from agent.flash.textnorm import normalize, tokens as _tokens  # noqa: F401


def _term_bucket(text: str, alert_class: AlertClass) -> str | None:
    for bucket, terms in alert_class.terms.items():
        if any(term in text for term in terms):
            return bucket
    return None


def _location(text: str, alert_class: AlertClass,
              allowed_rings: tuple[str, ...]) -> tuple[str, str] | None:
    """(ring, token) or None. Single-word terms are whole-token matches;
    multi-word phrases are substring matches. RING PRECEDENCE FIRST: the
    rings are ordered by importance in the config (iran_geo before
    actors), so «حمله آمریکا به لارک» displays لارک (the target), never
    آمریکا (the attacker). WITHIN a ring the most specific token wins:
    longest, ties broken by earliest occurrence. `allowed_rings` is the
    bucket's ring_requirements — action buckets must hit Iran territory,
    statement buckets may match actors (owner live feedback 2026-08-31:
    routine Gaza-front coverage is not escalation)."""
    tokens = _tokens(text)
    for ring, locations in alert_class.locations.items():
        if ring not in allowed_rings:
            continue
        best: tuple[int, int, str] | None = None  # (len, -index, loc)
        for loc in locations:
            if " " in loc:
                index = text.find(loc)
                if index < 0:
                    continue
                candidate_len = len(loc)
            elif loc in tokens or (
                # Arabic attaches the definite article: «الإيراني» is the
                # only Iran-marker in the live IRGC-navy item, and whole-
                # token matching cannot see it. Token-END matching covers
                # it; the >=4-char floor keeps «ری» from matching «خبری».
                len(loc) >= 4 and any(t.endswith(loc) for t in tokens)
            ):
                index = text.find(loc)
                candidate_len = len(loc)
            else:
                continue
            key = (candidate_len, -index)
            if best is None or key > (best[0], best[1]):
                best = (candidate_len, -index, loc)
        if best is not None:
            return ring, best[2]
    return None


@dataclass(frozen=True, slots=True)
class Match:
    class_name: str
    term_bucket: str
    location_ring: str
    location_token: str
    item: object
    signature: str  # class-level for burst_scope: class, else class|bucket|ring


def _fresh(item, freshness_minutes: int, now: datetime) -> bool:
    published = item.published_at
    if published is None or getattr(item, "date_only", False):
        return False
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    published = published.astimezone(timezone.utc)
    return published >= now - timedelta(minutes=freshness_minutes)


def match_items(items, config: FlashConfig, now: datetime):
    """(matches, kills). kills = (source_id, reason) for the alert log —
    a no-term item is routine volume and is NOT logged, a term-hit that
    fails location or exclusion is a near-miss and IS."""
    matches: list[Match] = []
    kills: list[tuple[str, str]] = []
    for item in items:
        if not _fresh(item, config.freshness_minutes, now):
            kills.append((item.source_id, "stale"))
            continue
        text = normalize(f"{item.title}\n{item.body[:config.window_chars]}")
        full = normalize(f"{item.title}\n{item.body}")
        if any(term in full for term in config.exclusions):
            kills.append((item.source_id, "excluded"))
            continue
        for class_name, alert_class in config.classes.items():
            bucket = _term_bucket(text, alert_class)
            if bucket is None:
                continue
            allowed = alert_class.ring_requirements.get(bucket) or tuple(
                alert_class.locations)
            location = _location(text, alert_class, allowed)
            if location is None:
                # A term hit without an allowed location must not block
                # later classes: «حمله موشکی» in a non-Tehran item is a
                # tehran no_location but may be a live escalation
                # (the Larak FA item carries موشک). A Gaza artillery
                # story is a strike whose ring requirement (iran_geo)
                # is unmet — killed here, exactly as designed.
                kills.append((item.source_id, f"{class_name}:no_location"))
                continue
            ring, token = location
            signature = (
                class_name if alert_class.burst_scope == "class"
                else f"{class_name}|{bucket}|{ring}"
            )
            matches.append(Match(
                class_name=class_name, term_bucket=bucket,
                location_ring=ring, location_token=token, item=item,
                signature=signature,
            ))
            break  # class precedence: first matching class wins
    return matches, kills
