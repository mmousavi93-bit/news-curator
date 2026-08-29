"""Row model for `lead_outcomes` (Phase 9, SCHEMA_VERSION 2 additive).

Written silently by pipeline/validate.py -- the data v1.1's earned-trust
ladder needs (LEAD_HANDLING.md "v1 -- ship"). No user-visible effect and no
demotion in v1: these rows are the measurement, the ladder is the later
decision. Outcomes: `raised` (lead-only cluster -- the lead was first),
`confirmed` (the lead's cluster gained >=2 independent corroborators),
`unconfirmed` (corroborated by fewer than 2).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from agent.memory.models import to_utc_iso

_COLUMNS = ("lead_source_id", "event_key", "outcome", "observed_at")
_INSERT_SQL = (
    f"INSERT INTO lead_outcomes ({', '.join(_COLUMNS)}) "
    f"VALUES ({', '.join('?' for _ in _COLUMNS)})"
)


@dataclass(frozen=True, slots=True)
class LeadOutcome:
    lead_source_id: str
    event_key: str
    outcome: str  # raised | confirmed | unconfirmed
    observed_at: datetime


def insert_lead_outcomes(
    conn: sqlite3.Connection, outcomes: Iterable[LeadOutcome]
) -> int:
    rows = [
        (o.lead_source_id, o.event_key, o.outcome, to_utc_iso(o.observed_at))
        for o in outcomes
    ]
    if not rows:
        return 0
    cursor = conn.executemany(_INSERT_SQL, rows)
    return int(cursor.rowcount)


def read_lead_outcomes(
    conn: sqlite3.Connection, *, lead_source_id: str | None = None
) -> list[tuple[str, str, str]]:
    """(lead_source_id, event_key, outcome) tuples, oldest first."""
    sql = "SELECT lead_source_id, event_key, outcome FROM lead_outcomes"
    params: list = []
    if lead_source_id is not None:
        sql += " WHERE lead_source_id = ?"
        params.append(lead_source_id)
    sql += " ORDER BY id"
    return [(r[0], r[1], r[2]) for r in conn.execute(sql, params).fetchall()]
