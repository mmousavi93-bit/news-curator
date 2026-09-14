# Session 9Z — Digest-side de-escalation 📉 notice (Phase 2 of 9Y)

## Decision (owner-approved 2026-09-13)

9Y moved escalation out of the 15-min flash channel (flash = `tehran` only) and
deferred the 📉 notice to Phase 2: a **stateful, deterministic, zero-LLM**
de-escalation notice inside the 3-hourly digest. This session wires it in.

## What it does

Once per run, the digest records (in its own `meta` table) whether ≥1 event
that *clears the `strategic` relevance tier* AND is `military`/`security` in
category was actually **delivered**. The 📉 notice fires when:

- escalation was delivered on ≥ `streak_days` (3) distinct Tehran days within
  the last `window_days` (30), AND
- the stream has been quiet for ≥ `quiet_days` (3) days since that last
  escalation day, AND
- the last notice is older than `cooldown_days` (7).

The notice is prepended **above the digest header** (next to the flash-watchdog
warning), on every path — including the "nothing new" one-liner, which is
exactly when it is most relevant.

## Rationale

- **Definition of "escalation surfaced"** = an event that reached the
  `strategic` tier (relevance score ≥ 4) in a `military`/`security` category.
  This reuses the existing relevance pipeline; no new classification, no LLM.
- **No fabricated content**: the notice is only ever emitted from *real,
  delivered* history. The days list is recorded only after every real Telegram
  send succeeded (same all-real gate as received-markers in `deliver.py`), so a
  failed send never pollutes the trend.
- **Stateful, digest-owned**: state lives in the digest's `meta` table
  (`deescalation_v1`), not the flash DB. The flash DB is Tehran-only now and
  lives on the flash-state branch — the two are unrelated.
- **Starts fresh, deliberately**: the digest's meta table has no escalation
  history before this deploy, so the notice cannot fire for at least
  `streak_days` + `quiet_days` of real traffic. No backfill from the (closed,
  differently-defined) flash escalation bursts — the flash "escalation" taxonomy
  is not the digest's military/security-strategic definition, and mixing them
  would fabricate a trend.

## Scope — files touched

### New
- `config/deescalation.yaml` — thresholds: enabled, streak_days=3,
  window_days=30, quiet_days=3, cooldown_days=7.
- `src/agent/pipeline/deescalation.py` — `validate_deescalation`,
  `is_escalation`, `maybe_deescalation_notice`, `record_escalation_day`,
  `mark_notice_sent`. All pure, `conn=None`/`config=None`/disabled → no-op.

### Wired in
- `src/agent/config.py` — `Config.deescalation` field; `load_all` loads and
  validates `deescalation.yaml` (reports problems in the same single
  `ConfigError`, like the other files).
- `src/agent/pipeline/labels.py` — `deescalation` label (fa + en), a single
  `{days}` placeholder filled with Persian digits.
- `src/agent/pipeline/compose.py` — computes the notice once and builds a
  `preamble` (watchdog warning + notice) used on the header and both one-liner
  paths; sets `ctx.deescalation_notice` and `ctx.escalation_delivered`.
- `src/agent/pipeline/deliver.py` — after the all-real-success gate:
  `record_escalation_day` (if escalation delivered) and `mark_notice_sent` (if
  the notice was in the message).

## Invariants respected
- **Additive-only schema**: no schema change — `meta` table already existed.
- **Deterministic, zero LLM**: pure date/score math; no model calls.
- **Never fabricate**: notice derives only from delivered-event history.
- **Graceful degradation**: missing db / disabled / unset config never crash a
  run (all entry points return `None` or no-op).

## Test changes
- `tests/unit/test_deescalation.py` — 18 new tests: config shape, category +
  strategic-tier gate, notice fire/streak/quiet/cooldown/corrupt-clock, day
  recording + idempotence + pruning, clock stamping.
- `tests/conftest.py` — `tmp_config_dir` fixture now copies `deescalation.yaml`.
- `tests/unit/test_config.py` — the every-problem test writes a valid
  `deescalation.yaml` so it keeps proving exactly three problems.
- Full suite: **839 passed** (was 821).

## What this does NOT do
- No backfill of pre-deploy escalation history (see Rationale).
- No change to flash (Tehran-only, committed separately as `d4e4eac`).
- Phase 3 (cadence fix) still deferred — infra, not code.
