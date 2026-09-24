"""Pre-LLM drop threshold calibration -- pure, offline, no embeddings.

Real CI runs do not yet carry `prellm_score`: the drop is gated off and the
scores exist only as a log histogram. This module is the ANALYSIS half of
the calibration loop. It consumes rows that do carry a score (`fate` +
`prellm_score` + `on_mission`) -- produced either by a real run once the
wiring is enabled, or by tools/score_historical_prellm.py on CI, which
rebuilds clusters from a run's read.csv and re-scores them with the real
MiniLM model -- and answers one question:

    at which threshold does dropping off-mission clusters remove the most
    LLM-budget waste (clusters the understand LLM later calls irrelevant or
    clickbait) without removing a cluster that would have been sent?

The droppable rule mirrors prellm_drop.drop_off_mission exactly: a row is
dropped at threshold T iff `on_mission` is false AND `score < T`. Clusters
the keyword gate marks on-mission are NEVER droppable, so they are reported
separately as guard-protected waste -- an honest ceiling on this lever.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

# The fates whose LLM call was spent and then thrown away. These are the
# only rows the drop is trying to save budget on.
WASTE_FATES = frozenset({"irrelevant", "clickbait"})

# The fates that actually produced a sent story. Losing one of these to the
# pre-LLM drop is the only real cost. Everything ELSE (repeat_dropped,
# cap_dropped, oversized, rank_dropped, lang_dropped, relevance_dropped,
# unparseable) never produces a story and is dropped later anyway, so a
# threshold that removes it pre-LLM is budget savings, not a loss -- it is
# reported separately and never counted in keep_lost.
KEEP_FATES = frozenset({"sent", "sent_followup", "lead_only"})


@dataclass(frozen=True, slots=True)
class Row:
    fate: str
    score: float
    on_mission: bool
    n_members: int = 0
    run: str = ""
    text: str = ""

    @property
    def is_waste(self) -> bool:
        return self.fate in WASTE_FATES

    @property
    def is_keep(self) -> bool:
        return self.fate in KEEP_FATES


@dataclass
class Loaded:
    """What a directory of scored CSVs yielded, plus what it could not."""

    rows: list[Row] = field(default_factory=list)
    unscored: int = 0          # rows with no usable prellm_score
    guard_unknown: int = 0     # rows with no on_mission column at all

    @property
    def waste(self) -> list[Row]:
        return [r for r in self.rows if r.is_waste]

    @property
    def keep(self) -> list[Row]:
        return [r for r in self.rows if r.is_keep]

    @property
    def other(self) -> list[Row]:
        """Dropped-anyway clusters (repeat/cap/oversized/rank/lang/...): never
        sent, dropped later by a cheaper gate or the cap. Removing them early
        saves budget but is neither waste caught nor a kept story lost."""
        return [r for r in self.rows if not r.is_waste and not r.is_keep]


@dataclass(frozen=True, slots=True)
class Point:
    threshold: float
    waste_caught: int
    waste_total: int
    keep_lost: int
    keep_total: int

    @property
    def recall(self) -> float:
        return self.waste_caught / self.waste_total if self.waste_total else 0.0

    @property
    def lost_rate(self) -> float:
        return self.keep_lost / self.keep_total if self.keep_total else 0.0


def _as_bool(raw: str | None) -> bool:
    return str(raw if raw is not None else "").strip().lower() in {"1", "true", "yes"}


def _as_float(raw: str | None) -> float | None:
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        return None


def rows_from_csv(path: str | Path, run: str = "") -> Loaded:
    """Read one scored cluster CSV (chosen_*.csv or a joined export)."""
    path = Path(path)
    loaded = Loaded()
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        for record in csv.DictReader(fh):
            score = _as_float(record.get("prellm_score"))
            if score is None:
                loaded.unscored += 1
                continue
            if "on_mission" not in record:
                loaded.guard_unknown += 1
            loaded.rows.append(Row(
                fate=str(record.get("fate") or "").strip(),
                score=score,
                on_mission=_as_bool(record.get("on_mission")),
                n_members=int(_as_float(record.get("n_members")) or 0),
                run=str(record.get("run_at_utc") or record.get("run") or run),
                text=str(record.get("headline") or record.get("text") or "").strip(),
            ))
    return loaded


def load(paths) -> Loaded:
    merged = Loaded()
    for path in paths:
        part = rows_from_csv(path)
        merged.rows.extend(part.rows)
        merged.unscored += part.unscored
        merged.guard_unknown += part.guard_unknown
    return merged


def sweep(rows, steps: int = 101) -> list[Point]:
    """Every grid threshold, so the caller can see the whole trade curve."""
    if not rows:
        return []
    waste_total = sum(1 for r in rows if r.is_waste)
    keep_total = sum(1 for r in rows if r.is_keep)
    points = []
    for index in range(steps):
        threshold = index / (steps - 1)
        droppable = [r for r in rows if not r.on_mission and r.score < threshold]
        points.append(Point(
            threshold=threshold,
            waste_caught=sum(1 for r in droppable if r.is_waste),
            waste_total=waste_total,
            keep_lost=sum(1 for r in droppable if r.is_keep),
            keep_total=keep_total,
        ))
    return points


def recommend(points: list[Point]) -> Point | None:
    """The most aggressive threshold that actually BUYS something: it drops
    at least one waste cluster and drops no cluster that would have been
    sent. Returns None when no such threshold exists.

    `threshold == 0.0` is excluded on purpose -- it is trivially free and
    buys exactly nothing, so counting it would launder "no separation" into
    a passing recommendation."""
    useful = [p for p in points if p.keep_lost == 0 and p.waste_caught > 0]
    if not useful:
        return None
    return max(useful, key=lambda p: p.threshold)


def guard_blocked_waste(rows) -> list[Row]:
    """Waste the drop can NEVER reach: the keyword gate called it on-mission,
    so prellm_drop keeps it regardless of embedding score."""
    return [r for r in rows if r.on_mission and r.is_waste]


def render_report(loaded: Loaded, points: list[Point]) -> str:
    lines = [
        f"scored clusters : {len(loaded.rows)}",
        f"  waste (irrelevant/clickbait fate) : {len(loaded.waste)}",
        f"  kept  (sent/sent_followup/lead_only) : {len(loaded.keep)}",
        f"  other (dropped later anyway)      : {len(loaded.other)} -- budget the",
        "        drop can also save pre-LLM, not scored as waste or loss",
    ]
    if loaded.unscored:
        lines.append(f"  SKIPPED (no prellm_score)         : {loaded.unscored}")
    if loaded.guard_unknown:
        lines.append(
            f"  WARNING: {loaded.guard_unknown} rows had no on_mission column --"
            " the keyword guard's contribution is unknown for these, so the"
            " recoverable-waste numbers below are an UPPER BOUND."
        )
    blocked = guard_blocked_waste(loaded.rows)
    lines.append("")
    lines.append("guard-protected waste (on_mission=1 AND fated irrelevant/clickbait):")
    lines.append(f"  {len(blocked)} clusters -- unreachable by this lever at any threshold")
    lines.append("")
    lines.append(" threshold  waste_caught/ total  recall  kept_lost/ total  lost_rate")
    for point in points:
        if point.threshold * 100 % 5 or point.threshold == 0.0:
            continue  # print every 0.05 for legibility
        if point.waste_caught or point.keep_lost:
            lines.append(
                f"    {point.threshold:.2f}      {point.waste_caught:4d}/{point.waste_total:<4d}"
                f"   {point.recall:6.1%}   {point.keep_lost:4d}/{point.keep_total:<4d}"
                f"   {point.lost_rate:6.1%}"
            )
    best = recommend(points)
    lines.append("")
    if best is None:
        if any(p.waste_caught > 0 for p in points):
            lines.append("VERDICT: waste only scores at or above kept clusters -- every"
                         " threshold that catches any of it also drops a story that would"
                         " have been sent. Do not enable.")
        else:
            lines.append("VERDICT: no waste cluster scores below any kept cluster, so no"
                         " threshold buys anything. Do not enable.")
    else:
        lines.append(
            f"VERDICT: threshold {best.threshold:.2f} drops {best.waste_caught} of"
            f" {best.waste_total} waste clusters ({best.recall:.1%}) at zero cost"
            f" to kept clusters."
        )
    return "\n".join(lines)
