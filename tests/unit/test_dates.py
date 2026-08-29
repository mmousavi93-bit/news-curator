from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agent.collectors import dates


def _utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


def test_parse_date_rfc822():
    assert dates.parse_date("Tue, 16 Aug 2026 08:00:00 GMT") == _utc(2026, 8, 16, 8, 0, 0)


def test_parse_date_iso8601():
    assert dates.parse_date("2026-08-17T09:30:00+00:00") == _utc(2026, 8, 17, 9, 30, 0)


def test_parse_date_empty_or_garbage_returns_none_never_fabricates_now():
    assert dates.parse_date("") is None
    assert dates.parse_date("not a date at all") is None


# ---------------------------------------------------------------------------
# US-style "M/D/YYYY H:MM:SS AM/PM" -- the third fallback branch.
# ---------------------------------------------------------------------------

def test_parse_date_us_style_with_israel_flag_applies_summer_offset():
    assert dates.parse_date("8/16/2026 11:00:00 AM", israel_local=True) == _utc(2026, 8, 16, 8, 0, 0)


def test_parse_date_us_style_with_israel_flag_applies_winter_offset():
    assert dates.parse_date("12/16/2026 11:00:00 AM", israel_local=True) == _utc(2026, 12, 16, 9, 0, 0)


def test_parse_date_us_style_pm_and_midnight_edge_cases():
    noon = dates.parse_date("1/1/2026 12:00:00 PM", israel_local=True)
    assert noon == _utc(2026, 1, 1, 10, 0, 0)      # 12 PM local -> 10:00Z (winter, +2)
    midnight = dates.parse_date("1/1/2026 12:00:00 AM", israel_local=True)
    assert midnight == _utc(2025, 12, 31, 22, 0, 0)  # 12 AM -> hour 0 local -> prior day


def test_parse_date_us_style_without_flag_is_not_shifted():
    # REGRESSION, adversarial review 2026-08-19 (CRITICAL). This branch used
    # to apply the Israel offset unconditionally, so ANY source emitting a
    # US-style stamp -- a wholly plausible format for state_dept_travel or
    # the_war_zone -- was silently moved 2-3 hours, contradicting the stated
    # "keyed on source id, never sniffed" guarantee. Untagged sources must
    # be read as naive UTC, exactly like the other two branches.
    assert dates.parse_date("8/16/2026 11:00:00 AM") == _utc(2026, 8, 16, 11, 0, 0)


# ---------------------------------------------------------------------------
# Israel wall-clock correction. Regression tests for the collect-test gate
# failure of 2026-08-19: ynet_he reported 17:11:47+00:00 against a workflow
# start of 14:20:12+00:00 -- an item published 2h51m in the future, i.e. the
# feed emits Israel local time and the parser trusted the declared zone.
# Both candidate wire formats must land on the same corrected instant.
# ---------------------------------------------------------------------------

def test_parse_date_israel_local_discards_false_gmt():
    # The exact CI failure: local 17:11:47 falsely stamped GMT, August => +3.
    assert dates.parse_date("Tue, 19 Aug 2026 17:11:47 GMT", israel_local=True) == _utc(2026, 8, 19, 14, 11, 47)


def test_parse_date_israel_local_shifts_naive_iso():
    # The other candidate: no zone declared at all.
    assert dates.parse_date("2026-08-19T17:11:47", israel_local=True) == _utc(2026, 8, 19, 14, 11, 47)


def test_parse_date_israel_local_is_idempotent_on_a_correct_offset():
    # If Ynet ever starts declaring +0300 honestly, the wall clock is already
    # the local reading, so the same rule still yields the right instant.
    assert dates.parse_date("Tue, 19 Aug 2026 17:11:47 +0300", israel_local=True) == _utc(2026, 8, 19, 14, 11, 47)


def test_parse_date_israel_local_uses_winter_offset():
    assert dates.parse_date("Tue, 15 Dec 2026 17:00:00 GMT", israel_local=True) == _utc(2026, 12, 15, 15, 0, 0)


def test_parse_date_without_flag_still_trusts_the_declared_zone():
    # The shift must NOT leak to other sources: a feed that honestly says GMT
    # is still GMT. Guards against "fixing" ynet by breaking all 50 others.
    assert dates.parse_date("Tue, 19 Aug 2026 17:11:47 GMT") == _utc(2026, 8, 19, 17, 11, 47)


# ---------------------------------------------------------------------------
# Israel DST boundaries.
# ---------------------------------------------------------------------------

def test_israel_dst_boundaries_match_the_published_rule():
    # REGRESSION, adversarial review 2026-08-19 (MAJOR). The previous
    # month-based approximation (+3 for months 4-9) was wrong for ~30 days a
    # year -- Mar 27-31 and Oct 1-25 -- and wrong in the dangerous direction:
    # one hour too little subtracted yields a timestamp up to an hour in the
    # FUTURE, re-creating the very gate failure this path exists to prevent.
    #
    # 2026: IDT runs 02:00 Fri 2026-03-27 to 02:00 Sun 2026-10-25.
    cases = [
        (datetime(2026, 3, 26, 12, 0), 2),   # day before the switch
        (datetime(2026, 3, 27, 1, 59), 2),   # minutes before the switch
        (datetime(2026, 3, 27, 2, 0), 3),    # the switch itself
        (datetime(2026, 3, 29, 12, 0), 3),   # inside the old approximation's blind spot
        (datetime(2026, 8, 19, 17, 11), 3),  # the CI failure's own date
        (datetime(2026, 10, 20, 12, 0), 3),  # still IDT; approximation said +2
        (datetime(2026, 10, 25, 1, 59), 3),  # minutes before the fall back
        (datetime(2026, 10, 25, 2, 0), 2),   # fall back
        (datetime(2026, 12, 15, 17, 0), 2),
        (datetime(2027, 1, 5, 9, 0), 2),     # rule is computed per year, not hardcoded
    ]
    for moment, expected in cases:
        assert dates.israel_utc_offset_hours(moment) == expected, moment


def test_israel_dst_rule_is_computed_per_year_not_pinned_to_2026():
    # 2027: last Sunday of March is the 28th, so IDT starts Fri the 26th;
    # last Sunday of October is the 31st.
    assert dates.israel_utc_offset_hours(datetime(2027, 3, 25, 12, 0)) == 2
    assert dates.israel_utc_offset_hours(datetime(2027, 3, 27, 12, 0)) == 3
    assert dates.israel_utc_offset_hours(datetime(2027, 10, 30, 12, 0)) == 3
    assert dates.israel_utc_offset_hours(datetime(2027, 11, 1, 12, 0)) == 2


# --- date-only values (g1, 2026-08-19) -------------------------------------
# state_dept_travel emits 'Wed, 19 Aug 2026' with no time on 95 of 95 items.
# Every parser rejected it, so parse_date returned None for the whole feed and
# 15% of the corpus arrived undated while the collect-test gate said PASSED.

def test_date_only_rfc822_parses_to_midnight_utc_and_is_flagged():
    dt, date_only = dates.parse_date_ex("Wed, 19 Aug 2026")
    assert dt == datetime(2026, 8, 19, 0, 0, tzinfo=timezone.utc)
    assert date_only is True


def test_date_only_without_weekday_also_parses():
    dt, date_only = dates.parse_date_ex("19 Aug 2026")
    assert dt == datetime(2026, 8, 19, 0, 0, tzinfo=timezone.utc)
    assert date_only is True


def test_full_rfc822_is_not_flagged_date_only():
    dt, date_only = dates.parse_date_ex("Sun, 16 Aug 2026 14:30:06 GMT")
    assert dt == datetime(2026, 8, 16, 14, 30, 6, tzinfo=timezone.utc)
    assert date_only is False


def test_israel_offset_is_not_applied_to_a_date_only_value():
    # Shifting a placeholder midnight by -3h lands on 21:00 the PREVIOUS DAY,
    # inventing a date error on top of an unknown time.
    dt, date_only = dates.parse_date_ex("Wed, 19 Aug 2026", israel_local=True)
    assert dt == datetime(2026, 8, 19, 0, 0, tzinfo=timezone.utc)
    assert date_only is True


def test_israel_offset_still_applies_to_a_full_value():
    dt, date_only = dates.parse_date_ex("Wed, 19 Aug 2026 17:11:47 GMT", israel_local=True)
    assert dt == datetime(2026, 8, 19, 14, 11, 47, tzinfo=timezone.utc)
    assert date_only is False


def test_parse_date_wrapper_still_returns_a_bare_datetime():
    assert dates.parse_date("Sun, 16 Aug 2026 14:30:06 GMT") == datetime(
        2026, 8, 16, 14, 30, 6, tzinfo=timezone.utc
    )
    assert dates.parse_date("") is None
    assert dates.parse_date("garbage") is None


def test_garbage_is_not_swept_into_the_date_only_branch():
    # The date-only regex is anchored; it must not rescue unparseable text.
    for bad in ("garbage", "Aug 2026", "19 2026", "Wed, 19 Aug"):
        assert dates.parse_date_ex(bad) == (None, False), bad


# --- Tehran display ---------------------------------------------------------

def test_tehran_is_fixed_utc_plus_3_30_year_round():
    # Iran abolished DST 2022-09-21 and has rejected reinstatement since,
    # so this must NOT vary by season.
    for month in (1, 4, 7, 10):
        dt = datetime(2026, month, 15, 12, 0, tzinfo=timezone.utc)
        assert dates.to_tehran(dt).utcoffset() == timedelta(hours=3, minutes=30)


def test_midnight_utc_displays_as_0330_in_tehran():
    # The reason date_only exists: this 03:30 is a fabricated clock time and
    # the composer must never print it as one.
    dt = datetime(2026, 8, 19, 0, 0, tzinfo=timezone.utc)
    assert dates.to_tehran(dt).strftime("%Y-%m-%d %H:%M") == "2026-08-19 03:30"


def test_israel_wall_clock_set_is_evidence_based():
    # ynet/ynet_he proven liars 2026-08-19 (gate), walla 2026-08-29 (gate,
    # 2h08m future). Everything else stays out until the gate says so --
    # membership here must be evidence, never suspicion.
    assert dates.ISRAEL_WALL_CLOCK_SOURCE_IDS == frozenset({"ynet", "ynet_he", "walla"})
