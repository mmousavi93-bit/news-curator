# Phase 6 Brief — Understand (embed → cluster → summarise)

> **STATUS: BUILT and gate-green 2026-08-29. Suite 415, 0 failed (predicted 409;
> reconciled: baseline bookkeeping was 367 not the recorded 363, +4; new files 48,
> exactly as predicted). All ten gate items pass. Details in POSTMORTEMS.md §Status
> (Phase 6) and CLAUDE.md §Phase 6 outcomes.**
>
> Deviations from this brief, both deliberate and recorded:
> - NumPy NOT added: the greedy clusterer over ~120 unseen items runs well under a
>   second in pure Python, and an unused dependency violates constraint 13 harder
>   than a missing one. Add it only when a profile says so.
> - `circuit_breaker_failures: 5 → 2` applied in settings.yaml (the brief's §6
>   justification stands; flag if the owner objects).
> - The understand loop BREAKS on `refused_cap` (budget gone, further calls would be
>   refused too); `unavailable` skips the cluster and keeps trying (transient).
> - CI: the `test` job deliberately does not install [embeddings]; a new
>   `embeddings` job caches the model and smokes the real MiniLM to 384 dims.

Architect brief, written 2026-08-29 after Phase 5 closed green (suite 361). An
Implementer runs from this file plus `CLAUDE.md` and `ARCHITECTURE.md`.

---

## Why this phase is sixth

Phase 6 is "the one phase not to cut" (ARCHITECTURE.md): it is where the pipe starts to
feel like less noise rather than more. Ten articles about one event become one event —
that is the whole product promise, and the gate for this phase is exactly that sentence.

It also lands two session-5 decisions that have been waiting since 2026-08-17:

1. **The cluster/call cap becomes enforced code.** `max_clusters_per_run: 40` exists in
   settings but nothing consumes it. Session-5 decision 1 found the real defect: a budget
   stated in prose and enforced by nothing, and a fault that only fires on the busiest
   news day is discovered unattended during exactly the event this system exists for.
   Phase 6 ranks clusters by priority and truncates deterministically at the cap.
2. **The topic gate becomes enforced code.** Six feeds are marked `TOPIC-GATE` in
   `sources.yaml` (aawsat, dw, reuters_gnews, ap_gnews, state_dept_travel, france24 —
   753 items, 37% of the corpus). They are general-interest feeds that flood the funnel.
   Phase 6 gates them by topic keyword — reversible reduction, not deletion.

---

## Deliverable

Ten articles about one event become one event. The pipeline stages `filter`, `embed`,
`cluster`, `understand` go from no-ops to real stages; `vision` stays a passthrough
(no collector extracts images in v1 — see Out of scope).

### Files (all under ~200 lines — split rather than exceed)

```
src/agent/pipeline/filter.py     topic gate for topic_gate: true sources
src/agent/pipeline/embed.py      Embedder protocol + MiniLmEmbedder + FakeEmbedder
src/agent/pipeline/cluster.py    greedy cosine clustering + priority ranking + cap
src/agent/pipeline/understand.py one router.complete() per cluster, JSON parse, filters
config/topics.yaml               per-language keyword lists (owner-editable)
config/prompts/understand.txt    summarisation prompt (Phase 6 creates config/prompts/)
config/prompts/vision.txt        vision prompt (used when images exist; inert in v1)
tests/unit/test_pipeline_*.py    offline, fake embedder, mock transport
```

Edits: `settings_schema.py` + `settings.yaml` + fixture (`pipeline.embed_model`),
`collectors/base.py` + `collectors/registry.py` + `sources.yaml` (`topic_gate` flag),
`memory/models.py` (event_to_row/row_to_event/insert_events), `pipeline/__init__.py`,
`run.py` (wire real stages), `pyproject.toml` (two justified deps).

---

## Requirements

### 1. Embeddings are local; sentence-transformers is a lazy, OPTIONAL dependency

`embed.Embedder` is a Protocol: `embed(texts) -> list[list[float]]`.

`MiniLmEmbedder` imports `sentence_transformers` **inside `__init__`**, never at module
level — the requests pattern (delivery/transport.py), now for a 2 GB dependency. The
owner's Windows pytest suite and `--dry-run`/`--collect-only` must run WITHOUT torch
installed. A missing package fails at construction with a `pip install` instruction,
never later out of `.embed()`.

Model: `paraphrase-multilingual-MiniLM-L12-v2`, `normalize_embeddings=True` (cosine is
then a dot product). Model name lives in `settings.yaml` as `pipeline.embed_model` —
owner-editable, no code edit to swap.

`pyproject.toml` gains, each with a one-line justification (constraint 13):
- `numpy` — ARCHITECTURE.md chose SQLite + NumPy brute-force cosine (constraint 5);
  ~900 × 384-d vectors, numpy makes it ~100 ms vs minutes in pure Python.
- `sentence-transformers>=3` as an **optional extra** `[embeddings]`, NOT a core
  dependency: the pipeline needs it, the owner's pytest does not. CI installs
  `.[embeddings]`; the offline suite must pass with it unimportable
  (`sys.modules["sentence_transformers"] = None`), same mechanism as
  `test_no_requests.py`.

CI install size is real and accepted: torch CPU wheel ~200 MB download, model ~470 MB,
both cached by GitHub's pip/model cache — first run slow (~3–5 min), later runs fast.
The run itself is unaffected: embedding ~120 unseen items on CPU is seconds.

### 2. Deterministic clustering: order is defined, tie-breaks are explicit

Greedy incremental cosine clustering (constraint 5: no vector DB). Input items sorted by
a fixed key — `published_at DESC, source_id ASC, url ASC` — so identical input produces
identical clusters. For each item: cosine against cluster centroids; join the best if
sim above `cluster_similarity_threshold` (0.62, the unmeasured placeholder — still a
guess, do not pretend otherwise), else start a cluster. Centroids are the running mean
of normalised member vectors.

No time window in Phase 6 clustering. The 30-minute near-duplicate window belongs to
Phase 9's lead collapse (LEAD_HANDLING.md); when it is built it must skip date-only
items (all-midnight UTC is not simultaneity) — that pending item stays open, noted here.

`FakeEmbedder` for tests: deterministic hash-derived unit vectors (sha256 of text →
seeded RNG → normalised 64-d vector). Deterministic across runs and machines; the real
MiniLM is exercised only where a model can be downloaded, which is not the offline
suite. That trade is explicit: cluster ASSIGNMENT logic is fully tested; embedding
QUALITY is tuned later on real data (Phase 10).

### 3. The cluster cap truncates by priority, deterministically, and says so

After clustering, if `len(clusters) > max_clusters_per_run`: rank and cut.

Priority of a cluster = `(max_source_tier_weight, latest_published_at, size)` where
tier weight comes from `credibility.yaml` (tier 1 = 1.0, 2 = 0.8, 3 = 0.5 — already
validated config, loaded via `ctx.config.credibility`). Higher tier first, then newer,
then larger. Full ordering, no coin flips.

Truncation logs ONE line: kept count, dropped count, and the dropped cluster keys (ids,
not text). Dropped clusters get no LLM call — that is the session-5 decision made real.
The understand stage calls the router for the kept clusters only.

### 4. The topic gate is a filter, not a blacklist, and only for gated sources

`config/topics.yaml`:

```yaml
# Keyword gate for sources marked topic_gate: true in sources.yaml.
# Match = any keyword, case-insensitive, substring, on title + body.
topics:
  en: [iran, tehran, khamenei, irgc, hezbollah, houthi, yemen, lebanon, syria,
       iraq, persian gulf, strait of hormuz, rial, opec, crude, israel, idf, gaza]
  fa: [ایران, تهران, خامنه‌ای, سپاه, حزب‌الله, لبنان, اسرائیل, یمن, نفت, ارز]
  ar: [إيران, طهران, حزب الله, لبنان, اليمن, إسرائيل, النفط, غزة]
  he: [איראן, טהרן, חיזבאללה, לבנון, ישראל, עזה, נפט]
```

`filter.py` keeps an item if the source is NOT `topic_gate: true` (pass-through), or if
any keyword for the item's language matches title+body (case-insensitive substring).
Language comes from the item, not the source. If the item's language has no keyword
list, the gate passes it (fail-open, never silently drop a possibly-relevant item).
Counters: `filter.in` / `filter.out` on `ctx.counters`.

`sources.yaml` gains a real `topic_gate: true` field on the six marked sources (the
marker was comment-only until now), `SourceSpec` gains `topic_gate: bool = False`,
`registry.py` passes it through. Strict settings validation is NOT extended to
sources.yaml (registry validates sources differently); keep the pass-through lenient
and typed by YAML truthiness.

### 5. Understand: one router call per cluster, JSON out, two filters

For each kept cluster, in priority order:

1. Render the prompt from `config/prompts/understand.txt` — the tone contract lives in
   that file, never in code (CLAUDE.md). Template placeholders: `{items}` (per item:
   source, date, title, body — body truncated to N chars per item, N in settings as
   `pipeline.item_body_chars`).
2. `router.complete(prompt, stage="understand")`.
3. Parse the response as JSON: `{headline, summary, entities: [...], clickbait: bool,
   irrelevant: bool}`. Parse failure → skip the cluster (log provider+cluster key, no
   retry at this layer — the router already did its retries).
4. Drop the cluster if `clickbait` or `irrelevant` is true (gate: the clickbait /
   irrelevance filter). Log the drop.
5. On `refused_cap` / `unavailable` → stop the loop, log once, continue the run
   degraded (the router's contract, phase 5).
6. Write the kept events to the `events` table (frozen schema, created not written):
   `event_key` = sha256 of sorted member urls (deterministic, unique),
   `summary` = summary text, `entities` = JSON array, `claim_status` = 'unconfirmed'
   (Phase 9 owns the real values), `source_count` = members, `first_seen_at` /
   `last_updated_at` = min/max published_at. Model code in `memory/models.py`
   (event_to_row / row_to_event / insert_events). This RESOLVES the deferred decision:
   **`items` survives** as the intra-run raw tier (pruned on `events_days`); `events`
   is the post-understand store; no second raw tier is written.

The compose reservation (Phase 5's `reserve(1, "compose")`) is a Phase 8 concern;
understand just calls. Budget: vision (0 in v1) + clusters ≤ 40 + compose = ≤ 41 calls,
under the 51 ceiling.

### 6. Wiring

`run.py._build_stages()` returns real stages for `filter`, `embed`, `cluster`,
`understand`; `vision` stays a passthrough stage (no images in v1 — see Out of scope).
`RunContext` gains `router`, `embedder`, `clusters`, `events` fields (None until wired).
`ops.mock_mode: true` builds the router with a mock transport and a FakeEmbedder —
mock mode is the default in tests, and it is not a stub of the stages (same principle
as Phase 5 §11).

Breaker/backoff reconciliation (pending item, resolved here): with `max_retries: 3` the
rotation loop makes ≤ 4 attempts, so `circuit_breaker_failures: 5` can never open.
Change to **`circuit_breaker_failures: 2`** — two consecutive failures on one provider
(schema garbage, repeated 429s) skips it for the rest of the run. Mechanism already
gate-tested in Phase 5; this is a settings value plus its comment.

### 7. Offline and no-new-secrets

- Every `agent.pipeline.*` module in `OFFLINE_MODULES` (test_no_requests.py).
- A new integration test: `sentence_transformers` made unimportable → suite imports and
  `MiniLmEmbedder()` fails at construction with the actionable message.
- No clock reads anywhere in the new files; `now` comes from `RunContext` (run.py's
  discipline). Cluster ordering uses `published_at`, not `now`.

---

## Gate

1. **Ten articles about one event become one event** — synthetic fixture: 10 items, same
   event, varied wording; 2 items about something else → 2 clusters → 2 events,
   asserted end-to-end through the real stages with a FakeEmbedder and mock router.
2. **Cap enforced**: 45 synthetic distinct topics with `max_clusters_per_run` lowered
   in a test settings file → exactly N clusters reach understand; dropped keys logged
   once; priority order is tier-then-recency (a tier-1 cluster beats a tier-3 one even
   when older).
3. **Topic gate**: a `topic_gate: true` source with off-topic items → all dropped;
   on-topic kept; a non-gated source passes everything through; unknown language
   fail-open.
4. **Determinism**: same input twice → identical cluster assignment and event keys
   (FakeEmbedder).
5. **Clickbait/irrelevance**: mock router returns `irrelevant: true` → no event written.
6. **Router contract holds under the stage**: refused_cap stops the understand loop,
   one log line, run continues; no exception escapes.
7. **Events round-trip**: insert 2 events, read back, fields match; `event_key` unique
   constraint respected (same cluster twice in one run → second insert ignored or
   upserted, no crash).
8. Offline: suite passes with `requests` AND `sentence_transformers` unimportable;
   `--dry-run` still exits 0 with the summary line.
9. Full suite green on the owner's Windows. Baseline is **361**. State the new expected
   number before running, then reconcile.
10. Grep gates: no `datetime.now`/`time.time` in `pipeline/`; no prompt string literal
    in any `.py` (prompts live in `config/prompts/`).

---

## Out of scope — do not build

- **Vision image extraction.** No collector extracts images in v1 (Item has no image
  field). `vision` stays a passthrough; `config/prompts/vision.txt` is written for when
  images exist, and the router's `see()` is already gate-tested. Extracting images is a
  collector change, own decision, not a drive-by.
- Validation / RUMOUR labelling / lead handling / confirmation counting. Phase 9.
- Composer, digest layout, delivery wiring. Phase 8.
- Workflows, secrets, state branch. Phase 7.
- Embedding-quality tuning on real data (threshold 0.62 stays a placeholder). Phase 10.
- The 30-minute near-duplicate window and the date-only guard for it. Phase 9.
- `signals_covered` in sources.yaml. Phase 8.

---

## Open owner decisions that touch this phase

None block the build. Two recorded so the Implementer does not "fix" them:

- `circuit_breaker_failures: 5 → 2` is a settings change the brief justifies (see §6).
  Flag, do not silently apply, if the owner wants the old value kept for other reasons.
- `sentence-transformers` as an optional extra means the owner's local pytest does NOT
  exercise the real model — only CI does. Accepted trade (2 GB install on a
  non-developer's PC is a worse failure); recorded so it is not mistaken for a gap.
