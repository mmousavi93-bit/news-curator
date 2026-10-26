"""Strict offline evidence envelope. Quotes prove provenance, never truth.

Predicate names are semantic assertions made by the classifier, not Python
keyword heuristics. Their entailment must be measured on reviewed history.
Item URLs are the stable item IDs; source names and cluster keys are not IDs.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import re


class EvidenceError(ValueError):
    """Rejected or quarantined evidence; the message is an audit reason."""


PREDICATES = {
    "A1": "identified_csg ordered_centcom", "A2": "simultaneous_csg_overlap",
    "A3": "new_battery_emplaced", "A4": "bombers_deployed_in_range",
    "A5": "same_tanker_surge", "A6": "actual_munitions_movement",
    "A7": "gulf_access_restricted", "A8": "regeneration_or_exercise_completed",
    "A9": "mobilization_order", "B1": "official_ordered_embassy_departure",
    "B2": "authorized_military_family_departure", "B3": "advisory_escalation",
    "B4": "prestrike_suspension_or_insurance_spike",
    "B5": "schedule_cancelled", "B6": "major_prestrike_airspace_closure",
    "C1": "authority_dated_deadline", "C2": "final_round_failed maximalist_demands",
    "C3": "verified_nonconvening", "C4": "formal_censure_or_snapback",
    "C5": "enacted_force_predicate", "C6": "named_mediator_quits",
    "D1": "leader_ultimatum timeframe_or_targets", "D2": "same_leader_negative_shift",
    "D3": "iranian_route_closure_shift", "D4": "chronic_threat_or_anonymous_preparation",
    "E1": "iran_attributed commercial_vessel_impact gulf_waters",
    "E2": "distinct_second_vessel_attack iran_attributed commercial_vessel_impact gulf_waters", "E3": "us_gulf_asset_impact",
    "E4": "proxy_front_widens", "E5": "major_interception_or_near_miss",
    "E6": "major_infrastructure_cyberattack corroborated_attribution",
    "H1": "mass_protests lethal_crackdown", "H2": "national_blackout measurement_org",
    "H3": "comparable_bazaar_rial",
    "soothing": "soothing official_statement", "schedule": "schedule",
}
STATE_ENDS = {
    "A1": "csg_exits_or_order_cancelled", "A2": "theater_count_below_two",
    "A3": "battery_removed_or_deactivated", "A4": "bombers_leave_range",
    "A7": "restriction_lifted", "H1": "lethal_crackdown_ends",
}


@dataclass(frozen=True)
class Support:
    item_id: str
    quote: str
    relay_of: str | None = None  # configured origin source ID; never a new group
    uncertain: bool = False


@dataclass(frozen=True)
class Candidate:
    signal_id: str
    claim: str
    actor: str
    action: str
    object: str
    location: str
    occurrence_id: str
    episode_anchor: str
    supports: tuple[Support, ...]
    predicates: frozenset[str]
    occurrence_date: date | None = None
    transition: str = "start"  # start, reaffirm, end
    effective_date: date | None = None
    prior_ids: tuple[str, ...] = ()
    numbers: tuple[float, ...] = ()
    dates: tuple[date, ...] = ()
    status: str = "current_affirmative"


def _required(raw, name):
    value = raw.get(name)
    if not isinstance(value, str) or not value.strip():
        raise EvidenceError(f"missing {name}")
    return value.strip()


def validate_candidate(raw, items, catalog, *, calendar=False):
    """Validate each nomination against only its own exact member quotes.

    Numeric/date literals in claims and structured fields must appear in every
    support quote. Relative dates need a separately grounded ISO date in the
    evidence; unresolved dates are deliberately quarantined for temporal review.
    """
    if not isinstance(raw, dict):
        raise EvidenceError("candidate must be an object")
    sid = _required(raw, "signal_id")
    allowed = ({"C1"} if calendar is True else {calendar}) if calendar else set(catalog) - {"C1", "G1", "G2"}
    if sid not in allowed or sid not in PREDICATES:
        raise EvidenceError(f"unknown/foreign signal ID: {sid}")
    allowed_fields = {"signal_id", "claim", "actor", "action", "object", "location",
                      "occurrence_id", "episode_anchor", "supports", "predicates",
                      "occurrence_date", "transition", "effective_date", "prior_ids",
                      "numbers", "dates", "status"}
    if set(raw) - allowed_fields:
        raise EvidenceError("unknown candidate fields (model event dates are forbidden)")
    names = ("claim", "actor", "action", "object", "location", "occurrence_id", "episode_anchor")
    fields = {name: _required(raw, name) for name in names}
    if raw.get("status") != "current_affirmative":
        raise EvidenceError("noncurrent, hypothetical, denied or historical claim")
    transition = raw.get("transition", "start")
    if transition not in {"start", "reaffirm", "end"}:
        raise EvidenceError("invalid transition")
    if transition != "start" and sid not in STATE_ENDS:
        raise EvidenceError("non-state transition")
    predicates = raw.get("predicates")
    if not isinstance(predicates, list) or not all(isinstance(p, str) for p in predicates):
        raise EvidenceError("missing predicates")
    required = {STATE_ENDS[sid]} if transition == "end" else set(PREDICATES[sid].split())
    if not required <= set(predicates):
        raise EvidenceError("unsupported mapping predicate")
    evidence = raw.get("supports")
    if not isinstance(evidence, list) or not evidence:
        raise EvidenceError("missing supports")
    supports = []
    for row in evidence:
        if not isinstance(row, dict):
            raise EvidenceError("invalid support")
        item_id, quote = _required(row, "item_id"), _required(row, "quote")
        if item_id not in items:
            raise EvidenceError("foreign item ID")
        item = items[item_id]
        if quote not in item.title and quote not in item.body:
            raise EvidenceError("nonmatching quote")
        relay = row.get("relay_of")
        if relay is not None and (not isinstance(relay, str) or not relay):
            raise EvidenceError("invalid relay origin")
        if type(row.get("uncertain", False)) is not bool:
            raise EvidenceError("invalid provenance flag")
        supports.append(Support(item_id, quote, relay, row.get("uncertain", False)))
    if len({s.item_id for s in supports}) != len(supports):
        raise EvidenceError("duplicate supporting item")
    def grounded_date(value):
        if value is None:
            return None
        try:
            parsed = date.fromisoformat(value)
        except (ValueError, TypeError):
            raise EvidenceError("unsupported date") from None
        if not all(value in s.quote for s in supports):
            raise EvidenceError("unsupported date")
        return parsed
    occurrence_date = grounded_date(raw.get("occurrence_date"))
    effective_date = grounded_date(raw.get("effective_date"))
    dates_raw, numbers = raw.get("dates", []), raw.get("numbers", [])
    if not isinstance(dates_raw, list) or not isinstance(numbers, list):
        raise EvidenceError("invalid dates/numbers")
    dates = tuple(grounded_date(d) for d in dates_raw)
    if None in dates:
        raise EvidenceError("unsupported date")
    if any(type(n) not in (int, float) for n in numbers):
        raise EvidenceError("unsupported number")
    # Unicode decimal digits normalize via int/float (including Persian/Arabic).
    literal_text = " ".join(fields[n] for n in ("claim", "actor", "action", "object", "location"))
    claimed = re.findall(r"\d+(?:\.\d+)?", literal_text)
    for support in supports:
        quoted = {float(v) for v in re.findall(r"\d+(?:\.\d+)?", support.quote)}
        if any(float(v) not in quoted for v in (*claimed, *numbers)):
            raise EvidenceError("unsupported number/date literal")
    prior = raw.get("prior_ids", [])
    if not isinstance(prior, list) or not all(isinstance(p, str) and p for p in prior):
        raise EvidenceError("invalid historical references")
    return Candidate(sid, **fields, supports=tuple(supports), predicates=frozenset(predicates),
                     occurrence_date=occurrence_date, transition=transition,
                     effective_date=effective_date, prior_ids=tuple(prior),
                     numbers=tuple(numbers), dates=dates)


def validate_envelope(payload):
    if not isinstance(payload, dict) or type(payload.get("none")) is not bool:
        raise EvidenceError("explicit none boolean required")
    signals = payload.get("signals", [])
    countdowns = payload.get("countdowns", [])
    side = [payload.get(name, []) for name in ("soothing_statements", "scheduled_events")]
    if not all(isinstance(rows, list) for rows in [signals, countdowns, *side]):
        raise EvidenceError("candidates must be lists")
    if payload["none"] and (signals or countdowns or any(side)):
        raise EvidenceError("none plus candidates contradiction")
    if not payload["none"] and not signals and not countdowns and not any(side):
        raise EvidenceError("empty extraction must explicitly say none")
