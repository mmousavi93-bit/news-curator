# Session 13 Brief — Repeat-gate content novelty + coverage cap

**Round goal:** noticeably raise digest quality on busy war-heavy windows.
**Evidence anchor:** run `35103425928` (2026-09-16T13:40Z, 324 items → 165 clusters).

## Findings (measured, not guessed)

1. **The repeat gate blocked the run's two highest-scored items as `no_development`.**
   `score=17.97` (Houthi strike on Aramco ینبع + Khamis Mushait base, 450 airstrikes) and
   `score=15.21` (Gaza building collapse, 20 dead) — both plainly *new developments* of a
   previously-delivered story, but the `developed` ratchet only looks at
   `independent_count↑` or `claim↑`. Corroboration and confidence were frozen, so the
   gate dropped the digest's best stories. Also wrongly blocked: `13.99` (south-Lebanon
   towns shelled), `13.12` (Jordan protests Houthi drone on Mecca), `12.20` (Aoun
   security deal). Five above-floor items in total.

2. **The coverage cap dropped 110 of 165 clusters — 34 of them on-mission** (18 with
   corroborating_count=1, incl. Mecca "red line", Araghchi US-casualties, Israeli
   double-tap, Malaysia shipments). `max_clusters_per_run=55` is the binding constraint,
   not groq cost.

## Changes

### Fix 1 — content novelty as a third "development" signal (PRIMARY)
`src/agent/pipeline/repeat_decision.py`: `developed` becomes
`indep↑ OR claim↑ OR content-novelty`, where content-novelty is deterministic and
zero-LLM — the new summary contains a **number** (death toll, count, date) or a
**named entity** (actor/place/org) that none of the matched priors carried. Both
signals are computed after `textnorm.normalize()` digit-folding so ۲۰/٢٠/20 collapse.

Why zero-LLM: the digest already spends its free-tier LLM budget on extraction; a
deterministic ratchet on the summaries/entities we already hold is free and cannot
add latency. False positives (keeping a true duplicate) are far cheaper than the
current false negatives (killing a real development).

### Fix 2 — raise `max_clusters_per_run` 55 → 90 (SECONDARY)
`config/settings.yaml` + `tests/fixtures/settings_minimal.yaml`. Recover the 34
on-mission clusters the cap drops. Cost check: batch_size=5 → 90 clusters = 18
calls/run × 6 runs = 108 calls/day vs groq `rpd: 1000` (~11%) and
`max_calls_per_run: 70` (18 ≪ 70). No run-duration watchdog is affected
(`flash_watchdog_max_age_minutes: 720` is the *commit-age* heartbeat, not a job timer).

## Tests

- `test_bypass_via_content_novelty_new_number` — same story, new number, indep/claim
  frozen → bypass.
- `test_bypass_via_content_novelty_new_entity` — same story, new named entity,
  indep/claim frozen → bypass.
- Existing `no_development` / high-water / reason-tag tests must stay green (their
  summaries carry no digits and no entities, so content-novelty is inert there).

## Rollback

- Fix 1: revert the `or _content_novelty(...)` clause and the two helpers.
- Fix 2: `max_clusters_per_run: 55` in both YAML files.

## Follow-up (2026-09-17) — Fix 3, the missed timeout interaction

The brief's cost note ("No run-duration watchdog is affected") was **wrong**: GitHub's
job `timeout-minutes: 30` IS a run-duration cap, and the 55→90 raise pushed a busy run
past it. Manual dispatch `35197529008` was cancelled at exactly 30 min mid-summarize
(measured: cap-55 run `35161171996` = 20.0 min; cap-90 needs ~35 min with groq's
8/18 429-and-retry). No digest was delivered that run.

Fix 3: `.github/workflows/pipeline.yml` `timeout-minutes: 30` → `90`. Public repo =
unlimited minutes; the LLM breaker — not this timer — is the real hung-call guard.
Re-verify by re-dispatching and confirming clusters > 55 AND a delivered digest.
