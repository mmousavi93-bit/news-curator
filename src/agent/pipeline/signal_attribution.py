"""Pure deterministic field derivation; no scoring or model dependencies."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from agent.pipeline.independence import independent_groups
from agent.pipeline.signal_contract import Candidate, EvidenceError, STATE_ENDS

TEHRAN = ZoneInfo("Asia/Tehran")


@dataclass(frozen=True)
class AttributedSignal:
    signal_id: str
    event_date: date
    source_tier: int
    independent_source_count: int
    novelty: float
    state_end_date: date | None

    def fields(self):
        return vars(self).copy()


def local_day(now):
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise EvidenceError("evaluation requires an aware injected datetime")
    return now.astimezone(TEHRAN).date()


def publication_day(item, now):
    value = item.published_at
    if value is None:
        return None
    if isinstance(value, datetime):
        if item.date_only:
            day = value.date()  # preserve the original calendar date
        elif value.tzinfo is None:
            return None
        elif value > now:
            return None
        else:
            day = value.astimezone(TEHRAN).date()
    elif type(value) is date:
        day = value
    else:
        return None
    return day if day <= local_day(now) else None


def supporting_fields(candidate, items, credibility, now):
    """Join strictly; exclude relays, uncertain origins and noncorroborators."""
    local_day(now)
    eligible, nominations = [], []
    for support in candidate.supports:
        if support.item_id not in items:
            raise EvidenceError("foreign item ID")
        item = items[support.item_id]
        if item.source_id not in credibility:
            raise EvidenceError(f"missing credibility join: {item.source_id}")
        entry = credibility[item.source_id]
        tier = getattr(entry, "tier", None)
        if tier != "lead" and (type(tier) is not int or tier not in (1, 2, 3)):
            raise EvidenceError("credibility tier must be strict integer 1/2/3 or lead")
        if support.relay_of and support.relay_of not in credibility:
            raise EvidenceError("missing relay credibility join")
        if support.uncertain:
            raise EvidenceError("uncertain provenance: review required")
        if support.relay_of or tier == "lead":
            continue
        day = publication_day(item, now)
        if day is not None:
            (eligible if tier in (1, 2) else nominations).append((item, tier, day))
    rows = eligible or nominations
    if not rows:
        raise EvidenceError("missing/all-future/ambiguous supporting publication dates or lead-only")
    count = len(independent_groups([r[0].source_id for r in eligible], credibility))
    return min(r[2] for r in rows), min(r[1] for r in rows), count


def historical_predicates(candidate, history, day):
    """Ground joins against retained accepted evidence, with no lookahead."""
    sid = candidate.signal_id
    if sid not in {"A2", "E2", "D2", "D3", "B3", "B5", "C3", "H3", "A5"}:
        return
    if sid == "A5":
        if len(candidate.numbers) != 1 or candidate.numbers[0] < 10:
            raise EvidenceError("A5 requires at least ten aircraft in one surge")
        return
    if sid == "H3":
        if (len(candidate.numbers) != 2 or len(candidate.dates) != 2
                or not 0 < (candidate.dates[1] - candidate.dates[0]).days <= 7
                or candidate.dates[1] > day or min(candidate.numbers) <= 0):
            raise EvidenceError("invalid comparable rial observations")
        # IRR per USD rising means the rial depreciates; model does no arithmetic.
        if "irr_per_usd" not in candidate.predicates or candidate.numbers[1] / candidate.numbers[0] - 1 <= .15:
            raise EvidenceError("H3 depreciation must exceed 15 percent")
        return
    if history is None:
        raise EvidenceError("insufficient_history")
    if sid in {"A2", "E2"}:
        prior = [history.records.get(key) for key in candidate.prior_ids]
        prior = [r for r in prior if r and r.event.independent_source_count >= 2
                 and r.event.event_date <= day]
        if sid == "A2":
            active = [r for r in prior if r.event.signal_id == "A1"
                      and r.candidate.location.casefold() == candidate.location.casefold()
                      and (r.event.state_end_date is None or r.event.state_end_date > day)]
            if len({r.candidate.object.casefold() for r in active}) < 2:
                raise EvidenceError("A2 requires supported simultaneous theater posture")
        else:
            occurred = candidate.occurrence_date
            if occurred is None or not any(
                r.event.signal_id == "E1" and r.candidate.occurrence_date is not None
                and r.candidate.occurrence_id != candidate.occurrence_id
                and 0 <= (occurred - r.candidate.occurrence_date).days <= 7
                for r in prior
            ):
                raise EvidenceError("E2 requires distinct accepted attack within seven days")
        return
    baselines = [history.baselines.get(key) for key in candidate.prior_ids]
    baselines = [b for b in baselines if b and b.actor == candidate.actor
                 and b.topic == candidate.object and b.observed <= day
                 and 0 <= (day - b.observed).days <= 30]
    kind = {"D2": "positive_statement", "D3": "route_control", "B3": "advisory",
            "B5": "schedule", "C3": "schedule"}[sid]
    baselines = [b for b in baselines if b.kind == kind]
    if not baselines:
        raise EvidenceError("insufficient_history")
    if sid == "D2" and not any(0 < (day - b.observed).days <= 14 for b in baselines):
        raise EvidenceError("D2 mixed same-day or out-of-window statements")
    if sid in {"B5", "C3"} and not any(
        b.due is not None and ((abs((day - b.due).days) <= 7) if sid == "B5" else day > b.due)
        for b in baselines
    ):
        raise EvidenceError("schedule not due / outside cancellation window")


def derive(candidate: Candidate, items, credibility, *, now, history=None):
    day, tier, count = supporting_fields(candidate, items, credibility, now)
    historical_predicates(candidate, history, day)
    if candidate.occurrence_date and candidate.occurrence_date > day:
        raise EvidenceError("future occurrence")
    if candidate.occurrence_date and candidate.occurrence_date < day:
        if history is None or history.find(candidate) is None:
            raise EvidenceError("historical recap / late first report: temporal review")
    if candidate.effective_date and candidate.transition != "end":
        raise EvidenceError("effective end date on non-end evidence")
    if candidate.transition == "end":
        if candidate.signal_id not in STATE_ENDS or count < 2:
            raise EvidenceError("uncorroborated/invalid state end")
        end = candidate.effective_date or day
        if end > local_day(now):
            raise EvidenceError("future withdrawal pending verification")
    else:
        end = None
    novelty = history.factor(candidate.signal_id) if history else 1.0
    return AttributedSignal(candidate.signal_id, day, tier, count, novelty, end)
