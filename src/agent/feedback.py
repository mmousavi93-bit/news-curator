"""Feedback harvest entrypoint: `python -m agent.feedback --db state.db`.

Runs once per pipeline, after the digest is sent and before state is
re-encrypted. Collects the owner's Telegram feedback (reactions on digest
messages + DMs to the bot) via getUpdates, writes feedback_<ts>.csv next to
the other observability CSVs, and persists the getUpdates offset in the state
DB's `meta` table so no update is read twice.

Read-only (owner decision 2026-09-13): this module never changes a scoring,
clustering, or pipeline threshold. It harvests; the tuning happens by hand
from the CSV it writes.

Why `meta` and not a schema bump: the offset is a single integer, and `meta`
is already the state DB's generic key/value table (it stores schema_version).
A dedicated table + SCHEMA_VERSION migration for one integer would be ceremony
with no data-migration risk to justify it.
"""

from __future__ import annotations

import argparse
import csv
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from agent.delivery.feedback import FeedbackClient, FeedbackRecord
from agent.memory import db as memory_db
from agent.util.logging import PROCESS_FILTER, get_logger, register_env_secrets

_OFFSET_KEY = "feedback_last_update_id"

_CSV_COLUMNS = (
    "run_at_utc", "update_id", "kind", "chat_id", "chat_title", "user_id",
    "user_name", "message_id", "reactions", "text", "date_utc",
)


@dataclass
class HarvestResult:
    records: list[FeedbackRecord]
    wrote_csv: Path | None
    offset_before: int | None
    offset_after: int | None
    mock: bool


def load_offset(conn: sqlite3.Connection) -> int | None:
    row = conn.execute(
        "SELECT value FROM meta WHERE key = ?", (_OFFSET_KEY,)
    ).fetchone()
    if row is None:
        return None
    try:
        return int(row[0])
    except (TypeError, ValueError):
        return None


def save_offset(conn: sqlite3.Connection, offset: int) -> None:
    # autocommit (isolation_level=None in memory.db) -- no explicit commit.
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
        (_OFFSET_KEY, str(offset)),
    )


def _iso_utc(unix: int) -> str:
    if unix <= 0:
        return ""
    try:
        return datetime.fromtimestamp(unix, timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return ""


def write_feedback_csv(
    records: list[FeedbackRecord], report_dir: Path, now: datetime
) -> Path:
    path = report_dir / f"feedback_{now.strftime('%Y%m%dT%H%M%SZ')}.csv"
    run_at = now.astimezone(timezone.utc).isoformat()
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=_CSV_COLUMNS)
        writer.writeheader()
        for record in records:
            writer.writerow({
                "run_at_utc": run_at,
                "update_id": record.update_id,
                "kind": record.kind,
                "chat_id": record.chat_id,
                "chat_title": record.chat_title,
                "user_id": "" if record.user_id is None else record.user_id,
                "user_name": record.user_name,
                "message_id": "" if record.message_id is None else record.message_id,
                "reactions": record.reactions,
                "text": record.text,
                "date_utc": _iso_utc(record.date),
            })
    return path


def harvest(
    client: FeedbackClient,
    conn: sqlite3.Connection | None,
    report_dir: Path | None,
    now: datetime,
) -> HarvestResult:
    offset = load_offset(conn) if conn is not None else None
    records, next_offset = client.fetch(offset)

    wrote_csv = None
    if report_dir is not None and records:
        report_dir.mkdir(parents=True, exist_ok=True)
        wrote_csv = write_feedback_csv(records, report_dir, now)

    if conn is not None and next_offset is not None and next_offset != offset:
        save_offset(conn, next_offset)

    return HarvestResult(
        records=records,
        wrote_csv=wrote_csv,
        offset_before=offset,
        offset_after=next_offset,
        mock=client.mock_mode,
    )


def _parse_args(argv) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="agent.feedback")
    parser.add_argument("--db", type=Path, default=None, help="state db path")
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)
    logger = get_logger("agent.feedback")
    logger.setLevel(args.log_level.upper())
    register_env_secrets(PROCESS_FILTER, os.environ)

    client = FeedbackClient.from_env(os.environ)
    if client.mock_mode:
        logger.info("feedback: mock mode (no TELEGRAM_BOT_TOKEN); nothing to harvest")
        return 0

    conn: sqlite3.Connection | None = None
    if args.db is not None:
        try:
            conn = memory_db.open_db(args.db)
        except memory_db.StateError as exc:
            # Feedback never breaks the run: degrade to harvesting without
            # offset persistence (updates may re-read until state returns).
            logger.warning(
                "feedback: cannot open state db (%s); harvesting without offset persistence",
                exc,
            )
            conn = None

    report_dir = (
        Path(os.environ["NEWS_CURATOR_REPORT_DIR"])
        if "NEWS_CURATOR_REPORT_DIR" in os.environ
        else None
    )

    try:
        result = harvest(client, conn, report_dir, datetime.now(timezone.utc))
        logger.info(
            "feedback: harvested %d record(s) (offset %s -> %s, csv=%s)",
            len(result.records),
            result.offset_before,
            result.offset_after,
            result.wrote_csv.name if result.wrote_csv else None,
        )
    finally:
        if conn is not None:
            conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
