# Session 9s Brief — Batched cluster extraction

SESSION 9s — Cut LLM calls ~5x by sending N clusters per call. One architectural change,
one behaviour-neutral instrument, one coupled presentation fix. Nothing else.

Gate: `PYTHONPATH=src python3 tools/pytest_shim.py tests` → state your predicted count before
running and reconcile it exactly. Baseline is **769**. 9q shipped +7 against 5 claimed and that
was recorded as a defect; do not repeat it.

## Why this session exists — the measured cause

Read `analysis/RUN_REVIEW_20260908_2256Z.md`, then this. The 2026-09-08 22:56Z Actions log
(`outputs/output-log.txt`, 70 attempts) classifies every failure:

| provider | ok | fail | class |
|---|---|---|---|
| groq | 25 | 35 | `status_429`, all 35 |
| gemini | 1 | 3 | `status_503`, all 3 |
| bai | 2 | 4 | `transport_error`, all 4 |

Groq attempts per minute from run start:

    min:  4  5  6  7  8  9 10 11 12 13 14 15 16 17 18 19
    ok :  7  5  7  2  1  0  0  0  0  0  0  0  0  0  1  0
    429:  1  2  2  1  2  3  3  2  3  2  2  3  2  3  2  2

Console limits for `qwen/qwen3.8-27b`, verified 2026-09-09 (`outputs/groqlimits.txt`):
**RPM 30, RPD 1K, TPM 8K, TPD 200K.** The old CLAUDE.md line "30 RPM, 14,400 RPD" was wrong —
14.4K RPD belongs to the `llama-prompt-guard-2-*` models — and it recorded no TPM at all.

**TPM 8,000 is the binder, and nothing else is close.** One extraction call is ~3,450 tokens
(~3,050 in + ~400 out) → **2.3 calls/minute sustainable, not 30.** The run fired 8–9 req/min
≈ 30K tokens/min into an 8K window. Minutes 9–19 were 100% failure with zero recovery, which means
**rejected requests still consume the token window** — aggressive retry is self-sustaining lockout.

Two candidate explanations were tested and one was killed: a *daily* ceiling predicts later runs
do worse, but the 18:03Z run got 9 of 62 groq calls through and the **later** 22:56Z run got 25 of
60. Later-is-better disproves TPD/RPD. It is TPM.

**Therefore `RpmPacer` is not "working" — it is measuring the wrong quantity.** It paces requests;
the limit is tokens. This is why token-aware pacing is in scope below and not deferred: batching
alone fires 8 calls in about a minute, which is ~46K tokens into an 8K window, and fails exactly
the same way.

Two corrections to the earlier review, now evidence-backed:
- Gemini was **not** RPD-exhausted. It made 4 attempts, took three 503s, and its breaker opened at
  2 strikes. Its 20 RPD was never reached. (Fix deferred to 9t — the standing 503-cooldown item.)
- `CallBudget.acquire()` fires before the request is built (`limits.py:46`), so all 42 failures
  charged the 70-call budget. Real, but moot once calls drop 5x — an 8-call run cannot reach 70.
  **Do not fix budget accounting in this session.**

**The problem is demand, not reliability.** 40 clusters x 6 runs = ~240 calls/day against a supply
of roughly 25 (groq) + 20 (gemini) + unknown (bai).

## Honest arithmetic — do not oversell this change

Per-call input is ~3,050 tokens, of which the prompt template is the bulk and cluster content is
~700. Batching shares the boilerplate:

| | calls/run | tokens/call | tokens/run | min. minutes at 8K TPM | verdict |
|---|---|---|---|---|---|
| now (1 cluster/call) | 40 | ~3,450 | ~138K | **17** | fails; run is ~8 min |
| batch 5 | 8 | ~5,800 | ~46K | **6** | fits |
| batch 7 | 6 | ~7,600 | ~46K | 6 | fits, no headroom |
| batch 10 | 4 | ~9,300 | ~37K | — | **impossible** |

Two hard results from the TPM ceiling:

1. **Batching works, and the reason is token count, not call count.** It cuts tokens/run from
   ~138K to ~46K because the ~2,300-token prompt template stops being paid 40 times. Six minutes
   of TPM budget fits inside a run; seventeen does not.
2. **Batch size has a hard ceiling around 7.** A single request above 8,000 tokens can never
   succeed on groq — it exceeds the per-minute allowance by itself, so it 429s forever regardless
   of pacing. `batch 10` is not a slower option, it is a permanently broken one. Enforce this in
   config validation, not in a comment.

Gemini's TPM is 250K and bai's is unrecorded, so this ceiling is groq-specific. Batch sizing must
therefore be **per-provider**, bounded by the smallest TPM in the cascade.

The remaining levers if this proves insufficient — fewer clusters per run, or full summarization
only on the canonical 09:00 digest run — are **explicitly out of scope here** and get decided on
9s's measured results.

## FILES

    config/prompts/understand.txt          — JSON contract becomes an ARRAY over clusters.
    src/agent/pipeline/understand.py       — batch assembly + per-item result mapping.
                                              Already 217 lines and over the cap; split the
                                              batching into pipeline/batch.py rather than grow it.
    src/agent/pipeline/batch.py            — new: chunk clusters, build the batch payload,
                                              map results back by cluster key, isolate failures.
    src/agent/pipeline/samerun_dedup.py    — emit a pair record per comparison. Drop logic UNCHANGED.
    src/agent/report/pairs_csv.py          — new writer: pairs_<ts>.csv.
    src/agent/pipeline/render.py           — replace the raw-headline fallback.
    src/agent/llm/limits.py                — new TokenPacer alongside RpmPacer. Watch the line cap.
    src/agent/llm/failover.py              — charge the token pacer on EVERY attempt, incl. failures.
    config/settings.yaml                   — batch_size, per-provider tpm, pair log floor.
                                              NO threshold edits.
    src/agent/config/settings_schema.py    — declare the new keys + the batch-vs-tpm guard.

## SIGNATURES

    # batch.py
    def chunk(clusters: Sequence[Cluster], size: int) -> list[list[Cluster]]
    def build_payload(batch: Sequence[Cluster]) -> str          # numbered, key-tagged
    def map_results(batch: Sequence[Cluster], parsed: object) -> dict[str, EventDraft | None]
    #   Returns one entry PER CLUSTER IN THE BATCH. A cluster the model omitted maps to None.

    # limits.py
    class TokenPacer:
        def wait(self, provider: str, tpm: int | None, estimated_tokens: int) -> None
        def charge(self, provider: str, tokens: int) -> None   # called on EVERY attempt

    # samerun_dedup.py
    def collapse(events, *, embedder, threshold, log_floor) -> tuple[list[Event], list[PairRecord]]
    #   decision in {"dropped_b", "dropped_a", "kept_below_threshold"}
    #   Records EVERY pair with similarity >= log_floor, dropped or not.

    # pairs_csv.py
    def write_pairs(path: Path, rows: Sequence[PairRecord]) -> None
    # Fields — a new fate MUST carry its text (Masafer Yatta lesson, 9p item 5):
    #   run_at_utc, key_a, key_b, similarity, decision, threshold,
    #   n_members_a, n_members_b, score_a, score_b, headline_a, headline_b

## Requirements

### 1. Partial failure must not void the batch — this is THE risk of this change
One malformed object in a 5-cluster response currently costs you 5 summaries instead of 1. That
turns a 5x efficiency win into a 5x blast radius. Non-negotiable behaviour:
- Parse per-element. A cluster whose element is missing, malformed, or fails the existing
  clickbait/irrelevance checks is marked `unavailable` **individually**; its batch-mates ship.
- Never re-map by position alone. Every request element carries its cluster key and every
  response element must echo it. Position-only mapping silently attaches summary A to cluster B,
  which is a fabrication path and violates constraint 11.
- A key in the response that was not in the request is dropped and logged, never trusted.
- Test the four cases explicitly: all-good, one-malformed, one-missing, echoed-key-mismatch.

### 2. `observability.samerun_pair_log_floor`, default 0.40
Not a tuning variable — it controls only row count. Say so in the `settings.yaml` comment so a
future session cannot mistake it for a threshold. ~40 events is ~780 pairs; expect 50–150 rows at
0.40. Above ~400 rows, raise the floor, never the file size.

### 3. `llm.batch_size`, default 5, and a per-provider `tpm` with enforced validation
Owner-editable, with the arithmetic table above sitting next to it in `settings.yaml`. `1` must
remain a valid value and must reproduce today's one-call-per-cluster behaviour exactly — that is
the rollback path and it needs a test.

Add `tpm:` to each provider block (groq **8000**, gemini 250000, bai unrecorded → treat a missing
value as unconstrained but log it once). `settings.py` cross-section validation must **refuse to
load** a config whose estimated batch request size exceeds the smallest `tpm` in `llm.order`.
This is the same class of guard as 9q's `event_repeat_threshold < event_match_threshold` check.
A batch that cannot fit in one minute's allowance is not slow — it is permanently impossible, and
a config that silently allows it recreates this defect with no error message.

### 3b. Token-aware pacing — `TokenPacer` in `limits.py`
`RpmPacer` stays (RPM is still a real ceiling) but a token pacer runs alongside it and, on groq,
will always be the one that binds. Requirements:
- Sliding 60-second window of tokens **charged on every attempt, including failures.** The log
  proves rejected requests consume the window; a pacer that only counts successes will reproduce
  the eleven-minute lockout exactly.
- Estimate a request's token cost **before** sending it (template length + payload length, a
  cheap character-based estimate is fine and must be documented as an estimate). If the estimate
  does not fit the remaining window, sleep until it does rather than sending and hoping.
- Clock and sleep injected, as everywhere else in this package. No real sleeping in tests.
- On a 429 despite pacing, the existing 9o `CooldownRegister` handles it. Do not change the
  30s cooldown value in this session, and keep 429s off the circuit breaker — that standing
  decision is not up for revision here.

### 4. The drop decision must stay byte-identical to 9r
`collapse` still deletes on `digest_rank.event_repeat_threshold` (0.80) and still breaks the inner
loop when `event` itself is the loser (the 9r non-transitivity fix). Logging observes; it must not
reorder, re-embed, or re-compare. Pin with a test asserting the surviving set is invariant to
`log_floor`.

### 5. Replace the raw-headline fallback
`⚠️ خلاصه خودکار در دسترس نیست — عناوین خام منابع:` dumped untranslated English and Arabic
headlines into both Persian messages. Replace with an honest Persian one-liner carrying the count
only (e.g. «⚠️ ۱۳ خبر بدون خلاصه ماند»). This ships here rather than in 9t because batching
changes the shape of failure — one batch loss now voids several clusters at once, making this text
more prominent, not less. Constraint 11 is satisfied: a count is a fact.

### 6. Prompt edits go in the file, never in Python
`config/prompts/understand.txt` only. The JSON contract becomes an array; keep the per-cluster
field set identical so `event_models.py` needs no change. Do not add the persona / «چرا مهم است»
field — that is an open owner decision, still unapproved.

### 7. Budget interaction
`compose` must still be able to `reserve(1)` so the final message is never starved. Verify the
reservation still holds when the run makes 8 calls instead of 40.

### 8. Mock mode
Batching must be exercisable with the recording transport mock and `FakeEmbedder`. The sandbox has
no PyPI and no `sentence-transformers`; no test may require real MiniLM.

## MOST LIKELY CONSTRAINT VIOLATION IN THIS SESSION

**Constraint 11 (never invent content), via position-based result mapping.** The convenient
implementation zips request order against response order. The moment a model omits or reorders one
element, cluster B receives cluster A's summary and the system publishes a confident, well-formed,
completely fabricated event. Key-echo validation is not optional.

Runner-up: constraint 12 — `understand.py` is already at 217 lines, over the cap. Batching goes in
a new file.

## OUT OF SCOPE — do not build

- Any change to `event_repeat_threshold` (0.80), `event_match_threshold` (0.55),
  `digest_rank.min_score` (8), `max_calls_per_run` (70), or any weight in `risk_weights.yaml` /
  `credibility.yaml`.
- `CallBudget` 429 accounting, and the 503-cooldown / `ready_alt` fix. Both are real, both are
  9t, both are moot-to-minor once calls drop 5x.
- The «پیگیری» label condition (fires on 16 of 19 — real defect, 9t).
- The clusterer: no second merge pass, no entity/number same-story override. 9t, and its design
  depends on the `pairs_csv` measurement this session produces.
- Flash alerting: the `پاسداران` negative-context rule, the single-source gate. Separate subsystem.
- Reducing clusters/run or restricting summarization to the digest run. Decided after 9s runs.
- `tg_tsepress` — probed USE_GATED, held.

## INPUTS THE IMPLEMENTER MAY READ

`CLAUDE.md`, `analysis/RUN_REVIEW_20260908_2256Z.md`, `outputs/groq_limits.txt`,
`agents/briefs/PHASE_5_BRIEF.md` §Requirements 8–9 (logging rules),
`agents/briefs/PHASE_6_BRIEF.md` (the original understand-stage contract),
`config/prompts/understand.txt`, `src/agent/pipeline/understand.py`,
`src/agent/pipeline/samerun_dedup.py`.

## What the owner does after this ships

One live run, then read three things:

1. `run.csv` — `calls_groq` should be single digits, not 60, and `fails_groq` should be near zero.
   If 429s persist at 8 calls/run the token estimate is too low, not the batch size too high.
2. The Actions log — expect `outcome=ok` to dominate. Also expect the run to take **longer**:
   token pacing deliberately sleeps, and ~46K tokens at 8K TPM is a ~6-minute floor on groq
   alone. A run stretching from ~8 to ~12 minutes is the fix working, not a regression.
3. `pairs_csv` — the similarity of the tanker (n=48/12/9/3) and Azraq (n=59/18/23/4) pairs. If they
   cluster at 0.60–0.79 the 9t clusterer fix is a same-story test that is not a single cosine; if
   they sit below 0.55, MiniLM is failing on Persian paraphrase and no threshold anywhere helps.

Only then is 9t designed. Its queue, in order: the clusterer, the «پیگیری» label condition
(16 of 19), the 503-cooldown / `ready_alt` fix (gemini benched itself on 3 flakes), `CallBudget`
429 accounting, then flash (`پاسداران` negative context, single-source gate).
