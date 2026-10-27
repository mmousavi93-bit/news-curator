"""Stage 3a persistence and offline measurement gates; no historical gold."""
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest

from agent.memory.db import (initialize, open_db, persist_signal_history,
                             load_signal_history, StateError)
from agent.memory.retention import prune
from agent.memory.signal_models import RawCandidateRecord, pack, unpack, digest
from agent.pipeline.signal_history import SignalHistory, occurrence_key
from agent.pipeline.signal_calendar import SignalCalendar
from agent.pipeline.signal_attribution import derive
from agent.pipeline.signal_contract import STATE_ENDS, EvidenceError
from agent.pipeline.signals import attribute_run
from agent.risk.engine import SignalEvent
from unit.test_signal_attribution import CATALOG, NOW, fixture
from unit.test_signal_history import WEIGHTS
from unit.test_memory_retention import SETTINGS

ROOT = Path(__file__).resolve().parents[2]


def setup_history(sid="B1"):
    candidate, items, cred = fixture(sid)
    history = SignalHistory(CATALOG)
    attribute_run([candidate], items, cred, now=NOW, history=history, weights=WEIGHTS)
    sources = {k: vars(v) for k, v in cred.items()}
    return history, candidate, items, cred, sources


def save(conn, history, sources, **kwargs):
    persist_signal_history(conn, history, run_key=kwargs.pop("run_key", "r1"),
        observed_at=kwargs.pop("observed_at", NOW), source_snapshots=sources, **kwargs)


def counts(conn):
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    return {t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}


def test_roundtrip_restart_and_replay(tmp_path):
    path = tmp_path / "state.db"
    conn = initialize(path)
    history, candidate, items, cred, sources = setup_history()
    history.ledger["B1"].factor = .36
    history.ledger["B1"].quiet_days = 5
    original = tuple(SignalEvent(**e.fields()) for e in history.materialize())
    raw = RawCandidateRecord("r1", "cluster", digest("input"), "p1", "m1", "provider", "s1",
                             '{"none":true,"signals":[]}', NOW, items, sources, "none")
    save(conn, history, sources, raw_records=[raw])
    before = counts(conn)
    save(conn, history, sources, raw_records=[raw])
    assert counts(conn) == before
    conn.close()
    conn = open_db(path)
    restored, calendar, snapshots = load_signal_history(conn, CATALOG)
    assert tuple(SignalEvent(**e.fields()) for e in restored.materialize()) == original
    assert pack(vars(restored)) == pack(vars(history))
    assert snapshots == sources
    save(conn, restored, snapshots, raw_records=[raw])
    assert counts(conn) == before
    events = attribute_run([candidate], items, cred, now=NOW, history=restored, weights=WEIGHTS)
    assert events == original
    save(conn, restored, snapshots, run_key="r2")
    assert counts(conn)["signal_occurrences"] == 1
    assert counts(conn)["signal_events"] == 1
    assert conn.execute("SELECT event_date, observed_at FROM signal_occurrences").fetchone()[0] == NOW.date().isoformat()
    conn.close()


@pytest.mark.parametrize("sid", ["A1", "A3", "A4", "A7", "H1"])
def test_open_end_evidence_and_health_roundtrip(tmp_path, sid):
    conn = initialize(tmp_path / "state.db")
    history, candidate, items, cred, sources = setup_history(sid)
    later = NOW + timedelta(days=2)
    end, eitems, _ = fixture(sid, day=later, transition="end", predicates=[STATE_ENDS[sid]])
    event = derive(end, eitems, cred, now=later, history=history)
    history.accept(end, event, eitems, cred, now=later)
    history.find(candidate).observation_health = "uncertain"
    save(conn, history, sources)
    loaded, _, _ = load_signal_history(conn, CATALOG)
    assert pack(vars(loaded)) == pack(vars(history))
    assert len(loaded.find(candidate).end_evidence) == 1
    conn.close()


def test_atomic_conflicting_run_and_missing_source(tmp_path):
    conn = initialize(tmp_path / "state.db")
    history, _, _, _, sources = setup_history()
    save(conn, history, sources)
    before = counts(conn)
    history.ledger["B1"].factor = .6
    with pytest.raises(StateError, match="conflicting replay"):
        save(conn, history, sources)
    assert counts(conn) == before
    with pytest.raises(StateError, match="missing source"):
        save(conn, history, {}, run_key="r2")
    assert counts(conn) == before
    with pytest.raises(StateError, match="out-of-order"):
        save(conn, history, sources, run_key="old", observed_at=NOW - timedelta(days=1))
    conn.close()


def test_transaction_rollback_on_late_write_failure(tmp_path):
    conn = initialize(tmp_path / "state.db")
    history, _, _, _, sources = setup_history()
    conn.execute("CREATE TRIGGER fail_checkpoint BEFORE INSERT ON signal_history_meta "
                 "BEGIN SELECT RAISE(ABORT, 'injected failure'); END")
    before = counts(conn)
    with pytest.raises(sqlite3.IntegrityError, match="injected failure"):
        save(conn, history, sources)
    assert counts(conn) == before
    conn.close()


def test_missing_corrupt_ledger_and_catalog_halt(tmp_path):
    conn = initialize(tmp_path / "state.db")
    with pytest.raises(StateError, match="missing required"):
        load_signal_history(conn, CATALOG)
    history, _, _, _, sources = setup_history()
    save(conn, history, sources)
    with pytest.raises(StateError, match="catalog"):
        load_signal_history(conn, {"B1"})
    conn.execute("DELETE FROM signal_novelty WHERE signal_id='B1'")
    with pytest.raises(StateError, match="ledger"):
        load_signal_history(conn, CATALOG)
    conn.close()


def test_additive_extension_on_actual_v4_database(tmp_path):
    path = tmp_path / "old.db"
    # Build the pre-extension schema, not a new schema with a rewound version.
    schema = (ROOT / "src/agent/memory/schema.sql").read_text()
    legacy = schema.split("-- SIGNAL_EXTRACTION_V5", 1)[0]
    conn = sqlite3.connect(path)
    conn.executescript(legacy)
    conn.execute("INSERT INTO meta VALUES ('schema_version', '4')")
    conn.execute("INSERT INTO signal_events(signal_id, observed_at) VALUES ('B1', 'old')")
    before = {r[0]: list(conn.execute(f'PRAGMA table_info("{r[0]}")')) for r in
              conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    conn.commit()
    conn.close()
    conn = open_db(path)
    for table, columns in before.items():
        assert [tuple(r) for r in conn.execute(f'PRAGMA table_info("{table}")')] == columns
    assert conn.execute("SELECT observed_at FROM signal_events").fetchone()[0] == "old"
    assert conn.execute("SELECT value FROM meta WHERE key='signal_schema_version'").fetchone()[0] == "1"
    conn.close()
    conn = open_db(path)
    assert counts(conn)["signal_events"] == 1
    conn.close()


def test_retention_protects_active_state_and_required_baseline(tmp_path):
    conn = initialize(tmp_path / "state.db")
    history, candidate, _, _, sources = setup_history("A1")
    save(conn, history, sources)
    prune(conn, SETTINGS, NOW + timedelta(days=400))
    loaded, _, _ = load_signal_history(conn, CATALOG)
    assert loaded.materialize() == history.materialize()
    assert counts(conn)["signal_events"] == 1
    assert len(loaded.ledger) == len(CATALOG)
    conn.close()


def test_expired_occurrence_pruned_but_quiet_ledger_preserved(tmp_path):
    conn = initialize(tmp_path / "state.db")
    history, _, _, _, sources = setup_history()
    history.ledger["B1"].factor = .6
    save(conn, history, sources)
    prune(conn, SETTINGS, NOW + timedelta(days=181))
    loaded, _, _ = load_signal_history(conn, CATALOG)
    assert loaded.materialize() == ()
    assert loaded.factor("B1") == .6
    conn.close()


def harness():
    spec = importlib.util.spec_from_file_location("eval_signal_extraction", ROOT / "analysis/eval_signal_extraction.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_harness_empty_labels_and_all_five_calibration_bands():
    import yaml
    h = harness()
    assert h.measure([], None)["label_status"] == "no labels"
    assert h.measure([], {"runs": []})["accuracy_gate"] == "incomplete"
    weights = yaml.safe_load((ROOT / "config/risk_weights.yaml").read_text())
    assert len(h.calibration(weights)) == 5


def test_harness_captured_none_and_budget_failure_stay_distinct():
    h = harness()
    _, _, items, _, sources = setup_history()
    members = []
    for item in items.values():
        from dataclasses import asdict
        row = asdict(item)
        row["published_at"] = item.published_at.isoformat()
        members.append(row)
    capture = {"provider": "captured", "model_version": "test", "schema_version": "1",
               "prompt_version": "1", "raw_response": '{"none":true,"signals":[]}'}
    dataset = {"runs": [{"run_key": "run", "cutoff": NOW.isoformat(), "sources": sources,
        "complete": True, "clusters": [
            {"cluster_key": "none", "items": members, "capture": capture},
            {"cluster_key": "cap", "items": [], "fate": "refused_cap"}]}]}
    import yaml
    weights = yaml.safe_load((ROOT / "config/risk_weights.yaml").read_text())
    result = h.replay(dataset, weights, "{items}")
    report = h.measure(result)
    assert report["statuses"] == {"none": 1, "refused_cap": 1}
    assert not result[0]["complete"]
    assert report["unknown_abstentions"] == 1


def test_measure_abstention_is_false_negative_without_gold_generation():
    # A controlled metric identity test, never source history or human labels.
    h = harness()
    gold = {"runs": [{"run_key": "absent", "clusters": [
        {"cluster_key": "c", "language": "fa", "candidates": ["occurrence"]}], "accepted": {}}]}
    result = h.measure([], gold)
    assert result["candidate_metrics"]["fn"] == 1
    assert result["candidate_metrics"]["recall"] == 0


def test_baseline_calendar_and_source_snapshots_survive_restart(tmp_path):
    from agent.pipeline.signal_calendar import Deadline
    conn = initialize(tmp_path / "state.db")
    history, candidate, items, cred, sources = setup_history()
    baseline = replace(candidate, predicates=candidate.predicates | {"positive_statement"})
    history.add_baseline("baseline", "positive_statement", baseline, items, cred, now=NOW)
    history.find(candidate).candidate = replace(candidate, prior_ids=("baseline",))
    calendar = SignalCalendar()
    calendar.statements["soothing"] = (NOW, candidate)
    calendar.deadlines["deadline"] = Deadline("deadline", NOW.date() + timedelta(days=3), candidate, NOW.date())
    calendar.schedules["meeting"] = Deadline("meeting", NOW.date() + timedelta(days=2), candidate, NOW.date())
    calendar.updates.append(calendar.deadlines["deadline"])
    save(conn, history, sources, calendar=calendar)
    loaded, restored_calendar, snapshots = load_signal_history(conn, CATALOG)
    assert pack(vars(loaded)) == pack(vars(history))
    assert pack(vars(restored_calendar)) == pack(vars(calendar))
    assert snapshots == sources
    before = counts(conn)
    save(conn, loaded, sources, calendar=restored_calendar)
    assert counts(conn) == before
    assert conn.execute("SELECT source_id FROM speaker_statements WHERE tone='soothing'").fetchone()[0] in sources
    prune(conn, SETTINGS, NOW + timedelta(days=50))
    assert counts(conn)["signal_baselines"] == 1  # referenced by retained occurrence
    loaded, cal, _ = load_signal_history(conn, CATALOG)
    assert "baseline" in loaded.baselines
    assert not cal.statements
    assert len(cal.deadlines) == 1
    conn.close()


def test_checkpoint_and_linked_consequences_are_not_reapplied(tmp_path):
    conn = initialize(tmp_path / "state.db")
    history, candidate, items, cred, sources = setup_history()
    attack, aitems, _ = fixture("E1")
    event = derive(attack, aitems, cred, now=NOW, history=history)
    history.accept(attack, event, aitems, cred, now=NOW)
    from agent.pipeline.signal_attribution import TEHRAN
    canonical = NOW.astimezone(TEHRAN).replace(hour=9, minute=0, second=0, microsecond=0)
    history.advance(now=canonical, complete=True,
        linked_consequences=[(occurrence_key(candidate), occurrence_key(attack))])
    save(conn, history, sources)
    restored, _, _ = load_signal_history(conn, CATALOG)
    assert pack(vars(restored)) == pack(vars(history))
    restored.advance(now=canonical, complete=True)
    assert restored.checkpoints == history.checkpoints
    assert restored.ledger == history.ledger
    conn.close()


def test_immutable_raw_quote_snapshots_and_cascade_retention(tmp_path):
    conn = initialize(tmp_path / "state.db")
    history, candidate, items, _, sources = setup_history()
    from dataclasses import asdict
    raw = RawCandidateRecord("r1", "cluster", digest("input"), "p", "m", "provider", "s",
        json.dumps({"none": False, "signals": [{"supports": [asdict(s) for s in candidate.supports]}]}),
        NOW, items, sources, "ok")
    save(conn, history, sources, raw_records=[raw])
    quote = conn.execute("SELECT quote_snapshot FROM signal_supports ORDER BY item_key").fetchone()[0]
    assert unpack(quote) == [candidate.supports[0].quote]
    frozen = conn.execute("SELECT payload FROM signal_raw_candidates").fetchone()[0]
    with pytest.raises(StateError, match="conflicting"):
        save(conn, history, sources, raw_records=[replace(raw, raw_response="changed")])
    assert conn.execute("SELECT payload FROM signal_raw_candidates").fetchone()[0] == frozen
    prune(conn, SETTINGS, NOW + timedelta(days=181))
    assert counts(conn)["signal_raw_candidates"] == 0
    assert counts(conn)["signal_supports"] == 0
    conn.close()


def test_date_only_snapshot_and_order_independent_encoding():
    _, _, items, _, _ = setup_history()
    item = replace(next(iter(items.values())), date_only=True)
    assert unpack(pack(item)) == item
    assert pack({"b": {"y", "x"}, "a": (1, 2)}) == pack({"a": (1, 2), "b": {"x", "y"}})


def test_harness_validated_candidate_and_oracle_score_comparison():
    # Controlled stage-2 fixture only; no historical labels are created.
    from dataclasses import asdict
    import yaml
    h = harness()
    history, candidate, items, _, sources = setup_history()
    weights = yaml.safe_load((ROOT / "config/risk_weights.yaml").read_text())
    members = [{**asdict(i), "published_at": i.published_at.isoformat()} for i in items.values()]
    raw = json.loads(json.dumps(asdict(candidate), default=lambda v: sorted(v) if isinstance(v, frozenset) else v.isoformat()))
    capture = {"provider": "captured", "model_version": "test", "schema_version": "1",
               "prompt_version": "1", "raw_response": json.dumps({"none": False, "signals": [raw]})}
    dataset = {"runs": [{"run_key": "r", "cutoff": NOW.isoformat(), "sources": sources,
        "complete": True, "market_events": [], "clusters": [
            {"cluster_key": "c", "language": "en", "items": members, "capture": capture}]}]}
    results = h.replay(dataset, weights, "{items}")
    identity = occurrence_key(candidate)
    assert list(results[0]["accepted"]) == [identity]
    labels = {"runs": [{"run_key": "r", "clusters": [
        {"cluster_key": "c", "language": "en", "candidates": [identity]}],
        "accepted": {identity: h.event_fields(history.materialize()[0])},
        "side_channels": {"deadline": None, "deception": False}}]}
    measured = h.measure(results, labels, weights)
    assert measured["candidate_metrics"]["f1"] == 1
    assert measured["accepted_metrics"]["f1"] == 1
    assert measured["field_accuracy"] == 1
    assert measured["score_comparisons"][0]["delta"] == {"STRAT": 0, "TACT": 0}
