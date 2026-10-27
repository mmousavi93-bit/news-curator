# Session 27 — bounded news-quality and runtime release

Base: `29626f035efaf58a5227f8ff8f561f0c0fdffa0e`. Local branch: `dev/session27-quality-runtime`.
Fresh baseline: 1265 tests passed in 65.10s. Production stays GitHub Actions.

## Diagnosis and scope

Archived Actions logs and report CSVs, not estimated provider speed, establish:
- Run 40332907395: 118 clusters; extraction 13:45:34–14:34:18 UTC (~49 min). 43 total LLM attempts; summed HTTP latency 165041 ms. Groq served 27 successful attempts, groq2 six, Mistral one. The existing failover starts each logical call at the preferred provider and blocks in its 120-second token pacer, even while an equivalent overflow model is idle. Synthesis subsequently waited almost two more minutes.
- Scheduled 40348842883: 132 clusters, 70 attempts. Mistral had 47 transport-successful calls but this does NOT establish valid extraction. Do not make Mistral the proactive workhorse: changing the model mix can sacrifice coverage/quality.
- Current synthesis uses only a language/word gate; no cited event identity or evidence validation. Its prompt's examples assert unsupported multi-front and historical comparisons. It runs before character packing and can describe an excluded item. `why_matters` is generated background, not independent evidence.

This release fixes those demonstrated problems. Do not tune the ranking floor, source set, cluster cap, similarity thresholds, or introduce a new relevance-deleting gate on these two runs. They cannot calibrate those safely. Preserve top-five-as-maximum, full 24-hour admission queue, follow-up standalone context, claim labels, and real page acknowledgement semantics. The risk-extraction work on the base is unrelated and must remain untouched.

## Required implementation decisions

### A. Conservative capacity scheduling (no weaker-model substitution)

Keep the existing cascade and all retry/cooldown/breaker/budget behavior. Before blocking on RPM/TPM pacing, a request may use the equivalent configured `groq`/`groq2` rung with the SAME exact model id when that rung will be ready sooner. Do not proactively select Mistral, Gemini, paid providers, or a different model. This is rate-capacity overflow, not model-quality substitution; the preferred provider still wins ties and ready ordinary calls. Do not parallelize HTTP.

Add side-effect-free readiness/delay queries to the RPM and token pacers. Token readiness must model the existing 120s window, 0.65 safety factor, purging, post-call corrections and oversized-single-request behavior EXACTLY; no token booking or sleeping in a probe. Account for all bookings that must expire, not just the oldest. Add a side-effect-free provider-spend capacity check so an exhausted daily/run/spend rung is never selected as an escape. Never consume provider/global/reserved slots for merely considering/skipping a rung. Actual attempts still call existing pacing/booking/acquisition and usage correction paths. Do not busy-loop when every provider is blocked. Preserve attempt caps and vision restrictions. Put selection helpers in a small module, not a new scheduler framework. Preserve imports if splitting RPM pacing from limits.py to meet the ~200-line rule.

### B. Evidence-bound, optional synthesis

Prompt contract: JSON `{ "synthesis": "...", "evidence": [{"event_id":"<exact key>","quote":"<exact current headline/summary excerpt>"}, ...] }`.

Input is a JSON array containing exact event keys, headline, summary, claim_status, and clearly separated previous_context (when available). Do NOT include why_matters as supporting evidence. Mark input as untrusted source data, not instructions. A single short Persian overview must connect at least TWO distinct selected, fully rendered events. No filler line when no supported connection: return empty synthesis/evidence. Remove unsupported trend/multi-front examples. No forecast, invented consequence, historical comparison without explicit prior evidence, or certainty upgrade. Keep existing Persian/word bounds.

Enforce in production: synthesis is a string; evidence is a bounded list; at least two distinct known keys; each nonempty quote is at least three words and an exact whitespace-normalized substring of THAT event's CURRENT headline or summary, not another event, prior_context or generated why_matters. Reject unknown ids, fabricated/mismatched quotes, malformed structure, and any numeral in the line absent from the CURRENT cited evidence text. Compare Unicode digit variants consistently without changing values or accepting numeric substrings (7 is not 70). This is a structural grounding guard, NOT a claim of semantic entailment proof. No extra verification LLM call. Optional failures omit only the line, never the digest. Preserve uncertainty conservatively: if any cited event is not `confirmed`, prefix the accepted line with a clear fixed Persian unverified-reports attribution. Do not silently relabel the source events.

Expose/reset the accepted evidence event keys on context for packing verification. HTML-escape the generated sentence before inserting it into the HTML header. Do not let markup/links from model output become executable formatting.

Build digest items and do a base pack FIRST. Only full-form events surviving that base pack may be sent to synthesis (not excluded/headline-only items). Pack again with the candidate synthesis. If adding it makes ANY originally full item lose full form or disappear, omit the optional line and use the original base pack; do NOT rerun the model. Thus overview prose cannot displace substantive news or cite a budget-excluded item. Keep exact page identity/forms/truncated-order bookkeeping from the chosen pack; no headline matching. Return maximum one logical synthesis call. Preserve no-change/coverage/degraded/pending-drain paths.

## Machine-checkable gates

1. Genuine chronological RED→GREEN tests per behavior; keep red/green logs under ignored `.continuation/session27/`. No production edits before the first corresponding failing test. Transport/clock/Telegram mocked, production router/composer/SQLite real.
2. Router regressions: same-model overflow avoids a 120s idle wait; ready preferred rung still wins; different model is NOT proactively selected; exhausted/cooling/open-breaker alternatives do not steal slots; both token windows occupied still pace safely; no additional quota acquisition; existing router/reservation tests green.
3. Quality regressions: valid cited Persian overview; unknown IDs/wrong quote/one-event evidence/new numeral/why_matters-only quote rejected; uncertainty attributed; empty/unavailable/malformed output safely omitted; malicious HTML escaped; overview cannot reference excluded or headline-only news; adding overview cannot evict full news; pending drain and existing queue/receipt regressions pass.
4. Parent supplies chronological archived-CSV replay and baseline-vs-new fake-clock benchmark; compare the REAL production selection/composition/router paths, not copied algorithms. Same candidate input/coverage, <=5 selected, all unselected admissions retained, no fake claims of measured live speedup.
5. Full suite, independent fresh-context read-only review: SHIP, zero BLOCKING/MAJOR. Parent verifies findings and exact changed hashes. Scoped credential scan and clean diff before publication. Exact-head CI must pass.

## Writer scope / constraints / handoff

Implementation writer may edit ONLY `src/agent/llm/{limits.py,token_pacer.py,failover.py}` plus small new `rpm_pacer.py` / `overflow.py`, `src/agent/pipeline/{synthesis.py,compose.py}` plus a small new synthesis contract/packing helper, `config/prompts/synthesis.txt`, and focused `tests/unit/test_*synthesis*.py`, `test_*overflow*.py`, `test_*readiness*.py`. Existing synthesis happy-path fixtures must be updated to the new explicit evidence contract, never weakened. Parent owns this brief, tools/benchmarks/replay fixtures, docs, review and release.

No edits to CLAUDE.md, config/settings.yaml, Hermes config, auth files, secrets, workflows, delivery acknowledgement implementation, risk code, dependencies or other worktrees. No network/API/Telegram access, no commits/pushes, no model installation, no fan-out. Test interpreter: `C:/Users/CafeBazaar/AppData/Local/Programs/Python/Python312/python.exe`, env `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src PYTHONIOENCODING=utf-8`; pytest `-q -p no:cacheprovider` with ignored basetemp. Report exact changed paths, RED/GREEN log paths, test counts, and any scope or correctness blocker. A worker report is not acceptance.

## Release boundary

No manual live digest dispatch and no messages. Read-only verification of already scheduled runs is permitted. Publication activates normal existing schedules; rollback is a revert of this bounded release, never a state reset. Production runtime speed and reader engagement are not established by offline replay or CI; report those limitations honestly.
