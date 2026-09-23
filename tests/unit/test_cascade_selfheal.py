"""Self-healing provider demotion + per-provider skip-reason logging.

Cascade order is ["gemini", "groq", "groq2", "mistral"]. gemini's free tier
dropped to ~20 RPD and limits.py quota-skips it, so a provider demoted for
sickness used to stay demoted forever (0/0 across runs: never attempted, so
never re-measured). health.py now counts cooldown in SAVED RUNS and re-admits
a demoted provider after DEMOTION_RETRY_AFTER_RUNS, and every skip states its
reason in the log.

Synthetic health samples + a recording logger double only: no network, no
model, no clock, no sleep.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agent.flash import store as flash_store
from agent.llm import health
from agent.llm.limits import ProviderBudget

NOW = datetime(2026, 9, 23, 5, 30, tzinfo=timezone.utc)
ORDER = ["gemini", "groq", "groq2", "mistral"]
SICK = {"calls": 6, "failed": 5}  # >= _MIN_SAMPLES calls, >= _FAIL_RATE failed
HEALTHY = {"calls": 8, "failed": 0}


class RecordingLogger:
    """Logger double recording (level, message), as the limits tests do."""

    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def error(self, msg, *args):
        self.messages.append(("error", msg % args if args else msg))

    def warning(self, msg, *args):
        self.messages.append(("warning", msg % args if args else msg))

    def info(self, msg, *args):
        self.messages.append(("info", msg % args if args else msg))


def _db(tmp_path):
    return flash_store.open_flash_db(tmp_path / "h.db", create_if_absent=True)


def _sick(runs: int) -> dict:
    return dict(SICK, demoted_runs=runs)


def _all_providers(gemini: dict) -> dict:
    """What ProviderStats.as_dict() hands save_health: every configured
    provider, with {calls: 0, failed: 0} for one that was never attempted."""
    out = {name: dict(HEALTHY) for name in ORDER}
    out["gemini"] = gemini
    return out


# ---------------------------------------------------------------------------
# (a) a demoted provider is retried after the cooldown elapses
# ---------------------------------------------------------------------------


def test_demoted_provider_held_until_cooldown_elapses():
    log = RecordingLogger()
    held = {"gemini": _sick(health._DEMOTION_RETRY_AFTER_RUNS - 1),
            "groq": dict(HEALTHY)}
    assert (health.cascade_order(ORDER, held, logger=log)
            == ["groq", "groq2", "mistral", "gemini"])
    assert any("skip_reason=demoted" in m for _, m in log.messages)


def test_demoted_provider_retried_once_cooldown_elapses():
    log = RecordingLogger()
    retried = {"gemini": _sick(health._DEMOTION_RETRY_AFTER_RUNS),
               "groq": dict(HEALTHY)}
    # Re-admitted to its CONFIGURED position, not appended to the front.
    assert health.cascade_order(ORDER, retried, logger=log) == list(ORDER)
    assert any("re-admitted" in m and "skip_reason=demoted" in m
               for _, m in log.messages)


def test_default_logger_needs_no_caller_opt_in():
    retried = {"gemini": _sick(health._DEMOTION_RETRY_AFTER_RUNS)}
    assert health.cascade_order(ORDER, retried) == list(ORDER)


def test_failing_retry_is_re_demoted_cleanly():
    # After a retry that fails again the sample is still sick; the NEXT saved
    # run that attempts it restarts the cooldown at 1 -- straight back to the
    # end of the cascade, exactly as before the retry.
    demoted = {"gemini": _sick(1), "groq": dict(HEALTHY)}
    assert (health.cascade_order(ORDER, demoted, logger=RecordingLogger())
            == ["groq", "groq2", "mistral", "gemini"])


# ---------------------------------------------------------------------------
# (b) a healthy provider stays first
# ---------------------------------------------------------------------------


def test_healthy_provider_stays_first():
    h = {"gemini": {"calls": 8, "failed": 0, "demoted_runs": 99},
         "groq": _sick(1)}
    assert health.cascade_order(ORDER, h, logger=RecordingLogger())[0] == "gemini"


def test_healthy_configuration_is_untouched():
    h = {name: dict(HEALTHY) for name in ORDER}
    assert health.cascade_order(ORDER, h, logger=RecordingLogger()) == list(ORDER)


# ---------------------------------------------------------------------------
# (c) skip reason is recorded per provider
# ---------------------------------------------------------------------------


def test_skip_reason_logged_once_per_demoted_provider():
    log = RecordingLogger()
    h = {"gemini": _sick(0), "groq": dict(HEALTHY), "mistral": _sick(1)}
    assert (health.cascade_order(ORDER, h, logger=log)
            == ["groq", "groq2", "gemini", "mistral"])
    demoted = [m for _, m in log.messages if "skip_reason=demoted" in m]
    assert len(demoted) == 2  # exactly one line per skipped provider
    assert "gemini" in demoted[0] and "mistral" in demoted[1]
    assert all("cooldown run" in m for m in demoted)


def test_quota_exhausted_skip_reason_logged_per_provider():
    log = RecordingLogger()
    gemini = ProviderBudget(
        name="gemini", max_calls_per_run=40, max_spend_usd=None,
        halt_on_exceeded=True, input_usd_per_mtok=0.0, output_usd_per_mtok=0.0,
        logger=log, max_calls_per_day=20, daily_calls_used=20,
    )
    assert gemini.acquire() is False  # 20 RPD free tier spent for today
    assert any("gemini" in m and "skip_reason=quota_exhausted" in m
               for _, m in log.messages)


# ---------------------------------------------------------------------------
# Cooldown bookkeeping end-to-end through save_health (the pipeline path)
# ---------------------------------------------------------------------------


def test_saved_runs_advance_cooldown_then_readmit(tmp_path):
    conn = _db(tmp_path)
    runs = health._DEMOTION_RETRY_AFTER_RUNS
    try:
        # Run 1: gemini IS attempted and fails -> demoted, cooldown starts at 1.
        health.save_health(conn, _all_providers(dict(SICK)), NOW)
        stored = health.load_health(conn)
        assert stored["gemini"]["demoted_runs"] == 1
        assert health.cascade_order(ORDER, stored)[-1] == "gemini"

        # Runs where the provider is skipped (quota-starved): calls stays 0,
        # the counter advances instead of the demotion sticking forever.
        for i in range(runs - 1):
            health.save_health(conn, _all_providers({"calls": 0, "failed": 0}),
                               NOW + timedelta(hours=i + 1))
            stored = health.load_health(conn)
            assert stored["gemini"]["calls"] == SICK["calls"]  # sample untouched
            assert stored["gemini"]["demoted_runs"] == i + 2

        # Cooldown elapsed -> re-admitted to its configured position.
        assert stored["gemini"]["demoted_runs"] >= runs
        assert health.cascade_order(ORDER, stored, logger=RecordingLogger()) == ORDER

        # The failing retry: attempted again, still sick -> clean re-demotion.
        health.save_health(conn, _all_providers({"calls": 2, "failed": 2}),
                           NOW + timedelta(hours=runs + 1))
        stored = health.load_health(conn)
        assert stored["gemini"]["demoted_runs"] == 1
        assert (health.cascade_order(ORDER, stored, logger=RecordingLogger())
                == ["groq", "groq2", "mistral", "gemini"])
    finally:
        conn.close()


def test_window_cap_reset_does_not_bypass_cooldown(tmp_path):
    """A chronically sick provider (199/199) pushed past _MAX_CALLS has its
    sample reset to 0/0 by the JSON-bounding cap -- the reset must NOT become
    a free re-admission ahead of the healthy providers (5a)."""
    conn = _db(tmp_path)
    try:
        # Still demoted: 199 calls, 199 failed -> far above _MIN_SAMPLES/0.5.
        health.save_health(conn, _all_providers({"calls": 199, "failed": 199}),
                           NOW)
        stored = health.load_health(conn)
        assert stored["gemini"]["calls"] == 199
        assert stored["gemini"]["demoted_runs"] == 1  # attempted-sick
        assert health.cascade_order(ORDER, stored)[-1] == "gemini"

        # Two more attempted calls push it past _MAX_CALLS: the sample resets
        # to 0/0, but the cooldown counter must survive the reset.
        health.save_health(conn, _all_providers({"calls": 2, "failed": 2}),
                           NOW + timedelta(hours=1))
        stored = health.load_health(conn)
        assert stored["gemini"]["calls"] == 0  # window cap reset the sample
        assert stored["gemini"]["demoted_runs"] == 1  # cooldown survives
        assert health.cascade_order(ORDER, stored)[-1] == "gemini"

        # A skipped (unattempted) run still advances the cooldown, never stuck.
        health.save_health(conn, _all_providers({"calls": 0, "failed": 0}),
                           NOW + timedelta(hours=2))
        stored = health.load_health(conn)
        assert stored["gemini"]["demoted_runs"] == 2
    finally:
        conn.close()
