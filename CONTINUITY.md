# Continuity

Two-writer repo — writable from BOTH the laptop and the VPS agent
(`hermes@130.185.122.72`). The VPS agent continues this project when the laptop is off.

## Resume (VPS agent)
1. `cd /home/hermes/work/news-curator` then `git pull --ff-only`
2. Read `CLAUDE.md` (full project context, constraints, LLM cascade) and `agents/ORCHESTRATION.md`, then resume from where it left off.
3. Commit and `git push` (local origin — no key needed).

## Discipline (both agents)
1. `git pull --ff-only` before committing/pushing.
2. Never `push --force`.
3. Update `CLAUDE.md` / `STATE.md` at the end of every session so the other side can resume.

## Runtime note
This repo runs unattended on **GitHub Actions (CI)**, NOT on the VPS. The VPS agent only does
DEVELOPMENT: edit code, commit, push to this VPS hub; the laptop relays to GitHub and CI runs.
Branches `state`, `flash-state`, `probe-results`, `run-reports` are CI-owned runtime state —
the VPS agent touches **`main` only**.

## Active continuation — Session 25 → 26

Read `SESSION_25_VPS_HANDOFF.md` first, then `agents/briefs/SESSION_26_VPS_BRIEF.md`.
The owner moved development to the VPS. The handoff preserves Astra's review,
parent-verified findings, corrected audit statistics, decisions and evidence paths.
First increment: queue retention, quiet-run selection, per-message delivery
acknowledgments and accurate queued-event reporting. No ranking/flash/provider
changes bundled in. Runtime remains GitHub Actions. Check
`analysis/SESSION_26_PROGRESS.md` and `.continuation/session26/` for the active
job before starting another writer.

## Laptop catch-up
At logon, the laptop runs `pull-all.sh` to bring down the VPS agent's work.
