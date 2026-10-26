# News Curator — Session 25 to VPS handoff

## Active handoff / resume point

Owner subsequently restored VPS access and explicitly chose **local development**. Active writer: `Hermes Projects/News Curator/session26-local`, branch `dev/session26-queue-local`, based on `72a568eda00d5eeff132b6abf52bbd6869b6cff3`. Runtime remains GitHub Actions; do NOT deploy a VPS news service.

The old VPS job made no changes: bubblewrap blocked execution despite wrapper exit0. VPS is clean at the same base, with **1034 tests passing** in a parent-run validation after recovery. No remote implementation job is running or was restarted; no remote work needs merging.

The bounded increment remains `agents/briefs/SESSION_26_VPS_BRIEF.md` (local paths/branch supersede its VPS-only narrative). **Locally accepted: 1059 tests passed** (1034 baseline + 25 new cases), focused **99 passed**, final fresh `deepseek-v4-pro` round 3 **SHIP 38/40, zero BLOCKING/MAJOR**. Parent verified all 11 source/test SHA256s unchanged and independently reran 19 reviewer assertions plus an 18-case report matrix. Publication/admission-clock and same-key snapshot/fate/provenance reporting regressions are fixed. Two non-blocking reporting nits and durability limits are recorded in `analysis/SESSION_26_PROGRESS.md`; full contract is `docs/SESSION_26_QUEUE_RELIABILITY.md`, evidence `.continuation/session26-local/`. The owner subsequently authorized commit/push; fresh prepublication tests again passed 1059/1059. Verify the remote commit and its GitHub CI before claiming publication complete. No manual production workflow/live send or ranking/flash/provider/presentation redesign is part of this release. Broader product work has not started. Keep a single local writer.

## Product requirements

- Persian intelligence for an Iranian reader concerned with war and economic consequences.
- LESS reading with MORE situational understanding; do not mistake decorations or fewer headlines for better understanding.
- Maximum 5 selected items per run, not a mandatory quota.
- Eligible undelivered candidates retained for 24h. A morning's sixth-place story may become afternoon's second-place story.
- Follow-ups must be understandable without reading prior messages. Prefer a short context clause plus actual new development, not two pasted summaries.
- Grounded holistic synthesis, only if it adds value. Never forced generic narrative or unsupported consequences.
- Native Telegram layout only; font selection is not controllable.

## Code and audit state

Reviewed and shipped code: `cfa11e9929547c55fbaea7214a56c04592b31e3d` plus handoff-only docs. Session 25 spans `27da60c` AND `cfa11e9`; base before the feature is `8337d3e`. Looking only at `cfa11e9^..cfa11e9` misses the initial queue/schema/synthesis work.

Shipped: pending table/schema v4, 24h queue, max5, `min_score: 15`, optional synthesis, prior-context rendering. Full suite independently rerun locally: **1034 passed in 40.45s**. Passing tests do not establish queue correctness or the optimum selection policy.

Independent consultation: Codex **gpt-6-astra** (model confirmed in CLI header), completed exit0. Full report: `analysis/SESSION_25_ASTRA_REVIEW.md`. Astra's recommendation: KEEP direction, MODIFY implementation. Its report is a review, not automatic final acceptance.

## Parent-verified findings and corrections

1. **Selected fresh candidates lack durable pending retention.** Real production queue probe: 8 admitted, 5 selected, 3 persisted; zero delivered markers. If no selected send succeeds, only the 3 losers are recoverable through the queue. Budget-excluded selected fresh items have the same gap. `pipeline/queue.py:97–102`; `compose.py:207–226`; `deliver.py:57–70`.
2. **Quiet runs skip pending.** Probe: 3 valid pending candidates, fresh empty, Compose emits 0 and 'nothing new'. This was earlier intentional policy; changing it now is an explicit correction to satisfy queue intent, not an accidental regression. `compose.py:110–154`.
3. **Queued winners disappear from detailed CSVs.** Probe: 3 pending + 1 fresh selected => 4 keys but only 1 summaries row. `report_csv.py:77–85`.
4. **Floor15's earlier economic-protection justification is wrong at realistic ages.** Real production classification/ranking/settings: single-member tier2 economics scores 15.000 at0min, 14.996 at1min, 14.750 at60min; the latter two fail admission. Tier3-only economics has 0 corroborating groups and scores11, not13. Do not choose a replacement threshold without replay. Keep floor15 unchanged in P0, then compare alternatives.
5. **Worker diagnostic bug reproduced offline.** A mocked successful GitHub dispatch204 returns `/run`502 because the handler tests a timestamp-prefixed string with `startsWith("204")`. Actual deployed Worker, cron and token state unverified. Public `/run` also permits unauthenticated repeated dispatch; concurrency is not idempotency. Deferred from P0.
6. Synthesis lacks structured claim status, source attribution, time and prior_context. Prompt's examples introduce unsupported comparisons. Format/language gates do not prove grounding. Later attributable synthesis should permit empty output and reference evidence IDs; compare against no-synthesis baseline.
7. Pending loads happen after fresh repeat validation, so current delivered-history semantic checks do not cover deferred candidates. Stored recency scores are frozen. These require a later bounded provenance/combined-pool revalidation increment, not casual threshold changes now.

## Seven-day audit (Oct18–25 pre-feature evidence)

28 pipeline artifact runs: 756 recorded composed/sent items, median27 max38; 645 follow-up labels (85.3%), NOT proof645 duplicate stories. Most were MID-band related-story matches. Provider attempt failures: Gemini40/49; Groq268/731; Groq2 101/450; Mistral0/409. Mistral still had contract/language losses; zero attempt failures is not zero quality failures.

1515 irrelevant; 749 cap_dropped;183 cap_refused. Cluster cap150 hit **19/28**, NOT every run (corrects earlier statement).

44 flash artifact runs: **5 keyword matches**, NOT5 proven sends. Four appear rhetoric/posture/history; western-Tehran air-defense activity remains unresolved. No incident recall estimate can be made from this sample. Flash tuning CSV writes matcher output regardless of send outcome.

Scheduling records: all sampled events=schedule. Pipeline creation gaps median4.743h/max12.620h; flash median3.309h/max7.345h. This does NOT prove the deployed Worker's root cause. Separate dispatch acceptance, run start, successful scan and saved state.

Last checked live Session25 run: `40197094693` on cfa11e9 was pending, not verified completed. Recheck by public GitHub REST before claiming live behavior.

## Evidence locations

Tracked in repo: Astra review, this handoff, Session26 brief, `analysis/session25-verification/` (actual parent probe results and audit aggregates).

Private VPS local audit bundle: `.continuation/session25/nc_review/` (256 artifact files, including CSVs) and `.continuation/session25/runs.json`. These are only news/artifact evidence, not credentials. No `gh_cred.txt`, .env, auth.json, SSH keys, API keys, Telegram chat IDs, decrypted state DBs, or Hermes session DB were transferred.

Historical files have truncated bodies/incomplete reconstructed state. They support screening and annotation, not a faithful full production replay by themselves. No full chronological queue replay or post-feature live digest acceptance has been completed.

## Priority sequence after P0

1. Queue lifecycle, acknowledgment mapping, honest reports (Session26 brief).
2. Flash asserted-incident/locality policy and Worker diagnostics; held-out real positives AND negatives, then authorized live verification.
3. Chronological selection comparisons: floor, current-recency decay, material-delta/same-thread consolidation, soft diversity. No mandatory category quotas; no blanket follow-up ban.
4. Compact self-contained thread prose and attributable synthesis; test comprehension/grounding versus no-synthesis.
5. Provider/prefilter efficiency; distinguish transport, contract and semantic loss; preserve coverage/fallback diversity.

## Safety / publishing

Read project constraints but do not edit CLAUDE.md/config/skills/memories/.ssh. No new runtime dependencies, paid services, vector DB/agent framework, MTProto, local embedding installation, or live Telegram messages. Do not touch CI state/flash-state branches. Never force-push. Pull --ff-only before commit/push, stop on divergence. Implementation does not commit/push until independent acceptance; laptop relays accepted code to GitHub/CI later.
