"""Connection, integrity check, schema-version check, halt-on-failure.

Constraint 14 is the whole point of this file: **failure must halt, never
reset.** If the database cannot be opened, fails `PRAGMA integrity_check`, or
reports a schema version this code does not understand, every function here
raises `StateError` and the caller exits non-zero. Nothing in this module
creates a replacement database, repairs anything, or falls back to empty state.

The reasoning, because a future reader will be tempted: starting from empty
memory is a WORSE outcome than crashing. A crash is visible -- the run fails,
no message arrives, the owner investigates. An empty memory is invisible: the
agent re-sends weeks of already-seen stories and looks like it is working
perfectly while having silently destroyed the only thing it accumulates.

`initialize()` is therefore separate from `open_db()` and never called as a
fallback. Creation is an explicit act (`--init-db`), not a recovery path.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from agent.memory.upgrades import ADDITIVE_UPGRADES
from agent.util.logging import get_logger

# Bump ONLY together with a migration. An unrecognised version halts.
# 2 = Phase 9 additive lead_outcomes table (no data migration; see
# upgrades.py -- old databases gain the table on open).
# 3 = Phase 10 additive delivered table (owner received-marker for the
# anti-repetition window; same additive pattern).
# 4 = Session 25 additive pending table (24h digest queue -- ranked-but-not-
# delivered events stay candidates across runs).
SCHEMA_VERSION = 5

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")
# Keep the scoped migration beside its DDL; earlier migration paths stay intact.
_signal_ddl = _SCHEMA_PATH.read_text(encoding="utf-8").split(
    "-- SIGNAL_EXTRACTION_V5", 1)[1].split("-- END_SIGNAL_EXTRACTION_V5", 1)[0]
ADDITIVE_UPGRADES = {**ADDITIVE_UPGRADES, 4: _signal_ddl.split("\n", 1)[1]}
_VERSION_KEY = "schema_version"

logger = get_logger("agent.memory.db")


class StateError(Exception):
    """Raised for every condition that must halt the run rather than degrade.

    Missing database, unreadable file, failed integrity check, absent or
    unrecognised schema version. Callers map this to a non-zero exit.
    """


def _connect(path: Path) -> sqlite3.Connection:
    """Open `path` with no side effects beyond what SQLite requires.

    `journal_mode` is deliberately left at the default (DELETE) rather than set
    to WAL. WAL leaves `-wal` and `-shm` sidecar files next to the database,
    which would have to be flushed before the age-encrypt step in Phase 7 and
    would break the "no new database file exists afterwards" assertion this
    module owes the corruption test. There is exactly one process touching this
    file, on a runner, once every three hours -- WAL buys nothing here.
    """
    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn


def _check_integrity(conn: sqlite3.Connection, path: Path) -> None:
    try:
        rows = conn.execute("PRAGMA integrity_check").fetchall()
    except sqlite3.DatabaseError as exc:
        # A scribbled or truncated file raises here rather than returning a
        # verdict -- "file is not a database". Same outcome, different path.
        raise StateError(f"state database at {path} is unreadable: {exc}") from exc
    verdict = rows[0][0] if rows else "(no result)"
    if verdict != "ok":
        raise StateError(f"state database at {path} failed integrity_check: {verdict}")


def read_schema_version(conn: sqlite3.Connection) -> int:
    """The stored version, or raise. A zero-byte file is a VALID empty SQLite
    database -- integrity_check passes on it -- so the absence of `meta` is the
    only thing standing between "truncated to nothing" and "fresh empty state
    that looks fine". It halts."""
    try:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (_VERSION_KEY,)).fetchone()
    except sqlite3.DatabaseError as exc:
        raise StateError(
            f"state database cannot be read for schema_version -- no meta table: {exc}"
        ) from exc
    if row is None:
        raise StateError("state database has no schema_version row")
    try:
        return int(row[0])
    except (TypeError, ValueError) as exc:
        raise StateError(f"state database has a non-integer schema_version: {row[0]!r}") from exc


def initialize(path: Path) -> sqlite3.Connection:
    """Create a new state database at `path` and return the open connection.

    Refuses to overwrite. This is the ONLY code path that creates a database,
    and it is never reached from an error handler -- see the module docstring.
    """
    path = Path(path)
    if path.exists():
        raise StateError(f"refusing to initialize over an existing file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = _connect(path)
    try:
        conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?)", (_VERSION_KEY, str(SCHEMA_VERSION))
        )
    except Exception:
        conn.close()
        # Half-written schema is not state worth keeping and this file did not
        # exist a moment ago, so removing it destroys nothing. This is the one
        # place a unlink is safe, precisely because we created it in this call.
        path.unlink(missing_ok=True)
        raise
    conn.execute("PRAGMA foreign_keys = ON")
    logger.info("initialized state database (schema v%d)", SCHEMA_VERSION)
    return conn


def open_db(path: Path, *, create_if_absent: bool = False) -> sqlite3.Connection:
    """Open an existing state database, validated. Halts on anything unexpected.

    `create_if_absent` defaults to False on purpose. Phase 7 restores the
    database from the encrypted state branch before this is called; if that
    restore fails, the file is absent, and a default of True would turn a
    decryption failure into a silent fresh start -- the exact constraint-14
    failure. The caller must ask for creation explicitly and only when it knows
    no prior state existed.
    """
    path = Path(path)
    if not path.exists():
        if not create_if_absent:
            raise StateError(
                f"no state database at {path}. Refusing to create one implicitly "
                "(constraint 14): an absent database may mean a failed restore, "
                "and starting from empty memory is worse than halting."
            )
        return initialize(path)

    conn = _connect(path)
    try:
        _check_integrity(conn, path)
        version = read_schema_version(conn)
        if version > SCHEMA_VERSION:
            # The DB is NEWER than this code: a future schema this build does
            # not know how to read. Stamping it down would be silent data
            # destruction (constraint 14). Halts.
            raise StateError(
                f"state database schema version {version} is newer than "
                f"{SCHEMA_VERSION}; this code does not know how to read it. "
                "Halting rather than guessing."
            )
        if version != SCHEMA_VERSION:
            # Idempotent CREATE IF NOT EXISTS only (upgrades.py): an existing
            # database gains the new tables and keeps every row (Phase 9,
            # lead_outcomes -- the spec-vs-build gap found pre-production;
            # Phase 10, delivered). EVERY step from the stored version is
            # applied -- the live DB is at 2 (one step), but a dormant v1 DB
            # must gain both tables before the meta row moves (review finding
            # 2026-08-30: one-script-per-key would stamp 3 without creating
            # delivered, and the lied-about meta row would block every retry).
            for step in range(version, SCHEMA_VERSION):
                if step not in ADDITIVE_UPGRADES:
                    raise StateError(
                        f"state database schema version {version} has no additive "
                        f"upgrade path to {SCHEMA_VERSION}. Halting rather than guessing."
                    )
                conn.executescript(ADDITIVE_UPGRADES[step])
            conn.execute(
                "UPDATE meta SET value=? WHERE key=?",
                (str(SCHEMA_VERSION), _VERSION_KEY),
            )
            logger.info(
                "upgraded state database schema %d -> %d (additive only)",
                version, SCHEMA_VERSION,
            )
    except Exception:
        conn.close()
        raise
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def assert_halt_flags(ops) -> None:
    """Constraint 14 is not a preference, so its two settings flags are not
    switches -- they are assertions. `settings.yaml` carries
    `halt_on_state_decrypt_failure` and `halt_on_db_integrity_failure`; if
    either is ever edited to false, the config disagrees with the code and the
    run stops here instead of somewhere subtler later.
    """
    for name in ("halt_on_state_decrypt_failure", "halt_on_db_integrity_failure"):
        if getattr(ops, name) is not True:
            raise StateError(
                f"ops.{name} is false. Constraint 14 forbids continuing past a state "
                "failure; this flag cannot be turned off."
            )


def persist_signal_history(conn, history, *, run_key, observed_at, raw_records=(),
                           calendar=None, source_snapshots=None):
    """Atomic offline state/evidence/checkpoint write. A replay is a no-op.

    Reusing a run key with different state or provenance halts. Older runs
    cannot overwrite newer state. No clock or classifier is consulted here.
    Source snapshots must include configured tier/group, not today's config
    substituted later on reload.
    """
    from agent.memory import signal_models as sm
    from agent.memory.models import to_utc_iso
    from agent.pipeline.signal_history import occurrence_key

    stamp = to_utc_iso(observed_at)
    if not run_key or stamp is None:
        raise StateError("signal run requires key and observation timestamp")
    sources = source_snapshots or {}
    header = sm.history_header(history)
    cal = sm.calendar_payload(calendar)
    baselines = {k: (v, history.baseline_evidence[k])
                 for k, v in history.baselines.items()}
    raw_records = tuple(sorted(raw_records, key=lambda r: r.key))
    payload = sm.pack((header, history.records, history.ledger, baselines, cal,
                       raw_records_payload(raw_records), sources))
    state_hash = sm.digest(payload)
    # Validate before beginning a transaction; even a failed input is harmless.
    for key, record in history.records.items():
        if occurrence_key(record.candidate) != key:
            raise StateError("corrupt occurrence key")
        for item in record.items.values():
            if item.source_id not in sources:
                raise StateError(f"missing source snapshot: {item.source_id}")
    for raw in raw_records:
        if raw.run_key != run_key or to_utc_iso(raw.observed_at) != stamp:
            raise StateError("raw record run/observation mismatch")
        if not all((raw.input_hash, raw.prompt_version, raw.model_version,
                    raw.provider, raw.schema_version)):
            raise StateError("raw record missing provenance version")
        if any(i.source_id not in raw.sources for i in raw.items.values()):
            raise StateError("raw record missing source snapshot")
    conn.execute("SAVEPOINT signal_write")
    try:
        old = conn.execute("SELECT state_hash FROM signal_runs WHERE run_key=?",
                           (run_key,)).fetchone()
        if old:
            if old[0] != state_hash:
                raise StateError("conflicting replay of signal run")
            conn.execute("RELEASE signal_write")
            return
        latest = conn.execute("SELECT observed_at FROM signal_history_meta").fetchone()
        if latest and latest[0] > stamp:
            raise StateError("out-of-order signal run")
        for raw in raw_records:
            conn.execute("INSERT INTO signal_raw_candidates VALUES (?, ?, ?, ?, ?)",
                         (raw.key, run_key, raw.cluster_key, stamp, sm.pack(vars(raw))))
            for item_key, item in sorted(raw.items.items()):
                conn.execute("INSERT INTO signal_supports VALUES (?, ?, ?, ?)",
                             (raw.key, item_key, sm.pack(item),
                              sm.pack(raw.sources[item.source_id])))
        for key, record in sorted(history.records.items()):
            e = record.event
            old = conn.execute("SELECT signal_event_id FROM signal_occurrences "
                               "WHERE occurrence_key=?", (key,)).fetchone()
            end = e.state_end_date.isoformat() if e.state_end_date else None
            if old:
                event_id = old[0]
                conn.execute("UPDATE signal_events SET state_ended_at=?, tier=? WHERE id=?",
                             (end, e.source_tier, event_id))
            else:
                event_id = conn.execute(
                    "INSERT INTO signal_events (signal_id, observed_at, state_ended_at, tier) "
                    "VALUES (?, ?, ?, ?)", (e.signal_id, stamp, end, e.source_tier)).lastrowid
            snapshot = {i.source_id: sources[i.source_id] for i in record.items.values()}
            conn.execute(
                "INSERT INTO signal_occurrences VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(occurrence_key) DO UPDATE SET "
                "state_end_date=excluded.state_end_date, "
                "last_confirmed_at=excluded.last_confirmed_at, "
                "last_checked_at=excluded.last_checked_at, observed_at=excluded.observed_at, "
                "payload=excluded.payload",
                (key, event_id, e.event_date.isoformat(), end,
                 record.last_confirmed_at.isoformat(), record.last_checked_at.isoformat(),
                 stamp, sm.pack((record, snapshot))))
        for sid, cycle in sorted(history.ledger.items()):
            conn.execute("INSERT INTO signal_novelty VALUES (?, ?) ON CONFLICT(signal_id) "
                         "DO UPDATE SET payload=excluded.payload", (sid, sm.pack(cycle)))
        for key, (baseline, evidence) in sorted(baselines.items()):
            candidate, items, _ = evidence
            old = conn.execute("SELECT statement_id FROM signal_baselines WHERE baseline_key=?",
                               (key,)).fetchone()
            statement_id = old[0] if old else None
            if statement_id is None:
                source_id = items[candidate.supports[0].item_id].source_id
                statement_id = conn.execute(
                    "INSERT INTO speaker_statements (speaker, source_id, said_at, tone, text_hash) "
                    "VALUES (?, ?, ?, ?, ?)", (baseline.actor, source_id,
                    baseline.observed.isoformat(), baseline.kind, sm.digest(candidate.claim))).lastrowid
            snapshot = {i.source_id: sources[i.source_id] for i in items.values()}
            conn.execute("INSERT INTO signal_baselines VALUES (?, ?, ?, ?) "
                         "ON CONFLICT(baseline_key) DO UPDATE SET observed_day=excluded.observed_day, "
                         "payload=excluded.payload",
                         (key, statement_id, baseline.observed.isoformat(),
                          sm.pack(((baseline, evidence), snapshot))))
        _persist_signal_calendar(conn, cal, stamp)
        conn.execute("INSERT INTO signal_history_meta VALUES (1, ?, ?, ?) "
                     "ON CONFLICT(singleton) DO UPDATE SET header=excluded.header, "
                     "calendar=excluded.calendar, observed_at=excluded.observed_at",
                     (sm.pack(header), sm.pack(cal), stamp))
        conn.execute("INSERT INTO signal_runs VALUES (?, ?, ?)", (run_key, state_hash, stamp))
        conn.execute("RELEASE signal_write")
    except Exception:
        conn.execute("ROLLBACK TO signal_write")
        conn.execute("RELEASE signal_write")
        raise


def raw_records_payload(records):
    return tuple(vars(r) for r in records)


def _persist_signal_calendar(conn, calendar, stamp):
    """Mirror side channels into legacy tables with stable companion keys."""
    from agent.memory.signal_models import digest
    from agent.memory.models import to_utc_iso

    for kind in ("deadlines", "schedules", "statements"):
        for identity, entry in sorted(calendar[kind].items()):
            key = kind + ":" + identity
            old = conn.execute("SELECT schedule_id, statement_id FROM signal_calendar_rows "
                               "WHERE calendar_key=?", (key,)).fetchone()
            if kind == "statements":
                observed, candidate = entry
                if not old:
                    row_id = conn.execute(
                        "INSERT INTO speaker_statements (speaker, source_id, said_at, tone, text_hash) "
                        "VALUES (?, ?, ?, 'soothing', ?)", (candidate.actor,
                        candidate.supports[0].item_id, to_utc_iso(observed),
                        digest(candidate.claim))).lastrowid
                    conn.execute("INSERT INTO signal_calendar_rows VALUES (?, NULL, ?)", (key, row_id))
            else:
                status = "cancelled" if entry.cancelled else "active"
                if old:
                    conn.execute("UPDATE scheduled_events SET due_at=?, status=?, resolved_at=? "
                                 "WHERE id=?", (entry.due.isoformat(), status,
                                 stamp if entry.cancelled else None, old[0]))
                else:
                    row_id = conn.execute(
                        "INSERT INTO scheduled_events (title, kind, due_at, status, first_seen_at) "
                        "VALUES (?, ?, ?, ?, ?)", (entry.candidate.claim, kind,
                        entry.due.isoformat(), status, stamp)).lastrowid
                    conn.execute("INSERT INTO signal_calendar_rows VALUES (?, ?, NULL)", (key, row_id))


def load_signal_history(conn, catalog):
    """Load required state; absence is an error, never an implicit new ledger.

    Returns (history, calendar, source snapshots). A new empty history must be
    constructed explicitly by the offline caller and persisted once.
    """
    from agent.memory import signal_models as sm
    from agent.pipeline.signal_calendar import SignalCalendar

    try:
        row = conn.execute("SELECT header, calendar FROM signal_history_meta WHERE singleton=1").fetchone()
        if row is None:
            raise StateError("missing required signal history")
        header = sm.unpack(row[0])
        if header["catalog"] != frozenset(catalog):
            raise StateError("signal history catalog mismatch")
        records, sources, baselines = {}, {}, {}
        for r in conn.execute("SELECT occurrence_key, payload FROM signal_occurrences ORDER BY occurrence_key"):
            records[r[0]], snapshot = sm.unpack(r[1])
            sources.update(snapshot)
        ledger = {r[0]: sm.unpack(r[1]) for r in conn.execute("SELECT signal_id, payload FROM signal_novelty")}
        for r in conn.execute("SELECT baseline_key, payload FROM signal_baselines ORDER BY baseline_key"):
            baselines[r[0]], snapshot = sm.unpack(r[1])
            sources.update(snapshot)
        history = sm.restore_history(header, records, ledger, baselines)
        calendar = SignalCalendar()
        calendar.__dict__.update(sm.unpack(row[1]))
        return history, calendar, sources
    except StateError:
        raise
    except (ValueError, KeyError, TypeError, sqlite3.DatabaseError) as exc:
        raise StateError(f"corrupt required signal history: {exc}") from exc
