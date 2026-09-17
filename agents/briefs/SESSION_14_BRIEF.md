# Session 14 Brief — Token pacer window: 60s → 120s to match groq's real window

**Round goal:** stop the groq 429 thrash that leaves clusters "بدون خلاصه" on busy runs.

**Evidence anchor:** run `35209729646` (2026-09-17T10:19Z, 80 clusters, on `886bfec`):
`groq 34 call / 22 fail (65%)`, all `status_429`, every one `tokens_in=0 tokens_out=0`
— rejected at the door, never reaching token accounting — and each failing
`prompt_hash` retried 3–4× before giving up. Result: `repeat_dropped=17` and the
"۱۷ خبر بدون خلاصه" spike the user flagged.

## Finding (measured, not guessed)

groq's effective token window is **~120s, not the 60s the pacer models.** First
measured in run `34857770910` (see `src/agent/llm/token_pacer.py` history): a call
fired exactly 60s after the previous still 429'd because groq counts a request's
tokens past the nominal minute (4.6K + 1.5K + 2.2K ≈ 8.4K > 8K). That run had a
single residual 429 and it was recorded as a **follow-up: "widen the pacer window
to ~120s or drop `_SAFETY_FACTOR` to ~0.5" if it recurred.**

It recurred, at scale: at 80 clusters (16 batches) the 60s window lets the next
batch fire while groq still counts the previous, so the first attempt 429s, and the
fixed 30s failover cooldown (30s < 120s) retries into the still-full window — each
batch burns 3–4 attempts before giving up (`unavailable after 4 attempt(s)`). The
result is ~10 clusters dropped as `unavailable` and 5 more as `unparseable`.

## Change

`src/agent/llm/token_pacer.py`: `_WINDOW_SECONDS = 60.0` → `120.0`. The pacer now
books each attempt against a 120s trailing window, so a batch only fires once the
previous batch's tokens have drained from groq's real window. `_SAFETY_FACTOR`
stays 0.65 (per-request headroom unchanged) — this is the "widen the window" arm of
the recorded follow-up, chosen over dropping the factor because it matches the
measured ~120s window rather than shrinking the per-request allowance.

Cost check: a busy 80-cluster run becomes ~16 batches × 120s ≈ 32 min of groq
pacing, still well inside `timeout-minutes: 90`. Quiet windows (few clusters) slow
by a few minutes at most — correctness over speed for an unattended 3-hourly job.

## Tests

- NEW `test_calls_one_window_apart_do_not_age_out_too_early` — two bookings 60s
  apart must make the second wait 60s (fails with a 60s window, passes with 120s).
- Updated 5 existing tests whose hard-coded sleeps encoded the 60s window
  (55.0→115.0, 59.9→119.9, 58.0→118.0, 57.0→117.0; the two `clock.t=61.0`
  "aged out" assertions move to 121.0, one test renamed).
- All other tests stay green (safety-factor, per-provider, no-TPM, charge no-op,
  overfit-log paths are window-size-agnostic).

## Rollback

- `_WINDOW_SECONDS = 60.0` and revert the test file's sleep values.
