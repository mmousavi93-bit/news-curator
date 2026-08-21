"""State: SQLite schema, connection guards, row mapping, dedup, retention, age.

Phase 4. The rule that governs every file in this package is CLAUDE.md
constraint 14 -- a state failure HALTS, it never resets. See db.py.

No LLM call, no embedding, no risk score belongs in here.
"""
