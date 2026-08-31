"""Unit tests for flash/run_flash.py — the entry point. Offline:
registry.collect_all is stubbed with canned items, the Telegram client
is a recording fake (mock mode is mandatory). The real flash_alert.yaml
and real config dir are used (same precedent as test_pipeline_understand
loading the real prompt)."""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from agent.collectors import registry
from agent.collectors.base import Item
from agent.delivery.telegram import TelegramClient
from agent.flash import run_flash

_REPO_ROOT = Path(__file__).parent.parent.parent
_CONFIG_DIR = _REPO_ROOT / "config"


class _Res:
    def __init__(self, items):
        self.items = items
        self.error = None
        self.kept = len(items)


class _Report:
    def __init__(self, items):
        self.results = {"tg_tabzlive": _Res(items)}


class _FakeClient:
    def __init__(self) -> None:
        self.sent: list[str] = []

    def send(self, text: str, **kwargs):
        self.sent.append(text)
        return type("R", (), {"ok": True})()


def _larak_item(url="https://t.me/tabzlive/999"):
    now = datetime.now(timezone.utc)
    return Item(source_id="tg_tabzlive", url=url,
                title="حمله آمریکا به جزیره لارک؛ سپاه وعده پاسخ قاطع داد",
                body="منابع از حمله نظامی آمریکا به مواضع ایران خبر می‌دهند.",
                published_at=now, lang="fa", raw_hash="f" * 8)


def _args(**overrides) -> argparse.Namespace:
    defaults = dict(db=None, config_dir=_CONFIG_DIR, dry_run=False,
                    system_down=False)
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _patch(monkeypatch, items, client):
    monkeypatch.setattr(registry, "collect_all",
                        lambda sources, settings, now: _Report(items))
    monkeypatch.setattr(run_flash, "_client", lambda: client)


def test_end_to_end_alert_sent_once_then_deduped(tmp_path, monkeypatch):
    db = tmp_path / "flash.db"
    client = _FakeClient()
    _patch(monkeypatch, [_larak_item()], client)
    assert run_flash._run(_args(db=db)) == 0
    assert len(client.sent) == 1
    assert "افزایش تنش" in client.sent[0]
    assert "شایعه" in client.sent[0]
    # Second run: same URL already seen -> no duplicate alert.
    client2 = _FakeClient()
    _patch(monkeypatch, [_larak_item()], client2)
    assert run_flash._run(_args(db=db)) == 0
    assert client2.sent == []


def test_dry_run_writes_csv_and_sends_nothing(tmp_path, monkeypatch):
    client = _FakeClient()
    _patch(monkeypatch, [_larak_item()], client)
    monkeypatch.setenv("NEWS_CURATOR_REPORT_DIR", str(tmp_path))
    assert run_flash._run(_args(dry_run=True)) == 0
    assert client.sent == []
    csvs = list(tmp_path.glob("flash_*.csv"))
    assert len(csvs) == 1
    assert "match" in csvs[0].read_text(encoding="utf-8-sig")


def test_missing_flash_source_id_fails_loudly(tmp_path, monkeypatch):
    import yaml

    from agent.flash.loader import validate_flash
    raw = yaml.safe_load((_CONFIG_DIR / "flash_alert.yaml").read_text(encoding="utf-8"))
    broken = replace(validate_flash(raw), flash_source_ids=("bogus_source",))
    monkeypatch.setattr(run_flash, "_load_flash", lambda base: broken)
    assert run_flash._run(_args(db=tmp_path / "flash.db")) == 1


def test_system_down_sends_notice_and_exits_1(tmp_path, monkeypatch):
    client = _FakeClient()
    monkeypatch.setattr(run_flash, "_client", lambda: client)
    assert run_flash._run(_args(system_down=True)) == 1
    assert len(client.sent) == 1
    assert "از کار افتاده" in client.sent[0]


def test_flash_channel_env_overrides_and_falls_back():
    # NOTE: the token is long and unique on purpose — register_credentials
    # registers it with the redaction filter with NO length guard, and a
    # short token would redact every matching substring in every later
    # log record (a one-letter "t" poisoned three caplog assertions
    # in-suite, 2026-08-30).
    env = {"TELEGRAM_BOT_TOKEN": "fake-bot-token-1234567890-long",
           "TELEGRAM_CHANNEL_ID": "@mainchannel",
           "FLASH_CHANNEL_ID": "@flashalertchannel"}
    client = TelegramClient.from_env(env, transport=None,
                                     channel_env_var="FLASH_CHANNEL_ID")
    assert client._channel_id == "@flashalertchannel"
    env_no_flash = {"TELEGRAM_BOT_TOKEN": "fake-bot-token-1234567890-long",
                    "TELEGRAM_CHANNEL_ID": "@mainchannel"}
    client2 = TelegramClient.from_env(env_no_flash, transport=None,
                                      channel_env_var="FLASH_CHANNEL_ID")
    assert client2._channel_id == "@mainchannel"
