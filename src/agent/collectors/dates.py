"""Feed date parsing, including the Israel wall-clock correction.

Split out of rss.py 2026-08-19: the Israel DST rule below pushed rss.py
past the ~200-line cap in constraint 12, and date parsing is a separable
job from feed-structure parsing anyway. telegram_web.py may reuse this.

No zoneinfo/tzdata anywhere in here, deliberately -- tzdata is not an
approved dependency and is not guaranteed present on the owner's clean
Windows Python or on a bare CI runner. The Israel rule is implemented from
the published law instead, in ~10 lines of stdlib.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

# A US-style "M/D/YYYY H:MM:SS AM/PM" stamp, which neither
# email.utils.parsedate_to_datetime nor datetime.fromisoformat accepts.
# Originally added for Ynet, whose date the probe's DATE_RE never matched.
# It is NOT evidence of an Israeli source -- see parse_date, where the
# Israel offset is applied only under the israel_local flag.
_US_STYLE_DATE_RE = re.compile(
    r"(\d{1,2})/(\d{1,2})/(\d{4})\s+(\d{1,2}):(\d{2}):(\d{2})\s*(AM|PM)?", re.I
)

# Sources whose timestamps are Israel wall-clock time regardless of what
# timezone the feed DECLARES. Established by evidence, not by guessing:
# the collect-test gate failed 2026-08-19 with ynet_he reporting
# 17:11:47+00:00 against a workflow start of 14:20:12+00:00 -- an item
# published 2h51m in the FUTURE, which is impossible. Israel is UTC+3 in
# August, so the true publication time was 14:11:47Z, nine minutes before
# the run: the feed emits local wall-clock and the parser trusted it.
#
# That failure also proves _US_STYLE_DATE_RE is dead code against the live
# Ynet feed -- _DATE_RE matched and an earlier branch parsed the string, so
# Ynet's format is ordinary and only its declared zone lies. The branch is
# kept for the case the feed changes back.
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
    dst_start = datetime.combine(_last_sunday(year, 3) - timedelta(days=2), datetime.min.time()).replace(hour=2)
    dst_end = datetime.combine(_last_sunday(year, 10), datetime.min.time()).replace(hour=2)
    return 3 if dst_start <= moment < dst_end else 2


def israel_wall_clock_to_utc(dt: datetime) -> datetime:
    """Take the wall-clock reading off dt, DISCARDING any declared tzinfo,
    and reinterpret it as Israel local. Correct for both live candidates:
    a naive timestamp, and one falsely stamped GMT/+0000 -- in each case the
    digits are the local reading and the declared zone is noise."""
    naive = dt.replace(tzinfo=None)
    return (naive - timedelta(hours=israel_utc_offset_hours(naive))).replace(tzinfo=timezone.utc)


def _finish(dt: datetime, israel_local: bool) -> datetime:
    if israel_local:
        return israel_wall_clock_to_utc(dt)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_date(raw: str, *, israel_local: bool = False) -> datetime | None:
    """Returns an aware UTC datetime, or None. Never fabricates `now`."""
    text = raw.strip()
    if not text:
        return None

    try:
        return _finish(parsedate_to_datetime(text), israel_local)
    except (TypeError, ValueError, IndexError):
        pass

    try:
        # A trailing 'Z' is the canonical Atom-spec example
        # (<updated>2003-12-13T18:30:02Z</updated>) and is common on RSS
        # dc:date too. datetime.fromisoformat() only accepts it from Python
        # 3.11 -- normalise explicitly rather than depending on whichever
        # interpreter the collector happens to run under.
        iso_text = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
        return _finish(datetime.fromisoformat(iso_text), israel_local)
    except ValueError:
        pass

    match = _US_STYLE_DATE_RE.match(text)
    if match:
        month, day, year, hour, minute, second, ampm = match.groups()
        hour_i = int(hour)
        if ampm:
            if ampm.upper() == "PM" and hour_i != 12:
                hour_i += 12
            elif ampm.upper() == "AM" and hour_i == 12:
                hour_i = 0
        try:
            naive = datetime(int(year), int(month), int(day), hour_i, int(minute), int(second))
        except ValueError:
            return None
        # This branch carries no timezone at all, so israel_local decides.
        # Before 2026-08-19 it applied the Israel offset unconditionally --
        # meaning ANY source emitting a US-style stamp was silently shifted
        # 2-3 hours, which is a plausible format for state_dept_travel or
        # the_war_zone and would have been invisible.
        return _finish(naive, israel_local)

    return None
