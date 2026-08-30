"""Pipeline entrypoint: RunContext -> stage sequence -> one summary line.
A stage that raises fails the run loudly. `now` is injected here only.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from agent.collectors import registry, report
from agent.config import Config, ConfigError, load_all
from agent.memory import dedup, retention
from agent.memory import db as memory_db
from agent.delivery.credentials import TelegramConfigError
from agent.delivery.formatter import format_single
from agent.delivery.message import Item, Message
from agent.delivery.telegram import SendResult, TelegramClient
from agent.pipeline import Stage, build_stages
from agent.util.logging import PROCESS_FILTER, get_logger, register_env_secrets


@dataclass
class RunContext:
    config: Config
    dry_run: bool
    now: datetime
    counters: dict[str, int] = field(default_factory=dict)
    # Pipeline stages read/write these.
    items: list = field(default_factory=list)       # unseen post-dedup
    embeddings: list = field(default_factory=list)  # aligned with items
    clusters: list = field(default_factory=list)    # priority-ordered
    events: list = field(default_factory=list)      # validated events (main)
    lead_events: list = field(default_factory=list) # lead-only (never main)
    router: object | None = None                    # mock in dry-run
    embedder: object | None = None                  # Embedder protocol
    db: object | None = None                        # sqlite3 conn
    daily_digest: bool = False                      # 07:00 canonical run
    leads_channel_id: str | None = None             # optional lead channel
    report_dir: Path | None = None                  # CSV observability output


def run_pipeline(ctx: RunContext, stages: Sequence[Stage], logger: logging.Logger) -> None:
    """Execute `stages` in order against `ctx`, then write the observability
    CSVs and emit one summary line. Pure given its inputs -- no clock reads,
    no global state -- so tests can call it twice with the same `ctx.now`
    and expect identical output."""
    for stage in stages:
        stage.run(ctx)

    if ctx.report_dir is not None:
        try:
            from agent.report_csv import write_run_reports
            written = write_run_reports(ctx, ctx.report_dir)
            logger.info(
                "reports: wrote %s", ", ".join(p.name for p in written)
            )
        except Exception as exc:  # noqa: BLE001 -- reporting never breaks a run
            logger.error("reports: writing failed: %s", exc)

    logger.info(
        "run summary: items=%d clusters=%d messages=%d",
        ctx.counters.get("collect", 0),
        ctx.counters.get("cluster", 0),
        ctx.counters.get("deliver", 0),
    )


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="agent.run")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--send-test", action="store_true")
    parser.add_argument("--collect-only", action="store_true")
    parser.add_argument("--report-path", type=Path, default=None)
    # Explicit, never a fallback: absent db may mean FAILED decrypt.
    parser.add_argument("--db", type=Path, default=None, help="state db path")
    parser.add_argument("--init-db", action="store_true")
    parser.add_argument("--config-dir", type=Path, default=None)
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args(argv)

def _sample_message() -> Message:
    item = Item(headline="This is a --send-test message, not a real alert.",
                priority=0, detail="It proves the delivery path works end to end.")
    return Message(header="News Curator -- send-test diagnostic",
                   items=(item,), footer="agent.run --send-test")


def run_send_test(logger: logging.Logger) -> int:
    """Send the fixed sample (mock when credentials absent); never raises."""
    text = format_single(_sample_message())
    try:
        client = TelegramClient.from_env(os.environ)
    except TelegramConfigError as exc:
        logger.error("send-test: %s", exc)
        return 0
    try:
        result = client.send(text)
    except Exception as exc:  # noqa: BLE001 -- a delivery failure must never crash the run
        logger.error("send-test: send() raised unexpectedly: %s", exc)
        result = SendResult(ok=False, status_code=None, description="unexpected exception", attempts=0)
    if result.mocked:
        logger.info("send-test: mock mode (no credentials set), nothing sent. Would have sent:\n%s", text)
    elif result.ok:
        logger.info("send-test: sent (status=%s, attempts=%d)", result.status_code, result.attempts)
    else:
        logger.error(
            "send-test: send failed (status=%s, description=%s, attempts=%d)",
            result.status_code, result.description, result.attempts,
        )
    return 0


def run_collect_only(
    config: Config, now: datetime, logger: logging.Logger,
    report_path: Path | None, config_dir: Path | None,
    db_path: Path | None = None, init_db: bool = False,
) -> int:
    """Fetch every enabled source, print the table, write the gate report,
    and (with --db) persist dedup-survivors + prune. Credibility join is
    checked BEFORE fetching: a missing id halts loudly."""
    try:
        sources = registry.load_sources(base=config_dir)
    except registry.SourcesError as exc:
        logger.error("collect-only: %s", exc)
        return 1
    try:
        registry.validate_join(sources, config.credibility)
    except registry.SourcesError as exc:
        logger.error("collect-only: %s", exc)
        return 1

    collect_report = registry.collect_all(sources, config.settings, now)
    sources_by_id = {s.id: s for s in sources}
    print(report.format_table(collect_report, sources_by_id))

    if report_path is not None:
        report_path.write_text(json.dumps(report.build_json_report(collect_report, sources_by_id), indent=2))
        logger.info("collect-only: wrote report to %s", report_path)

    if db_path is not None:
        items = [item for res in collect_report.results.values() for item in res.items]
        try:
            memory_db.assert_halt_flags(config.settings.ops)
            with closing(memory_db.open_db(db_path, create_if_absent=init_db)) as conn:
                stored = dedup.store_new(conn, items, now)
                pruned = retention.prune(conn, config.settings.retention, now)
        except memory_db.StateError as exc:
            logger.error("collect-only: %s", exc)
            return 1
        # Per layer, not a total: a jump in norm-url against a flat url column is
        # what a layer-2 over-match looks like from outside. A total hides it.
        logger.info("storage: collected=%d new=%d dup(url/norm/title)=%d/%d/%d pruned=%d",
                    len(items), len(stored.new), stored.by_url, stored.by_norm_url,
                    stored.by_title, sum(pruned.values()))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    logger = get_logger("agent.run")
    logger.setLevel(args.log_level.upper())
    register_env_secrets(PROCESS_FILTER, os.environ)

    if args.send_test:
        return run_send_test(logger)

    try:
        config = load_all(base=args.config_dir)
    except ConfigError as exc:
        logger.error("config load failed: %s", exc)
        return 1

    if args.collect_only:
        return run_collect_only(
            config, datetime.now(timezone.utc), logger, args.report_path,
            args.config_dir, args.db, args.init_db,
        )

    stages, router, embedder = build_stages(
        config, os.environ, base=args.config_dir, force_mock=args.dry_run
    )
    db_conn = None
    if args.db is not None:
        try:
            memory_db.assert_halt_flags(config.settings.ops)
            db_conn = memory_db.open_db(args.db, create_if_absent=args.init_db)
        except memory_db.StateError as exc:
            logger.error("run: %s", exc)
            return 1
    ctx = RunContext(
        config=config, dry_run=args.dry_run, now=datetime.now(timezone.utc),
        router=router, embedder=embedder, db=db_conn,
        daily_digest=os.environ.get("NEWS_CURATOR_DIGEST") == "true",
        leads_channel_id=os.environ.get("TELEGRAM_LEADS_CHANNEL_ID") or None,
        # CSV observability output (owner download from the Actions
        # artifacts). Set in the workflow; absent in tests -> no writes.
        report_dir=Path(os.environ["NEWS_CURATOR_REPORT_DIR"]) if "NEWS_CURATOR_REPORT_DIR" in os.environ else None,
    )
    try:
        run_pipeline(ctx, stages, logger)
    finally:
        if db_conn is not None:
            db_conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
