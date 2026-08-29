from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from agent.config import load_all
from agent.pipeline import STAGES, build_stages
from agent.run import RunContext, main, run_pipeline


def _stages(config, base: Path):
    stages, router, embedder = build_stages(config, {}, base=base)
    return stages


def test_build_stages_matches_declared_order(tmp_config_dir: Path):
    config = load_all(base=tmp_config_dir)
    stages = _stages(config, tmp_config_dir)
    assert tuple(s.name for s in stages) == STAGES


def test_run_pipeline_reports_zero_counts_and_one_summary_line(tmp_config_dir: Path, caplog):
    config = load_all(base=tmp_config_dir)
    ctx = RunContext(config=config, dry_run=True, now=datetime(2026, 8, 11, tzinfo=timezone.utc))
    logger = logging.getLogger("test.dry_run.summary")
    logger.setLevel(logging.INFO)

    with caplog.at_level(logging.INFO, logger="test.dry_run.summary"):
        run_pipeline(ctx, _stages(config, tmp_config_dir), logger)

    # Only this test's own logger: the pipeline stages emit their own lines
    # on the agent.pipeline logger (Phase 7), which is not this contract.
    messages = [r.getMessage() for r in caplog.records if r.name == "test.dry_run.summary"]
    assert messages == ["run summary: items=0 clusters=0 messages=0"]


def test_run_pipeline_is_deterministic_across_runs(tmp_config_dir: Path, caplog):
    config = load_all(base=tmp_config_dir)
    fixed_now = datetime(2026, 8, 11, tzinfo=timezone.utc)
    logger = logging.getLogger("test.dry_run.deterministic")
    logger.setLevel(logging.INFO)

    with caplog.at_level(logging.INFO, logger="test.dry_run.deterministic"):
        run_pipeline(RunContext(config=config, dry_run=True, now=fixed_now), _stages(config, tmp_config_dir), logger)
        first = [r.getMessage() for r in caplog.records if r.name == "test.dry_run.deterministic"]
        caplog.clear()
        run_pipeline(RunContext(config=config, dry_run=True, now=fixed_now), _stages(config, tmp_config_dir), logger)
        second = [r.getMessage() for r in caplog.records if r.name == "test.dry_run.deterministic"]

    assert first == second


def test_main_dry_run_exits_zero_with_one_summary_line(caplog):
    with caplog.at_level(logging.INFO, logger="agent.run"):
        exit_code = main(["--dry-run"])

    assert exit_code == 0
    summary_lines = [
        r.getMessage() for r in caplog.records
        if r.name == "agent.run" and r.getMessage().startswith("run summary:")
    ]
    assert summary_lines == ["run summary: items=0 clusters=0 messages=0"]
