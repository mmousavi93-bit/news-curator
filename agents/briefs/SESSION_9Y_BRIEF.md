# Session 9Y — Flash redesign: drop escalation, flash becomes Tehran-only

## Decision (owner-approved 2026-09-13)

The flash monitor was designed for two signals: `tehran` (explosion/attack on
Iranian territory) and `escalation` (tension escalation via maritime/strike/
posture/apparatus/ultimatum/response-threat taxonomy). Owner's new framing:

> "فلش قرار بود اگه صدای انفجار توی تهران شنیده شد یا اسکلیت شدن تنش خبر بده.
> به نظرم این توی ران های ۳ ساعته جا میشه" — escalation is a TREND, not an
> event; it belongs in the 3-hourly digest, not a 15-min channel.

**Agreed path:** flash = `tehran` ONLY (narrow life-safety channel, fires rarely
by design — an explosion in Tehran is "look up now"). Escalation moves to the
digest (which already surfaces it via the `strategic` relevance tier +
importance ranking). The momentum layer (background-bucket, novelty-restore)
and the 📉 de-escalation notice are escalation-rhetoric-specific and are
REMOVED with the class.

## Rationale (why the whole momentum layer goes, not just the class)

- **background-bucket / streak**: "day 3+ of the same attack pattern is the new
  normal" is escalation rhetoric logic. For a Tehran explosion, MORE days = WORSE,
  never background. The tehran class keeps its own anti-FP (quiet window 24h,
  re-fire at ≥3 sources) — no day-level suppression.
- **novelty-restore**: "a new target domain restores escalation weight" is
  redundant for tehran — a new location already produces a new signature = a new
  burst that fires at 1 source (the quiet window is signature-scoped).
- **de-escalation 📉 notice**: only ever computed for the `escalation` class.
  With escalation gone it can never fire. Porting it to the digest is a separate
  feature (Phase 2, needs digest-side state + a definition of "escalation
  surfaced"), NOT a port.

## Scope — files touched

### Remove (escalation + momentum)
- `config/flash_alert.yaml`: drop `escalation` class, `momentum:` block,
  `deescalation:` block, `burst.novelty_min_gap_minutes`, `{convergence}`
  placeholder in the `first` template, and the `deescalation` template.
  `version: 2 → 3`.
- `src/agent/flash/momentum.py`: DELETE (escalation-only).
- `src/agent/flash/config.py`: drop `novelty_min_gap_minutes`,
  `momentum_*`, `deescalation_*` from `FlashConfig`; drop `deescalation` from
  `_TEMPLATE_KEYS`; drop `{convergence}` + `deescalation` from
  `_REQUIRED_PLACEHOLDERS`.
- `src/agent/flash/loader.py`: `_CONFIG_VERSION → 3`; drop momentum/deescalation/
  novelty-gap validation + constructor kwargs.
- `src/agent/flash/policy.py`: drop the momentum `override`/`novel` branch, the
  novelty-gap re-alert, the convergence note, and the de-escalation call.
- `src/agent/flash/history.py`: drop the three momentum/convergence queries
  (`first_seen_since`, `location_tokens_since`, `recent_distinct_buckets`) and
  their now-unused imports.
- `src/agent/flash/frames.py`: drop the `convergence` param from `render_first`.

### Unchanged (deliberately)
- `src/agent/flash/store.py`: keeps 30-day burst retention. Nothing now needs a
  30-day lookback (tehran quiet window = 24h), but reducing it would churn
  `test_prune_removes_old_closed_bursts_and_urls` for no behavioral gain. Left
  as-is; can be trimmed later.
- `src/agent/flash/schema.sql`: no change (additive-only rule; `location_ring`/
  `buckets`/`location_display` are still used by tehran).
- `src/agent/flash/matcher.py`: class-agnostic; with only `tehran` in config it
  matches only tehran. No change.
- `src/agent/flash/run_flash.py`: no change (imports only `history` url/seen
  helpers, not momentum).
- Digest pipeline (`relevance.py`, `relevance.yaml`, etc.): NO change this
  session. Escalation already surfaces via the `strategic` tier (regional
  anchors + نفتکش/هشدار سفر/محاصره) and importance ranking.

## Invariants respected
- **Additive-only schema**: no schema change (constraint: never rewrite).
- **Config version gate**: `version` bump 2→3 makes a stale-shape config fail
  loudly in the loader (the loader only knows the shape it was written for).
- **Never silently narrow/wipe state**: removing a class narrows the alert —
  this is the deliberate, owner-approved outcome, not an accident.
- **Green suite**: `PYTHONPATH=src python tools/pytest_shim.py tests`.

## What this does NOT do (deferred phases)
- **Phase 2** — digest-side escalation tier + a stateful de-escalation 📉 notice
  in the digest. NOT done here (would need digest state + a definition of
  "escalation surfaced"). Escalation still surfaces today via `strategic`; the
  📉 notice is temporarily gone.
- **Phase 3** — cadence fix (external scheduler → `workflow_dispatch`). Infra,
  not code; documented in SESSION_9W. The flash watchdog liveness signal
  (`flash_watchdog.py`) is unchanged and still correct for a rare-fire channel.

## Test changes
- `test_flash_matcher.py`: remove ~14 escalation/Larak/war-pattern tests; fix
  `test_real_config_loads_two_classes` (now one class), `test_malformed_terms`
  (target `tehran`), `test_strike_outside_iran` (tehran-only kill),
  `test_wrong_config_version` (bad version is now 4, not 3).
- `test_flash_policy.py`: remove escalation/momentum/deescalation/convergence
  tests; fix the two cap-deferral tests (4th event → `tehran|attack_air|region`);
  fix the Arabic lang-prefix test to use a Tehran explosion.
- `test_flash_run.py`: `_larak_item` → a Tehran explosion item; assert
  "هشدار انفجار".
- `test_flash_store.py`: unchanged (does not exercise the removed queries).
