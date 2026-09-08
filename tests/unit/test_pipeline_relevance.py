"""Tests for pipeline/relevance.py: the deterministic Iran-relevance tier
matcher behind digest ranking (owner decision 2026-08-30 -- relevance leads,
category is a tie-breaker). No LLM, no network: config validation and
substring matching only."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agent.config import ConfigError
from agent.pipeline.relevance import passes_gate, score_relevance, validate_relevance

_REPO_ROOT = Path(__file__).parent.parent.parent


def _real_cfg():
    # The SHIPPED config/relevance.yaml, not a test-only copy -- these are
    # regression tests for real keyword gaps (fix 4, 2026-09-06).
    raw = yaml.safe_load((_REPO_ROOT / "config" / "relevance.yaml").read_text(encoding="utf-8"))
    return validate_relevance(raw)

_GOOD = {
    "weights": {"iran_direct": 8, "strategic": 4, "economy": 3},
    "keywords": {
        "iran_direct": ["ایران", "iran"],
        "strategic": ["جنگ", "war"],
        "economy": ["نفت", "oil"],
    },
}


def _cfg(**overrides):
    raw = {**_GOOD, **overrides}
    return validate_relevance(raw)


def test_good_config_loads():
    cfg = _cfg()
    assert cfg.weights["iran_direct"] == 8.0
    assert "جنگ" in cfg.keywords["strategic"]


def test_unknown_tier_rejected():
    with pytest.raises(ConfigError, match="unknown weight tier"):
        _cfg(weights={**_GOOD["weights"], "nope": 1})


def test_negative_weight_rejected():
    with pytest.raises(ConfigError, match="non-negative"):
        _cfg(weights={**_GOOD["weights"], "strategic": -1})


def test_non_string_keyword_rejected():
    with pytest.raises(ConfigError, match="non-empty strings"):
        _cfg(keywords={**_GOOD["keywords"], "economy": ["نفت", 3]})


def test_weight_without_keywords_rejected():
    with pytest.raises(ConfigError, match="weight but no keywords"):
        _cfg(keywords={k: v for k, v in _GOOD["keywords"].items() if k != "economy"})


def test_not_a_mapping_rejected():
    with pytest.raises(ConfigError, match="expected a mapping"):
        validate_relevance([1, 2])


def test_score_relevance_highest_tier_wins_not_additive():
    cfg = _cfg()
    # Matches both strategic ("جنگ") and economy ("نفت"): takes 4, not 7.
    assert score_relevance(cfg, "جنگ نفت در منطقه") == 4.0
    assert score_relevance(cfg, "قیمت نفت بالا رفت") == 3.0
    assert score_relevance(cfg, "no keywords here") == 0.0


def test_score_relevance_iran_direct_beats_strategic():
    cfg = _cfg()
    assert score_relevance(cfg, "حمله به ایران با موشک") == 8.0


def test_score_relevance_none_config_is_zero():
    assert score_relevance(None, "ایران") == 0.0
    assert score_relevance(_cfg(), "") == 0.0


def test_min_relevance_string_rejected():
    with pytest.raises(ConfigError, match="min_relevance"):
        validate_relevance({**_GOOD, "min_relevance": "high"})


def test_passes_gate_thresholds():
    cfg = _cfg(min_relevance=4)
    assert passes_gate(cfg, "قیمت نفت بالا رفت") is False  # economy 3 < 4
    assert passes_gate(cfg, "حمله به ایران") is True       # iran_direct 8 >= 4
    assert passes_gate(None, "anything") is True           # no config: no gate


def test_yemen_actor_and_city_terms_score_strategic():
    # Fix 4, 2026-09-06: "یمن"/"yemen" (the bare country name) was already
    # in strategic, but a Yemen-war story datelined to a specific actor or
    # city never says the country name. Real config/relevance.yaml, not the
    # test-only _GOOD stub.
    cfg = _real_cfg()
    assert score_relevance(cfg, "انصارالله در تعز حمله کرد") == cfg.weights["strategic"]
    assert score_relevance(cfg, "Houthi forces advance near Hodeidah") == cfg.weights["strategic"]


def test_putin_moscow_talks_score_strategic():
    # Fix 4, 2026-09-06: Putin/Witkoff/Kushner Moscow talks (importance
    # 10.99) were relevance-gated out entirely before this keyword add.
    cfg = _real_cfg()
    assert score_relevance(
        cfg, "پوتین و ویتکاف در مسکو درباره اوکراین مذاکره کردند"
    ) == cfg.weights["strategic"]
    assert score_relevance(cfg, "Kushner meets Putin in the Kremlin") == cfg.weights["strategic"]


def test_bare_kyiv_airraid_stays_off_mission():
    # Deliberately NOT added: bare اوکراین/ukraine, کییف/kyiv, زلنسکی/
    # zelensky. Two Kyiv air-raid OSINT-spam clusters must stay off-mission
    # -- only the US-Russia negotiation actors (test above) are Iran-
    # relevant, not the war itself.
    cfg = _real_cfg()
    assert score_relevance(cfg, "حمله هوایی روسیه به کییف در شب گذشته") == 0.0
    assert score_relevance(cfg, "Zelensky says Kyiv air raid killed civilians") == 0.0


# --- round-3 review, fix 1: Arabic/Hebrew orthography normalization -------
# on_mission was a language DETECTOR, not a relevance test: plain casefold()
# substring matching never folds Arabic spellings (إيران، غزة، إسرائيل) to
# their Persian keyword forms (ایران، غزه، اسرائیل), and there were zero
# Hebrew keywords at all. Measured against outputs/read_20260905T221247Z.csv:
# Arabic 3/32 (9%) on-mission, Hebrew 0/9 (0%), before this fix.


def test_arabic_hamza_alef_variant_matches_persian_iran_keyword():
    # إيران (hamza-below alef + ي) must match the same keyword ایران does --
    # this is the exact substitution the reviewer measured as broken.
    cfg = _cfg(keywords={**_GOOD["keywords"], "iran_direct": ["ایران"]})
    assert score_relevance(cfg, "إيران تعلن عن مناورات عسكرية") == cfg.weights["iran_direct"]


def test_arabic_teh_marbuta_matches_persian_heh_keyword():
    # غزة (teh marbuta) must match غزه (heh) -- config/relevance.yaml only
    # carries the heh spelling.
    cfg = _cfg(keywords={**_GOOD["keywords"], "strategic": ["غزه"]})
    assert score_relevance(cfg, "قوات الاحتلال تقصف غزة مجددا") == cfg.weights["strategic"]


def test_hebrew_keyword_matches_hebrew_text():
    cfg = _cfg(keywords={**_GOOD["keywords"], "iran_direct": ["איראן"]})
    assert score_relevance(cfg, "איראן מכריזה על תרגיל צבאי חדש") == cfg.weights["iran_direct"]


def test_real_config_scores_arabic_and_hebrew_iran_headlines():
    # Fix 1e: the test class that would have caught this -- the REAL
    # config/relevance.yaml, not a synthetic stub, must score a
    # representative Arabic and a representative Hebrew Iran headline > 0.
    cfg = _real_cfg()
    assert score_relevance(cfg, "إيران مباشر.. واشنطن وطهران تتبادلان قصف ناقلات") > 0
    assert score_relevance(cfg, "איראן מכריזה על תרגיל צבאי חדש בעקבות המתיחות") > 0


# --- round-4 review, fix 4: Arabic ب-for-پ transliteration gap ------------
# Arabic has no پ/گ/ژ/چ, so Arabic sources write Persian names with the
# nearest Arabic letter (ب for پ). The fold table folds Arabic->Persian
# orthography (إ->ا, ي->ی, ك->ک...) but must NOT fold ب->پ -- that would
# match every legitimate Arabic ب. Explicit keyword variants close the gap
# instead (same precedent as بزشکیان next to پزشکیان, 2026-09-06).


def test_arabic_putin_spelling_scores_same_as_persian():
    # al_manar (outputs/read_20260905T221247Z.csv rows 139/140): two
    # Putin/Trump-envoy items scored 0.0 before this fix because Arabic
    # spells Putin بوتين (ب), not پوتین (پ), and had no other strategic
    # anchor in the headline.
    cfg = _real_cfg()
    headline = "مساعد الرئيس الروسي: بوتين أكد خلال اجتماعه مع مبعوثي ترامب ضرورة معالجة الأسباب"
    assert score_relevance(cfg, headline) == cfg.weights["strategic"]
    # The identical claim in Persian must score the same tier.
    assert score_relevance(cfg, "بوتین در دیدار با مبعوثان ترامپ") == cfg.weights["strategic"]


def test_arabic_irgc_and_hormuz_spellings_score_iran_direct():
    # سپاه (IRGC) and تنگه هرمز (Strait of Hormuz) both carry a پ/گ Persian
    # sources write; Arabic substitutes ب/ك (سباه, تنکه هرمز).
    cfg = _real_cfg()
    assert score_relevance(cfg, "سباه الحرس الثوري ينشر بيانا") == cfg.weights["iran_direct"]
    assert score_relevance(cfg, "توتر في تنکه هرمز بعد استهداف ناقلة") == cfg.weights["iran_direct"]


def test_arabic_opec_spelling_scores_economy():
    cfg = _real_cfg()
    assert score_relevance(cfg, "اجتماع اوبک يبحث خفض الإنتاج") == cfg.weights["economy"]
