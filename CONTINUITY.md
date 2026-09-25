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

## Laptop catch-up
At logon, the laptop runs `pull-all.sh` to bring down the VPS agent's work.
