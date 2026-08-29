"""Jalali (Persian solar) calendar conversion, stdlib only.

The digest is Persian; Persian readers expect Jalali dates. No dependency
is acceptable here (constraint 13 -- a calendar is ~60 lines), so this is
a port of the well-known jalaali-js algorithm (MIT) to pure Python.

Correctness is anchored in tests against fixed known dates (Nowruz), not
against itself: gregorian 2026-03-21 == jalali 1405/01/01, and month
lengths 1-6 are always 31 days, so 2026-08-29 == 1405/06/07.
"""

from __future__ import annotations

from datetime import datetime

# Leap-cycle break points of the 33-year Persian cycle (jalaali-js).
_BREAKS = [
    -61, 9, 38, 199, 426, 686, 756, 818, 1111, 1181, 1210,
    1635, 2060, 2097, 2192, 2262, 2324, 2394, 2456, 3178,
]

_PERSIAN_DIGITS = "۰۱۲۳۴۵۶۷۸۹"


def _div(a: int, b: int) -> int:
    # Truncation toward zero, NOT floor: jalaali-js uses ~~(a/b), and the
    # algorithm's negative deltas break under Python's floor semantics
    # (mid-year dates drifted to month 18 -- caught by the anchor tests).
    # Values stay well inside float64's exact range.
    return int(a / b)


def _mod(a: int, b: int) -> int:
    # a - trunc(a/b)*b: the sign of the dividend, matching JS %.
    return a - _div(a, b) * b


def _g2d(gy: int, gm: int, gd: int) -> int:
    """Gregorian date -> Julian day number."""
    d = _div((gy + _div(gm - 8, 6) + 100100) * 1461, 4)
    d += _div(153 * _mod(gm + 9, 12) + 2, 5)
    d += gd - 34840408
    d -= _div(_div(gy + 100100 + _div(gm - 8, 6), 100) * 3, 4)
    return d + 752


def _jal_cal(jy: int) -> tuple[int, int, int]:
    """(leap, gregorian_year, farvardin_march_day) for a Jalali year."""
    gy = jy + 621
    leap_j = -14
    jp = _BREAKS[0]
    jm = jump = 0
    for i in range(1, len(_BREAKS)):
        jm = _BREAKS[i]
        jump = jm - jp
        if jy < jm:
            break
        leap_j += _div(jump, 33) * 8 + _div(_mod(jump, 33), 4)
        jp = jm
    n = jy - jp
    leap_j += _div(n, 33) * 8 + _div(_mod(n, 33) + 3, 4)
    if _mod(jump, 33) == 4 and jump - n == 4:
        leap_j += 1
    leap_g = _div(gy, 4) - _div((_div(gy, 100) + 1) * 3, 4) - 150
    march = 20 + leap_j - leap_g
    if jump - n < 6:
        n = n - jump + _div(jump + 4, 33) * 33
    leap = _mod(_mod(n + 1, 33) - 1, 4)
    if leap == -1:
        leap = 4
    return leap, gy, march


def jalali_date(dt: datetime) -> tuple[int, int, int]:
    """Aware/naive datetime -> (jalali_year, month, day). Timezone-blind:
    the DATE is taken from the datetime as given (callers pass Tehran
    wall-clock, not UTC -- convert first, exactly like to_tehran())."""
    gy, gm, gd = dt.year, dt.month, dt.day
    jy = gy - 621
    leap, _gy2, march = _jal_cal(jy)
    jdn1f = _g2d(gy, 3, march)
    k = _g2d(gy, gm, gd) - jdn1f
    if k >= 0:
        if k <= 185:
            return jy, 1 + _div(k, 31), _mod(k, 31) + 1
        k -= 186
    else:
        jy -= 1
        k += 179
        if leap == 1:
            k += 1
    return jy, 7 + _div(k, 30), _mod(k, 30) + 1


def to_persian_digits(text: str) -> str:
    """'1405' -> '۱۴۰۵'. Digit mapping only; everything else passes through."""
    return "".join(_PERSIAN_DIGITS[int(ch)] if ch.isdigit() else ch for ch in text)


def format_jalali(dt: datetime, *, with_time: bool = False) -> str:
    """Tehran datetime -> '۱۴۰۵/۰۶/۰۷' or '۱۴۰۵/۰۶/۰۷ — ۱۷:۴۴'."""
    year, month, day = jalali_date(dt)
    base = to_persian_digits(f"{year}/{month:02d}/{day:02d}")
    if with_time:
        base += " — " + to_persian_digits(f"{dt.hour:02d}:{dt.minute:02d}")
    return base
