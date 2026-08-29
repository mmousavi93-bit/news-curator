# Phase 9 Brief — Validate (RUMOUR labels, independence, lead handling)

> **STATUS: BUILT and gate-green 2026-08-29 (suite 464, 0 failed). All seven
> gate items pass. Two real defects found and fixed (lead_outcomes schema gap
> fixed additively -- SCHEMA_VERSION 2; NULL-date events silently dropped by
> INSERT OR IGNORE -- now written with observation time). The accuracy-gate
> harness is re-filed to v1.5/Phase 11 per the session-3 scope cut: a gate for
> extraction code that does not exist measures nothing. Details in
> POSTMORTEMS.md §Status.**

Architect brief, written 2026-08-29 after Phase 8 closed (suite 438). An
Implementer runs from this file plus `CLAUDE.md`, `ARCHITECTURE.md` and
`analysis/LEAD_HANDLING.md`.

---

## Why this phase is ninth

Phases 5–8 made the pipe run and the funnel honest. What it still cannot do
is tell the owner how much to believe what it says -- and that is the other
half of the product promise. Gate: **rumours are labelled rumours; a lead
alone never reaches output.**

This phase is deterministic Python end to end (constraint 3): the LLM
already did its extraction; everything here is arithmetic over
credibility.yaml, which was built for exactly this.

---

## Deliverable

`validate` goes from no-op to real: every event gets `independent_count` and
`claim_status` (likely / unconfirmed / rumour), lead-only clusters are split
out of the main message, RUMOUR events are labelled in the digest, and lead
outcomes are persisted silently so v1.1's earned-trust ladder has data.

### Files (all under ~200 lines)

```
src/agent/pipeline/validate.py   ValidateStage: independence, status, lead split
src/agent/memory/lead_models.py  lead_outcomes row model (schema below)
tests/unit/test_pipeline_validate.py
tests/unit/test_lead_models.py
```

Edits: `memory/schema.sql` (additive lead_outcomes table, SCHEMA_VERSION 2),
`memory/event_models.py` (update_validation), `pipeline/compose.py` (RUMOUR
label, skip lead-only), `pipeline/deliver.py` (optional leads channel),
`pipeline/__init__.py` (wire).

---

## Requirements

### 1. Independence counts GROUPS, not sources (decision 4)

`independent_count` = number of DISTINCT credibility groups among members
whose tier is 1 or 2. Same-group duplicates (BBC English + BBC Persian, or
two outlets of one state owner) count once. Tier 3 and lead members never
count as corroborators (rulebook Step 1: >=2 independent NON-tier-3
sources). `group: null` resolves to the source's own id -- the documented
fallback (fully independent by default).

### 2. claim_status is deterministic arithmetic

- independent_count >= 2 -> `likely`
- independent_count == 1 -> `unconfirmed`
- 0 corroborating groups and at least one non-lead member -> `rumour`
- 0 corroborating groups and ONLY lead members -> the event is a
  lead-only event (below), not a rumour: leads cannot even be rumoured
  into the main feed.

Persisted via `UPDATE events SET claim_status=?, independent_count=? WHERE
event_key=?` (new `update_validation` in event_models). The understand
stage still inserts with 'unconfirmed' -- validate is the writer of truth.

### 3. Lead handling, v1 scope (LEAD_HANDLING.md "v1 — ship")

- Lead-only clusters are split into `ctx.lead_events` and NEVER reach
  `ctx.events` -- the main message cannot contain a lead alone (gate).
- Lead members of CORROBORATED clusters are fine: the event stands on its
  tier-1/2 members; leads contributed nothing to that count.
- `lead_outcomes` rows are written silently (no user-visible effect, no
  demotion): one row per (lead source, event) with outcome
  `confirmed` (the lead's cluster gained >=2 independent corroborators),
  `unconfirmed` (cluster corroborated by <2), or `raised` (cluster is
  lead-only -- the lead was first). v1.1 computes aged_out/contradicted
  from these rows; the columns exist from day one.
- The separate leads channel is OPTIONAL config: if
  `TELEGRAM_LEADS_CHANNEL_ID` is set, lead events compose into a second
  message delivered there, labelled "lead channel -- unverified". If not
  set, lead events are stored and never sent -- honest degradation, the
  owner chose "leads visible" and gets the knob either way.

### 4. Schema: additive only, no migration

`lead_outcomes` was promised by LEAD_HANDLING.md rev 2 ("schema carries
every field from day one") but is NOT in the Phase 4 schema -- a spec-vs-
build gap found this phase. Fixing it now is additive and safe: `CREATE
TABLE IF NOT EXISTS` runs on every open, existing databases gain the table
unchanged, and no production state exists yet. This is NOT a migration
against encrypted state; it is the last moment one can be avoided.
`SCHEMA_VERSION` 1 -> 2 records the addition.

```sql
CREATE TABLE IF NOT EXISTS lead_outcomes (
    id         INTEGER PRIMARY KEY,
    lead_source_id TEXT NOT NULL,
    event_key   TEXT NOT NULL,
    outcome     TEXT NOT NULL,  -- raised | confirmed | unconfirmed
    observed_at TEXT NOT NULL
);
```

### 5. Compose and deliver learn two things

- RUMOUR events render with a `[RUMOUR]` prefix on the headline line --
  constraint 10 made visible to the owner, not hidden in a database.
- `ctx.lead_events` compose into `ctx.lead_message` only when the leads
  channel id is configured; deliver sends it after the main message.

---

## Gate

1. **Independence by group**: an event whose members are BBC English +
   BBC Persian (same group) + one tier-3 channel -> independent_count 1,
   `unconfirmed`. The same event with Reuters + AP (different groups) ->
   2, `likely`.
2. **RUMOUR**: a single tier-2 source alone -> `rumour`, and the composed
   message carries `[RUMOUR]`.
3. **Lead alone never reaches output**: a lead-only cluster -> ctx.lead_events,
   ctx.events empty, no main message (the honest one-liner instead).
4. **lead_outcomes rows**: lead member of a corroborated cluster ->
   `confirmed` row; lead-only cluster -> `raised` rows, silently.
5. **Additive schema**: an existing (pre-Phase-9) database opens cleanly,
   gains the table, keeps its rows -- no migration, no data loss.
6. Suite green; baseline **438**. State the prediction before running.
7. Grep: no clock reads in validate/; no LLM calls anywhere in validate/
   (determinism, constraint 3).

---

## Out of scope — do not build

- The earned-trust ladder, demotion, scoreboard. v1.1 (needs two weeks of
  lead_outcomes rows -- the data this phase starts writing).
- The accuracy-gate eval harness and labels (extraction F1). That belongs
  to Phase 11's scorer gate; building it before the extraction prompt
  exists would be building a gate for code that does not exist.
- Risk scores, alert tiers, markets fetcher. Phase 11.
