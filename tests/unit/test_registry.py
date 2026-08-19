from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent.collectors import registry
from agent.collectors.base import Item, SourceResult, SourceSpec
from agent.config import SourceCredibility
from agent.settings import Settings

FIXTURES_SETTINGS = Path(__file__).parent.parent / "fixtures" / "settings_minimal.yaml"


def _settings() -> Settings:
    import yaml
    return Settings.from_dict(yaml.safe_load(FIXTURES_SETTINGS.read_text(encoding="utf-8")))


def _write_sources(tmp_path: Path, body: str) -> Path:
    (tmp_path / "sources.yaml").write_text(body, encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# load_sources
# ---------------------------------------------------------------------------

def test_load_sources_parses_valid_rows(tmp_path):
    _write_sources(tmp_path, """
version: 1
sources:
  - id: a
    name: A
    url: https://a.test/feed
    type: rss
    lang: en
    enabled: true
  - id: b
    name: B
    url: https://t.me/s/b
    type: telegram
    lang: en
    enabled: false
    max_items: 5
""")
    sources = registry.load_sources(base=tmp_path)
    assert [s.id for s in sources] == ["a", "b"]
    assert sources[0].enabled is True
    assert sources[1].enabled is False
    assert sources[1].max_items == 5


def test_load_sources_rejects_non_https_url_even_when_disabled(tmp_path):
    # Requirement: the https:// assertion covers EVERY row, enabled or not --
    # `mee` was plaintext http:// through four probe rounds while disabled.
    _write_sources(tmp_path, """
version: 1
sources:
  - id: plaintext_source
    name: Plaintext
    url: http://insecure.test/feed
    type: rss
    lang: en
    enabled: false
""")
    with pytest.raises(registry.SourcesError, match="plaintext_source"):
        registry.load_sources(base=tmp_path)


def test_load_sources_collects_multiple_errors_before_raising(tmp_path):
    _write_sources(tmp_path, """
version: 1
sources:
  - id: bad_one
    name: Bad
    url: http://insecure.test/feed
    type: rss
    lang: en
    enabled: false
  - name: missing_id
    url: https://ok.test/feed
    type: rss
    lang: en
    enabled: false
""")
    with pytest.raises(registry.SourcesError) as excinfo:
        registry.load_sources(base=tmp_path)
    message = str(excinfo.value)
    assert "bad_one" in message
    assert "missing 'id'" in message


# ---------------------------------------------------------------------------
# validate_join
# ---------------------------------------------------------------------------

def test_validate_join_passes_when_every_id_is_known():
    sources = [SourceSpec(id="a", name="A", url="https://a.test", type="rss", lang="en", enabled=True)]
    credibility = {"a": SourceCredibility(tier=2, group="g")}
    registry.validate_join(sources, credibility)  # must not raise


def test_validate_join_raises_naming_a_missing_id_on_a_disabled_entry():
    # Requirement 1, explicit: the join check covers ALL 51 entries, not just
    # the 10 enabled -- a typo in a staged (disabled) row must not survive
    # silently until Phase 8 flips its flag and it degrades to tier 3/unlisted.
    sources = [
        SourceSpec(id="known", name="Known", url="https://a.test", type="rss", lang="en", enabled=True),
        SourceSpec(id="typo_id", name="Typo", url="https://b.test", type="rss", lang="en", enabled=False),
    ]
    credibility = {"known": SourceCredibility(tier=2, group="g")}
    with pytest.raises(registry.SourcesError, match="typo_id"):
        registry.validate_join(sources, credibility)


# ---------------------------------------------------------------------------
# collect_all / _collect_one -- fetch and dispatch are monkeypatched so this
# tests registry's own orchestration (per-source isolation, host bucketing,
# aggregation), not rss.py/telegram_web.py's parsing.
# ---------------------------------------------------------------------------

def _item(source_id: str, dt) -> Item:
    return Item(source_id=source_id, url=f"https://x/{source_id}", title="t", body="b",
                published_at=dt, lang="en", raw_hash="h" * 8)


def test_collect_all_isolates_a_fetch_failure_from_other_sources(monkeypatch):
    good = SourceSpec(id="good", name="Good", url="https://good.test/feed", type="rss", lang="en", enabled=True)
    bad = SourceSpec(id="bad", name="Bad", url="https://bad.test/feed", type="rss", lang="en", enabled=True)

    def fake_fetch(url, *, user_agent, timeout_seconds):
        if "bad" in url:
            raise registry.fetch.FetchError("simulated: HTTP 500")
        from agent.collectors.fetch import FetchResult
        return FetchResult(status_code=200, body=b"<rss></rss>", content_type="application/rss+xml")

    def fake_rss_collect(spec, raw, content_type, max_items):
        return SourceResult(source_id=spec.id, raw_entries=1, parsed=1, kept=1,
                             items=[_item(spec.id, datetime(2026, 8, 1, tzinfo=timezone.utc))])

    monkeypatch.setattr(registry.fetch, "fetch", fake_fetch)
    monkeypatch.setattr(registry, "_DISPATCH", {"rss": fake_rss_collect, "telegram": fake_rss_collect})

    report = registry.collect_all([good, bad], _settings(), datetime.now(timezone.utc))

    assert report.results["good"].kept == 1
    assert report.results["good"].error is None
    assert report.results["bad"].kept == 0
    assert "500" in report.results["bad"].error
    assert report.total_kept == 1
    assert report.sources_with_items == 1
    assert report.sources_enabled == 2


def test_collect_all_only_dispatches_enabled_sources(monkeypatch):
    enabled = SourceSpec(id="on", name="On", url="https://on.test/feed", type="rss", lang="en", enabled=True)
    disabled = SourceSpec(id="off", name="Off", url="https://off.test/feed", type="rss", lang="en", enabled=False)

    calls = []

    def fake_fetch(url, *, user_agent, timeout_seconds):
        calls.append(url)
        from agent.collectors.fetch import FetchResult
        return FetchResult(status_code=200, body=b"", content_type="application/rss+xml")

    def fake_collect(spec, raw, content_type, max_items):
        return SourceResult(source_id=spec.id, raw_entries=0, parsed=0, kept=0, items=[])

    monkeypatch.setattr(registry.fetch, "fetch", fake_fetch)
    monkeypatch.setattr(registry, "_DISPATCH", {"rss": fake_collect})

    report = registry.collect_all([enabled, disabled], _settings(), datetime.now(timezone.utc))

    assert calls == ["https://on.test/feed"]
    assert "off" not in report.results
    assert report.sources_enabled == 1


def test_collect_one_records_unknown_source_type_as_an_error(monkeypatch):
    spec = SourceSpec(id="mystery", name="Mystery", url="https://x.test/feed", type="carrier_pigeon",
                       lang="en", enabled=True)
    result = registry._collect_one(spec, _settings())
    assert result.kept == 0
    assert "unknown source type" in result.error


def test_collect_one_records_a_parse_exception_without_raising(monkeypatch):
    spec = SourceSpec(id="flaky", name="Flaky", url="https://x.test/feed", type="rss", lang="en", enabled=True)

    def fake_fetch(url, *, user_agent, timeout_seconds):
        from agent.collectors.fetch import FetchResult
        return FetchResult(status_code=200, body=b"<rss></rss>", content_type="application/rss+xml")

    def exploding_collect(spec, raw, content_type, max_items):
        raise RuntimeError("boom")

    monkeypatch.setattr(registry.fetch, "fetch", fake_fetch)
    monkeypatch.setattr(registry, "_DISPATCH", {"rss": exploding_collect})

    result = registry._collect_one(spec, _settings())
    assert result.kept == 0
    assert "boom" in result.error
