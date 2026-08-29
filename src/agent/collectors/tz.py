"""Timezone rules and display helpers, split out of dates.py 2026-08-29.

dates.py was 211 lines (over the ~200 cap in constraint 12) doing two jobs:
date parsing and timezone rules. This is the timezone half. dates.py imports
from here and re-exports, so existing callers (`dates.to_tehran`,
`dates.israel_utc_offset_hours`) are unchanged.

No zoneinfo/tzdata anywhere, deliberately -- tzdata is not an approved
dependency and is not guaranteed present on the owner's clean Windows
Python or on a bare CI runner. The Israel rule is implemented from the
published law instead, in ~10 lines of stdlib.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

# Sources whose timestamps are Israel wall-clock time regardless of what
# timezone the feed DECLARES. Established by evidence, not by guessing:
# the collect-test gate failed 2026-08-19 with ynet_he reporting
# 17:11:47+00:00 against a workflow start of 14:20:12+00:00 -- an item
# published 2h51m in the FUTURE, which is impossible. Israel is UTC+3 in
# August, so the true publication time was 14:11:47Z: the feed emits local
# wall-clock and the parser trusted it.
#
# Deliberately keyed on source id rather than sniffed from the value:
# silently "correcting" any future-dated timestamp would mask exactly the
# class of feed lie the collect-test gate exists to surface. Any OTHER
# source that starts future-dating must fail the gate loudly.
#
# walla and maariv are Israeli too but are staged enabled:false and have
# never been collected, so whether they also lie is UNVERIFIED. Adding them
# here on suspicion would be guessing; the gate will say so at Phase 8.
ISRAEL_WALL_CLOCK_SOURCE_IDS = frozenset({"ynet", "ynet_he"})


def _last_sunday(year: int, month: int) -> date:
    first_of_next = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    last_day = first_of_next - timedelta(days=1)
    # date.weekday(): Mon=0 .. Sun=6, so (weekday + 1) % 7 is days since Sunday.
    return last_day - timedelta(days=(last_day.weekday() + 1) % 7)


def israel_utc_offset_hours(moment: datetime) -> int:
    """+3 during IDT, +2 otherwise, from the actual Israeli rule: IDT runs
    from 02:00 on the Friday before the last Sunday of March to 02:00 on the
    last Sunday of October.

    The earlier month-based approximation (+3 for months 4-9) was wrong for
    ~30 days a year -- Mar 27-31 and Oct 1-25 -- and wrong in the dangerous
    direction: it subtracted one hour too few, producing a timestamp up to
    an hour in the future, i.e. the same gate failure this whole path exists
    to prevent, just smaller. Found by adversarial review 2026-08-19.

    `moment` is a naive Israel-local reading. Residual imprecision: the two
    transition instants are compared at date+02:00 granularity, so the one
    ambiguous local hour each October resolves to +2. That is a one-hour
    error on at most one hour per year, on a timestamp that is genuinely
    ambiguous in local time regardless.
    """
    year = moment.year
    dst_start = datetime.combine(
        _last_sunday(year, 3) - timedelta(days=2), datetime.min.time()
    ).replace(hour=2)
    dst_end = datetime.combine(
        _last_sunday(year, 10), datetime.min.time()
    ).replace(hour=2)
    return 3 if dst_start <= moment < dst_end else 2


def israel_wall_clock_to_utc(dt: datetime) -> datetime:
    """Take the wall-clock reading off dt, DISCARDING any declared tzinfo,
    and reinterpret it as Israel local. Correct for both live candidates:
    a naive timestamp, and one falsely stamped GMT/+0000 -- in each case the
    digits are the local reading and the declared zone is noise."""
    naive = dt.replace(tzinfo=None)
    return (naive - timedelta(hours=israel_utc_offset_hours(naive))).replace(tzinfo=timezone.utc)


# Iran is UTC+3:30 ALL YEAR. DST was abolished 2022-09-21 (approved by
# Parliament 2022-03-15, communicated 2022-05-22) and reinstatement bills have
# been rejected since. Verified 2026-08-19. So this is a constant, not a rule,
# and needs no tzdata.
#
# If Iran ever restores DST this becomes silently wrong by one hour for half
# the year. It would show up as timestamps an hour off in the digest, not as a
# crash. Recheck if the owner ever reports that.
TEHRAN = timezone(timedelta(hours=3, minutes=30), "IRST")


def to_tehran(dt: datetime) -> datetime:
    """UTC -> Tehran wall clock, for DISPLAY ONLY.

    Everything is stored in UTC. Converting at storage time makes deltas,
    dedupe windows and trend comparisons depend on a display preference, which
    is how timezone bugs become data bugs. Convert at composition, never before.
    """
    return dt.astimezone(TEHRAN)
