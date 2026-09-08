"""Tests for flash/textnorm.py's normalize(): the shared folding table used
by the flash matcher, the flash config loader AND (as of round-3 review,
fix 1) pipeline/relevance.py. Pins the orthography and digit folding this
brief extended -- these are exactly the transforms that make Arabic- and
Persian-spelled text meet in the same matching space."""

from __future__ import annotations

from agent.flash.textnorm import normalize


def test_arabic_yeh_and_kaf_fold_to_persian():
    assert normalize("ايران") == normalize("ایران")
    assert normalize("كابل") == normalize("کابل")


def test_alef_hamza_and_madda_fold_to_bare_alef():
    # أ (hamza above), إ (hamza below), آ (madda) all fold to bare ا.
    assert normalize("أنقرة") == normalize("انقرة")
    assert normalize("إيران") == normalize("ایران")
    assert normalize("آمریکا") == normalize("امریکا")


def test_teh_marbuta_folds_to_heh():
    assert normalize("غزة") == normalize("غزه")


def test_zwnj_stripped():
    assert normalize("آتش‌بازی") == normalize("آتشبازی")


def test_arabic_indic_digits_fold_to_ascii():
    assert normalize("٣٠") == "30"


def test_persian_digits_fold_to_ascii():
    assert normalize("۳۰") == "30"


def test_latin_lowercased():
    assert normalize("IRAN") == normalize("iran")


def test_hebrew_passes_through_unchanged_besides_case_noop():
    # Hebrew has no case and no entries in the Arabic->Persian folding
    # table, so normalize() must not mangle it.
    assert normalize("איראן") == "איראן"
    assert normalize("ישראל") == "ישראל"
