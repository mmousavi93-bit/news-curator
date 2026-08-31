"""The flash monitor entry point: fetch fast subset -> match -> burst
policy -> send -> persist. ZERO LLM calls.

    python -m agent.flash.run_flash --db flash.db
    python -m agent.flash.run_flash --db flash.db --dry-run
    python -m agent.flash.run_flash --system-down

--dry-run: live fetch + match, writes a tuning CSV, sends NOTHING and
writes NO state — the iterate-until-good loop (owner 2026-08-30):
dispatch from CI, read matches, tune config/flash_alert.yaml.
--system-down: sends the failure notice and exits 1 — the workflow calls
it when decrypt/state fails (constraint 14: halt AND alert).

The workflow (flash-alert.yml) owns the age decrypt/encrypt and the
flash-state branch push; this module owns everything between.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from agent.collectors import registry
from agent.config import ConfigError, load_all
from agent.delivery.telegram import TelegramClient
from agent.flash import history, store
from agent.flash.config import FlashConfig
from agent.flash.loader import validate_flash
from agent.flash.matcher import match_items
from agent.flash.policy import evaluate
from agent.util.logging import PROCESS_FILTER, get_logger, register_env_secrets

_SYSTEM_DOWN_FALLBACK = (
    "⚠️ سیستم هشدار از کار افتاده است (رمزگشایی state ناموفق یا خطای بحرانی). "
    "تا رفع مشکل، به مرور اخبار عادی تکیه کنید."
)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="agent.flash.run_flash")
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--config-dir", type=Path, default=Path("config"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--system-down", action="store_true")
    return parser.parse_args(argv)


def _load_flash(config_dir: Path) -> FlashConfig:
    path = config_dir / "flash_alert.yaml"
    if not path.exists():
        raise ConfigError(f"flash_alert.yaml missing at {path}")
    return validate_flash(yaml.safe_load(path.read_text(encoding="utf-8")))


def _client() -> TelegramClient:
    # FLASH_CHANNEL_ID overrides, TELEGRAM_CHANNEL_ID is the fallback —
    # owner moves the alert channel by adding ONE secret, no code change
    # (owner decision 2026-08-30: same channel for now).
    return TelegramClient.from_env(
        os.environ, transport=None, channel_env_var="FLASH_CHANNEL_ID"
    )


def _system_down(config_dir: Path) -> int:
    try:
        flash = _load_flash(config_dir)
        text = flash.templates["system_down"]
    except ConfigError:
        text = _SYSTEM_DOWN_FALLBACK
    result = _client().send(text)
    return 1 if result.ok else 1


def _write_tuning_csv(items, matches, kills, flash: FlashConfig, now: datetime) -> None:
    report_dir = os.environ.get("NEWS_CURATOR_REPORT_DIR")
    if not report_dir:
        return
    out = Path(report_dir) / f"flash_{now:%Y%m%dT%H%M%SZ}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["run_at_utc", "class", "term_bucket", "location_ring",
                         "location_token", "source_id", "title", "kind"])
        for match in matches:
            writer.writerow([now.isoformat(), match.class_name, match.term_bucket,
                             match.location_ring, match.location_token,
                             match.item.source_id, match.item.title, "match"])
        for source_id, reason in kills:
            writer.writerow([now.isoformat(), "", "", "", "", source_id, "", reason])
    get_logger("agent.flash").info("flash: tuning CSV -> %s", out)


def _run(args: argparse.Namespace) -> int:
    logger = get_logger("agent.flash")
    register_env_secrets(PROCESS_FILTER, os.environ)
    if args.system_down:
        return _system_down(args.config_dir)
    try:
        config = load_all(base=args.config_dir)
        flash = _load_flash(args.config_dir)
    except ConfigError as exc:
        logger.error("flash: config load failed: %s", exc)
        return 1
    if not flash.enabled:
        logger.info("flash: disabled in flash_alert.yaml — nothing to do")
        return 0

    try:
        sources = registry.load_sources(base=args.config_dir)
    except registry.SourcesError as exc:
        logger.error("flash: %s", exc)
        return 1
    by_id = {s.id: s for s in sources}
    missing = [sid for sid in flash.flash_source_ids if sid not in by_id]
    if missing:
        logger.error("flash: flash_source_ids not in sources.yaml: %s", missing)
        return 1
    subset = [by_id[sid] for sid in flash.flash_source_ids]

    now = datetime.now(timezone.utc)
    report = registry.collect_all(subset, config.settings, now)
    items = [item for res in report.results.values() for item in res.items]

    if args.dry_run:
        matches, kills = match_items(items, flash, now)
        _write_tuning_csv(items, matches, kills, flash, now)
        logger.info("flash: dry-run fetched=%d matched=%d killed=%d (no sends)",
                    len(items), len(matches), len(kills))
        return 0

    if args.db is None:
        logger.error("flash: --db is required (no dry-run)")
        return 1
    try:
        conn = store.open_flash_db(args.db, create_if_absent=not args.db.exists())
    except Exception as exc:
        # A decryptable-but-corrupt DB: halt AND alert — never re-encrypt
        # over the last good copy (reviewer finding 2026-08-31).
        logger.error("flash: cannot open state DB: %s", exc)
        _client().send(flash.templates["system_down"])
        return 1
    try:
        hashes = {history.url_hash(i.url) for i in items}
        known = history.known_urls(conn, hashes)
        new_items = [i for i in items if history.url_hash(i.url) not in known]
        matches, kills = match_items(new_items, flash, now)
        stats = evaluate(matches, conn, flash, now, _client().send, logger)
        # mark_seen AFTER evaluate: a crash mid-alert must re-fire the
        # item next tick, not vanish it (reviewer finding 2026-08-31).
        history.mark_seen(conn, hashes, now)
        pruned = store.prune(conn, now)
        _write_tuning_csv(new_items, matches, kills, flash, now)
        logger.info(
            "flash: fetched=%d new=%d matched=%d sent=%d open=%d pruned=%d",
            len(items), len(new_items), len(matches), stats["sent"],
            stats["open_bursts"], sum(pruned),
        )
    finally:
        conn.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    return _run(_parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
