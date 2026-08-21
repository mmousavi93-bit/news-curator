"""Pipeline entrypoint.

Builds a RunContext, sequences STAGES in declared order, and emits exactly one
structured summary line. A stage that raises must fail the run loudly -- no
`try/except` around the stage loop; that is deliberate, not an oversight.

`now` is injected once here and threaded through RunContext. Nothing else in
the codebase may call datetime.now() -- once the risk engine exists (Phase 8),
a stray clock read makes scores irreproducible, and it is far cheaper to forbid
it here than to hunt it down later.
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
from agent.pipeline import STAGES, Stage
from agent.pipeline._noop import NoopStage
from agent.util.logging import PROCESS_FILTER, get_logger, register_env_secrets


@dataclass
class RunContext:
    config: Config
    dry_run: bool
    now: datetime
    counters: dict[str, int] = field(default_factory=dict)


def _build_stages() -> tuple[Stage, ...]:
    # Phase 1: every stage is a no-op. Later phases swap entries here for real
    # implementations, one at a time, without changing this function's shape.
    return tuple(NoopStage(name) for name in STAGES)


def run_pipeline(ctx: RunContext, stages: Sequence[Stage], logger: logging.Logger) -> None:
    """Execute `stages` in order against `ctx`, then emit one summary line.
    Pure given its inputs -- no clock reads, no global state -- so tests can
    call it twice with the same `ctx.now` and expect identical output."""
    for stage in stages:
        stage.run(ctx)

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
    parser.add_argument("--db", type=Path, default=None, help="persist collected items here")
    # Explicit, never a fallback: an absent db may mean a FAILED decrypt (Ph. 7).
    parser.add_argument("--init-db", action="store_true")
    parser.add_argument("--config-dir", type=Path, default=None)
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args(argv)


def _sample_message() -> Message:
    """Fixed message for --send-test. Never invents real content (constraint
    11) -- it is explicitly labelled a diagnostic, not news."""
    return Message(
        header="News Curator -- send-test diagnostic",
        items=(
            Item(
                headline="This is a --send-test message, not a real alert.",
                priority=0,
                detail="It proves the delivery path works end to end.",
            ),
        ),
        footer="agent.run --send-test",
    )


def run_send_test(logger: logging.Logger) -> int:
    """Build the fixed sample message and send it -- through the mock
    transport when credentials are absent, for real when they are present.
    Never raises: a malformed TELEGRAM_CHANNEL_ID or a failed send is
    reported and this still returns 0, because --send-test's job is to show
    the owner what happened, not to crash on them."""
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
    """Fetches every ENABLED source, prints the raw/parsed/kept table, writes
    the JSON report collect-test.yml asserts against, and -- with `--db`
    (Phase 4) -- persists what survived dedup and prunes aged rows. The
    credibility join is checked before anything is fetched: a missing id halts
    loudly rather than degrading to `tier 3, group unlisted`."""
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

    ctx = RunContext(config=config, dry_run=args.dry_run, now=datetime.now(timezone.utc))
    run_pipeline(ctx, _build_stages(), logger)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
