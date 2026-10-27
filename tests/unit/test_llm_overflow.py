import logging

import pytest

from agent.llm.limits import ProviderBudget
from agent.llm.providers import GroqAdapter, Groq2Adapter, MistralAdapter
from agent.llm.router import Router
from agent.llm.transport import HttpResponse, MockHttpTransport


def make_router(model2='same', **kwargs):
    now = [0.0]
    sleeps = []
    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds
    transport = MockHttpTransport(responses=[HttpResponse(200, {
        'choices': [{'message': {'content': 'ok'}}], 'usage': {}})] * 10)
    router = Router([GroqAdapter('same', 'fake'), Groq2Adapter(model2, 'fake'),
                     MistralAdapter('same', 'fake')], transport=transport,
                    clock=lambda: now[0], sleep=sleep,
                    tpm_by_provider={'groq': 8000, 'groq2': 8000}, **kwargs)
    return router, now, sleeps, transport


def test_same_model_overflow_avoids_wait_and_only_acquires_actual_attempt():
    router, now, sleeps, transport = make_router()
    assert router.reserve(2, 'reserved')
    assert router.complete('x' * 6000, use_reservation='reserved').provider == 'groq'
    assert router.complete('x' * 6000, use_reservation='reserved').provider == 'groq2'
    assert now[0] == 0
    assert sleeps == []
    assert router._budget.used == 2
    assert router._budget._reserved['reserved'] == 0
    assert len(transport.calls) == 2


@pytest.mark.parametrize('blocked', ['different', 'cooling', 'breaker', 'run', 'daily', 'spend'])
def test_ineligible_alternative_never_steals_slots(blocked):
    router, now, sleeps, transport = make_router('different' if blocked == 'different' else 'same')
    alternative = router._providers[1]
    if blocked == 'cooling':
        router._cooldowns.cool('groq2', 0)
    elif blocked == 'breaker':
        router._breaker._open.add('groq2')
    elif blocked in ('run', 'daily', 'spend'):
        alternative.spend = ProviderBudget(name='groq2', max_calls_per_run=0 if blocked == 'run' else 5,
            max_calls_per_day=0 if blocked == 'daily' else 5, max_spend_usd=0,
            halt_on_exceeded=True, input_usd_per_mtok=1, output_usd_per_mtok=1,
            logger=logging.getLogger(__name__))
        if blocked == 'spend':
            alternative.spend.record_usage(1, 0)
    router.complete('x' * 6000)
    assert router.complete('x' * 6000).provider == 'groq'
    assert now[0] == 120
    assert router._budget.used == 2
    if alternative.spend:
        assert alternative.spend.calls == 0


def test_ready_preferred_wins_and_both_occupied_wait_safely():
    router, now, sleeps, _ = make_router()
    assert router.complete('x' * 6000).provider == 'groq'
    assert router.complete('x' * 6000).provider == 'groq2'
    assert router.complete('x' * 6000).provider == 'groq'
    assert now[0] == 120
    assert sleeps == [120]
    assert router._budget.used == 3


def test_rpm_overflow_and_preferred_ready_tie():
    router, now, sleeps, _ = make_router(rpm_by_provider={'groq': 1, 'groq2': 1})
    assert router.complete('x').provider == 'groq'
    assert router.complete('x').provider == 'groq2'
    now[0] = 120
    assert router.complete('x').provider == 'groq'
    assert sleeps == []
