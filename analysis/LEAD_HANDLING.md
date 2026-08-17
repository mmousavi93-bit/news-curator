# LEAD_HANDLING.md — graded assurance for untrusted OSINT

Status: **spec, not built.** Rev 2, 2026-08-16 (session 5). Rev 1 was reviewed
adversarially the same day and came back APPROVE WITH CHANGES with six defects; all six
are folded in below and the rev-1 claims they killed are marked so nobody relitigates them.

Refines `credibility.yaml` DECISION 7 ("a `lead` never appears in output alone"). A lead
can now appear, labelled, under the rules here. Does not touch DECISION 4.

## The problem, stated honestly

During the 2025–26 Iran wars, low-trust Telegram channels carried real information that
tier-1 wires ran late or never. They also carried a large volume of false information.
A binary trusted/untrusted split throws away the first to avoid the second.

Owner does not want zero false positives. He wants **graded assurance** — enough signal to
act on, with the uncertainty visible rather than hidden.

## What the ladder operates on — DEFINED, was undefined in rev 1

Rev 1 said "distinct independent **lead** groups" in §3 but its worked example used three
tier-3 channels. Under a lead-only reading the candidate set contains ~7 leads, at most 2
military — so `G >= 3` was mathematically unreachable and the ladder was dead on arrival.

**Population = tier 3 + `lead`.** Both classes feed the ladder. This is consistent with
their existing definitions, which are about what they may *conclude*, not whether they may
be *counted*: tier 3 nominates but never corroborates a fact; `lead` (weight 0.0) cannot
corroborate or score. Neither ever produces a confirmed item on its own — only `T = 1`
(tier 1/2 present) does that. Counting them toward visibility is not corroboration.

Tier 1/2 sources never contribute to `G`. If they are in the cluster, it is L3 by
definition and `G` is irrelevant.

## Independence is computed from evidence, not read from config — CHANGED

Rev 1 counted the hand-assigned `group` field. That fails on the common case. Telegram in
wartime is a repost economy; mirror relationships among anonymous Persian channels are
unknowable in advance and change weekly. Worse: **not one of the 21 candidate channels has
an entry in `credibility.yaml`**, and its `defaults` block sets `group: null`, which
resolves to "own id, fully independent" — the maximally credulous outcome for exactly the
threat this section exists to stop. Twelve reposts would have counted as twelve groups.

`G` is computed in this order, cheapest first:

1. **Near-duplicate collapse.** The pipeline already computes embeddings. Posts with
   cosine similarity above `lead_dup_threshold` (start 0.92) or an identical
   normalized-text hash, inside a `lead_dup_window` (start 30 min), are **one origin**
   regardless of how many channels carried them. This catches copy-paste reposts, which
   carry no metadata at all and are the majority case.
2. **Forward-attribution collapse.** The `t.me/s/` preview exposes "Forwarded from" for
   native forwards. The collector must capture it. All channels in one forward chain
   collapse to one effective group for that cluster.
3. **Config group, as fallback only.** `credibility.yaml` `group:` still applies where
   known (mirror channels, shared ownership). It is now a backstop, not the defence.

`G` = distinct origins surviving all three collapses.

Residual risk: a paraphrased copy defeats steps 1 and 2. Accepted — that is the rarer and
more expensive case, and step 3 catches the known instances.

Two hard prerequisites, both **Phase 3 schema decisions that cannot be retrofitted**:
the collector must store (a) forward-from attribution and (b) a normalized-text hash of
every post. Data never stored cannot be recovered later.

## The observability enum — DEMOTED from "core idea" to modifier

Rev 1 called this the single highest-value idea in the document. That was overstated and
the review was right to say so.

The claim was that fabrication concentrates in unfalsifiable `INTENT` claims. It does not.
Fabricators write "explosions heard in X" *precisely because* it is persuasive and
unfalsifiable for hours. `OBSERVABLE_LOCAL` is the **easiest** class to fake credibly.
The enum grades falsifiability-in-principle, not verifiability-in-practice.

It is still worth keeping as a modifier — it correctly separates claims that need
institutional access (where anonymous channels are structurally worthless) from claims an
eyewitness could make — but it is not the discriminator. **Evidence-computed `G` is.**

One field on the **existing** extraction call. Zero additional LLM calls, so constraint 2
is untouched.

| Value | Definition | Examples |
|---|---|---|
| `OBSERVABLE_LOCAL` | Witnessable by an ordinary person present | explosion, siren, air-defence fire, convoy, airport closure, outage, petrol queue, bazaar price |
| `INSTITUTIONAL` | Requires institutional access to know | statement issued, decree, negotiation, commander appointed, casualty figure |
| `INTENT` | A future action, unfalsifiable when posted | "will retaliate within 48h", "collapse imminent" |

Assignment rules, all undefined in rev 1:

- **Per extracted claim**, not per cluster.
- A cluster inherits the **most restrictive** class present. Mixed
  "explosions near Natanz" + "strike expected tomorrow" → `INTENT`.
- Extractor uncertain → default `INTENT`. Fail toward silence.
- The enum is **printed in the 07:00 digest** so misassignment is visible. A wrong enum
  silently shifts windows and caps otherwise, and nothing else audits it.

## Promotion ladder

`G` per the evidence rules above. `T` = 1 if any tier 1/2 source is in the cluster.

| Level | Condition | Output |
|---|---|---|
| **L0 SILENT** | `G = 1` | Not shown. Row written to `lead_outcomes`. |
| **L1 WHISPER** | `G >= 2` | LEADS block. Headline only. |
| **L2 WATCH** | `G >= 3`, or `G >= 2` sustained across 2 consecutive runs | LEADS block. Headline + one line + origin count. |
| **L3 CONFIRMED** | `T = 1` | Leaves LEADS. Main channel as a normal item, annotated `leads had this Nh earlier`. |

Type overrides:

- `OBSERVABLE_LOCAL` — as written.
- `INSTITUTIONAL` — needs `G >= 3` for L1; cannot exceed L1 without `T = 1`.
- `INTENT` — capped at L1, **digest only**. An intent rumour repeated 8×/day is precisely
  the noise this project exists to remove.

**Known tension, deliberately unresolved in v1:** L0 silences `G = 1`, but a unique scoop
from a channel with a measured 83% hit rate is the owner's stated use case. The fix is a
per-channel earned-trust exception, which requires §Earned trust to have run first.
Revisit in v1.1, not before.

All thresholds are integers in `settings.yaml`. No LLM assigns a level — constraint 3 holds.

## Every lead resolves

A lead that reached L1/L2 and never got `T = 1` inside its window is emitted **once** as
`UNCONFIRMED — AGED OUT`, then closed.

| Type | Window | Note |
|---|---|---|
| `OBSERVABLE_LOCAL` | **24h** | Was 12h in rev 1. Too tight — the owner's own complaint is that wires took longer than that on local events in June 2025. |
| `INSTITUTIONAL` | 48h | |
| `INTENT` | 7d, digest only | |

If a later confirmed item contradicts a closed lead, emit `CONTRADICTED` once.

Deletion is invisible: `t.me/s/` gives no deletion signal, so a channel quietly removing a
false post can never be counted against it. Known blind spot, no free fix.

## Earned trust — scoring rule CORRECTED

Per channel, persist `raised`, `confirmed`, `aged_out`, `contradicted`.

Rev 1 demoted on `confirmed / raised`. That is wrong and it is the most damaging error in
rev 1. Hit rate under that formula is `P(true) × P(a wire covers it)`. Channels whose whole
value is covering wire blind spots — the owner's explicit reason for wanting them, and the
exact argument he made in defence of his economy channels — accumulate `aged_out` and get
auto-demoted for succeeding at their purpose. **`aged_out` is not a false positive.**

Corrected:

```
reliability = contradicted / (confirmed + contradicted)
```

- `aged_out` is **displayed as "unresolved" and never demotes anything.**
- Demote to L0-only when `reliability > lead_max_contradiction_rate` (start 0.35) over at
  least `lead_min_sample` resolved items (start 10 — resolved, not raised).
- Promotion to tier 3 is a **suggestion in the digest**. The owner decides. Never automatic.

Weekly digest line:

```
fighter_radar      23 raised   11 confirmed   3 contradicted   9 unresolved   contra 21%
tg_padeshah_fxn    41 raised    4 confirmed   8 contradicted  29 unresolved   contra 67%  DEMOTE
```

Storage: one `lead_outcomes` table, ~40 bytes/row. Negligible.

## Character budget, not a count cap

`delivery/budget.py` from Phase 2 already does priority truncation and the overflow marker.

- Main body: priority, takes what it needs.
- LEADS block: remainder, up to **35% of 4,096 ≈ 1,400 UTF-16 units**.
- Sort: level desc, then `G` desc, then recency.
- Overflow: existing marker — `+7 more leads`.

Volume self-limits by budget. No magic number.

## Separate destination

Leads go to their own Telegram channel, `TELEGRAM_LEADS_CHANNEL_ID` (env var only, public
repo). Main channel stays the calm curated feed. An L3 promotion appears in **main** —
promotion is the point.

Consistent with session-3 decision 4. Cost: one env var, one call in `delivery/`.

## What `t.me/s/<channel>` does and does not give — NEW, was silently assumed

Constraint 6 forbids Telethon/MTProto, so the public preview page is the only access.

Gives: ~20 most recent posts, reliable UTC timestamps, native forward-from attribution,
approximate view counts, text and image URLs.

Does **not** give:

- Anything at all if the channel has web preview disabled. **18 of the 21 candidates have
  never been probed from CI** — only `geopolitics_prime`, `militarywave` and `geopwatch`
  are known reachable. This is unknown, not assumed, and one `workflow_dispatch` settles it.
- Edit history.
- Deletions — see §Every lead resolves.
- More than ~20 posts. A burst-posting war channel can exceed 20 in a 3h window, so items
  will be silently missed unless the collector paginates via `?before=`, which costs
  requests. Budget: 21 channels × 8 runs/day = 168 fetches/day before pagination.
- Any guarantee Telegram will not throttle 21 channels × 8 runs/day from Azure IPs.
  Unknown. The probe measures one round, not sustained load.

## Composition gaps in the candidate set — NEW

The 21 channels are monocultural: fa/en military eyewitness. Against the project's own
en/fa/ar/he source-language spec there are **zero Arabic and zero Hebrew channels**.

Separately, `credibility.yaml` reserves four tier-3 OSINT slots — `tg_tanker_trackers`,
`tg_kpler_public`, `tg_flight_osint`, `tg_incident_aggregators` — and **not one has a real
handle**. Maritime and airspace facts are exactly what the 7 CUT_BOT_BLOCKED tier-1
mechanical feeds (UKMTO, ISW, safeairspace) were supposed to supply and no longer can.
Filling those four slots is worth more than any marginal 22nd military channel.

`tg_behold_israel` at trust 3 is indefensible — prophecy-framed commentary can corroborate
nothing. `lead` or cut.

21 is the right order of magnitude. The gap is coverage, not count.

## Constraint compliance

| Constraint | Status |
|---|---|
| 2 — max ~40 LLM calls/run | **Held.** Enum is a field on the existing extraction call; leads reuse the existing clustering stage. Zero new calls. |
| 3 — deterministic scoring | **Held.** LLM supplies the enum and the cluster. `G`, levels and reliability are integer/float arithmetic in Python. |
| 6 — no Telethon | **Held**, with the limits in §What `t.me/s/` gives now written down instead of assumed. |
| 8 — 4,096 chars | **Held.** Budget section, via existing `budget.py`. |
| 10 — never present unverified as fact | **Held.** Level label on every line; `INTENT` never in a per-run message. |
| 11 — never invent content | **Held.** Empty LEADS block prints nothing. |
| 12 — files under ~200 lines | New `pipeline/leads.py` + `lead_outcomes` in the memory package. |

## Build order — CIRCULARITY FIXED

Rev 1 deferred the resolution logic to v1.1 and then justified v1.1's thresholds with "two
weeks of `lead_outcomes` data" — but the deferred logic is what *writes* those rows. As
split, v1 produced no tuning data and v1.1 would have started as blind as v1.

**v1 — ship:**

- Leads cluster through the existing stage (§Independence steps 1 and 2 — near-duplicate
  and forward collapse). Collector stores forward-from and normalized-text hash.
- Ladder with one flat threshold on `G` and `T`. No type overrides.
- Character budget. Separate leads channel.
- `observability` extracted and **stored, not acted on**.
- **`lead_outcomes` rows written silently** — raised / confirmed / aged_out / contradicted
  all computed and persisted. No scoreboard message, no demotion, no user-visible effect.

**v1.1 — after two weeks of real rows:**

- Turn on the scoreboard message and auto-demotion.
- Type-conditioned windows and the `INSTITUTIONAL` / `INTENT` overrides.
- Per-channel earned-trust exception to L0.

Every threshold in this document is a guess: `G >= 3`, 0.92, 30 min, 24h, 0.35, 10. None is
measured. Building the full ladder before the data exists means tuning blind — the same
mistake as the `0.62` clustering threshold still sitting unmeasured in `settings.yaml`.

Schema carries every field from day one, so v1.1 is configuration, not rework.
