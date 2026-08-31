"""The flash monitor: near-instant, ZERO-LLM, deterministic Tehran
explosion/attack alerts (owner request 2026-08-30, design approved same
day).

Modules: config.py (strict loader for config/flash_alert.yaml),
matcher.py (term x location buckets, exclusions, freshness),
store.py (separate flash.db — its own state branch, never the pipeline's),
policy.py (burst collapse, follow-ups, caps, quiet windows),
run_flash.py (entry point: fetch subset -> match -> send -> persist).

Why a separate DB and state branch: the pipeline's state-branch push
dance (`git rm -rf .`) deletes everything on that branch except the one
encrypted DB, and two workflows force-pushing one branch would race on
lost updates (solver analysis 2026-08-30). The flash monitor never
touches the pipeline's state — the digest re-reports alerted stories
normally; that duplication is the design, not a bug.
"""
