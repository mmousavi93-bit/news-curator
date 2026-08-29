"""Feed date parsing.

Split out of rss.py 2026-08-19 (Israel DST rule pushed rss.py past the
~200-line cap); the timezone half split out again 2026-08-29 to tz.py when
this file crossed the cap itself (CLAUDE.md constraint 12). tz.py holds the
Israel rule, the Tehran display constant and their helpers; they are
imported and re-exported here so `dates.to_tehran` and
`dates.israel_utc_offset_hours` keep working for existing callers.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from agent.collectors.tz import (  # re-exported for compatibility
    ISRAEL_WALL_CLOCK_SOURCE_IDS,
    TEHRAN,
    israel_utc_offset_hours,
    israel_wall_clock_to_utc,
    to_tehran,
)

# A US-style "M/D/YYYY H:MM:SS AM/PM" stamp, which neither
# email.utils.parsedate_to_datetime nor datetime.fromisoformat accepts.
# Originally added for Ynet, whose date the probe's DATE_RE never matched.
# It is NOT evidence of an Israeli source -- see parse_date, where the
# Israel offset is applied only under the israel_local flag.
_US_STYLE_DATE_RE = re.compile(
    r"(\d{1,2})/(\d{1,2})/(\d{4})\s+(\d{1,2}):(\d{2}):(\d{2})\s*(AM|PM)?", re.I
)

# An RFC-822 date with NO time component: 'Wed, 19 Aug 2026'.
# This is what state_dept_travel actually emits, confirmed 2026-08-19 by
# dump_body.py: 95 of 95 items, every one time-less. All three parsers reject
# it -- parsedate_to_datetime RAISES, parsedate_tz returns None,
# fromisoformat raises -- so parse_date correctly returned None for the whole
# feed and 15% of the corpus arrived undated.
#
# Note the channel-level date on that same feed IS full ('Sun, 16 Aug 2026
# 14:30:06 GMT'), which is why check_feeds.py read a date off it in ci2/ci3/ci4
# while the collector got nothing. Neither was wrong; they read different
# elements. That is the whole g1 confusion in one line.
_DATE_ONLY_RE = re.compile(r"^(?:[A-Za-z]{3,9},\s*)?\d{1,2}\s+[A-Za-z]{3,9}\s+\d{2,4}$")


def _finish(dt: datetime, israel_local: bool) -> datetime:
    if israel_local:
        return israel_wall_clock_to_utc(dt)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_date(raw: str, *, israel_local: bool = False) -> datetime | None:
    """Returns an aware UTC datetime, or None. Never fabricates `now`.

    Thin wrapper over parse_date_ex for callers that do not care about
    precision. New code should prefer parse_date_ex -- a date-only value
    silently looks like midnight here, and midnight is a real publication
    time for some feeds.
    """
    return parse_date_ex(raw, israel_local=israel_local)[0]


def parse_date_ex(raw: str, *, israel_local: bool = False) -> tuple[datetime | None, bool]:
    """Returns (aware UTC datetime or None, is_date_only). Never fabricates `now`.

    `is_date_only` True means the source gave a DAY and no time, and the
    returned datetime is midnight UTC -- a placeholder, not an observation.
    It must not be rendered as a clock time: 00:00Z displays as 03:30 in
    Tehran, which would present an invented time as fact and break hard
    constraints 10 and 11. The composer prints the date and says the time was
    not stated.
    """
    text = raw.strip()
    if not text:
        return None, False

    try:
        return _finish(parsedate_to_datetime(text), israel_local), False
    except (TypeError, ValueError, IndexError):
        pass

    if _DATE_ONLY_RE.match(text):
        try:
            # Delegate to the same battle-tested RFC-822 parser rather than
            # hand-rolling a month-name table: append the time it is missing.
            midnight = parsedate_to_datetime(text + " 00:00:00 +0000")
        except (TypeError, ValueError, IndexError):
            pass
        else:
            # israel_local is deliberately NOT applied. Shifting a placeholder
            # midnight by -3h lands on 21:00 the PREVIOUS DAY -- inventing a
            # date error on top of an unknown time. A value with no time has no
            # meaningful wall clock to reinterpret. No Israeli source is
            # date-only today; this guard is here so that stays true by
            # construction rather than by luck.
            return midnight.replace(tzinfo=timezone.utc), True

    try:
        # A trailing 'Z' is the canonical Atom-spec example
        # (<updated>2003-12-13T18:30:02Z</updated>) and is common on RSS
        # dc:date too. datetime.fromisoformat() only accepts it from Python
        # 3.11 -- normalise explicitly rather than depending on whichever
        # interpreter the collector happens to run under.
        iso_text = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
        return _finish(datetime.fromisoformat(iso_text), israel_local), False
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
            return None, False
        # This branch carries no timezone at all, so israel_local decides.
        # Before 2026-08-19 it applied the Israel offset unconditionally --
        # meaning ANY source emitting a US-style stamp was silently shifted
        # 2-3 hours, which is a plausible format for state_dept_travel or
        # the_war_zone and would have been invisible.
        return _finish(naive, israel_local), False

    return None, False
