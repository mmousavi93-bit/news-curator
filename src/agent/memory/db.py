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

from agent.util.logging import get_logger

# Bump ONLY together with a migration. An unrecognised version halts.
SCHEMA_VERSION = 1

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")
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
        if version != SCHEMA_VERSION:
            raise StateError(
                f"state database schema version {version} is not {SCHEMA_VERSION}; "
                "this code does not know how to read it. Halting rather than guessing."
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
