"""Injectable in-memory occurrence, episode and quiet-cycle ledger.

Construct a fresh store explicitly for a new history. Pass the same store on
replay; missing/corrupt required ledgers raise rather than silently resetting.
No persistence, wall clock, risk imports or expiry policy lives here.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, timedelta
import json

from agent.pipeline.signal_contract import EvidenceError, STATE_ENDS
from agent.pipeline.signal_attribution import TEHRAN, local_day, supporting_fields


def normalize(value):
    return " ".join(value.casefold().split())


def state_key(candidate):
    # A2 is a single overlap state per theater, not a state per carrier pair.
    parts = (candidate.location,) if candidate.signal_id == "A2" else (
        candidate.actor, candidate.object, candidate.location)
    return (candidate.signal_id, *(normalize(p) for p in parts))


def occurrence_key(candidate):
    return json.dumps([*state_key(candidate), normalize(candidate.occurrence_id),
                       normalize(candidate.episode_anchor)], ensure_ascii=False)


@dataclass
class Record:
    candidate: object
    event: object
    items: dict
    last_confirmed_at: date
    last_checked_at: date
    observation_health: str = "observed"
    end_evidence: list = field(default_factory=list)
    linked_consequences: set = field(default_factory=set)


@dataclass
class Cycle:
    factor: float = 1.0
    armed: bool = False
    quiet_days: int = 0
    last_firing: date | None = None


@dataclass(frozen=True)
class Baseline:
    kind: str
    actor: str
    topic: str
    observed: date
    due: date | None = None


class SignalHistory:
    def __init__(self, catalog):
        self.catalog = frozenset(catalog)
        self.ledger = {sid: Cycle() for sid in catalog}
        self.records = {}
        self.baselines = {}
        self.baseline_evidence = {}
        self.last_checkpoint = None
        self.checkpoints = {}
        self.consequence_days = {}

    def factor(self, sid):
        if set(self.ledger) != self.catalog:
            raise EvidenceError("missing/corrupt required novelty ledger")
        cycle = self.ledger.get(sid)
        if (not isinstance(cycle, Cycle) or cycle.factor not in (1.0, .6, .36, .3)
                or type(cycle.quiet_days) is not int or cycle.quiet_days < 0):
            raise EvidenceError("missing/corrupt required novelty ledger")
        return cycle.factor

    def find(self, candidate):
        return self.records.get(occurrence_key(candidate))

    def add_baseline(self, key, kind, candidate, items, credibility, *, now, due=None):
        day, _, count = supporting_fields(candidate, items, credibility, now)
        if count < 2 or kind not in {"positive_statement", "route_control", "advisory", "schedule", "soothing"}:
            raise EvidenceError("unsupported baseline")
        if kind not in candidate.predicates:
            raise EvidenceError("missing baseline semantic predicate")
        if due is not None and due not in candidate.dates:
            raise EvidenceError("unsupported schedule date")
        self.baselines[key] = Baseline(kind, candidate.actor, candidate.object, day, due)
        self.baseline_evidence[key] = (candidate, dict(items), now)

    def accept(self, candidate, event, items, credibility, *, now):
        """Merge one occurrence; return whether it newly cleared corroboration."""
        self.factor(candidate.signal_id)
        key, today = occurrence_key(candidate), local_day(now)
        old = self.records.get(key)
        active = [r for r in self.records.values()
                  if state_key(r.candidate) == state_key(candidate)
                  and r.event.state_end_date is None and r.event.independent_source_count >= 2]
        if candidate.transition == "end":
            if old is None or old not in active:
                raise EvidenceError("unmatched/conflicting state end")
            if event.state_end_date < old.event.event_date:
                raise EvidenceError("end before start")
            old.event = replace(old.event, state_end_date=event.state_end_date)
            old.end_evidence.append((candidate, dict(items)))
            old.last_checked_at = today
            return False
        if candidate.signal_id in STATE_ENDS:
            if old and old.event.state_end_date is not None:
                raise EvidenceError("reactivation requires a new episode anchor")
            if active and (old is None or old not in active):
                raise EvidenceError("ambiguous duplicate open state")
            if candidate.transition == "reaffirm" and old is None:
                raise EvidenceError("reaffirmation without existing episode")
        if old:
            merged_items = {**old.items, **items}
            supports = {s.item_id: s for s in old.candidate.supports}
            for s in candidate.supports:
                if s.item_id in supports and supports[s.item_id] != s:
                    raise EvidenceError("conflicting provenance for existing item")
                supports[s.item_id] = s
            merged = replace(old.candidate, supports=tuple(supports[k] for k in sorted(supports)))
            _, tier, count = supporting_fields(merged, merged_items, credibility, now)
            new = old.event.independent_source_count < 2 <= count
            old.candidate, old.items = merged, merged_items
            old.event = replace(old.event, source_tier=tier, independent_source_count=count)
            old.last_confirmed_at, old.last_checked_at = today, today
            old.observation_health = "observed"
        else:
            # Identical grounded identity with contradictory episode anchors is
            # ambiguous, not permission to add another strike contribution.
            if any(state_key(r.candidate) == state_key(candidate)
                   and normalize(r.candidate.occurrence_id) == normalize(candidate.occurrence_id)
                   for r in self.records.values()):
                raise EvidenceError("ambiguous occurrence/episode identity")
            if any(r.event.signal_id == candidate.signal_id and
                   {s.item_id for s in r.candidate.supports} & {s.item_id for s in candidate.supports}
                   for r in self.records.values()):
                raise EvidenceError("ambiguous shared-item occurrence: review required")
            self.records[key] = Record(candidate, event, dict(items), today, today)
            new = event.independent_source_count >= 2
        if new:
            cycle = self.ledger[candidate.signal_id]
            cycle.armed, cycle.quiet_days, cycle.last_firing = True, 0, today
        return new

    def advance(self, *, now, complete, new_ids=(), linked_consequences=()):
        """Advance once at exactly 09:00 Tehran; incomplete/missed days unknown.

        linked_consequences holds (parent occurrence key, accepted E-event key).
        Retain those explicit linkage decisions, never infer a consequence from
        mere contemporaneous headlines.
        """
        today = local_day(now)
        linked_ids = set()
        for parent_key, consequence_key in linked_consequences:
            parent, consequence = self.records.get(parent_key), self.records.get(consequence_key)
            if (parent is None or consequence is None or not consequence.event.signal_id.startswith("E")
                    or consequence.event.independent_source_count < 2):
                raise EvidenceError("unsupported kinetic consequence link")
            parent.linked_consequences.add(consequence_key)
            linked_ids.add(parent.event.signal_id)
            self.ledger[parent.event.signal_id].quiet_days = 0
        self.consequence_days.setdefault(today, set()).update(linked_ids)
        linked_ids |= self.consequence_days[today]
        local = now.astimezone(TEHRAN)
        if (local.hour, local.minute, local.second, local.microsecond) != (9, 0, 0, 0):
            return
        if self.last_checkpoint is not None and today <= self.last_checkpoint:
            return  # idempotent daily replay, no retrospective rewriting
        contiguous = self.last_checkpoint == today - timedelta(days=1)
        active_ids = {r.event.signal_id for r in self.records.values()
                      if r.event.signal_id in STATE_ENDS and r.event.state_end_date is None
                      and r.event.independent_source_count >= 2}
        for sid, cycle in self.ledger.items():
            self.factor(sid)
            blocked = sid in set(new_ids) | linked_ids | active_ids or cycle.last_firing == today
            if not complete or blocked:
                cycle.quiet_days = 0
            elif cycle.armed:
                cycle.quiet_days = cycle.quiet_days + 1 if contiguous else 1
                if cycle.quiet_days >= 14:
                    cycle.factor = max(.3, round(cycle.factor * .6, 2))
                    cycle.armed, cycle.quiet_days = False, 0
        self.last_checkpoint = today
        self.checkpoints[today] = bool(complete)
        for r in self.records.values():
            r.last_checked_at = today
            if not complete:
                r.observation_health = "uncertain"

    def reset_for(self, new_events, weights):
        if any(e.independent_source_count >= 2 and e.signal_id[0] in "ABE"
               and weights["signals"][e.signal_id]["base"] * weights["tier_multipliers"][e.source_tier] >= 15
               for e in new_events):
            for sid, cycle in self.ledger.items():
                self.factor(sid)
                cycle.factor = 1.0

    def materialize(self):
        return tuple(replace(r.event, novelty=self.factor(r.event.signal_id))
                     for _, r in sorted(self.records.items()))
