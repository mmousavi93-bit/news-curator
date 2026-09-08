"""Unit tests for pipeline/flash_watchdog.py (session 9r, 2026-09-08).

The watchdog turns the age of the `flash-state` branch's last commit into one
line at the top of the digest. Its whole job is to be RIGHT about deadness:
a false alarm on every local run would train the owner to skim past the line,
and a missed alarm is the 9p failure mode (a review round spent guessing the
monitor was dead) in reverse. Both directions are pinned here.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from agent.pipeline.flash_watchdog import ENV_AGE, flash_warning
from agent.pipeline.labels import labels_for
from agent.settings import Settings

_FIXTURE = Path(__file__).parent.parent / "fixtures" / "settings_minimal.yaml"


def _settings(**overrides):
    raw = yaml.safe_load(_FIXTURE.read_text(encoding="utf-8"))
    raw["delivery"].update(overrides)
    return Settings.from_dict(raw)


FA = labels_for("fa")
EN = labels_for("en")


def test_fresh_heartbeat_is_silent():
    # 15-min cron + GitHub's 10-20 min median lateness: 40 minutes is a
    # HEALTHY monitor and must produce nothing.
    assert flash_warning(_settings(), FA, {ENV_AGE: "40"}) == ""


def test_exactly_at_the_threshold_is_silent():
    # Boundary is inclusive-silent: the warning fires only PAST the limit.
    assert flash_warning(_settings(), FA, {ENV_AGE: "180"}) == ""


def test_stale_heartbeat_warns_in_persian_with_hours():
    out = flash_warning(_settings(), FA, {ENV_AGE: "260"})
    assert out
    assert "پایش هشدار فوری" in out
    assert "4" in out           # 260 // 60 == 4 hours
    assert "{hours}" not in out  # the template was actually formatted


def test_missing_branch_warns_distinctly():
    out = flash_warning(_settings(), FA, {ENV_AGE: "missing"})
    assert out == FA["flash_missing"]
    # Distinct from the stale message: "never reported" and "stopped
    # reporting" need different owner actions.
    assert out != flash_warning(_settings(), FA, {ENV_AGE: "600"})


def test_unknown_age_is_silent():
    # No env var at all = local run, dry-run, mock mode. Warning here would
    # fire on every developer run and destroy the signal.
    assert flash_warning(_settings(), FA, {}) == ""
    assert flash_warning(_settings(), FA, {ENV_AGE: ""}) == ""
    assert flash_warning(_settings(), FA, {ENV_AGE: "   "}) == ""


def test_malformed_age_is_silent_not_an_alarm():
    # A broken workflow expression is evidence about the WORKFLOW, not about
    # the monitor. Never assert deadness on a value we cannot read.
    assert flash_warning(_settings(), FA, {ENV_AGE: "not-a-number"}) == ""
    assert flash_warning(_settings(), FA, {ENV_AGE: "12.5"}) == ""


def test_negative_age_is_silent():
    # Clock skew between the runner and the commit timestamp.
    assert flash_warning(_settings(), FA, {ENV_AGE: "-5"}) == ""


def test_disabled_never_warns():
    settings = _settings(flash_watchdog_enabled=False)
    assert flash_warning(settings, FA, {ENV_AGE: "9999"}) == ""
    assert flash_warning(settings, FA, {ENV_AGE: "missing"}) == ""


def test_threshold_is_owner_configurable():
    settings = _settings(flash_watchdog_max_age_minutes=60)
    assert flash_warning(settings, FA, {ENV_AGE: "90"}) != ""
    assert flash_warning(_settings(), FA, {ENV_AGE: "90"}) == ""


def test_english_labels_render_too():
    out = flash_warning(_settings(), EN, {ENV_AGE: "260"})
    assert "Flash monitor" in out and "4h" in out


def test_warning_fits_the_header_char_budget():
    # Constraint 8: the line is prepended BEFORE budgeting, so it consumes
    # the header budget. A warning that alone overran it would truncate the
    # digest header. Worst realistic case is a 3-digit hour count.
    settings = _settings()
    longest = flash_warning(settings, FA, {ENV_AGE: "99999"})
    assert len(longest) < settings.delivery.char_budget["header"] // 2
