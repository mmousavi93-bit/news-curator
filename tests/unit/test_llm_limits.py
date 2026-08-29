"""Unit tests for llm/limits.py: the per-run call budget and the provider
spend guards. Pacing and breaker tests live in test_llm_pacing_breaker.py
(split for the ~200-line convention). Everything clock-injected -- no test
sleeps and no test reads the wall clock (PHASE_5_BRIEF §5).
"""

from __future__ import annotations

from agent.llm.limits import CallBudget, ProviderBudget


class RecordingLogger:
    """Minimal logger double: records (level, message) tuples. Used where
    tests must assert a refusal was logged EXACTLY once, which caplog's
    shared-process state makes brittle."""

    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def error(self, msg, *args):
        self.messages.append(("error", msg % args if args else msg))

    def warning(self, msg, *args):
        self.messages.append(("warning", msg % args if args else msg))

    def info(self, msg, *args):
        self.messages.append(("info", msg % args if args else msg))


# ---------------------------------------------------------------------------
# CallBudget
# ---------------------------------------------------------------------------


def test_call_budget_acquires_up_to_cap():
    budget = CallBudget(2, RecordingLogger())
    assert budget.acquire("understand") is True
    assert budget.acquire("understand") is True
    assert budget.remaining == 0
    assert budget.acquire("understand") is False


def test_call_budget_refusal_logged_exactly_once():
    logger = RecordingLogger()
    budget = CallBudget(1, logger)
    budget.acquire("understand")
    budget.acquire("understand")
    budget.acquire("understand")
    refusals = [m for m in logger.messages if "refused" in m[1]]
    assert len(refusals) == 1
    assert "cap 1 reached" in refusals[0][1]


def test_reserve_reduces_headroom_for_later_calls():
    budget = CallBudget(3, RecordingLogger())
    assert budget.reserve(1, "compose") is True
    assert budget.remaining == 2
    assert budget.acquire("understand") is True
    assert budget.acquire("understand") is True
    assert budget.acquire("understand") is False  # headroom is gone


def test_consume_reserved_spends_without_double_count():
    budget = CallBudget(3, RecordingLogger())
    budget.reserve(1, "compose")
    budget.acquire("understand")
    budget.acquire("understand")
    assert budget.remaining == 0
    # The reserved slot still exists even though the shared pot is empty.
    assert budget.consume_reserved("compose") is True
    assert budget.consume_reserved("compose") is False  # one slot, used once


def test_release_returns_unused_reserved_slots():
    budget = CallBudget(3, RecordingLogger())
    budget.reserve(1, "compose")
    budget.release(1, "compose")
    assert budget.remaining == 3


def test_reserve_refused_when_it_cannot_fit():
    logger = RecordingLogger()
    budget = CallBudget(2, logger)
    assert budget.reserve(3, "compose") is False
    assert any("reserve 3 for compose refused" in m[1] for m in logger.messages)


# ---------------------------------------------------------------------------
# ProviderBudget (constraint #15 guard rails)
# ---------------------------------------------------------------------------


def _provider_budget(**overrides):
    kwargs = dict(
        name="anthropic",
        max_calls_per_run=15,
        max_spend_usd=5.0,
        halt_on_exceeded=True,
        input_usd_per_mtok=1.0,
        output_usd_per_mtok=5.0,
        logger=RecordingLogger(),
    )
    kwargs.update(overrides)
    return ProviderBudget(**kwargs)


def test_provider_budget_per_run_call_cap():
    budget = _provider_budget(max_calls_per_run=2, max_spend_usd=None)
    assert budget.acquire() is True
    assert budget.acquire() is True
    assert budget.acquire() is False
    assert any("per-run call cap (2) reached" in m[1] for m in budget._logger.messages)


def test_provider_budget_spend_halt_stops_further_calls():
    budget = _provider_budget()
    budget.acquire()
    # 1M in + 1M out at $1/$5 per MTok = $6.00 > $5.00 cap.
    budget.record_usage(1_000_000, 1_000_000)
    assert budget.halted is True
    assert budget.acquire() is False
    assert any("budget halt" in m[1] for m in budget._logger.messages)


def test_provider_budget_no_halt_flag_warns_and_continues():
    budget = _provider_budget(halt_on_exceeded=False)
    budget.acquire()
    budget.record_usage(1_000_000, 1_000_000)
    assert budget.halted is False
    assert budget.acquire() is True
    assert any("halt flag off" in m[1] for m in budget._logger.messages)


def test_provider_budget_zero_usage_costs_nothing():
    budget = _provider_budget()
    budget.acquire()
    budget.record_usage(0, 0)
    assert budget.spend_usd == 0.0
    assert budget.halted is False
