# SESSION_25_BRIEF — Limited holistic digest: top-5 cap, synthesis line, follow-up thread context

## Why (owner decision 2026-10-25, from a 7-day review)

The digest floods the owner: median 27 items/digest (max 38) across up to 6
messages, of which ~85% are "پیگیری" follow-ups of already-seen stories. The
owner wants a LIMITED feed of the most important items that together give a
HOLISTIC view, not a per-news dump. Three decisions locked in chat:

1. **Top 5 items max** per digest.
2. **Synthesis line: yes, quality is the point** — one insightful "big picture"
   line at the top (engineered, not a restatement).
3. **Follow-ups carry thread context** — a follow-up that earns a slot renders
   as a self-contained summary of the whole thread, emphasising the latest
   event (the reader may have missed earlier messages).

## Changes

1. **Top-5 cap** — new `digest_rank.max_items: 5`. `compose.py` slices the
   ranked `kept` list to `max_items` before building items. `kept` is already
   score-sorted desc, so this takes the 5 most important (follow-ups included
   when they score in). No other gate changes.
2. **Synthesis line** — new `config/prompts/synthesis.txt` + new module
   `pipeline/synthesis.py`. One extra LLM call per run (`stage="synthesis"`)
   over the top-N events (headline/summary/significance/why_matters). Output is
   ONE Persian sentence, OPTIONAL + cosmetic: parsed defensively, trimmed to
   bounds, silently omitted on any failure — it must never sink the digest.
   Rendered as the first line of the digest (a distinct header, not a news item).
3. **Follow-up thread context** — new in-memory `Event.prior_context` field
   (tuple of the matched priors' summaries, bounded). Populated in
   `repeats.py::drop_repeats` at bypass time (the `matched` list is already in
   hand). Rendered in `render.py`: a follow-up item shows
   «قبلتر: <prior summary head> · اکنون: <new summary>» so the thread reads
   whole, latest emphasised. Deterministic merge — NO extra LLM call, no
   hallucination surface.

## Scope (files)

NEW:
- `config/prompts/synthesis.txt`
- `src/agent/pipeline/synthesis.py`
EDIT:
- `src/agent/settings_schema.py` — `max_items: int`, `synthesis_enabled: bool` on DigestRankSettings
- `config/settings.yaml` — `max_items: 5`, `synthesis_enabled: true` (with a comment block)
- `src/agent/memory/event_models.py` — `prior_context: tuple[str, ...] = ()` (in-memory only)
- `src/agent/pipeline/repeats.py` — stamp `prior_context` on bypassed follow-ups
- `src/agent/pipeline/render.py` — follow-up thread-context rendering + numbered items (۱–۵)
- `src/agent/pipeline/labels.py` — new labels (`synthesis_header`, `prior_context` prefix)
- `src/agent/pipeline/contract.py` — `SYNTHESIS_WORD_BOUNDS = (4, 40)` (trim, not gate)
- `src/agent/pipeline/compose.py` — hook synthesis (after rank, before items) + max_items slice
- tests (unit, matching the existing patterns in tests/unit/)

## Constraints (hard, unchanged)

- **Never invent content.** The synthesis is grounded in the given events only;
  its anti-hallucination fence lives in the prompt, and it is a TRIM/omit, never
  a gate — a bad or absent synthesis line must not drop a real event.
- Follow-up context is a deterministic merge of already-persisted text — no new
  LLM call, no fabrication.
- ~200-line file cap (constraint 12): compose.py is near it — put synthesis
  logic in the new module, not inline.
- `max_items` is a cap, not a re-rank: slice after `rank_events`, never before.
- The synthesis call uses `ctx.router.complete(prompt, stage="synthesis")`; on
  `not result.ok` or parse failure, omit the line and log — never raise.

## Gate

- `python -m pytest -q` green (Py3.12 interpreter; full suite, note pass count).
- Secret scan clean.
- Manual `pipeline.yml` workflow_dispatch after push to verify the digest shape
  + synthesis on a live run.

## Out of scope (deferred, will brief separately)

Flash false-positive fix, Cloudflare Worker not-dispatching, gemini 82% fail.
