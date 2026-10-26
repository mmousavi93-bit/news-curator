# Session 10D — Risk signal extraction plan

Planning only. This document is the sole deliverable; all implementation paths below are future ownership assignments, not changes authorized in this session. `config/risk_weights.yaml` and `src/agent/risk/engine.py` remain unchanged. No commit.

## 1. Decision and evidence boundary

Choose **LLM classification of clusters, followed by deterministic evidence validation and field derivation**. Extraction may use an LLM in `pipeline/`; scoring may not. Constraint 3 explicitly permits extracting discrete signals while Python alone computes scores. Identical accepted events, history, configuration and evaluation date must reproduce identical scores. Fresh model calls are not guaranteed deterministic; store their responses and versions for exact replay rather than claiming temperature zero guarantees reproducibility.

Keyword rules are a useful test baseline, but are not the primary classifier: negation, ordered versus contemplated departures, attribution, multilingual paraphrases, and comparisons against earlier rhetoric are semantic distinctions. A keyword hit cannot establish them. The chosen approach follows ESCALATION_SCORING §1/§5 and WAR_SIGNALS_PAPER, with explicit evidence requirements and measured precision/recall rather than assumed superior recall. It must return zero or more candidates, including an explicit `none`; no forced best-fit label. Do not classify from the generated digest summary alone: give the classifier member titles/bodies, stable item IDs, publication metadata, and relevant historical evidence. Truncation means missing evidence, not permission to complete a story.

References read: `src/agent/risk/engine.py`, `config/risk_weights.yaml`, `analysis/ESCALATION_SCORING.md`, `analysis/SCORING_RULEBOOK.md`, `analysis/WAR_SIGNALS_PAPER.md`, `analysis/backtest_weights.py`, and `agents/briefs/PHASE_9_BRIEF.md`. Optional `references/risk-engine-phase-11.md` is absent. Additional integration references: credibility.yaml, CLAUDE.md decisions, pipeline wiring/understand/cluster/validate/independence/signals, Item fields, memory schema, AGENT_PROMPT and SESSION_20_BRIEF.

Checkout reconciliation: an **unwired offline prototype** already exists in `pipeline/signals.py`; the production score stage remains a no-op. Reuse and harden that prototype instead of duplicating it. Its current model-date/latest-publication fallback, cluster-wide corroboration and tier proxies, default novelty, and always-null state end are not production attribution. This does not change the brief's core problem: the live pipeline lacks the complete extraction-to-scoring path. The actual catalog includes **E6**, despite the brief's E1–E5 shorthand; include it. C1 is a side-channel countdown, and G1/G2 belong to the markets fetcher.

### Candidate contract and acceptance

Each candidate carries signal ID, structured actor/action/object/location, occurrence or state identity, supporting item IDs, exact source-language quotes per item, temporal qualifiers, and optional state/calendar/statement updates. Metadata and quotes remain in an evidence envelope outside the unchanged six-field SignalEvent. Reject unknown IDs, foreign item IDs, fabricated/nonmatching quotes, unsupported numbers, contradictory schema (`none` plus candidates), and unsupported dates. News text is untrusted input, never instructions. Quote matching proves provenance, not semantic truth; the history accuracy gate tests semantic entailment.

Validate each claim against its own supporting items. Two sources covering different facts in the same cluster cannot corroborate one another's signal. Known syndicated copies or explicit relay attribution do not count as independent evidence of that claim: retain one origin's support, then count configured groups. Uncertain provenance is flagged for review; do not invent a new independence group. A candidate with fewer than two eligible groups remains an auditable nomination/RUMOUR, with its true count, and contributes nothing. Never rely on the dataclass default count of 2.

### Mapping rules (required predicates; exclusions are binding)

All rows require current, affirmative evidence with relevant actors and geography. Hypothetical, denied, quoted-as-false and historical recap claims do not create new events. Multiple IDs are allowed only when each predicate is independently supported.

| ID | Required trigger and exclusion |
|---|---|
| A1 | Identified CSG ordered toward/into CENTCOM; not a possible deployment or unrelated transit. |
| A2 | At least two identified CSGs simultaneously in theater; derive overlap from supported posture intervals, not two deployments in different months. |
| A3 | Newly emplaced THAAD/Patriot/air-defense battery in region; shipment or longstanding presence alone is insufficient. |
| A4 | Strategic bombers deployed within operational range; not an exercise announcement or generic aircraft move. |
| A5 | At least 10 tanker aircraft moving to theater in the same evidenced surge; count aircraft, not reports. |
| A6 | Actual munitions shipment/pre-positioning; not procurement intent. |
| A7 | Gulf bystander restricts US basing/airspace access; not generic neutrality language. |
| A8 | Israeli force regeneration or completed large air exercise; not merely announced/in-progress exercise. |
| A9 | Reserve call-up, leave cancellation or mobilization order; not readiness rhetoric. |
| B1 | Official ordered departure of US embassy personnel in region; not advisory changes or contemplated drawdown. Authorized-only civilian departures remain review candidates under the rulebook's narrower wording. |
| B2 | Authorized/ordered CENTCOM military-family departures; not anonymous planning. |
| B3 | UKMTO/JMIC/MARAD advisory escalates scope or language against its own 30-day baseline; unchanged republication does not fire. |
| B4 | Pre-strike airline suspension or documented war-risk insurance spike; not ordinary disruption or reactive airline cancellations. |
| B5 | Relevant official's scheduled testimony/briefing/trip cancelled or postponed within seven days of due date; require prior schedule, exclude long-planned changes. |
| B6 | Major regional NOTAM/airspace closure before strike; not a closure during/after that strike. |
| C1 | Explicit authority-issued dated deadline; update countdown registry only, never pass C1 as SignalEvent. |
| C2 | Final/high-stakes round ends without deal AND maximalist demands; ordinary continuing negotiations insufficient. |
| C3 | Previously scheduled meeting verifiably failed to convene after its due time; silence alone insufficient, rescheduling updates registry. |
| C4 | Formal IAEA censure/non-compliance finding or snapback trigger; not routine reporting. |
| C5 | New enacted legal predicate for force, including failed restraint vote; introduced bills insufficient. |
| C6 | Named mediator publicly quits or declares mediation collapsed; difficulty alone insufficient. |
| D1 | Head-of-state/government ultimatum with explicit timeframe OR named targets; record C1 separately only if a date is grounded. |
| D2 | Same leader shifts optimism to pessimism on same live negotiations; retrieve 30-day baseline and require cited positive/negative statements within 14 days, excluding mixed same-day statements. |
| D3 | Iranian/IRGC official shifts route-control language to closure/mining/destruction versus own 30-day baseline; chronic harsh rhetoric insufficient. |
| D4 | Chronic threats or anonymous strike-preparation stories without a stronger supported trigger; retain the corroboration gate, never use as catch-all for unrelated news. |
| E1 | Iranian-attributed attack impacts commercial vessel in Hormuz/Gulf/Omani waters; not historical recap, unattributed damage or fully intercepted attempt. |
| E2 | Distinct second E1-type attack within seven days of a prior accepted attack; require both event identities/dates. Reposts and multiple vessels in one incident are not a second attack. |
| E3 | Attack impacts US base/military asset in Gulf; not threat or fully intercepted attempt. |
| E4 | Proxy action opens/widens front (PMF/Hezbollah/Houthi strikes or attacks on them); political statements and unchanged baseline insufficient. |
| E5 | Disclosed major interception/near-miss; not routine drone interception. |
| E6 | Major infrastructure/military communications cyberattack with corroborated attribution; not defacement or media DDoS. |
| H1 | Mass protests plus lethal crackdown order/action; localized routine protest insufficient. |
| H2 | National internet blackout supported by measurement organization; not regional throttling/platform block. |
| H3 | Comparable bazaar rial observations establish >15% depreciation within seven days; record units/direction and calculate in Python, not model arithmetic. |
| G1/G2 | Reject as news-emitted signals; separate market adapter supplies them. |

Where AGENT_PROMPT is broader than the scoring rulebooks (notably authorized-only B1 and unattributed E1), the above narrower predicates govern this plan. Keep disagreements visible in evaluation; do not broaden labels to hit score bands. Missing baseline yields `insufficient_history`, not invented D2/D3/B3 evidence. C3 requires positive non-convening evidence; polling silence only schedules verification.

## 2. Derivation of all six SignalEvent fields

| Field | Deterministic derivation after candidate classification |
|---|---|
| `signal_id: str` | Validate classifier ID against YAML keys minus C1 and market-only IDs; enforce the mapping predicates above, historical joins for A2/E2/D2/D3, and claim-specific evidence. No model-generated IDs or weights. |
| `event_date: date` | Earliest valid `published_at` among eligible Tier-1/2 items supporting this particular occurrence, not earliest/latest member of the entire cluster and not median. Normalize timestamped items to Asia/Tehran before taking date; preserve date-only calendar dates without inventing a time. Freeze that date across reposts. Missing/all-future/ambiguous timestamps quarantine the candidate; never substitute run time, epoch, or an LLM date. |
| `source_tier: int` | Best (lowest numeric) configured tier among eligible claim-supporting corroborators: 1 if present, otherwise 2; nomination with only Tier-3 support gets 3 and count 0. Lead-only/unknown-only evidence produces no scored event. Unrelated Tier-1 cluster members cannot upgrade it. |
| `independent_source_count: int` | Number of DISTINCT credibility groups among eligible supporting Tier-1/2 sources, after relay exclusion. Reuse `pipeline.independence.independent_groups`; do not use `Cluster.independent_count()` (includes Tier 3). Explicit null/empty group uses `__self__:<source_id>` to avoid collision with named groups. Missing credibility entry is a join/configuration error, not an independent fallback source (2026-08-17 regression). |
| `novelty: float` | Persist per-ID quiet-cycle factor: initially 1.0; multiply by 0.6 once per completed firing-followed-by-14-quiet-days cycle, minimum 0.3. New corroborated A/B/E occurrence with YAML base × configured tier multiplier >=15 resets all factors to 1.0 immediately. Apply reset before materializing this run's events; use stored factor for historical active contributions too. Repetition/dedup is separate, below. |
| `state_end_date: date | None` | Non-stateful IDs always None. For A1/A2/A3/A4/A7/H1 retain None while accepted state remains active; explicit corroborated end evidence sets the effective end date. Preserve original event_date and attach end evidence to the same episode; do not emit an additive duplicate event for the end. |

Publication date is an observation proxy, not guaranteed occurrence time. Store explicitly reported occurrence time separately for historical-recap rejection and attack ordering. A newly published report about an older event must not refresh that event or activate a new floor; attach to an existing episode or quarantine for temporal review if it cannot be resolved. This conservative policy can miss late first reports and must be measured. Evaluation date comes from injected `ctx.now`, in the same timezone. Engine windows remain its existing whole-day semantics; do not promise precise hourly floors or change the scorer.

### Deduplication, novelty and state memory

Persist an occurrence key built from normalized signal, actor/object/location, grounded occurrence identity and episode anchor. Cluster keys change when membership changes, so are not occurrence keys. Compare with the previous run AND retained history; exact source IDs/URLs and known event links merge evidence. Ambiguous semantic matches remain review candidates rather than automatically merging distinct strikes or multiplying repeated stories. One occurrence per score input, one open episode per identical state; A2 is one theater overlap state, not one event per CSG pair. Different episodes of the same ID remain distinct. E1 and E2 may both describe a qualifying second strike as different catalog predicates, once each.

Reposts update evidence/count/tier but never refresh event_date, create a firing, or multiply novelty by 0.6 per run. Quiet-cycle bookkeeping advances only at the canonical 09:00 Tehran daily evaluation. Define quiet as no new accepted occurrence of that ID and no supported linked kinetic consequence during the 14-day interval; active posture is not quiet. The phrase “no consequence” is otherwise underspecified: use linked E-event evidence, retain the linkage decision, and freeze this operational definition in labels before implementation. Incomplete collection/extraction days are unknown, never quiet. A closed cycle applies one decrement; further silent weeks without a new firing do not repeatedly decrement. Intraday expensive new signals may reset immediately but cannot advance quiet-day counters.

State-end predicates: A1 CSG exits/withdrawal order cancels the posture; A2 confirmed theater count falls below two; A3 battery removed/deactivated; A4 bombers leave range/end deployment; A7 access restriction lifted; H1 lethal crackdown explicitly ends. Match actor/asset/region to the open episode and require the same corroboration discipline for end evidence. Use an explicitly supported effective date; if only present-tense termination is known, use the end report's publication date, marked as a proxy. Future announced withdrawal is pending until effective and verified, not already ended. End-before-start/conflicting evidence requires review. Reopening creates a new episode only on supported reactivation.

No-news is not state-end evidence. Store `last_confirmed_at`, `last_checked_at`, and observation health separately; never confuse last confirmation with actual end. The rulebook's parenthetical “last date confirmed” conflicts with its state-end prose, so this plan selects explicit end semantics, matching engine arithmetic and posture persistence. Active episodes persist across quiet runs; stale/unobservable states are marked uncertain in assessment metadata and queued for verification, with no fabricated end date. A freshness/expiry policy needs measured evidence and is not silently introduced here. Missing/corrupt required history halts scoring rather than resetting memory.

## 3. Pipeline hook and persistence contract

Target linear flow: `cluster → understand (semantic candidates) → signal attribution → validate (digest policy) → score → compose`. All classification happens after clustering. Extend the existing understand response to carry signal candidates plus its usual digest fields in the SAME call; put parsing/candidate logic in `src/agent/pipeline/signals.py`, deterministic attribution stage in proposed `pipeline/signal_attribution.py`, and score adapter in proposed `pipeline/score.py`. Wire through `pipeline/__init__.py` and explicit RunContext fields in `run.py`. No LLM calls enter `risk/` or ValidateStage.

Attribution runs after understand and before validate's digest repeat suppression. Keep risk candidates separate from `ctx.events`; relevance decisions and digest suppression must not discard valid risk updates, including state reaffirmations. Independently recompute per-claim credibility rather than inheriting digest validation. Process history/calendar transitions even on a run with zero new clusters. Capture fates for omitted/truncated/budget-skipped clusters so incomplete coverage never appears as `none`.

First build and evaluate the existing standalone extractor offline; do not enable a second production LLM call for every cluster. Production same-call integration is conditional on accuracy, digest non-regression, and measured prompt/output tokens, latency, batching and router-call budget. If it fails, keep extraction offline and score unwired; do not exceed existing caps or purchase an adjudicator. Test single, batch and minimal-recovery contracts, with cluster-key mapping and explicit incomplete-risk status when recovery lacks risk fields.

Proposed evidence/storage layer: immutable raw candidate record (input hash, prompt/model/provider/schema versions and raw response), supporting item/quote rows, deduplicated occurrence/state episode rows, per-ID novelty ledger and canonical-day checkpoints. Adapt existing signal_events/speaker_statements/scheduled_events tables with additive schema upgrades and repository helpers; the existing single `source_group` column cannot represent claim corroboration. Retain source/group snapshots and event_date independently of observed_at, plus counts, tier, novelty and state end. Retain long-lived state and quiet-cycle checkpoints beyond raw item expiry; follow existing 180-day signal/45-day statement retention without deleting active state or necessary baselines. Atomic upsert/checkpoint transactions and unique occurrence/run keys make restart/replay idempotent.

C1/countdowns and soothing statements travel in a separate extraction envelope: validate exact evidence and corroboration before passing `deadline`/`deception` kwargs to `RiskEngine.evaluate`. C1 cannot be sent in events because engine's ordinary catalog excludes it. For multiple active conflict-relevant deadlines select earliest unexpired effective date, stable ID tie-break, and retain other entries; extensions/cancellations require grounded updates. Set deception true only for supported official soothing statements in the last 72 hours; the engine alone tests the A/B contribution and multiplies. Never subtract for soothing language or absent B signals. Score adapter combines deduplicated persisted news events with separately supplied markets events; unavailable markets are reported missing, never synthesized from headlines. Economic scoring is out of scope.

## 4. Tests and release gates

### Unit and integration tests (future implementation)

- Mapping: positive/negative/minimal-pair cases for every ID in Persian/English/Arabic; negation, historical recap, hypothetical orders, attribution, unsupported quantities, quote injection, multiple distinct facts in one cluster, invalid IDs and `none`. Cover E6, no C1/G emission, no baseline/no D2/D3/B3, C3 silence versus verified missed meeting, and E2 distinct-event sequencing.
- Date: supporting subset versus unrelated older member, timezone midnight, date-only, absent/future dates, old report, repost and changed cluster key; stable earliest publication date. Source tier: best supporting tier only, not cluster-wide best; strict integer tiers and no lead scoring.
- Independence: BBC English/Persian one group; Reuters/AP genuinely independent reports two; same-wire relay one; all Tier-3 zero; lead excluded; missing source join fails; null group prefix collision; only one member supports the nominated claim. Assert explicit count survives persistence and engine rejects count <2 including all floors.
- Novelty: prior-cycle duplicate versus new occurrence, 13/14/15 quiet days, incomplete days, 1.0→0.6→0.36→0.3, one decrement per cycle, no hourly decrement, reset threshold below/at 15 using YAML multiplier, simultaneous reset/decrement ordering, restart and missing ledger.
- State: each of six end predicates, silent run preserves active state, uncorroborated end rejected, partial withdrawal leaves A2 active, explicit end starts decay at end, future end pending, ambiguous contradiction flagged, reactivation new episode, active state not a new firing every run.
- Integration: mocked router, immutable captured-response replay, swapped batch keys, partial batch recovery, refusal/cap/unavailable distinct from none; state-only/empty runs; digest repeat suppression cannot suppress risk; additive DB upgrade/rollback/restart idempotence; no duplicate counts after rerun. Identical accepted inputs yield identical six-field events and engine results. Run existing scorer and relevant pipeline suites; no scorer/weight edits to fix failed tests.

### Reuse all five calibration scenarios without rewriting them

| Frozen scenario | Required existing state-mode score band |
|---|---|
| June 11 2025 evening | TACT >=75 (reference rounded 100.0) |
| February 21 2026 | STRAT 55–70 (reference rounded 63.9) |
| February 27 2026 | STRAT >=70 |
| July 7 2026 | TACT >=75 |
| Quiet April 20 2025 | STRAT strictly <15 |

Import `analysis/backtest_weights.py` SCENARIOS without calling its CSV-writing main. Translate pre-gated fixtures into SignalEvents with explicit count 2, preserving dates/tiers/novelty/until plus deadline/deception. Assert engine rounded parity with reference state-mode arithmetic AND all five bands. Keep decay-mode February failure as a diagnostic for why state persistence matters, not a production alternative. These synthetic inputs test scorer/adapter fidelity, not extraction accuracy or availability of real sources.

Then replay source-grounded historical clusters through extraction and compare to independently labeled events; do not manufacture articles from SCENARIOS and call them history. Inject market fixtures only at the market boundary and label them as such. Separate oracle-event score, extracted-event score and missing-source coverage. No-lookahead replay uses only evidence available at each cutoff, including for state ends and missed meetings; a future `until` in the synthetic backtest is not permission to use future history labels during extraction.

### Five hand-labeled-history accuracy gate

Freeze five datasets at the exact scenario cutoffs above, including April's quiet week, with prior history sufficient for 30-day baselines, state continuity and novelty (up to retained 180 days). Preserve original member texts, URLs, timestamps, source/group snapshots and collection availability. Label every sampled cluster including negatives, signal-instance identities, supporting evidence, six derived fields, state transitions and C1/F side channels. Labels are written without seeing model output, independently reviewed and disagreements adjudicated; unavailable source history is explicitly missing and blocks claims of full gate completion. Never treat model output as gold.

Use a separate development corpus for prompt tuning, then freeze labels, prompt/model version, schema and thresholds before these five acceptance histories. Measure candidate-level and accepted-event-level TP/FP/FN by occurrence+signal identity, micro precision/recall/F1, per-ID/category/language results, exact field accuracy, evidence validity, unknown/abstention counts, parse success and history/source coverage. Count abstention on a gold positive as FN; do not remove budget failures from the denominator. Compare keyword baseline and chosen LLM on the same data. Record sample sizes and uncertainty; five dates cannot establish a monthly false-alarm rate or coverage for absent signal classes.

Proposed acceptance thresholds (design targets, not measured results): accepted-event precision >=95%, recall >=90%, F1 >=92%; zero fabricated evidence/IDs/dates or inflated corroboration; 100% deterministic field derivation on fixtures; zero misses among available gold B1/B2/B6/E1/E2 floor-critical events; quiet-week correct-none >=90% and no false Tier-3/4 rise. Report unsupported classes as unmeasured and cover them with controlled unit fixtures, not invented historical examples. Report candidate metrics even when corroboration suppresses a false positive. Compare extracted scores against oracle scores and five historical bands, investigating any mismatch without tuning weights or relabeling to force a pass. Missing gold/source coverage means “gate incomplete,” not pass. Repeat live classification on frozen inputs to measure model variability separately from deterministic replay; log tokens/calls and digest regression before wiring. A failed gate keeps extraction in offline/shadow evaluation.

## 5. Build order and ownership by model tier

| Order / tier | Owned files (future changes unless noted) | Exit condition |
|---|---|---|
| 1 — **astra: this plan** | `analysis/SESSION_10D_EXTRACTION_PLAN.md` only | Architecture, mapping/field rules, tests and handoff documented. No code/config changes or commit. |
| 2 — **sol: contract and offline core** | Existing `src/agent/pipeline/signals.py`, proposed `signal_contract.py`, `signal_attribution.py`, `signal_history.py`, `signal_calendar.py`; `config/prompts/signal_extraction.txt`; `tests/unit/test_pipeline_signals.py` and proposed `test_signal_attribution.py`, `test_signal_history.py`, `test_signal_calendar.py` | Strict evidence contract, field rules, lifecycle and mocked tests; reuse independence helper, split modules near 200 lines. |
| 3 — **sol: persistence and measurement** | Proposed `src/agent/memory/signal_models.py`, `signal_state_models.py`; existing `memory/schema.sql`, `memory/db.py`, `memory/retention.py`; proposed `tests/unit/test_signal_models.py`, `tests/fixtures/risk_extraction/`, `analysis/eval_signal_extraction.py`, `analysis/SESSION_10D_EXTRACTION_EVAL.md` | Additive migration and replay pass; human-reviewed labels, five scorer fixtures, frozen history accuracy/token measurements recorded. Semantic labels require review, not boilerplate generation. |
| 4 — **sol: gated integration** | `src/agent/pipeline/understand.py`, `single_run.py`, `batch_run.py`, `batch.py`, `contract.py`, `__init__.py`, proposed `score.py`; `src/agent/run.py`; `config/prompts/understand.txt`, `understand_batch.txt`, `understand_minimal.txt`; existing relevant pipeline tests | Same-call extraction and score adapter only after accuracy/budget/digest gates; risk inputs survive digest filtering; no extra per-cluster call loop. |
| 5 — **luna: formatting/boilerplate** | Proposed `analysis/SESSION_10D_EXTRACTION_RUNBOOK.md`, `tests/fixtures/risk_extraction/README.md`, formatting-only cleanup of `analysis/SESSION_10D_EXTRACTION_EVAL.md` after sol handoff | Document measured operation, fixture layout and replay commands; no semantic label/prompt/rule changes. Sol reviews any generated test scaffolding before use. |

All tiers leave `config/risk_weights.yaml`, `src/agent/risk/engine.py` and the calibration scenario definitions untouched. Model tiers above describe future responsibility, not agents launched by this planning session. Production implementation and its gates are future work; this plan reports no measured extraction accuracy and no live deployment.
