# Session 11 — Flash-monitor watchdog threshold: 180 → 720 min

## Trigger (verified, 2026-09-14)

The digest's flash-liveness line fired on a **healthy** monitor: "⚠️ پایش
هشدار فوری پاسخ نمی‌دهد — آخرین ثبت وضعیت حدود 3 ساعت پیش". Investigation
showed `flash-alert.yml` is `active`, its last run (13:19 UTC) **succeeded**,
and the `flash-state` branch was only ~4 h stale. The alarm was wrong.

Root cause: the watchdog's 180-min threshold (session 9r) was chosen on the
assumption that GitHub's `*/15` cron is "10-20 min late at the median". That
assumption is falsified. Observed `flash-alert` run gaps on 2026-09-13/14:
**1.8 h, 1.9 h, 5.2 h, 6.8 h** (and 4+ h at check time) — GitHub throttles
public-repo `*/15` schedules to a handful of runs per day, not 96. The
flash monitor's own 15-min envelope is a separate, larger problem (see
"Known gap" below); this session only fixes the false alarm.

## Change

`config/settings.yaml` + `tests/fixtures/settings_minimal.yaml`:
`flash_watchdog_max_age_minutes: 180 → 720` (12 h), with the comment
rewritten to state the throttling observation.

12 h sits above the worst observed throttle gap (~7 h) so a healthy monitor
stays silent, while a genuinely dead monitor (60-day cron auto-disable,
broken edit, crash before `flash.ok`) is still flagged within half a day —
fine for a liveness check, whose latency budget is "inside a day", not "inside
15 min".

`tests/unit/test_flash_watchdog.py`: update the magic ages to the new
boundary (720 silent, 780 stale → "13 h", 1440 for the distinct-from-missing
case, 780 → "13h" for EN). No behaviour change beyond the threshold value.

## Known gap (NOT fixed here — needs infra, separate work)

The flash monitor is *meant* to run every 15 min (owner: 24/7 emergency
escalation), but GitHub's scheduler delivers it a few times a day. That is a
real latency gap for an emergency alerter, and a threshold change only stops
the digest from crying wolf about it — it does not restore the 15-min cadence.
Fixing it means moving the flash job off GitHub's free cron onto a reliable
scheduler (VPS cron / Cloudflare Workers / self-hosted runner). Deferred for
a separate decision; recorded in POSTMORTEMS.

## Invariants

- No secret is touched; no workflow change; `flash-alert.yml` unchanged.
- The watchdog stays pure (no clock/network/fs): only the threshold constant
  and its tests move.

## Scope — files

- `config/settings.yaml` — threshold + comment.
- `tests/fixtures/settings_minimal.yaml` — threshold.
- `tests/unit/test_flash_watchdog.py` — boundary ages.
- `POSTMORTEMS.md` — record the throttling finding + the 15-min-cadence gap.
