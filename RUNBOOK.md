# RUNBOOK — from this repo state to an unattended live system

Written 2026-08-29 after Phases 1–9 built and shim-green (suite 464, 0 failed).
This is the owner-facing sequence. It is ordered: each step verifies the one
before it. Do not skip a verification step — every one of them exists because
a real defect shipped past a cheaper check (see POSTMORTEMS.md).

---

## 0. Commit and push this session's work

All Phase 5–9 code is on disk, uncommitted. Agents never run git in this repo
(an agent-created `.git/index.lock` has broken your `git add` before).

On Windows, in the repo folder:

```powershell
git status
# If it says "Unable to create '.git/index.lock'", delete the file first:
#   Remove-Item .git\index.lock
git add -A
git commit -m "Phases 5-9: router, understand, actions, widen, validate (suite 464)"
git push origin main
```

VERIFY CONTENT, not HEAD: `git show origin/main:src/agent/llm/router.py | findstr "CallBudget"`
must print something. A push that says "Everything up-to-date" while local files
changed means the commit failed silently — same trap as probe round ci3.

Then GitHub → Actions → "tests" workflow: confirm green (it runs on push).

---

## 1. Accounts and keys (SETUP_ACCOUNTS.md has the walkthroughs)

You need, in order of importance:

1. **Gemini API key** (free, aistudio.google.com) — the pipeline's LLM.
2. **Groq API key** (free) — first fallback.
3. **OpenRouter API key** (free) — optional, emergency parachute.
4. **Telegram bot token** — @BotFather → /newbot. Save the token once.
5. **A private Telegram channel** — create it, add the bot as ADMIN.
   Get its channel id: forward one channel post to @userinfobot.
6. **age keypair** — generated locally, nothing to sign up for:
   ```powershell
   age-keygen   # prints "PUBLIC KEY: ..." and stores the secret key file
   ```
   The PUBLIC key goes in GitHub Secrets; the SECRET key too. Keep both.

A second **leads channel** is OPTIONAL: if you want the untrusted `lead`
sources' events in their own feed, create a second channel, add the bot as
admin, and put its id in a `TELEGRAM_LEADS_CHANNEL_ID` secret. Without it,
lead events are stored but never sent (honest degradation).

## 2. GitHub secrets

Repo → Settings → Secrets and variables → Actions → New repository secret.
Names must match EXACTLY (the workflows read these):

```
GEMINI_API_KEY
GROQ_API_KEY
OPENROUTER_API_KEY
TELEGRAM_BOT_TOKEN
TELEGRAM_CHANNEL_ID
TELEGRAM_LEADS_CHANNEL_ID    (optional)
AGE_PUBLIC_KEY
AGE_SECRET_KEY
```

Never paste any of these into chat or into a file in the repo — the repo is
PUBLIC (constraint 9).

## 3. Bootstrap the state database

The pipeline halts without a state file — by design (constraint 14: an absent
database may mean a failed restore, and empty memory is worse than halting).
Create it ONCE, locally:

```powershell
pip install -e .         # PyYAML + requests; pytest for the gate later
python -m agent.run --collect-only --db state.db --init-db
```

This creates an empty, correct state.db (schema v2). Then encrypt it with the
PUBLIC key from step 1:

```powershell
age -r <your public key> -o state.db.age state.db
```

Commit `state.db.age` to the ORPHAN state branch (the plaintext state.db is
gitignored and must never be committed):

```powershell
git checkout --orphan state
git rm -rf .
git add -f state.db.age          # -f: *.age is gitignored for main
git commit -m "state bootstrap"
git push -u origin state --force
git checkout main
```

From now on the workflow maintains that branch (one revision, force-pushed
every run).

## 4. Verify the delivery path

Actions → send-test → Run workflow → run. Green + log line "sent (status=200)"
means the bot token and channel id are correct end to end.

## 5. Verify the collectors — expect a FAILING gate first

Actions → collect-test → Run workflow (fresh dispatch, never "Re-run jobs").
**Expect GATE FAILED on the first run**, naming `state_dept_travel` (every
item date-only — the every-source null-date assertion) and possibly a dormant
source (>14 days staleness). That failure is the acceptance test for the gate
itself. Read the report: sources with per-source errors are collector bugs
(open an issue with the JSON report attached); date-only advisories and
dormant channels are source facts (record them, do not "fix" the gate).

## 6. First real pipeline run

Actions → pipeline → Run workflow (dispatch). Expect: a Telegram message
within ~10 minutes. First run message shape: "Nothing new since the last run."
is NORMAL (the state db already saw the items from step 3). Second run (3h
later or another dispatch) should list real events.

## 7. The gates

- **Phase 7 gate — three consecutive unattended runs**: let the cron run 3
  cycles (9+ hours) without touching anything. Each run must: exit green,
  produce one message, update the `state` branch, upload the `state-db`
  artifact. Check the artifact exists after each run.
- **Phase 8 gate — 800 items/run, no dead feeds**: one collect-test dispatch
  after widening: `total_kept` should be well above 160 (50 sources × ~20 cap
  ≈ up to 1,000); `sources_with_items` should be ≥8 and in practice ≥30.
  Any source that fails the 14-day staleness check is a decision, not a bug:
  replace or disable it.
- **Phase 10 gate — one week unattended**: leave it alone for 7 days. Read
  the digests. Only then decide threshold tuning (0.62 clustering is still
  the unmeasured placeholder — tuning it takes real data, which is what the
  week produces).

## 8. Ongoing: the 60-day cron kill switch

GitHub disables cron schedules after 60 days of repo inactivity. Any commit
(or manually re-enabling the workflow in the Actions UI) resets it. The
digest going quiet after ~2 months is this, not a failure — commit something.

## 9. Reading the daily digest

- Events are labelled: `[RUMOUR]` = one source family only, treat as a lead.
- "time not stated" = the source publishes dates without times. Never a
  clock time for those.
- "Nothing new since the last run." = exactly that.
- The 09:00 Tehran message is the daily digest (header says so).
