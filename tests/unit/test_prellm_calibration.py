"""Calibration-math tests for the pre-LLM drop threshold analyser.

The analyser is the half of the calibration loop that runs OFFLINE, so its
arithmetic (what a threshold catches, what it costs) must be provably right
before any CI measurement is trusted -- a wrong sweep would recommend a
threshold that silently deletes sent stories.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from agent.pipeline.prellm_calibration import (
    Row,
    guard_blocked_waste,
    load,
    recommend,
    render_report,
    rows_from_csv,
    sweep,
)


def _write(path: Path, records: list[dict], fieldnames: list[str]) -> Path:
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)
    return path


_COLS = ["fate", "prellm_score", "on_mission", "n_members", "headline"]


def _row(fate, score, on_mission=0, headline="h"):
    return {"fate": fate, "prellm_score": str(score), "on_mission": str(on_mission),
            "n_members": "2", "headline": headline}


def test_rows_from_csv_reads_scores_and_flags(tmp_path):
    path = _write(tmp_path / "chosen.csv", [
        _row("irrelevant", 0.11, 0),
        _row("sent", 0.88, 0),
        _row("cap_dropped", 0.40, 1),
    ], _COLS)
    loaded = rows_from_csv(path)
    assert [r.score for r in loaded.rows] == [pytest.approx(0.11),
                                              pytest.approx(0.88),
                                              pytest.approx(0.40)]
    assert [r.is_waste for r in loaded.rows] == [True, False, False]
    assert [r.on_mission for r in loaded.rows] == [False, False, True]
    assert loaded.unscored == 0
    assert loaded.guard_unknown == 0


def test_rows_from_csv_skips_unscored_and_detects_missing_guard(tmp_path):
    path = _write(tmp_path / "old.csv", [
        {"fate": "irrelevant", "prellm_score": "", "n_members": "1", "headline": "x"},
        {"fate": "sent", "prellm_score": "not-a-number", "n_members": "1", "headline": "y"},
        {"fate": "sent", "prellm_score": "0.5", "n_members": "1", "headline": "z"},
    ], ["fate", "prellm_score", "n_members", "headline"])
    loaded = rows_from_csv(path)
    assert len(loaded.rows) == 1
    assert loaded.unscored == 2
    # The on_mission column was absent entirely -> guard contribution unknown.
    assert loaded.guard_unknown == 1


def test_load_merges_many_files(tmp_path):
    first = _write(tmp_path / "a.csv", [_row("irrelevant", 0.2)], _COLS)
    second = _write(tmp_path / "b.csv", [_row("sent", 0.9), _row("clickbait", 0.3)], _COLS)
    merged = load([first, second])
    assert len(merged.rows) == 3
    assert len(merged.waste) == 2
    assert len(merged.keep) == 1


def test_sweep_counts_catches_and_losses():
    rows = [
        Row(fate="irrelevant", score=0.10, on_mission=False),
        Row(fate="clickbait", score=0.20, on_mission=False),
        Row(fate="sent", score=0.30, on_mission=False),
    ]
    points = {round(p.threshold, 2): p for p in sweep(rows, steps=11)}
    # Threshold 0.25 catches both waste clusters and none of the kept one.
    point = points[0.3]
    assert point.waste_total == 2 and point.keep_total == 1
    # < 0.30 is exclusive: the sent row at exactly 0.30 survives.
    assert point.waste_caught == 2
    assert point.keep_lost == 0
    # At 0.4 the sent row is now below threshold and gets killed.
    assert points[0.4].keep_lost == 1
    assert points[0.4].lost_rate == pytest.approx(1.0)


def test_sweep_never_drops_on_mission_rows():
    rows = [
        Row(fate="irrelevant", score=0.01, on_mission=True),   # guarded
        Row(fate="irrelevant", score=0.01, on_mission=False),  # droppable
    ]
    points = sweep(rows, steps=11)
    at_full = points[-1]
    assert at_full.waste_total == 2
    assert at_full.waste_caught == 1          # the guarded one is unreachable
    assert len(guard_blocked_waste(rows)) == 1


def test_recommend_picks_largest_zero_cost_threshold():
    rows = [
        Row(fate="irrelevant", score=0.12, on_mission=False),
        Row(fate="irrelevant", score=0.22, on_mission=False),
        Row(fate="sent", score=0.31, on_mission=False),
    ]
    best = recommend(sweep(rows, steps=101))
    assert best is not None
    assert best.keep_lost == 0
    assert best.waste_caught == 2
    assert best.recall == pytest.approx(1.0)
    # It must stop just under the kept cluster, not run past it.
    assert best.threshold <= 0.31


def test_recommend_returns_none_when_no_threshold_buys_anything():
    # Waste sits ABOVE every kept cluster, so any threshold that catches it
    # also kills a sent story -- there is no free window at all.
    rows = [
        Row(fate="sent", score=0.40, on_mission=False),
        Row(fate="irrelevant", score=0.55, on_mission=False),
    ]
    assert recommend(sweep(rows, steps=101)) is None


def test_recommend_requires_an_actual_catch():
    # Every threshold below the only cluster is free -- and catches nothing.
    # A recommendation must never come out of a curve with no waste in it.
    rows = [Row(fate="sent", score=0.10, on_mission=False),
            Row(fate="sent", score=0.50, on_mission=False)]
    assert recommend(sweep(rows, steps=101)) is None


def test_render_report_flags_guard_unknown_and_no_separation():
    from agent.pipeline.prellm_calibration import Loaded
    loaded = Loaded(rows=[Row(fate="sent", score=0.05, on_mission=False),
                          Row(fate="irrelevant", score=0.60, on_mission=False)],
                    guard_unknown=2)
    text = render_report(loaded, sweep(loaded.rows, steps=101))
    assert "UPPER BOUND" in text
    assert "Do not enable." in text


def test_render_report_states_the_verdict_and_catch():
    from agent.pipeline.prellm_calibration import Loaded
    loaded = Loaded(rows=[Row(fate="irrelevant", score=0.10, on_mission=False),
                          Row(fate="irrelevant", score=0.01, on_mission=True),
                          Row(fate="sent", score=0.70, on_mission=False)])
    text = render_report(loaded, sweep(loaded.rows, steps=101))
    assert "guard-protected waste" in text
    assert "drops 1 of 2 waste clusters" in text


def test_dropped_anyway_clusters_are_not_keep_losses():
    # A cap_dropped cluster below threshold is NOT a kept story lost -- the
    # pre-LLM drop removing it is pure budget savings. keep_lost must count
    # only sent-producing fates.
    rows = [
        Row(fate="irrelevant", score=0.10, on_mission=False),
        Row(fate="cap_dropped", score=0.15, on_mission=False),  # dropped-anyway
        Row(fate="sent", score=0.40, on_mission=False),
    ]
    points = {round(p.threshold, 2): p for p in sweep(rows, steps=101)}
    assert points[0.30].waste_caught == 1
    assert points[0.30].keep_lost == 0          # cap_dropped must not count
    assert points[0.30].keep_total == 1          # only the sent row
    best = recommend(sweep(rows, steps=101))
    assert best is not None
    assert best.waste_caught == 1
    assert best.keep_lost == 0
    assert best.threshold <= 0.40


def test_other_bucket_separates_dropped_anyway():
    from agent.pipeline.prellm_calibration import Loaded
    loaded = Loaded(rows=[
        Row(fate="sent", score=0.80, on_mission=False),
        Row(fate="sent_followup", score=0.60, on_mission=False),
        Row(fate="lead_only", score=0.55, on_mission=False),
        Row(fate="irrelevant", score=0.10, on_mission=False),
        Row(fate="repeat_dropped", score=0.20, on_mission=False),
        Row(fate="oversized", score=0.25, on_mission=False),
        Row(fate="rank_dropped", score=0.30, on_mission=False),
    ])
    assert len(loaded.keep) == 3
    assert len(loaded.waste) == 1
    assert len(loaded.other) == 3
    assert {r.fate for r in loaded.other} == {"repeat_dropped", "oversized", "rank_dropped"}


def test_empty_rows_is_not_a_crash():
    from agent.pipeline.prellm_calibration import Loaded
    loaded = Loaded()
    assert sweep(loaded.rows) == []
    assert recommend([]) is None
    assert "scored clusters : 0" in render_report(loaded, [])

