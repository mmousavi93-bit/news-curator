# Personal Intelligence Agent — Architecture (v1)

Status: **awaiting approval**. No implementation code until this is signed off.

Owner constraints locked: public GitHub repo, encrypted state, 8 pipeline runs/day plus a
09:00 Asia/Tehran daily digest, zero paid services, zero credit cards, owner's PC always off.

---

## 0. The four decisions that shaped everything

Before the diagrams, the reasoning that matters. Everything else follows from these.

**Cluster before you summarize.** Gemini free tier is 5 RPM / 250K TPM / 20 RPD
(3.8 Flash, corrected 2026-09-05; Groq's 14,400 RPD is the workhorse). A naive design calls
the LLM once per article. At roughly 800 articles collected per run that is 80 minutes of
pure rate-limit waiting per run and 6,400 requests per day against a 20/day ceiling. The
system would be dead on arrival. So clustering happens
first, using local embeddings that cost nothing and have no rate limit, and the LLM is only
ever shown a *cluster* — ten articles about the same event arrive as one prompt. That drops
the run from ~800 LLM calls to roughly 40, which fits inside the free tier with a 3x safety
margin. This single decision is load-bearing for the entire project.

**Risk scores are computed, not generated.** Asking an LLM for "7.4 out of 10" produces
±1.5 jitter on identical input, which makes every trend line meaningless noise. Instead the
LLM performs extraction only — it reports which discrete, observable signals appear in the
evidence (airspace closure, carrier movement, reservist mobilisation, embassy drawdown,
tanker rerouting, sanctions designation, and so on). A deterministic Python function maps
signal counts and source credibility onto a score. Identical evidence always produces an
identical score, so a delta of +0.8 genuinely means something changed in the world rather
than that the model was in a different mood.

**No OCR engine.** Tesseract scores roughly 40–60% accuracy on Persian and worse on Arabic
script over map imagery, and it cannot interpret a chart at all. Gemini Flash accepts image
input on the free tier and reads all four target languages natively while also describing
what a military map or an economic chart actually *shows*. Removing Tesseract removes a
system-level binary dependency, four language data packs, an image preprocessing stage, and
an entire phase from the roadmap. The vision path is just another LLM call.

**No vector database.** Thirty days of retention at an estimated 30 events per day is about
900 vectors. Brute-force cosine similarity over a 900×384 NumPy array takes well under a
millisecond. Chroma, Qdrant, Pinecone and pgvector are all solving a problem this project
does not have, while adding a service dependency, a free-tier quota to monitor, and a
failure mode. SQLite holds the records, NumPy does the maths.

---

## 1. System diagram

```
                        GitHub Actions cron  (09:00 Tehran digest + every 3h through 00:00 Tehran)
                                      |
                                      v
        +---------------------------------------------------------------+
        |                      RUNNER (ubuntu-latest)                    |
        |                                                                |
        |  1. STATE RESTORE                                              |
        |     git fetch state branch -> age-decrypt -> memory.db         |
        |                          |                                     |
        |                          v                                     |
        |  2. COLLECT  (async, bounded concurrency, robots-aware)        |
        |     rss.py web.py telegram_web.py gov.py markets.py [x: v2]    |
        |                          |                                     |
        |                          v  RawItem[]                          |
        |  3. CHEAP FILTER                                               |
        |     url hash -> title hash -> seen? DROP                       |
        |     ~800 items in, ~120 unseen out                             |
        |                          |                                     |
        |                          v                                     |
        |  4. VISION  (only items that are image-only + pass pre-filter) |
        |     Gemini Flash vision -> text  (budget: 10 calls/run)        |
        |                          |                                     |
        |                          v                                     |
        |  5. EMBED  (local MiniLM, multilingual, zero API cost)         |
        |                          |                                     |
        |                          v                                     |
        |  6. CLUSTER                                                    |
        |     new items <-> each other   (agglomerative, cosine)         |
        |     clusters  <-> open events in memory  (is this Day 7?)      |
        |                          |                                     |
        |                          v  ~25 clusters                       |
        |  7. UNDERSTAND   <<-- the only place the LLM writes prose      |
        |     one call per cluster -> summary, entities, signals,        |
        |     claim status (confirmed/likely/unconfirmed/rumour)         |
        |                          |                                     |
        |                          v                                     |
        |  8. VALIDATE     source count, independence, credibility       |
        |     -> confidence score          (pure Python, deterministic)  |
        |                          |                                     |
        |                          v                                     |
        |  9. RISK ENGINE  signals x weights -> STRAT / TACT / MSTRESS   |
        |     + ghost-meeting & countdown check vs event registry        |
        |     compare to previous -> trend  (pure Python, deterministic) |
        |                          |                                     |
        |                          v                                     |
        | 10. COMPOSE      1 LLM call -> Telegram message (<=4096 chars) |
        |                          |                                     |
        |                          v                                     |
        | 11. DELIVER      Telegram sendMessage, retry w/ backoff        |
        |                          |                                     |
        |                          v                                     |
        | 12. STATE SAVE   prune >30d -> age-encrypt -> force-push state |
        |                  + daily artifact backup                       |
        +---------------------------------------------------------------+
```

## 2. Data flow, stated as volumes

The numbers are the point. Each stage must survive the free tier.

| Stage | In | Out | Cost |
|---|---|---|---|
| Collect | 40 sources | ~800 raw items | network only, ~2 min |
| Hash filter | 800 | ~120 unseen | SQLite lookup, <1 s |
| Vision | ~15 image-only | ~10 transcribed | 10 Gemini calls |
| Embed | 120 texts | 120 × 384 vectors | local CPU, ~20 s |
| Cluster | 120 vectors + 900 stored | ~25 clusters | NumPy, <1 s |
| Understand | 25 clusters | 25 event records | 25 Gemini calls |
| Validate | 25 events | 25 + confidence | pure Python |
| Market metrics | 8 free series (stooq/FRED) | trigger flags | one fetch/run, <5 s |
| Risk | signals + metric triggers | STRAT, TACT, MSTRESS + tiers | pure Python |
| Compose | top events | 1 message | 1 Gemini call |
| **Total LLM** | | | **~36 calls/run, ~288/day** |

Against Gemini's 20/day ceiling the budget is provider-shared: gemini covers ~20
top-priority clusters a day and Groq (14,400 RPD) carries the rest, so total runtime is
bound by Groq's per-minute wall and wall-clock, not Gemini. At 5 RPM, gemini's 20 calls
cost ~4 minutes of pacing — the single largest fixed cost of the run.

## 3. Component responsibilities

**Collectors** turn the outside world into a uniform `RawItem` and know nothing about AI.
Each implements one interface, so adding X/Twitter later touches exactly one new file. RSS
uses feedparser with conditional GET via ETag and Last-Modified so unchanged feeds cost one
cheap 304 response. Web scraping uses trafilatura for article extraction and honours
robots.txt through a cached parser. Telegram channels are read through the public
`t.me/s/<channel>` preview endpoint, which requires no authentication, no API key, no phone
number and carries no account-ban risk — a Telethon MTProto session in CI would risk the
owner's personal Telegram account permanently, which is an unacceptable trade for marginally
better coverage. One collector is not a news collector: `markets.py` pulls eight free market
series (stooq CSV, FRED API — Brent/WTI, VIX, gold, HY spread, 10y–2y, S&P 500, rial trackers,
war-risk proxies) once per run straight into the `market_metrics` table; threshold crossings
are computed later, deterministically, in the risk package.

**Pipeline** is a linear sequence of pure-ish stages. Each stage takes a list and returns a
list. No stage knows about any other stage's internals. This is what makes the thing
testable: every stage can be exercised with a fixture list and no network.

**Vision** replaces the OCR component. It runs a cheap pre-filter first — an image below a
size threshold, or one whose entropy suggests a logo or avatar, never reaches the LLM. Only
images attached to items with no meaningful text body are candidates at all.

**Dedup** operates in four widening layers, cheapest first: exact URL hash, normalised URL
hash (query parameters and tracking tokens stripped), normalised title hash, then semantic
cosine similarity above a tuned threshold. The first three are free SQLite lookups that
eliminate the overwhelming majority of traffic before any expensive operation.

**Memory** is the SQLite database plus its access layer. It stores events, not articles. An
article is reduced to a summary and thrown away, both because the spec demands it and
because storing full text would bloat the repository. Memory also owns the
**scheduled-event registry**: a small table of announced future events — talks rounds,
ultimatum deadlines, IAEA board dates, planned summits — written during the understand
stage and checked every run. The two 2025–26 wars' best short-lead warnings were
*non-events* (a meeting that silently failed to convene, a deadline expiring); without a
registry the agent is structurally blind to them. Ghost-meeting (C3) and countdown (C1)
detection read this table (spec: `analysis/ESCALATION_SCORING.md` §5). Stateful posture
signals (carrier in theater, battery emplaced, crackdown ongoing) additionally carry a
last-confirmed timestamp, because their decay clock starts when the state *ends*, not when
it was first reported — the backtest showed this rule is worth ~24 points of STRAT on
Feb 21 2026.

**Risk engine** is deterministic and contains no LLM call. It computes three 0–100 scores
per run. **STRAT** (P(major escalation ≤7d), slow half-lives, posture-heavy) and **TACT**
(P(strike wave ≤48h), fast half-lives, trigger-heavy) follow `analysis/ESCALATION_SCORING.md`:
per-signal contribution = base × tier × novelty × decay, category caps on rhetoric and
markets, a convergence multiplier when 3+ categories fire within 72h, a deception multiplier
when official calm co-occurs with physical signals, and floor rules that guarantee proven
pre-strike signatures (paired evacuation orders, repeat vessel strikes, corroborated
airspace closure) are never diluted by decay arithmetic. **MSTRESS** follows
`analysis/ECONOMIC_SHOCK_SCORING.md`: metric threshold crossings plus decayed event scores,
with GEO-WAR / CREDIT / MIXED regime tagging. MSTRESS never raises TACT; TACT ≥75 adds to
MSTRESS. Single-source items are RUMOUR and contribute zero to any score. All weights live
in `config/risk_weights.yaml`, editable without touching Python; the two analysis files are
the normative specs.

**Composer** is the only component that decides what the owner actually sees, and it is the
one place tone lives. Alert tiers (0–4, mapped from max(STRAT, TACT)) modulate urgency, not
volume: each tier carries one line of practical guidance from the spec's tier table, tier
*transitions* drive prominence, and at tier 3+ the message recommends enabling the
high-frequency (1h) cron flag in `settings.yaml`. Digest layout is escalation first, markets
second, one line each when unchanged.

**LLM router** wraps every provider behind one `complete()` and one `see()` call, handles
rate-limit sleep, exponential backoff, provider failover, and a hard circuit-breaker that
degrades the run to "collect and store only, deliver nothing" rather than crashing.

## 4. Folder structure

```
news-curator/
├── .github/workflows/
│   ├── pipeline.yml            # every 3 hours
│   ├── digest.yml              # 09:00 Asia/Tehran
│   └── tests.yml               # on push / PR
├── config/
│   ├── sources.yaml            # THE file the owner edits to add feeds
│   ├── settings.yaml           # thresholds, schedules, feature flags
│   ├── credibility.yaml        # source -> credibility tier
│   ├── risk_weights.yaml       # signal -> indicator weight matrix
│   └── prompts/                # every prompt as an editable .txt
│       ├── understand.txt
│       ├── signals.txt         # from analysis/AGENT_PROMPT.md (Phase 7)
│       ├── vision.txt
│       └── compose.txt
├── src/agent/
│   ├── collectors/  base.py rss.py web.py telegram_web.py gov.py markets.py
│   ├── pipeline/    filter.py vision.py embed.py cluster.py
│   │                understand.py validate.py compose.py
│   ├── memory/      db.py schema.sql models.py retention.py
│   ├── risk/        engine.py signals.py indicators.py
│   ├── llm/         router.py gemini.py groq.py openrouter.py base.py
│   ├── delivery/    telegram.py formatter.py
│   ├── util/        logging.py http.py hashing.py lang.py crypto.py
│   ├── config.py    settings.py  run.py
├── tests/           unit/ integration/ fixtures/
├── docs/            SETUP.md OPERATIONS.md ADDING_SOURCES.md TROUBLESHOOTING.md
├── ARCHITECTURE.md
├── CLAUDE.md
└── pyproject.toml
```

No file should exceed roughly 200 lines. If one does, it is doing two jobs.

## 5. Technology choices and justification

| Choice | Why | What was rejected, and why |
|---|---|---|
| Plain Python, no agent framework | The pipeline is a fixed linear sequence with no branching decisions. There is no agentic loop to orchestrate. | LangGraph — the spec asks for it, but it adds a dependency, a mental model and a failure surface to solve routing that a `for` loop already solves. Revisit only if v2 adds conversational Q&A over the RAG. |
| SQLite + NumPy | ~900 vectors. Brute force is microseconds. Single file, trivially encryptable, zero services. | Chroma/Qdrant/pgvector — service dependency and quota for zero measurable gain. |
| sentence-transformers, multilingual MiniLM, local | No rate limit, deterministic, free, works offline in tests. Preserves the scarce Gemini quota for reasoning. | Gemini embedding API — burns the same 20 RPD quota that the summarisation stage needs. |
| Gemini Flash vision | Reads en/fa/ar/he natively, interprets maps and charts, already in the stack. | Tesseract — poor RTL accuracy, cannot read a chart, system binary + 4 language packs. |
| `t.me/s/` web preview | No auth, no secret, no ban risk. | Telethon MTProto — risks the owner's personal account. |
| age encryption | Single static binary, one keypair, no GPG keyring ceremony. | GPG — painful in CI, more moving parts. |
| Orphan `state` branch, force-pushed | Keeps exactly one revision. Repo stays ~15 MB forever. | Committing to `main` — 240 commits/month × 15 MB compounds to gigabytes and eventually breaks the clone. Actions cache alone — silent eviction means silent total memory loss. |
| GitHub Actions | Free, unlimited on public repos, secrets management included, no card. | Any VPS, Render, Fly, Railway — all eventually want a card or sleep the instance. |

Every dependency in `pyproject.toml` will carry a one-line comment justifying its presence.

## 6. Free-tier analysis

Verified August 2026.

| Service | Free limit | Our usage | Margin |
|---|---|---|---|
| Gemini Flash | 5 RPM, 250K TPM, 20 RPD, no card | ~20/day (top-priority) | head-start only |
| Gemini vision | included in above | inert in v1 (NoopStage) | — |
| Groq (workhorse) | 30 RPM, 14,400 RPD, no card | ~220/day (the bulk) | large |
| OpenRouter (fallback 2) | 20 RPM, **50 RPD**, roster rotates weekly | emergency only | thin — third tier only |
| GitHub Actions, public repo | unlimited minutes | ~1,450 min/month | n/a |
| Actions cache | 10 GB | ~500 MB (model + pip) | 20x |
| Actions artifacts | 500 MB free plan | ~15 MB/day, 14-day retention | fine |
| Telegram Bot API | 1 msg/sec per chat, **4,096 chars** | 9 msgs/day | trivial, but length is a hard design constraint |
| Git repo | 1 GB recommended | ~15 MB steady state | fine, *because* of force-push |

Two caveats worth stating plainly. OpenRouter's 50 requests per day makes it a genuine
emergency parachute, not a real fallback — Groq is the meaningful second tier. And Google's
free tier reserves the right to train on submitted prompts; for public news analysis this is
an acceptable trade, but it should be a conscious one and it means no private or personal
data may ever enter a prompt.

## 7. Bottlenecks

The binding constraint is Groq's per-minute token wall (~13-14 calls/run) and wall-clock.
Gemini's 20/day quota makes it a premium head-start, not capacity. A healthy run still
covers ~40 clusters — gemini first, then groq carries the tail. Mitigation is the
cluster-first design plus a hard cap on clusters per run, with overflow deferred to the next
run rather than dropped.

Second is embedding model cold start. The multilingual MiniLM download is roughly 470 MB and
takes 60–90 seconds. Mitigation is the Actions cache keyed on the model name, which makes
this a one-time cost, with a fallback to the Gemini embedding API if the cache misses and
the download fails.

Third is collector latency. Forty sources fetched serially at a 10-second timeout is a
worst case of nearly seven minutes. Mitigation is asyncio with bounded concurrency of eight
and an aggressive per-source timeout — a slow source is skipped, not waited on.

Fourth, and the one most likely to bite in month three, is the 4,096-character Telegram
limit. A busy news day will overflow it. The composer must budget characters explicitly and
truncate by priority rather than discovering the limit at send time.

## 8. Failure modes

| Failure | Detection | Response |
|---|---|---|
| Gemini quota exhausted | 429 | fail over to Groq, then OpenRouter |
| All LLM providers down | router circuit breaker | collect + store, send a one-line "AI unavailable" notice, resume next run |
| A source 404s or changes layout | parse yields zero items 3 runs running | mark degraded, alert owner in digest, keep running |
| Cron skipped or delayed by GitHub | run timestamp gap | every run looks back 6h, not 3h, so a skipped run loses nothing |
| Two runs overlap | Actions `concurrency` group | second run cancelled, not corrupted |
| State branch push conflict | git rejects | retry once with re-fetch; on second failure upload artifact and alert |
| Encryption key rotated or lost | decrypt fails | halt immediately, alert owner, never silently start from empty memory |
| Corrupt DB | `PRAGMA integrity_check` on load | restore from most recent artifact backup |
| Telegram send fails | non-200 | 3 retries with backoff; persist to outbox, prepend to next run |
| Scheduled workflow auto-disabled after 60 days inactivity | no message received | the state force-push each run counts as activity; a monthly keepalive job is belt-and-braces |
| LLM hallucinates an event | validation stage | any event with a single source is labelled Rumour and can never enter the risk score |
| Compressed onset: escalation-to-strike inside 48h vs 3h polling (~5h worst-case blind spot, seen Jun 2025) | alert tier ≥ 3 | composer recommends the 1h cron feature flag; still free on a public repo |
| Suppressed tells: evacuation-class signals deliberately withheld (seen Feb 2026) | n/a — by design | no signal category is necessary; posture, calendar and kinetic categories alone can reach tier 4, and absence of a signal never subtracts |

The last row is the important one. The defence against fabrication is not prompt
engineering, it is that the confidence and risk layers are deterministic Python that only
counts real sources.

## 9. Security review

Secrets live only in GitHub Actions Secrets and are referenced as environment variables;
nothing is ever committed. On a public repository every log line is world-readable, so the
logging utility runs a redaction filter over known secret values before anything is emitted,
and all logging is structured to make that reliable.

The state database is encrypted with `age` before it touches the state branch. The private
key lives in Actions Secrets. This means the repository can be public while the accumulated
intelligence, source list behaviour and analysis history stay unreadable.

Pull requests from forks must never receive secrets — the workflow will be configured so the
pipeline runs only on `schedule` and `workflow_dispatch`, never on `pull_request`. This is a
real and commonly exploited attack path on public repos and is worth being explicit about.

Scraped content is untrusted input. A hostile or compromised source could embed prompt
injection in an article body. Defences: all fetched content is wrapped in explicit
delimiters and labelled as untrusted data in the prompt, the LLM is asked for structured
JSON rather than free instructions, output is schema-validated before use, and the LLM has
no tools and no ability to take actions. Worst realistic case is one junk summary in one
message, not system compromise.

Third-party GitHub Actions are pinned to a full commit SHA rather than a tag, since tags are
mutable and are a known supply-chain vector. Dependencies are pinned with a lockfile.

Finally, free-tier Gemini may train on prompts. Only public news content is ever sent. No
personal data, no owner identity, no chat history.

## 10. GitHub Actions usage estimate

Per run: state restore ~15 s, collection ~120 s, filter and embed ~40 s, vision ~30 s,
LLM understanding ~240 s (rate-limit bound), risk and compose ~30 s, delivery ~5 s, state
save ~20 s. Total roughly 8 minutes, budgeted at 10 with overhead.

Eight runs a day is 80 minutes, plus a 3-minute digest job and CI on pushes. Call it
2,600 minutes a month. On a **public repository this is free and unlimited**, which is
precisely why the public-repo-plus-encryption choice matters: the same design on a private
repo would exceed the 2,000-minute allowance in the first three weeks with no room to debug.

## 11. Storage growth over 30 days

| Table | Row size | 30-day rows | Total |
|---|---|---|---|
| events (summary, entities, metadata) | ~1.2 KB | 900 | 1.1 MB |
| embeddings (384 dims, float16) | 768 B | 900 | 0.7 MB |
| event_timeline (per-update history) | ~300 B | 4,000 | 1.2 MB |
| seen_urls (hash only, 7-day retention) | ~80 B | 45,000 | 3.6 MB |
| risk_history (STRAT, TACT, MSTRESS + per-category detail × 8 runs/day) | ~200 B | 2,400 | 0.5 MB |
| scheduled_events (registry: talks, deadlines, board dates) | ~200 B | ~50 live | — |
| market_metrics (8 series × 8 runs/day, 30-day retention) | ~60 B | 1,920 | 0.1 MB |
| source_health | negligible | 40 | — |
| SQLite overhead + indexes | | | ~2 MB |
| **Total** | | | **~9 MB, encrypted ~9 MB** |

Steady state, because retention pruning runs every cycle. The repository stays around 15 MB
including code, permanently, because the state branch is force-pushed to a single commit and
therefore accumulates no history. Daily artifact backups with 14-day retention give a
recovery window of two weeks at a cost of roughly 130 MB against the 500 MB allowance.

## 12. Roadmap

**v1 — the ten phases below.** Runs unattended, delivers useful signal, costs nothing.

**v2** adds a conversational layer: reply to the bot with "what changed in the Red Sea this
week" and get a RAG-backed answer. This is the point at which LangGraph might finally earn
its place, because there would be an actual agentic loop. Also in v2: per-topic subscription
filters, a self-scoring loop where the system grades last week's risk calls against what
actually happened, and X/Twitter if any legal free path has reappeared.

**v3** adds a static GitHub Pages dashboard of risk trend lines rendered from the state
branch, multi-user support, and a monthly retrospective that reports the system's own
calibration. (Market data correlation, originally slated here, moved into v1 as the
MSTRESS layer — the metric fetches turned out to be free and trivial, and the escalation
scorer needs the oil/gold confirmation signals anyway.)

---

## Implementation phases

Each phase ends in a working, tested, committed increment. No phase begins before the
previous one is reviewed.

**Re-cut 2026-08-12 (session-3 decision 7).** The original order built 40 sources before
proving a message could reach the phone, and delivered no de-duplication benefit until
phase 6. Two problems with that: it front-loads the least reversible work (source curation)
behind the most uncertain (does any of this reach me?), and it gives the owner nothing to
react to for weeks. Sources are also the thing most likely to change once real output is
seen. So: prove the pipe end-to-end on 10 sources, then widen.

| # | Phase | Deliverable | Gate |
|---|---|---|---|
| 1 | Skeleton ✅ | folders, config loader, logging, CI, dry-run harness | tests green on empty pipeline |
| 2 | Telegram out | bot, private-channel delivery, formatter, 4,096-char budgeting, retries | a real message arrives on your phone |
| 3 | Collectors (thin) | RSS + web + `t.me/s/`, **10** sources only | ≥160 items post-cap from a **CI runner**, ≥8/10 sources, asserted by the workflow |
| 4 | Storage | SQLite schema (incl. scheduled-event registry + market_metrics), hash dedup, retention, age encryption | state survives a simulated restart; the same story twice is sent once |
| 5 | LLM router | Gemini + Groq + OpenRouter, backoff, circuit breaker, mock mode | passes with every provider force-failed |
| 6 | Understand | embeddings, clustering, cluster summarisation, clickbait/irrelevance filter | ten articles about one event become one event |
| 7 | Actions | both workflows, secrets, state branch, backups | three consecutive unattended runs |
| 8 | Widen sources | full `sources.yaml` incl. owner's `lead` channels, `signals_covered`, source-health | 800 items/run, no dead feeds |
| 9 | Validate | credibility tiers (per ESCALATION_SCORING.md §5), confirmation counting by independence group, confidence | rumours are labelled rumours; a `lead` alone never reaches output |
| 10 | Hardening | full docs, source-health alerting, threshold tuning on real data | one week unattended, zero intervention |

**v1 ends at phase 10.** That is the whole curator and it is a complete product.

| # | Phase | Deliverable | Gate |
|---|---|---|---|
| 11 | Risk engine (v1.5, optional) | STRAT/TACT/MSTRESS per analysis specs, markets.py fetcher, registry checks, weight matrix, trend | identical input → identical score, **and** the five §2 historical backtests hit their calibration targets |
| 12 | Settings bot (v2) | conversational config edit, auth, git write-back, validation | owner changes a threshold from his phone without breaking a run |

Phases 2 through 4 produce something you can actually hold — a bot that sends you real,
deduplicated headlines — before any of the clever parts exist. That is deliberate. If the
project stalls at phase 4 you still own something useful. Phase 6 is where it starts to
feel like less noise rather than more; that is the one phase not to cut.
