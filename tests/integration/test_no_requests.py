"""Regression guard for the Phase 2 Round-3 defect: `requests` imported at
module top in the delivery layer, which made --dry-run, mock mode and the
entire offline suite depend on an HTTP library they never call.

That bug survived a green suite in two different sandboxes for one reason
only: `requests` happened to be installed in both. It was caught on the
owner's clean Windows Python, and as of 2026-08-13 `requests` is installed
there too -- so the accident that caught it is gone. This file replaces the
accident with an assertion.

Mechanism: `sys.modules["requests"] = None` makes `import requests` raise
ImportError even when the package is installed on disk. Every agent module
is evicted from the module cache first so the imports genuinely re-execute
rather than returning an already-imported object.

If this file fails, someone added a top-level `import requests` (or a new
third-party import) to a code path that offline mode must reach. Fix it by
moving the import inside the function that needs the network -- never by
installing the package.
"""

from __future__ import annotations

import contextlib
import importlib
import logging
import sys

import pytest

# Modules that must import and function with no HTTP library present at all.
OFFLINE_MODULES = (
    "agent.run",
    "agent.delivery.telegram",
    "agent.delivery.transport",
    "agent.delivery.formatter",
    "agent.delivery.credentials",
    "agent.config",
    "agent.collectors.base",
    "agent.collectors.dates",
    "agent.collectors.tz",
    "agent.collectors.fetch",
    "agent.collectors.rss",
    "agent.collectors.telegram_web",
    "agent.collectors.registry",
    "agent.collectors.report",
    # Phase 5 (LLM router): every llm module must import and function with
    # no HTTP library present -- mock mode and the whole suite run offline.
    "agent.llm",
    "agent.llm.errors",
    "agent.llm.transport",
    "agent.llm.limits",
    "agent.llm.breaker",
    "agent.llm.call",
    "agent.llm.providers",
    "agent.llm.router",
    "agent.llm.wiring",
    # Phase 6: the pipeline stages and the event store.
    "agent.pipeline",
    "agent.pipeline.filter",
    "agent.pipeline.embed",
    "agent.pipeline.cluster",
    "agent.pipeline.understand",
    "agent.pipeline.collect",
    "agent.pipeline.compose",
    "agent.pipeline.deliver",
    "agent.pipeline.validate",
    "agent.memory.event_models",
    "agent.memory.lead_models",
    "agent.memory.source_health",
)


@contextlib.contextmanager
def requests_unimportable():
    """Make `import requests` fail, with every agent module freshly loadable.

    Restores sys.modules wholesale on exit so a module imported (or not
    imported) inside the block cannot leak into unrelated tests.
    """
    saved = dict(sys.modules)
    for name in [n for n in sys.modules if n == "agent" or n.startswith("agent.")]:
        del sys.modules[name]
    sys.modules["requests"] = None  # sentinel: `import requests` -> ImportError
    try:
        yield
    finally:
        sys.modules.clear()
        sys.modules.update(saved)


def test_sentinel_actually_blocks_the_import():
    """Guard the guard. If this ever stops raising, every other test in this
    file silently degrades into testing nothing."""
    with requests_unimportable():
        with pytest.raises(ImportError):
            import requests  # noqa: F401


def test_every_offline_module_imports_without_requests():
    with requests_unimportable():
        for name in OFFLINE_MODULES:
            importlib.import_module(name)  # raises -> test fails, which is the point


def test_dry_run_exits_zero_without_requests(caplog):
    with requests_unimportable():
        run = importlib.import_module("agent.run")
        with caplog.at_level(logging.INFO, logger="agent.run"):
            exit_code = run.main(["--dry-run"])

    summary_lines = [
        r.getMessage() for r in caplog.records
        if r.name == "agent.run" and r.getMessage().startswith("run summary:")
    ]
    assert exit_code == 0
    assert summary_lines == ["run summary: items=0 clusters=0 messages=0"]


def test_send_test_mock_mode_exits_zero_without_requests(monkeypatch, caplog):
    """--send-test with no credentials must take the mock path and never
    construct a real transport. _scrub_secret_env already removes
    TELEGRAM_BOT_TOKEN; TELEGRAM_CHANNEL_ID does not match its suffix
    heuristic, so drop it explicitly."""
    monkeypatch.delenv("TELEGRAM_CHANNEL_ID", raising=False)

    with requests_unimportable():
        run = importlib.import_module("agent.run")
        with caplog.at_level(logging.INFO, logger="agent.run"):
            exit_code = run.main(["--send-test"])

    messages = [r.getMessage() for r in caplog.records if r.name == "agent.run"]
    assert exit_code == 0
    assert any("mock mode" in m for m in messages), messages


def test_collect_only_exits_zero_without_requests(tmp_path, caplog):
    """`fetch.py` lazy-imports requests exactly like transport.py -- this is
    the collectors-side twin of test_dry_run_exits_zero_without_requests.
    Every enabled source will fail with FetchError("... requests package is
    required ..."), which registry.py must record as a per-source failure,
    never a crash. Deliberately uses the real config/sources.yaml (no
    --config-dir override), same convention as test_dry_run_exits_zero_
    without_requests -- the point of this test is that the real 51-row
    config imports and dispatches cleanly with requests unimportable, not
    that requests is avoided by pointing at a fixture instead."""
    report_path = tmp_path / "report.json"
    with requests_unimportable():
        run = importlib.import_module("agent.run")
        with caplog.at_level(logging.INFO, logger="agent.run"):
            exit_code = run.main([
                "--collect-only",
                "--report-path", str(report_path),
            ])
    assert exit_code == 0
    assert report_path.exists()


def test_requests_transport_construction_fails_with_actionable_message():
    """The one place that is ALLOWED to need requests. It must fail at
    construction with an instruction, not later from inside .post() with a
    bare ModuleNotFoundError during an unattended 03:00 run."""
    with requests_unimportable():
        transport = importlib.import_module("agent.delivery.transport")
        with pytest.raises(ImportError) as excinfo:
            transport.RequestsTransport()

    message = str(excinfo.value)
    assert "requests" in message
    assert "pip install requests" in message
