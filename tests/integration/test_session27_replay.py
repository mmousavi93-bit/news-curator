"""Observed queue snapshots replayed through real composition and SQLite."""
import importlib.util
import socket
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_archived_chronology_preserves_selection_and_every_deferred_key(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "snapshot_replay", ROOT / "tools/replay_digest_snapshots.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    previous_connect = socket.socket.connect
    report = module.replay(ROOT, ROOT / "tests/fixtures/session27_archive.json",
                           tmp_path / "isolated.db", tmp_path / "report.json")
    assert socket.socket.connect is previous_connect
    assert report["network_forbidden"] is True
    snapshots = report["snapshots"]
    assert [s["admitted"] for s in snapshots] == [40, 46]
    assert [s["deferred"] for s in snapshots] == [35, 41]
    assert all(len(s["selected_keys"]) == 5 for s in snapshots)
    assert all(s["overview_fixture_calls"] == 1 for s in snapshots)
