# Operations — day-to-day with the News Curator

The system runs itself: every 3 hours it fetches 50 sources, dedupes,
clusters, summarises, validates, and sends ONE message to your private
Telegram channel. A daily digest lands at 07:00 Tehran. Your PC can be off;
everything runs on GitHub's US runners, free.

## What you see

- One message per run. Short = normal. "Nothing new since the last run."
  is exactly what it says.
- `[RUMOUR]` prefix: the story comes from one source family. Treat as a
  lead, not a fact.
- `[LEAD]` items (only if you configured the leads channel): untrusted
  Telegram channels, weight zero. They exist to point at things early.
- "time not stated": the publisher gave a date without a time. The system
  never invents one.

## The one file you never edit by hand

`config/sources.yaml` — read the JOIN RULE at the top. Any id you add must
exist in `config/credibility.yaml` in the same edit, or the run halts.
New URLs must clear a probe round first (see ADDING_SOURCES.md).

## Where things live

- State: encrypted `state.db.age` on the orphan `state` branch + a 90-day
  artifact copy per run (Actions → pipeline → Artifacts → state-db).
- Logs: Actions → pipeline → a run → "Run pipeline" step. Everything is
  redacted by value; no key, prompt or article body ever appears.
- Prompts and tone: `config/prompts/*.txt` — edit those, never the code.
- Topic gate keywords: `config/topics.yaml`.

## Things that need you, rarely

- **The 60-day cron kill switch**: GitHub disables schedules after 60 days
  of repo inactivity. Any commit resets it. If the digest goes silent after
  ~2 months, this is why.
- **A source dying**: watch for `source health: X degraded` lines in the
  logs, and the collect-test staleness check. Dormant channels get
  replaced, not "fixed".
- **Budget**: 50 enabled sources × ~20 items ≈ up to 1,000 raw items per
  run; the topic gate trims the six general-interest feeds; clustering
  turns it into ≤40 stories; one Gemini call per story, ≤51 calls per run —
  inside every free-tier limit by design (CLAUDE.md).
