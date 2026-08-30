"""Unit tests for pipeline/langgate.py: the deterministic Persian output
gate. No LLM, no network -- script-marker arithmetic only.

Arabic-only letters are spelled as escapes so the test cannot silently
change script if a tool mangles the file's encoding: ي ك ة ى أ إ.
Persian equivalents must PASS: ی ک (different codepoints on purpose --
that difference IS the detector)."""

from __future__ import annotations

from agent.memory.event_models import Event
from agent.pipeline.langgate import is_persian_output, split_persian

AR_YA = "ي"
AR_KAF = "ك"
AR_TA = "ة"
AR_MAQSURA = "ى"
AR_HAMZA_UP = "أ"  # NOT a marker: Persian-productive (تأیید، مأموریت)
AR_HAMZA_DN = "إ"
FA_YA = "ی"
FA_KAF = "ک"
HEBREW = "התקפה"  # התקפה


def test_persian_plain_text_passes():
    assert is_persian_output("حمله به ناو در تنگه هرمز")


def test_persian_only_letters_pass():
    # پ چ ژ گ آ ئ and the Persian forms of the confusables -- the letters
    # that make Persian Persian must never trip the gate.
    assert is_persian_output("ژنرال در باغچه")
    assert is_persian_output("آب رئیس")  # آب رئیس
    assert is_persian_output(FA_YA + FA_KAF)


def test_arabic_ya_and_kaf_fail():
    assert not is_persian_output(AR_YA + AR_KAF)


def test_arabic_ta_marbuta_and_maqsura_fail():
    assert not is_persian_output(AR_TA + AR_MAQSURA)


def test_hamza_above_alef_is_persian_productive_and_passes():
    # أ is a deliberate non-marker: تأیید/تأثیر/مأموریت are standard
    # Persian. Dropping events on it would lose the stories the digest
    # exists for (review finding 2026-08-30).
    assert is_persian_output("تأیید مأموریت")


def test_hamza_below_alef_fails():
    assert not is_persian_output(AR_HAMZA_DN)


def test_hebrew_fails():
    assert not is_persian_output(HEBREW)


def test_latin_only_passes():
    # Documented boundary: the gate proves Arabic/Hebrew drift only; Latin
    # drift is prompt-enforced. Persian text legitimately contains Latin
    # (numbers, acronyms, names), so Latin cannot be a marker.
    assert is_persian_output("US reports oil transfers through Hormuz")


def test_split_persian_splits_by_script():
    good = Event(event_key="g" * 16, summary="حمله به ناو در تنگه هرمز")
    bad = Event(event_key="b" * 16, summary=AR_YA + AR_KAF)
    kept, dropped = split_persian([good, bad])
    assert kept == [good]
    assert dropped == [bad]


def test_either_field_violation_drops_the_event():
    # Mixed: Persian headline, Arabic summary (with true markers ة/ي --
    # the live-sample drift shape). The message renders both, so either
    # half violating the script drops the whole event.
    mixed = Event(
        event_key="m" * 16,
        headline="گزارش تازه از بندر",
        summary="أعلنت الحكومة العراقية",
    )
    kept, dropped = split_persian([mixed])
    assert kept == []
    assert dropped == [mixed]
