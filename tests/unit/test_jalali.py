"""Unit tests for util/jalali.py: the Persian calendar conversion, anchored
against fixed known dates (Nowruz and month-length arithmetic), never
against the algorithm itself."""

from __future__ import annotations

from datetime import datetime, timezone

from agent.util.jalali import format_jalali, jalali_date, to_persian_digits


def _dt(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def test_nowruz_1405_anchor():
    assert jalali_date(_dt(2026, 3, 21)) == (1405, 1, 1)


def test_nowruz_1404_anchor():
    assert jalali_date(_dt(2025, 3, 21)) == (1404, 1, 1)


def test_ordibehesht_starts_31_days_after_nowruz():
    # Farvardin is always 31 days in the modern scheme.
    assert jalali_date(_dt(2026, 4, 21)) == (1405, 2, 1)


def test_mordad_starts_after_four_31day_months():
    # Farvardin..Tir = 4 x 31 days: 2026-03-21 + 124 = 2026-07-23.
    assert jalali_date(_dt(2026, 7, 23)) == (1405, 5, 1)


def test_known_date_2026_08_29():
    # 2026-08-23 == 1405/06/01 (5 x 31 days after Nowruz), so +6 days.
    assert jalali_date(_dt(2026, 8, 29)) == (1405, 6, 7)


def test_persian_digits():
    assert to_persian_digits("1405/06/07") == "۱۴۰۵/۰۶/۰۷"
    assert to_persian_digits("17:44") == "۱۷:۴۴"


def test_format_jalali_date_only():
    assert format_jalali(_dt(2026, 8, 29)) == "۱۴۰۵/۰۶/۰۷"


def test_format_jalali_with_time():
    text = format_jalali(_dt(2026, 8, 29, 17, 44), with_time=True)
    assert text == "۱۴۰۵/۰۶/۰۷ — ۱۷:۴۴"
