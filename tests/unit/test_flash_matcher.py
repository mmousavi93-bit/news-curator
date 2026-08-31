"""Unit tests for flash/matcher.py — the scorecard (owner 2026-08-30:
"design, implement, score, iterate till good"). The scenario fixtures
are the Larak-attack class (US strike on Iran territory, 2026-08-30),
the Tehran-explosion class, and the noise classes that must NOT fire.
Includes a regression over the real 18:54 quiet-day items: zero matches
on a day with no Tehran explosion and no Iran strike."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from agent.collectors.base import Item
from agent.config import ConfigError
from agent.flash.loader import validate_flash
from agent.flash.matcher import match_items, normalize

_REPO_ROOT = Path(__file__).parent.parent.parent
_CONFIG = validate_flash(
    yaml.safe_load((_REPO_ROOT / "config" / "flash_alert.yaml")
                   .read_text(encoding="utf-8"))
)
NOW = datetime(2026, 8, 30, 18, 0, tzinfo=timezone.utc)


def _item(url: str, title: str, body: str = "", *, source_id: str = "tg_src",
          published_at: datetime | None = NOW, date_only: bool = False,
          lang: str = "fa") -> Item:
    return Item(source_id=source_id, url=url, title=title, body=body,
                published_at=published_at, lang=lang, raw_hash="f" * 8,
                date_only=date_only)


def _matches(items, now=NOW):
    return match_items(items, _CONFIG, now)[0]


# -- config shape ---------------------------------------------------------


def test_real_config_loads_two_classes():
    assert set(_CONFIG.classes) == {"tehran", "escalation"}
    assert _CONFIG.classes["escalation"].label == "افزایش تنش"
    assert _CONFIG.max_alerts_per_hour == 3


def test_malformed_terms_fail_loudly():
    # Bucket names are ARBITRARY by design (classes define their own
    # buckets) — the strictness is on shape, not vocabulary.
    raw = yaml.safe_load((_REPO_ROOT / "config" / "flash_alert.yaml")
                         .read_text(encoding="utf-8"))
    raw["classes"]["escalation"]["terms"] = "not a mapping"
    with pytest.raises(ConfigError):
        validate_flash(raw)


def test_missing_placeholder_fails_loudly():
    raw = yaml.safe_load((_REPO_ROOT / "config" / "flash_alert.yaml")
                         .read_text(encoding="utf-8"))
    raw["templates"]["followup"] = "no placeholders here"
    with pytest.raises(ConfigError):
        validate_flash(raw)


# -- escalation class: the Larak scenario --------------------------------


def test_larak_en_strike_fires_escalation():
    matches = _matches([_item(
        "https://x/1",
        "US forces strike two Iranian launchers on Iran's Larak island: Axios",
        "US struck Iranian launchers on the Strait of Hormuz.",
        source_id="osint613_a", lang="en",
    )])
    assert len(matches) == 1
    m = matches[0]
    assert m.class_name == "escalation"
    assert m.term_bucket == "strike"
    assert m.location_ring == "iran_geo"
    # Most specific token in the ring: the Hormuz phrase beats "larak".
    assert m.location_token == "strait of hormuz"


def test_larak_fa_strike_fires_escalation():
    matches = _matches([_item(
        "https://x/2",
        "حمله آمریکا به جزیره لارک؛ ادعای هدف قرار گرفتن سکوهای موشکی ایران",
        "منابع خبری از حمله نظامی آمریکا به مواضع ایران در تنگه هرمز خبر می‌دهند.",
    )])
    assert len(matches) == 1
    assert matches[0].signature == "escalation|strike|iran_geo"


def test_larak_ar_strike_fires_escalation():
    matches = _matches([_item(
        "https://x/3",
        "أمريكا تهاجم مواقع إيرانية في لارك لمنع تلغيم هرمز",
        "الضربة الأمريكية استهدفت قاذفتين إيرانيتين قرب مضيق هرمز.",
        source_id="al_manar", lang="ar",
    )])
    assert len(matches) == 1
    assert matches[0].class_name == "escalation"


def test_irgc_response_threat_fires_escalation():
    matches = _matches([_item(
        "https://x/4",
        "سپاه پاسداران: به آمریکا پاسخ قاطع خواهیم داد",
        "فرمانده کل سپاه در واکنش به حمله به لارک گفت پاسخ قاطع خواهیم داد.",
    )])
    assert len(matches) == 1
    m = matches[0]
    assert m.term_bucket == "strike"  # bucket precedence: strike > threat
    # Ring precedence: iran_geo (the target) beats actors (the speaker).
    assert m.location_ring == "iran_geo"
    assert m.location_token == "لارک"


def test_pure_response_threat_without_location_falls_to_actors():
    matches = _matches([_item(
        "https://x/4b",
        "سپاه پاسداران: به آمریکا پاسخ قاطع خواهیم داد",
        "",
    )])
    assert len(matches) == 1
    m = matches[0]
    assert m.term_bucket == "response_threat"
    assert m.location_ring == "actors"
    # Longest token in the actors ring: پاسداران (8) beats آمریکا (6).
    assert m.location_token == "پاسداران"


def test_strike_outside_iran_territory_is_killed():
    matches, kills = match_items([_item(
        "https://x/5",
        "Airstrike hits positions in Ukraine overnight",
        "missile attack on ukrainian positions",
        source_id="tg_src", lang="en",
    )], _CONFIG, NOW)
    assert matches == []
    # Term hits in BOTH classes, location in neither: two near-miss
    # records (tehran attack_air via "missile", escalation via "strike").
    assert kills == [("tg_src", "tehran:no_location"),
                     ("tg_src", "escalation:no_location")]


# -- tehran class ---------------------------------------------------------


def test_tehran_explosion_fires_tehran():
    matches = _matches([_item(
        "https://x/6",
        "انفجار مهیب در تهرانپارس؛ صدای انفجار شنیده شد",
        "شاهدان از شنیده شدن صدای انفجار در شرق تهران خبر می‌دهند.",
    )])
    assert len(matches) == 1
    assert matches[0].signature == "tehran|explosion|city"


def test_karaj_explosion_fires_region_ring():
    matches = _matches([_item(
        "https://x/7",
        "انفجار در کرج؛ جزئیات در حال تکمیل است",
    )])
    assert len(matches) == 1
    assert matches[0].location_ring == "region"
    assert matches[0].location_token == "کرج"


def test_tehran_item_wins_class_precedence_over_escalation():
    # «حمله هوایی» is escalation-strike; «انفجار» in Tehran is tehran.
    # Classes are checked in file order: tehran first, tehran wins.
    matches = _matches([_item(
        "https://x/8",
        "حمله هوایی و انفجار در تهران گزارش شد",
    )])
    assert len(matches) == 1
    assert matches[0].class_name == "tehran"


# -- war-pattern buckets (WAR_SIGNALS_PAPER taxonomy, owner 2026-08-31) --


def test_maritime_vessel_attack_fires_escalation():
    # The July 2026 resumption law: every US strike wave followed an
    # Iranian vessel attack by 24-72h. Maritime incidents lead the
    # escalation buckets.
    matches = _matches([_item(
        "https://x/m1",
        "حمله به کشتی تجاری در تنگه هرمز؛ خدمه نجات یافتند",
        "منابع از حمله دریایی به یک شناور تجاری در نزدیکی تنگه هرمز خبر می‌دهند.",
    )])
    assert len(matches) == 1
    assert matches[0].signature == "escalation|maritime|iran_geo"
    assert matches[0].location_token == "تنگه هرمز"


def test_posture_carrier_movement_fires_escalation():
    matches = _matches([_item(
        "https://x/m2",
        "ناوهواپیمابر لینکلن وارد خلیج فارس شد",
        "اعزام ناو هواپیمابر آمریکایی به منطقه.",
    )])
    assert len(matches) == 1
    m = matches[0]
    assert m.term_bucket == "posture"
    assert m.location_token == "خلیج فارس"


def test_apparatus_evacuation_fires_escalation():
    # Category B: apparatus protection — the shortest lead (0-3 days).
    matches = _matches([_item(
        "https://x/m3",
        "تخلیه سفارت آمریکا در تهران آغاز شد",
    )])
    assert len(matches) == 1
    assert matches[0].signature == "escalation|apparatus|iran_geo"


def test_ultimatum_fires_escalation():
    matches = _matches([_item(
        "https://x/m4",
        "ترامپ اولتیماتوم داد: ۴۸ ساعت فرصت دارید",
    )])
    assert len(matches) == 1
    assert matches[0].term_bucket == "ultimatum"
    assert matches[0].location_ring == "actors"


# -- noise classes that must NOT fire ------------------------------------


def test_exclusion_kills_film_mention():
    matches = _matches([_item(
        "https://x/9",
        "انفجار در فیلم جدید سینمایی در تهران اکران شد",
    )])
    assert matches == []


def test_exclusion_kills_anniversary_item():
    matches = _matches([_item(
        "https://x/10",
        "مراسم سالگرد انفجار در تهران برگزار شد",
    )])
    assert matches == []


def test_space_variant_fireworks_exclusion_kills():
    # The exclusion carries both «آتش‌بازی» and «آتش بازی»: the space
    # variant survives normalization, the ZWNJ variant does not.
    matches = _matches([_item(
        "https://x/15",
        "انفجار آتش بازی در تهران شنیده شد",
    )])
    assert matches == []


def test_wrong_config_version_fails_loudly():
    raw = yaml.safe_load((_REPO_ROOT / "config" / "flash_alert.yaml")
                         .read_text(encoding="utf-8"))
    raw["version"] = 3
    with pytest.raises(ConfigError):
        validate_flash(raw)


def test_whole_token_location_never_substring_matches():
    # «ری» is in the region ring; «خبری» contains ری but is one token.
    matches = _matches([_item(
        "https://x/11",
        "انفجار خبری مهم",
    )])
    assert matches == []


def test_stale_items_never_fire():
    old = NOW - timedelta(hours=5)
    matches = _matches([_item(
        "https://x/12", "انفجار در تهران", published_at=old,
    )])
    assert matches == []
    matches = _matches([_item(
        "https://x/13", "انفجار در تهران", date_only=True,
    )])
    assert matches == []
    matches = _matches([_item(
        "https://x/14", "انفجار در تهران", published_at=None,
    )])
    assert matches == []


def test_normalization_maps_arabic_letters():
    # Arabic-only «يهاجم» normalizes to «یهاجم»; «إسرائيل» to «اسرائیل».
    assert normalize("إسرائيل") == "اسرائیل"
    assert normalize("تهاجم") == "تهاجم"
    assert normalize("قتال‌ها") == "قتال‌ها".replace("‌", "")


def test_quiet_day_regression_zero_matches():
    # Real titles from the clean 18:54 run 2026-08-30: a day with no
    # Tehran explosion and no Iran strike must produce ZERO alerts.
    items = [
        _item("https://x/q1", "Former Israeli PM Naftali Bennett: We aggressively advanced a plan to kill Yahya Sinwar",
              "planned for summer 2022", source_id="tg_tabzlive", lang="en"),
        _item("https://x/q2", "Maritime analyst Martin Kelly: The IRGC is using small boats to identify ships transiting the SoH",
              "IRGC Monitors Ships in Strait of Hormuz", source_id="osint613_a", lang="en"),
        _item("https://x/q3", "وزیر انرژی اسرائیل: توافقم بشه ما جمهوری اسلامی رو می‌زنیم",
              source_id="tg_tweet_mardomi"),
        _item("https://x/q4", "Israeli artillery fire reported in Hadatha, southern Lebanon",
              source_id="tg_wfwitness", lang="en"),
        _item("https://x/q5", "المستشار الألماني فريدريش ميرتس: يجب علينا المساعدة في إنهاء الحرب ضد إيران",
              source_id="al_manar", lang="ar"),
        _item("https://x/q6", "مدافعان آسمان در کنار مردم به مناسبت فرارسیدن ۱۰ شهریور روز نیروی پدافند هوایی ارتش",
              source_id="tg_iranian_militarism"),
    ]
    matches, kills = match_items(items, _CONFIG, NOW)
    assert matches == []
    # The air-defense item is a near-miss (term hit, no location).
    assert any(k[0] == "tg_iranian_militarism" for k in kills)
