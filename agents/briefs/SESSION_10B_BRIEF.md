# Session 10B — Gemini dead-model probe (is any free gemini model actually live?)

## Trigger (verified, run 34869394223)

The model-aware health fix (Session 10A) worked as designed: gemini was
un-demoted and got its head-start calls again. But `gemini-3.8-flash` —
the model pinned in `1fd1508` as "the live one" — returned **503 on 4/4
calls** (578 ms / 965 ms, 0 tokens). That is the exact listed-but-dead
signature already documented for `gemini-flash-latest` (503 ~432 ms) and
`gemini-2.5-flash` (404, discontinued). So the pin replaced one dead id
with another dead id.

The free-tier `GEMINI_API_KEY` therefore appears to have **no serving
flash model** — but "listed" ≠ "serving" for Google's models endpoint, and
the only way to be sure is to call `generateContent` directly per
candidate. This is a probe, not a fix: it decides the fix.

## Probe (standalone workflow, ~30 s, no pipeline/state involved)

`.github/workflows/gemini-probe.yml` (workflow_dispatch) curls
`:generateContent` with a 1-token `"ping"` prompt against each candidate
flash/lite id and prints `HTTP <code>` + a 300-char body snippet, so we
see the actual error reason (overloaded vs not-found vs quota vs region).

Candidates (from the `v1beta/models` listing seen in run 34869394223):
`gemini-3.8-flash` (current, known-dead), `gemini-3.7-flash`,
`gemini-3.6-flash`, `gemini-3.5-flash`, `gemini-3.5-flash-lite`,
`gemini-2.5-flash-lite`, `gemini-flash-lite-latest`,
`gemini-3-flash-preview`.

## Decision rule (applied after the probe)

- If **any** candidate returns 200 with tokens → pin it, keep gemini in
  the cascade (restore the 20-RPD free head-start), done.
- If **all** candidates fail (503/404/403/429) → gemini's free key serves
  nothing usable; **remove gemini from the cascade** exactly like `bai`
  was removed (`ef50d06`): drop it from `order`/cascade and leave the
  provider block inert so re-enabling is a one-line edit. groq remains the
  sole free provider — its 14,400 RPD is ~34x the ~420 calls/day ceiling,
  which already satisfies the owner's "enough free LLM" constraint.

## Invariants

- No secret leaves a header; keys stay in `-H` (curl errors echo URLs, and
  logs are public). Probe body output is the API's own error text, not
  our data.
- Probe workflow touches nothing: no state decrypt, no pipeline, no push
  to `state`/`flash-state` branches, `permissions: contents: read` only.
- The probe is deleted after use; it is not a permanent fixture.

## Scope — files

- `.github/workflows/gemini-probe.yml` — new, temporary, deleted after.
- (follow-up commit, after probe) `config/settings.yaml` and/or
  `src/agent/llm/wiring.py` — pin the winning model OR drop gemini.
- `POSTMORTEMS.md` — record the finding either way.
