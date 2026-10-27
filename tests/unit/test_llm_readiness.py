import logging
from collections import deque

from agent.llm.limits import ProviderBudget, RpmPacer
from agent.llm.token_pacer import TokenPacer


def test_token_probe_accounts_for_all_expirations_without_mutating():
    now = [30.0]
    sleeps = []
    pacer = TokenPacer(lambda: now[0], sleeps.append, logging.getLogger(__name__))
    pacer._windows['groq'] = deque([(-100, 100), (0, 2000), (20, 2000), (25, 1000)])
    before = list(pacer._windows['groq'])
    assert pacer.delay('groq', 8000, 3500) == 110
    assert list(pacer._windows['groq']) == before
    assert sleeps == []
    assert pacer.delay('groq', 8000, 8001) == 0
    assert pacer.delay('groq', None, 3500) == 0


def test_token_probe_matches_wait_with_corrections_and_safety_band():
    now = [0.0]
    def sleep(seconds):
        now[0] += seconds
    pacer = TokenPacer(lambda: now[0], sleep, logging.getLogger(__name__))
    pacer.wait('groq', 8000, 2000)
    now[0] = 10
    pacer.charge('groq', 1500)
    now[0] = 20
    expected = pacer.delay('groq', 8000, 5201)
    pacer.wait('groq', 8000, 5201)
    assert expected == 110
    assert now[0] == 130
    assert pacer.delay('groq', 8000, 1) == 120


def test_rpm_probe_does_not_book_or_sleep():
    now = [0.0]
    sleeps = []
    pacer = RpmPacer(lambda: now[0], sleeps.append)
    assert pacer.delay('groq', 30) == 0
    pacer.wait('groq', 30)
    now[0] = 1
    assert pacer.delay('groq', 30) == 1
    assert pacer.delay('groq', None) == 0
    assert pacer._last == {'groq': 0}
    assert sleeps == []


def test_provider_capacity_probe_has_no_acquisition_or_logging_side_effect():
    budget = ProviderBudget(name='groq', max_calls_per_run=1,
        max_spend_usd=1, halt_on_exceeded=True, input_usd_per_mtok=1,
        output_usd_per_mtok=1, logger=logging.getLogger(__name__),
        max_calls_per_day=2, daily_calls_used=1)
    before = vars(budget).copy()
    assert budget.has_capacity()
    assert vars(budget) == before
    assert budget.acquire()
    assert not budget.has_capacity()
    budget.calls = 0
    assert not budget.has_capacity()  # daily cap
    budget.daily_calls = 0
    budget.record_usage(2000000, 0)
    assert not budget.has_capacity()


def test_spend_exhaustion_disqualifies_overflow_even_when_normal_guard_only_warns():
    budget = ProviderBudget(name='groq2', max_calls_per_run=None,
        max_spend_usd=1, halt_on_exceeded=False, input_usd_per_mtok=1,
        output_usd_per_mtok=1, logger=logging.getLogger(__name__))
    budget.record_usage(2000000, 0)
    before = vars(budget).copy()
    assert not budget.has_capacity()
    assert vars(budget) == before
    assert budget.acquire()  # ordinary cascade keeps its existing warn-only behavior
