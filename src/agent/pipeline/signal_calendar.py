"""Offline countdown/schedule/soothing side channels, never SignalEvents."""
from dataclasses import dataclass
from datetime import datetime, timedelta

from agent.pipeline.signal_attribution import local_day, supporting_fields
from agent.pipeline.signal_contract import EvidenceError


@dataclass(frozen=True)
class Deadline:
    identity: str
    due: object
    candidate: object
    observed: object
    cancelled: bool = False


class SignalCalendar:
    def __init__(self):
        self.deadlines = {}
        self.schedules = {}
        self.statements = {}
        self.updates = []

    def update_deadline(self, candidate, items, credibility, *, now):
        day, _, count = supporting_fields(candidate, items, credibility, now)
        if candidate.signal_id != "C1" or count < 2:
            raise EvidenceError("deadline requires corroborated authority-issued C1")
        identity = candidate.occurrence_id
        action = candidate.action.casefold()
        old = self.deadlines.get(identity)
        if action in {"extend", "cancel"} and old is None:
            raise EvidenceError("calendar update has no grounded prior deadline")
        if old and (old.candidate.actor != candidate.actor or old.candidate.object != candidate.object):
            raise EvidenceError("conflicting deadline authority/topic")
        if action == "cancel":
            due = old.due
        else:
            if len(candidate.dates) != 1:
                raise EvidenceError("deadline requires one grounded date")
            due = candidate.dates[0]
            if due < day:
                raise EvidenceError("expired newly issued deadline")
            if old and due != old.due and action != "extend":
                raise EvidenceError("changed deadline requires explicit extension")
        entry = Deadline(identity, due, candidate, day, action == "cancel")
        self.deadlines[identity] = entry
        self.updates.append(entry)

    def update_schedule(self, identity, candidate, items, credibility, *, now):
        day, _, count = supporting_fields(candidate, items, credibility, now)
        if count < 2 or "schedule" not in candidate.predicates or len(candidate.dates) != 1:
            raise EvidenceError("schedule requires corroborated dated prior arrangement")
        old = self.schedules.get(identity)
        if candidate.action in {"reschedule", "cancel"} and old is None:
            raise EvidenceError("unknown schedule update")
        self.schedules[identity] = Deadline(identity, candidate.dates[0], candidate, day,
                                            candidate.action == "cancel")

    def add_soothing(self, candidate, items, credibility, *, now):
        _, _, count = supporting_fields(candidate, items, credibility, now)
        if count < 2 or not {"soothing", "official_statement"} <= candidate.predicates:
            raise EvidenceError("unsupported official soothing statement")
        times = [items[s.item_id].published_at for s in candidate.supports
                 if not s.relay_of and not s.uncertain
                 and getattr(credibility[items[s.item_id].source_id], "tier") in (1, 2)]
        if any(not isinstance(t, datetime) or t.tzinfo is None for t in times) or any(
            items[s.item_id].date_only for s in candidate.supports
        ):
            raise EvidenceError("ambiguous 72-hour statement timestamp")
        observed = min(times)
        if observed > now:
            raise EvidenceError("future soothing statement")
        old = self.statements.get(candidate.occurrence_id)
        # Reposts cannot renew a statement's 72-hour window.
        if old is None:
            self.statements[candidate.occurrence_id] = (observed, candidate)

    def kwargs(self, *, now):
        day = local_day(now)
        active = sorted((entry for entry in self.deadlines.values()
                         if not entry.cancelled and entry.due >= day),
                        key=lambda e: (e.due, e.identity))
        deception = any(timedelta(0) <= now - observed <= timedelta(hours=72)
                        for observed, _ in self.statements.values())
        return {"deadline": active[0].due if active else None, "deception": deception}
