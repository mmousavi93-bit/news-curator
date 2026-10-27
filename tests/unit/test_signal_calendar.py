"""Calendar evidence stays outside the six-field scorer input."""
from dataclasses import replace
from datetime import timedelta
import pytest

from agent.pipeline.signal_calendar import SignalCalendar
from agent.pipeline.signal_contract import EvidenceError
from unit.test_signal_attribution import NOW, fixture


def deadline(identity="deadline", due="2026-06-15", action="issue", **kwargs):
    return fixture("C1", identity=identity, action=action, dates=[due],
                   text=f"Authority {action}: deadline {due}", **kwargs)


def test_earliest_unexpired_deadline_tie_break_extend_cancel_and_replay():
    calendar = SignalCalendar()
    for identity, due in [("b", "2026-06-15"), ("a", "2026-06-15"), ("c", "2026-06-20")]:
        candidate, items, cred = deadline(identity, due)
        calendar.update_deadline(candidate, items, cred, now=NOW)
    assert calendar.kwargs(now=NOW) == {"deadline": candidate.dates[0].replace(day=15), "deception": False}
    extended, items, cred = deadline("a", "2026-06-21", "extend")
    calendar.update_deadline(extended, items, cred, now=NOW)
    before = len(calendar.updates)
    calendar.update_deadline(extended, items, cred, now=NOW)
    assert len(calendar.updates) == before
    cancelled, items, cred = deadline("b", action="cancel")
    calendar.update_deadline(cancelled, items, cred, now=NOW)
    assert calendar.kwargs(now=NOW)["deadline"].day == 20
    assert len(calendar.deadlines) == 3
    calendar.update_deadline(cancelled, items, cred, now=NOW)
    assert calendar.kwargs(now=NOW)["deadline"].day == 20
    assert calendar.kwargs(now=NOW + timedelta(days=12))["deadline"] is None


@pytest.mark.parametrize("action", ["issue", "extend", "cancel", "invent"])
def test_uncorroborated_calendar_updates_rejected(action):
    calendar = SignalCalendar()
    candidate, items, cred = deadline(action=action)
    candidate = replace(candidate, supports=candidate.supports[:1])
    with pytest.raises(EvidenceError):
        calendar.update_deadline(candidate, items, cred, now=NOW)
    assert not calendar.deadlines


def test_deadline_changes_need_prior_grounded_identity_and_later_date():
    calendar = SignalCalendar()
    candidate, items, cred = deadline(action="extend")
    with pytest.raises(EvidenceError, match="prior deadline"):
        calendar.update_deadline(candidate, items, cred, now=NOW)
    candidate, items, cred = deadline()
    calendar.update_deadline(candidate, items, cred, now=NOW)
    for action, due in [("issue", "2026-06-16"), ("extend", "2026-06-14"), ("invent", "2026-06-16")]:
        update, items, cred = deadline(action=action, due=due)
        with pytest.raises(EvidenceError):
            calendar.update_deadline(update, items, cred, now=NOW)
    with pytest.raises(EvidenceError, match="authority/topic"):
        calendar.update_deadline(replace(candidate, actor="different"), items, cred, now=NOW)


def test_schedule_rescheduling_and_silence_only_updates_registry():
    calendar = SignalCalendar()
    candidate, items, cred = fixture("schedule", action="schedule", dates=["2026-06-12"], text="Meeting 2026-06-12")
    calendar.update_schedule("meeting", candidate, items, cred, now=NOW)
    moved, items, cred = fixture("schedule", action="reschedule", dates=["2026-06-18"], text="Meeting now 2026-06-18")
    calendar.update_schedule("meeting", moved, items, cred, now=NOW)
    assert calendar.schedules["meeting"].due.day == 18
    assert calendar.kwargs(now=NOW) == {"deadline": None, "deception": False}
    with pytest.raises(EvidenceError, match="unknown schedule"):
        calendar.update_schedule("unknown", moved, items, cred, now=NOW)
    with pytest.raises(EvidenceError, match="conflicting schedule"):
        calendar.update_schedule("meeting", replace(moved, actor="other"), items, cred, now=NOW)


def test_soothing_72_hour_boundary_and_repost_never_renews():
    calendar = SignalCalendar()
    candidate, items, cred = fixture("soothing", predicates=["soothing", "official_statement"])
    calendar.add_soothing(candidate, items, cred, now=NOW)
    assert calendar.kwargs(now=NOW + timedelta(hours=72))["deception"]
    later = NOW + timedelta(hours=72, seconds=1)
    assert not calendar.kwargs(now=later)["deception"]
    items = {k: replace(v, published_at=later) for k, v in items.items()}
    calendar.add_soothing(candidate, items, cred, now=later)
    assert not calendar.kwargs(now=later)["deception"]


def test_soothing_requires_exact_time_and_official_corroboration():
    candidate, items, cred = fixture("soothing")
    calendar = SignalCalendar()
    with pytest.raises(EvidenceError, match="unsupported official"):
        calendar.add_soothing(replace(candidate, supports=candidate.supports[:1]), items, cred, now=NOW)
    items = {k: replace(v, date_only=True) for k, v in items.items()}
    with pytest.raises(EvidenceError, match="72-hour"):
        calendar.add_soothing(candidate, items, cred, now=NOW)
