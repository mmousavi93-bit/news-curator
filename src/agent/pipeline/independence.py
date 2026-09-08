"""Shared "independent corroborating groups" arithmetic (rulebook Step 1):
distinct credibility GROUPS among tier-1/2 members only. Tier 3 and lead
members never corroborate -- rumour policy: repost volume is amplification,
not evidence.

Extracted from pipeline/validate.py 2026-09-06 (fix 3) so
pipeline/cluster.py's Cluster.corroborating_count can reuse the EXACT tier
logic validate.py uses for claim_status, without a validate<->cluster
import cycle (validate.py imports the Cluster type at runtime; cluster.py
now needs this function too) and without copying the tier/group fallback
rule into a second file, where it could drift.
"""

from __future__ import annotations

from typing import Mapping, Sequence


def _tier(credibility: Mapping[str, object], source_id: str) -> str:
    entry = credibility.get(source_id)
    if entry is None:
        return "3"  # unlisted = tier 3 fallback (the join check prevents this)
    return str(getattr(entry, "tier", "3"))


def _group(credibility: Mapping[str, object], source_id: str) -> str:
    entry = credibility.get(source_id)
    group = getattr(entry, "group", None) if entry is not None else None
    # group: null resolves to a PREFIXED self-identifier, not the bare
    # source id (fix F, 2026-09-06 review) -- 19 strings in
    # credibility.yaml are simultaneously a source id and another
    # source's explicit `group:` value, so a bare fallback could silently
    # collide two sources into one corroborating group. Matches
    # cluster.py's `entry.group or f"__self__:{member.source_id}"`
    # exactly -- same rule, same string, one file each.
    return group if isinstance(group, str) and group else f"__self__:{source_id}"


def independent_groups(
    source_ids: Sequence[str], credibility: Mapping[str, object]
) -> set[str]:
    """Distinct corroborating groups among tier-1/2 members. Tier 3 and
    lead members never corroborate (rulebook Step 1)."""
    groups: set[str] = set()
    for sid in source_ids:
        if _tier(credibility, sid) in ("1", "2"):
            groups.add(_group(credibility, sid))
    return groups
