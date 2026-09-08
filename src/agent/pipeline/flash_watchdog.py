"""Flash-monitor liveness watchdog (session 9r, 2026-09-08).

The flash monitor is the only near-real-time component in the system, and it
had no in-band liveness signal: when it stops (60-day cron auto-disable, a
config `enabled: false`, a broken edit, a crash before `flash.ok`) nothing
says so. Session 9p spent a review round concluding the monitor was dead
purely because no artifact existed, and session 9q found it had been alive
the whole time. An emergency alerter whose liveness depends on the owner
remembering to open the Actions tab is not an emergency alerter.

Mechanism, deliberately the cheapest one that works: `flash-alert.yml`
force-pushes a commit to the orphan `flash-state` branch on every successful
run (*/15 cron), so the AGE OF THAT COMMIT is the monitor's heartbeat. The
pipeline workflow fetches the branch, exports the age in minutes, and this
module turns it into one line at the top of the digest. No GitHub API, no new
secret, no new dependency, no network call from Python.

Deviation from the 9p sketch, on purpose: the 9p design put the line in the
09:00 digest only. That leaves a 23.5-hour blind window for a monitor that
dies at 09:30 -- the whole point is bounded detection latency, so the check
runs on EVERY pipeline run. Cost is one line of text on a run that is already
being sent; the warning only appears while the monitor is actually down.

UNKNOWN AGE IS SILENT, and that is a real trade-off. A local run, a dry-run,
mock mode, or a workflow whose watchdog step was removed all produce no env
var, and this returns "". Warning on unknown would fire on every local run and
train the owner to skim past the line, which destroys the signal it exists to
carry. The residual gap: the watchdog can itself be silently absent. It is
bounded by the workflow step always setting the variable -- to the literal
`missing` when the branch cannot be fetched -- so in production the only way
to get an empty value is deleting the step, which is a visible diff.
"""

from __future__ import annotations

import os
from typing import Mapping

#: Set by .github/workflows/pipeline.yml. Minutes as a decimal string, or the
#: literal "missing" when the flash-state branch could not be fetched.
ENV_AGE = "NEWS_CURATOR_FLASH_AGE_MINUTES"

_MISSING = "missing"


def flash_warning(settings, labels: Mapping[str, str], env=None) -> str:
    """One Persian/English warning line, or "" when the monitor looks alive
    or its state is unknown. Pure: no clock, no network, no filesystem --
    the age is computed by the workflow and handed in, so the whole
    behaviour is reachable from a unit test with a dict."""
    env = os.environ if env is None else env
    delivery = settings.delivery
    if not delivery.flash_watchdog_enabled:
        return ""
    raw = (env.get(ENV_AGE) or "").strip()
    if not raw:
        return ""
    if raw == _MISSING:
        # The branch does not exist. Either the monitor has never completed a
        # run, or someone deleted the branch. Both need the owner's eyes.
        return labels["flash_missing"]
    try:
        age_minutes = int(raw)
    except ValueError:
        # A malformed value is a workflow bug, not evidence about the
        # monitor. Never assert deadness on a value we cannot read.
        return ""
    if age_minutes < 0:
        # Clock skew between the runner and the commit timestamp.
        return ""
    if age_minutes <= delivery.flash_watchdog_max_age_minutes:
        return ""
    return labels["flash_stale"].format(hours=age_minutes // 60)
