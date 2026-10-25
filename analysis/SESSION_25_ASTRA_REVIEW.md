# Verdict

**MODIFY. Keep the maximum-five/rolling-24-hour direction, but DO NOT SHIP this revision as satisfying that contract.** Two blocking queue defects remain: quiet runs cannot drain pending candidates, and selected fresh candidates are not retained through delivery failure or complete budget exclusion.

Inspected revision: **`cfa11e9929547c55fbaea7214a56c04592b31e3d`**.

The implementation reduces volume, but “five highest scores plus an optional sentence” has not demonstrated holistic understanding. The economic-admission justification is also materially wrong under the production scoring path.

This was a read-only consultation. No implementation, network requests, deployments, messages, installations, or child agents. Initial and final `git --no-optional-locks status --porcelain` were empty; final tracked and staged diffs were empty, and HEAD remained unchanged.

## Evidence

**Evidence labels:** **Fact** means directly observed code, local artifact data, or an executed offline check. **Hypothesis** means an interpretation or expected production consequence that still needs validation.

### Verification performed

**Fact:** Targeted tests passed: **176 passed in 4.73 seconds**, using Python 3.12, `PYTHONDONTWRITEBYTECODE=1`, `PYTHONPATH=src`, `-p no:cacheprovider`, and a scratch-only `--basetemp`.

Test files run:

```text
tests/unit/test_pipeline_queue.py
tests/unit/test_pipeline_synthesis.py
tests/unit/test_pipeline_compose.py
tests/unit/test_pipeline_repeats.py
tests/unit/test_pipeline_deliver.py
tests/unit/test_memory_db.py
tests/unit/test_flash_matcher.py
tests/unit/test_flash_policy.py
tests/unit/test_llm_router.py
tests/unit/test_llm_router_budget.py
tests/unit/test_llm_health.py
tests/integration/test_delivery_mock.py
```

Additional inline offline checks exercised production queue, scoring, classification, delivery-marker, synthesis, and flash-matching functions with in-memory SQLite and stubbed responses. Results appear below. These were focused reproductions, **not chronological replay**.

The full suite was not run. **1034 passing remains a prior claim**, not this review’s result.

**Fact:** Session 25 spans two commits. Several components—including pending schema, synthesis prompt/module, and follow-up rendering—already existed in parent `27da60c`; `cfa11e9` wires queue selection and synthesis into composition and changes the floor. Reviewing only its diff would miss substantial shipped behavior.

### Local audit: confirmed quantities, corrected meanings

I aggregated all matching CSVs in the allowed `nc_review/pipeline/` and `nc_review/flash/` directories and scheduling records in the allowed `runs.json`.

| Observation | Local result |
|---|---:|
| Pipeline artifact runs | 28 |
| Recorded `sent` items | 756; median 27; maximum 38 |
| Recorded follow-ups | 645/756, 85.3% |
| Follow-up bands | 572 MID; 73 HIGH |
| Gemini failed attempts | 40/49, 81.6% |
| Groq failed attempts | 268/731, 36.7% |
| Groq2 failed attempts | 101/450, 22.4% |
| Mistral failed attempts | 0/409 |
| `irrelevant` clusters | 1515 |
| Runs reporting 150 clusters | 19/28 |
| `cap_dropped` / `cap_refused` | 749 / 183 |
| `unavailable` / `oversized` / `lang_dropped` | 17 / 9 / 21 |
| Pipeline run-creation gaps | Median 4.743h; maximum 12.620h |
| Flash run-creation gaps | Median 3.309h; maximum 7.345h |
| Flash artifact runs | 44 |
| Recorded flash matches | 5 |
| Trigger types in sampled scheduling records | All `schedule` |

Three qualifications matter:

- **“Sent” is a composition label, not an item-level delivery receipt.** All 28 runs report equal message and delivery counts, which supports successful delivery but does not repair that reporting definition.
- **Five flash matches are not proof of five sends.** The tuning CSV writes `kind=match` from matcher output, independently of policy/send outcome: `src/agent/flash/run_flash.py:80`, `:90`, `:154`.
- **Failed-attempt counters are broader than transport failures.** They also reflect HTTP and adapter-schema failures. They do not measure semantic correctness: `src/agent/llm/call.py:64`, `:96`; `src/agent/llm/failover.py:121`.

### B1 — BLOCKING: eligible pending candidates are inaccessible on quiet runs

**Fact:** Composition returns before queue selection both when there are no current events and when current events fail ranking. Queue selection occurs only afterward.

Evidence: `src/agent/pipeline/compose.py:110`, `:125`, `:134`, `:148`, `:154`.

**Executed reproduction:** Store one unexpired pending candidate scoring 20, set current events to empty, and call `ComposeStage.run`. Result: `compose=0`; pending count remains one.

**Second reproducible scenario:** Store the same pending candidate and supply only a current below-floor event. The second early return produces the same starvation.

**Impact:** Morning’s sixth-place item cannot become afternoon’s second-place item unless afternoon admits something fresh. A sequence of quiet runs can let it expire unseen. Calling the output “nothing new” also conflates “nothing freshly admitted” with “nothing eligible and undelivered.”

**Required correction:** Evaluate pending on every composition run, including zero-fresh, all-gated, and provider-degraded runs. Communicate degraded current coverage separately when necessary.

### B2 — BLOCKING: selection is mistaken for durable delivery responsibility

**Fact:** The queue stores only fresh candidates outside the selected keys. Selected fresh candidates never enter pending. Composition excludes fully truncated items from delivered keys but does not enqueue them. Delivery records markers only if **every** main-feed message succeeds.

Evidence:

- `src/agent/pipeline/queue.py:97–102`
- `src/agent/pipeline/compose.py:207–226`
- `src/agent/pipeline/deliver.py:43–46`, `:57–70`

**Executed reproduction:** Select six eligible fresh candidates. Five are selected; one is pending; **zero selected candidates are persisted in pending**. Feed one successful and one unsuccessful result to `_mark_if_all_real`: **zero delivered markers**.

**Reproducible failure scenarios:**

1. Five fresh candidates selected; Telegram fails. Next run has no fresh candidates. None of those five is available through pending.
2. A selected fresh candidate is fully excluded by the character budget. It is neither marked delivered nor queued.
3. Page one succeeds and page two fails. No item is acknowledged. Previously pending items on page one remain retryable despite having been seen; selected fresh items on page two lack queue retention.

This is more than a missing marker: collection records seen URLs before later processing, so refetching identical articles is not a reliable recovery mechanism. Evidence: `src/agent/pipeline/collect.py:83–86`; `src/agent/memory/dedup.py:142–154`, `:185–194`.

**Fact:** No operational outbox was found in the source path. The architectural statement promising outbox persistence is stale.

**Required correction:** Persist all admitted candidates before selection; associate rendered items with specific messages; acknowledge successful messages individually; retain unacknowledged candidates until expiry. Define an explicit “unknown send outcome” policy. Telegram success followed by a crash before durable state upload prevents a blanket exactly-once guarantee.

### M1 — MAJOR: pending candidates bypass current repeat validation and carry stale ranking/context

**Fact:** Fresh events pass repeat and same-run duplicate checks during validation. Pending candidates are merged later, without those checks. Pending filtering only excludes identical delivered keys.

Evidence: `src/agent/pipeline/validate.py:157–175`; `src/agent/pipeline/queue.py:35–41`, `:86–97`.

**Reproducible scenario:** Defer candidate A. Deliver a materially equivalent story B under another cluster key. On a later run with an eligible fresh item, A remains selectable because only B’s key appears in delivered. A and a fresh equivalent can also occupy separate slots.

**Executed check:** A pending candidate remained selectable after another key representing the same summary was marked delivered. This establishes the queue’s lack of semantic revalidation; it does not quantify live duplicate frequency.

Related facts:

- Pending scores are frozen at admission; fresh scores include current recency decay: `queue.py:41`, `:83`, `:96`; `rank.py:109–120`.
- Ranking ties originally use recency, but queue ties use key alone: `rank.py:135–138`; `queue.py:96`.
- Ordinary pending loading **does not reset expiry**. However, `INSERT OR REPLACE` resets `ranked_at` when the same key is stored again: `queue.py:44–53`.
- An executed same-key storage check at hour 23 kept that candidate loadable at hour 25. **Hypothesis:** routine URL dedup makes this uncommon in ordinary collection; the storage API nevertheless lacks an immutable first-admission bound.
- Fresh same-key replacement only changes the selection pool. If selected, it does not refresh the persisted JSON before a failed send.
- Persisted Event JSON omits `confidence` and cluster provenance. It preserves most rendering fields, but “full fidelity” is not literally complete: `src/agent/memory/event_json.py:23–41`; `event_models.py:69`.

**Required correction:** Preserve immutable admission/expiry timestamps and source-time/provenance fields; revalidate the combined pool against current delivered history and against itself; refresh legitimate versions without extending the original candidate’s lifetime accidentally.

Frozen recency is a policy choice, not automatically a bug. It must be compared against decay, rather than described as current importance.

### M2 — MAJOR: floor 15 does not provide the claimed economic protection

**Fact:** Production corroboration counts only tier-1/2 groups. Tier-3-only clusters have zero independent corroborating groups.

Evidence: `src/agent/pipeline/independence.py:39–48`; `validate.py:76–79`; `rank.py:105–120`; `config/settings.yaml:632–640`.

Executed production-function arithmetic:

| Candidate | Age | Score |
|---|---:|---:|
| One tier-2 economic source/group | 0 minutes | 15.000 |
| Same | 1 minute | 14.996 |
| Same | 60 minutes | 14.750 |
| One tier-3-only economic source | 0 minutes | 11.000 |

**Reproducible scenario:** Construct one economic event with `significance=economy`, one tier-2 member, and a publication timestamp one minute before the run. Classify it, then call `score_event` and `rank_events` using production settings. It falls below 15.

Thus:

- “Fresh tier-2 economics reaches 15” is true only at essentially zero age for the single-member case.
- “Tier-3 economics reaches 13” incorrectly grants it independent-group credit.
- Normal ingestion delay makes this a practical policy issue, especially with observed multi-hour gaps.

Additional gates precede the floor: source/topic filtering, cluster cap, understanding/contract acceptance, lead separation, repeats, language, and `significance=none`. Lowering or preserving a score floor cannot recover candidates lost earlier.

**Hypothesis:** Floor 15 disproportionately excludes single-source economic/context reporting while high-corroboration military follow-ups continue to dominate. This needs chronological, human-labelled replay; the arithmetic alone does not establish population-level impact.

### M3 — MAJOR: synthesis has a formatting contract, not a grounding contract

**Fact:** Synthesis receives headline, category, significance, summary, and `why_matters`. It does **not** receive `prior_context`, structured claim status, source attribution, or observation times.

Evidence: `src/agent/pipeline/synthesis.py:20–26`.

The prompt requests a delta or connection while forbidding inference. Its examples contradict that boundary: the first turns a warning into an additional response front; the second asserts a comparison with previous weeks absent from its illustrative inputs.

Evidence: `config/prompts/synthesis.txt:13–24`, `:32–37`.

**Executed reproduction:** A stub router returned valid Persian asserting an unsupported oil price of 999 dollars, while input events contained no price. `build_synthesis` accepted it.

Validation checks JSON shape through `extract_json`, then word count and script/language markers. None proves factual support: `src/agent/pipeline/synthesis.py:55–71`.

Other limitations:

- Selected-only material can support a summary of **these developments**, not necessarily “today’s picture.”
- Synthesis runs before final budgeting, so it can refer to a subsequently excluded item: `compose.py:167–188`, `:207`.
- The escape hatch explicitly produces a generic “no common thread” sentence instead of omitting low-value synthesis: `config/prompts/synthesis.txt:25–27`, `:43–44`.
- No production caller reserves synthesis capacity. It spends from the remaining shared budget; one logical completion can entail several provider attempts: `synthesis.py:49`; `llm/failover.py:76`, `:95`, `:121`.
- Normal unavailable/parse/length/language failures omit the line. That is useful graceful degradation, but not universal exception containment.

**Required correction:** Replace the “insightful sentence” contract with a bounded, attributable comparison contract described below. Prompt instructions alone are insufficient evidence of grounding.

### M4 — MAJOR: flash matching confuses weapons/actors with local incidents

**Fact:** Matcher logic combines a weapon/action substring and location token within the lead window. It does not require an asserted incident or a grammatical relationship between action and location. Exclusions scan the entire body.

Evidence: `src/agent/flash/matcher.py:31–35`, `:124–146`; `config/flash_alert.yaml:62–83`, `:88–95`.

The five local `match` rows, each at CSV line 2, are:

| Artifact run | Text interpretation |
|---|---|
| `39168847546` | IRGC spokesperson rhetoric and missile imagery; `پاسداران` mistaken for a district |
| `39694796501` | Reported air-defense activity in western Tehran; potentially relevant, unresolved |
| `40104408729` | Carrier rearming and possible renewed strikes; “Tehran” used politically |
| `40136922460` | Intelligence discussion of mobile launchers; `پاسداران` again names the organization |
| `40181257061` | Air-defense equipment moved near Tehran **last week** |

These are under `flash/<run>/flash_<timestamp>.csv`.

**Executed reproductions:**

- “Iranian Air Defense Moved Near Tehran … moved near Tehran last week” matches `attack_air`.
- Persian text meaning “The IRGC published a missile picture” matches `attack_air`.
- “Explosion reported in Tehran. Officials denied an unrelated report yesterday.” is excluded wholesale.
- “Air defense activity reportedly seen in western Tehran” matches.

Consequently, **four matches appear non-incident/posture/rhetoric**. Calling all five false positives would overstate the evidence. Calling them verified attacks would also be wrong.

**Required correction:** Introduce scoped incident assertion, actor/location disambiguation, and scoped negation/history handling. Do not merely add global exclusions such as “video,” “possible,” or “reported”: those can suppress genuine early reports.

### M5 — MAJOR: queue delivery becomes partly invisible to the audit

**Fact:** Summary reporting filters `ctx.events` by composed keys, but pending candidates are not inserted into `ctx.events`. Chosen reporting iterates current clusters. Fresh deferred candidates fall through to `event_unresolved`; “sent” is decided from composition keys, not successful delivery.

Evidence:

- `src/agent/report_csv.py:77–85`, `:129–142`
- `src/agent/report_csv_chosen.py:29–46`, `:77–85`
- `src/agent/pipeline/compose.py:154`, `:226`

**Reproducible scenario:** Load a pending candidate absent from current events/clusters, select and successfully send it. The run can count its composed key while its summary/provenance is missing from detailed CSVs. Conversely, fail delivery and current composed items still carry `sent` fates.

**Impact:** Post-change follow-up ratios, queue coverage, and provider-to-delivered-quality comparisons become misleading.

**Required correction:** Report lifecycle transitions explicitly: admitted, deferred, selected, rendered-full, rendered-headline-only, acknowledged, failed/unknown, superseded, expired, and rejected on revalidation.

## Shipped changes: keep / modify / remove

| Change | Decision | Reason |
|---|---|---|
| Maximum five selected items | **Keep** | Fixed owner constraint; never a quota |
| Rolling 24-hour eligibility | **Keep intent; modify implementation** | Current starvation and retention failures violate it |
| Separate pending table and additive v4 upgrade | **Keep** | Appropriate for encrypted SQLite/Actions; migration tests passed |
| Event JSON persistence | **Modify** | Preserve provenance, scoring inputs, immutable lifetime, and relevant version/context data |
| Static floor 15 | **Provisional; modify justification** | No comparative replay; economic protection claim fails under ingestion delay |
| Stored admission score | **Modify/evaluate** | Separate stable importance from recency; do not recompute without preserved inputs |
| Optional synthesis call | **Keep optionality; replace output contract** | Grounding and incremental reader value unproven |
| Numbering | **Optional cosmetic detail** | It does not solve repetition or comprehension; HIGH follow-ups are currently unnumbered |
| Prior-summary/current-summary concatenation | **Modify** | Provides memory assistance, not a coherent whole-thread briefing |

The follow-up implementation is not the brief’s bounded tuple of prior summaries. It uses **one best-matching prior summary string**: `src/agent/pipeline/repeats.py:130–135`.

MID follow-ups append prior context after the current summary and explanation. HIGH follow-ups use prior-head plus current summary, drop ordinary confidence/meta labels, and move below all normal entries regardless of score: `src/agent/pipeline/render.py:140–144`, `:174–201`.

That ordering can be reasonable for repetitive corroboration updates, but it is not appropriate merely because similarity is high. A decisive correction or casualty change can be more important than a new low-impact story.

The formatter also tries headline-only rendering before moving an item onward. Such an item counts as rendered even though the substantive update disappeared: `src/agent/delivery/budget.py:73–98`. The detail block is entirely italicized: `formatter.py:46–52`.

**Smallest substantive redesign:** Each selected thread should convey a short stable context clause, the actual new development, and necessary attribution/uncertainty. Avoid repeating the headline in the summary or appending an entire old summary. Treat prior/current contradictions as corrections, not additive prose.

Two feasible Telegram layouts follow. **Both are schematic templates, not invented news reports.**

**Layout A — compact thread cards**

> **[تیترِ تغییر اصلی]**  
> زمینه: [یک عبارت کوتاه برای فهم موضوع]. تازه: [تغییر مشخص و منتسب به منبع].  
> [پیامد مستند، فقط اگر لازم است]  
> [وضعیت تأیید] · [زمان گزارش] · [یک پیوند منبع]

**Layout B — briefing first, evidence underneath**

> **در این نوبت چه تغییر کرد؟**  
> [یک مقایسه کوتاه و مستند؛ در نبود ارزش افزوده حذف شود]
>
> **[موضوع اول] — [تغییر اصلی]**  
> [زمینه و تازه‌ترین تحول در دو جمله کوتاه]
>
> **[موضوع دوم] — [تغییر اصلی]**  
> [زمینه و تازه‌ترین تحول]

Use native bold, plain body text, whitespace, restrained symbols, and sparse links. No custom-font promise. A source link requires retained provenance; current digest items do not supply URLs.

## Next work: prioritized plan

| Priority | Bounded increment | Reader value | Correctness risk addressed | Complexity | Measurable payoff |
|---|---|---|---|---|---|
| P0 | Queue lifecycle, per-message acknowledgments, lifecycle CSV | High | Blocking loss/starvation | Medium | No silent candidate loss; accurate delivery ledger |
| P1 | Flash incident/locale policy and dispatch diagnostics | High | False alarms and blind intervals | Medium | Held-out precision/recall; measured scan gaps |
| P2 | Chronological selection replay | High | Economic exclusion, recycled coverage | Medium | Human-rated novelty/coverage per reading minute |
| P3 | Compact thread rendering and attributable synthesis | High | Repetition, unsupported narrative | Medium | Better comprehension at lower reading burden |
| P4 | Provider/prefilter efficiency | Medium | Waste and incomplete coverage | Low–medium initially | Valid useful candidates per attempt and minute |

### Selection: compare small alternatives

**Fixed:** max five; rolling 24 hours; no forced filling; unchanged risk-scoring weights.

**Negotiable:** digest admission floor, selection decay, same-thread limits, diversity, and follow-up eligibility.

Compare these separately before combining:

1. **Calibrated static floor:** simple, auditable; test realistic source ages rather than zero-age examples.
2. **Admission eligibility plus selection-time decay:** preserve admitted candidates while allowing newer developments to rise. Persist original source timestamps and score components.
3. **Same-thread consolidation/limits:** allocate scarce slots to materially different developments. Do not treat cosine similarity as proof of thread identity.
4. **Soft diversity:** compare economic, diplomatic, and security coverage when useful candidates exist. Reject mandatory category quotas.
5. **Material-delta eligibility:** distinguish new consequences, changed conditions, corrections, and confidence upgrades from retelling.

The 85% follow-up ratio is not an 85% repetition rate: most were MID-band matches. The current MID gate admits by score without a development test, while HIGH novelty can be satisfied by any new number or entity: `repeat_decision.py:50–65`, `:99–119`. Both need human adjudication. Dates and incidental entities can be false novelty; meaningful qualitative reversals can lack either.

### Synthesis: an evaluable replacement contract

Retain at most one optional logical completion, with an explicit attempt/time allowance.

Supply structured selected-thread records containing:

- stable thread/event IDs;
- current facts and claim status;
- dated prior context where relevant;
- source attribution;
- explicit material delta;
- evidence identifiers.

Request:

```text
text: short Persian synthesis, or empty
supporting_event_ids: [...]
relation: change | contrast | shared_consequence | none
```

Rules:

- Empty output is preferred to generic filler.
- No new numbers, entities, dates, or historical comparisons without support.
- Preserve uncertainty and attribution.
- A comparison requires both sides in supplied evidence.
- Validate evidence IDs and literal quantities; use human entailment evaluation for claims that deterministic checks cannot establish.
- Generate from the final substantive render set, or validate/rebuild after trimming.
- Describe the selected developments, not comprehensive global conditions.

Only later compare a tightly bounded background input against selected-only synthesis. Broader context might improve interpretation, but also increases narrative and grounding risk. Small-model capability on this contract is **unknown** until evaluated.

### Flash and Worker: diagnose before increasing frequency

Candidate policy should distinguish asserted explosions, impacts, launches, interceptions, and observed air-defense engagement from equipment mentions, movement, readiness, conditional threats, historical descriptions, and predictions.

Use clause/sentence scope and explicit place roles. `سپاه پاسداران` is an organization, not the Tehran district; “Washington and Tehran” may describe governments. Keep a route for hedged but genuine eyewitness reports.

Do not require multiple sources for every first alert without measuring lost early-warning recall. Current policy intentionally permits a first single-source alert and counts distinct source IDs as amplification, not independent corroboration: `src/agent/flash/policy.py:14–24`, `:58–60`, `:80–95`.

Current 120-minute freshness versus observed median 3.309-hour scan gaps creates opportunities for missed incidents. Expanding freshness would produce older alerts, not restore timeliness.

**Worker facts:**

- Code dispatches `flash-alert.yml` on `main`, and workflow dispatch is enabled.
- No deployment or cron activation state was observed.
- Missing `workflow_dispatch` in the sample does not identify the cause.
- **MINOR diagnostic bug:** `/run` tests `r.startsWith("204")`, but successful `dispatch()` returns a timestamp-prefixed string. A successful dispatch can therefore yield HTTP 502 from `/run`: `deploy/flash-dispatcher-worker.js:34–36`, `:62–67`.
- `/health` represents the latest stored dispatch response, not completed workflow execution. Missing-token and thrown-request paths do not provide equivalent heartbeat records: `:30–32`, `:45–65`.
- Public `/run` is not truly idempotent merely because workflow concurrency exists: repeated requests can create repeated runs. Remove that public mutation endpoint or restrict it during any Worker revision.

Measure cron invocation → dispatch response → Actions creation/start/completion → successful scan → state persistence. Repair diagnostics before attributing failure to deployment or raising frequency.

### Providers and prefilter

**Fact:** The audit shows successful failover and incomplete coverage simultaneously. `llm_failed=0` in all 28 runs means at least one LLM call succeeded, not that all candidates were processed: `src/agent/pipeline/understand.py:116–120`.

Recorded sent candidates came from Groq 345, Groq2 212, Mistral 196, Gemini 3. All nine `oversized` rows were attributed to Mistral; it also had four language drops despite zero recorded failed attempts.

Therefore reject both “provider failures only waste latency” and “Mistral is reliable enough to replace the cascade.” Transport/adapter success is not content validity.

Low-risk next steps:

- Separate HTTP/timeout/schema errors from missing batch elements, contract rejection, language drift, and human grounding failures.
- Record attempts and latency per logical request and resulting useful candidate.
- Inspect whether existing cooldown/demotion behavior matches its intent before adding another retry policy.
- Compare bounded Gemini probes with existing re-admission behavior; do not remove it solely from the aggregate failure rate.
- Calibrate the existing disabled prefilter using local scored rows and human labels. Do not treat the model’s `irrelevant` labels as unquestionable truth.
- Preserve source/provider coverage and fallback diversity. No wholesale pruning.

## Astra's independent ideas

1. **Make “what changed for the reader?” the selection unit.**  
   A development can merit a slot because it changes an earlier understanding, even with low volume. Five distinct material changes are more useful than five high-scoring clusters. Begin with annotated replay, not a new thread-resolution framework.

2. **Separate admission, selection, and delivery utility.**  
   One score currently carries too many responsibilities. Admission asks whether something deserves consideration; selection asks what adds most understanding now; delivery accounting asks what actually reached the reader. Keep these explicit without touching risk weights.

3. **Protect correction visibility.**  
   A correction can be less corroborated or less lexically novel than the original claim. Add a replay category for retractions and reversals so the repeat gate cannot silently preserve an obsolete picture.

4. **Measure comprehension, not engagement alone.**  
   Ask the reader to identify the main change, economic implication, and unresolved uncertainty after reading competing outputs. Track reading time and factual answers. Fewer clicks can be success for this product.

5. **Treat missing coverage as uncertainty about the feed.**  
   A quiet run after cap exhaustion or failed understanding is not evidence of a quiet world. Keep an unobtrusive coverage indicator distinct from substantive geopolitical interpretation.

6. **Give every expiry an auditable reason.**  
   Expiry after 24 hours is an intended terminal state, not silent loss. Inspect which important economic or contextual candidates repeatedly expire, and why they never earned a slot.

**Do not build:** a VPS/daemon, vector database, agent framework, local embedding installation, paid-provider dependency, MTProto integration, broad conversational layer, or dashboard before the core briefing works.

**Reject for now:** tighter admission without replay; percentile admission; mandatory five-item filling; mandatory economic quotas; blanket bans on follow-ups; global flash exclusion expansion; higher alert frequency before policy/telemetry repair; compulsory synthesis on every run; an unbounded LLM rewrite of the digest.

## Validation gates

### 1. Queue correctness — locally demonstrable offline

Use actual production functions and SQLite, not a separate reimplementation of the selector.

Chronologically execute admission, validation, selection, rendering, mocked sending, acknowledgment, and restart:

- Six or more eligible candidates; deliver five; select the remainder on a zero-fresh run.
- Repeat with all current candidates below floor, language-dropped, or unavailable.
- Fail every send, then succeed within 24 hours.
- Succeed page one and fail page two; retry only unacknowledged candidates.
- Force full exclusion and headline-only rendering; distinguish their outcomes.
- Reopen SQLite between runs.
- Test expiry boundary explicitly; same-key updates must not extend immutable expiry inadvertently.
- Deliver a semantically equivalent different key between deferral and selection.
- Combine fresh/pending equivalents; preserve distinct material developments.
- Assert at most five selected items and allow zero.
- Assert every admitted candidate is accounted for as active, acknowledged, explicitly superseded/rejected, or expired.

“No candidate loss” means no unaccounted disappearance before expiry—not a promise to send every eligible candidate.

Test post-send/pre-state-save failures separately. Define recovery for uncertain Telegram outcomes rather than claiming exactly-once delivery.

### 2. Chronological policy replay — partially possible now

Freeze admission times, publication times, candidate identities, historical state, and send outcomes. Replay by actual run timestamps with production classification, ranking, queue, repeat, and formatting functions.

Compare the baseline against one policy change at a time. Use held-out days and separate development days.

Human labels should cover:

- relevance to this Iranian reader;
- material novelty;
- same-thread relationships;
- economic/context coverage;
- corrections and uncertainty;
- redundant reading burden.

Acceptance requires max-five/24-hour invariants, no unaccounted candidate loss, and reader-value improvement without unacceptable coverage loss. Pre-register comparison criteria before viewing held-out results.

**Current limitation:** CSVs contain truncated article bodies, incomplete rejected-event information, and insufficient historical state/vectors for a faithful full replay. Existing artifacts support screening and annotation, not a demonstrated complete production replay. Use future CI-captured replay bundles or already-available compatible evidence; do not install embeddings locally for this consultation.

### 3. Synthesis and rendering — offline fixtures plus later model evidence

Locally test malformed output, unsupported numbers/entities, unsupported prior-period comparisons, uncertainty loss, contradictory sources, unrelated events, absent context, budget exclusion, and exhausted call budget.

Require:

- zero unsupported claims in the adjudicated acceptance set;
- graceful omission on invalid or low-value output;
- valid evidence references;
- no synthesis referring to excluded material;
- readable self-contained updates without duplicated old summaries;
- UTF-16/HTML budget compliance.

Human blind comparison must show comprehension gain over the no-synthesis baseline. Mock tests cannot establish real model capability. Later authorized CI/model runs are necessary.

### 4. Flash precision/recall — offline labelled corpus, then live telemetry

Hold out genuine positives and hard negatives in Persian, English, and Arabic. Include:

- direct explosions and impacts;
- hedged eyewitness reports;
- air-defense engagement versus equipment movement;
- conditional threats;
- historical recaps and “last week” reports;
- scoped and unrelated denials;
- IRGC/Pasdaran ambiguity;
- district names and political uses of Tehran;
- multiple incidents within one burst window.

Report precision and recall with denominators, by case type. Adjudicate the western-Tehran activity separately. Five matches cannot establish recall.

Acceptance should eliminate the known rhetoric/movement cases while preserving held-out true incidents; unresolved trade-offs require owner judgement before rollout.

### 5. Scheduling and provider gates — later CI/live evidence required

Worker unit fixtures can verify status handling offline. Actual deployment, cron activation, authorization, dispatch acceptance, execution delay, scan success, and state persistence require later authorized live evidence.

Likewise, provider efficiency needs error-class and latency telemetry joined to semantic acceptance and delivered usefulness. Require coverage/failover comparison before changing order or removing a provider/source.

## Uncertainties

- No full-suite rerun, post-Session-25 live digest, or end-to-end chronological replay was demonstrated.
- Actual Worker deployment, trigger activation, token validity, and dispatch root cause remain unknown.
- Flash CSVs do not prove send outcomes; their 200-character snippets may omit decisive context.
- Provider counters do not identify Gemini’s failure causes or quantify recovered versus permanently lost useful content.
- Aggregate artifacts cannot establish whether particular stories are true or whether every labelled follow-up is redundant.
- Live frequency of same-key renewal and stale-pending duplication is unmeasured; reproducible code paths establish the missing safeguards.
- Actual comprehension gains from synthesis, diversity, and revised thread prose remain hypotheses.

**Recommendation:** keep the product direction, modify the implementation. First complete a bounded queue-retention/acknowledgment/telemetry increment. Then fix flash incident discrimination and dispatch diagnostics, and run chronological selection comparisons before changing admission further. Evaluate compact thread prose and attributable synthesis against a no-synthesis baseline. The current revision reduces output volume, but it does not yet establish reliable 24-hour selection or improved situational understanding.