import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from agent.llm.errors import LlmResult
from agent.pipeline.synthesis import build_synthesis
from agent.memory.event_models import Event
import logging

TPL = 'SYNTH PROMPT\n{events}'


def _Log():
    return logging.getLogger(__name__)


def _events():
    return [Event(event_key=f'k{i}', headline=f'تیتر {i}',
                  summary=f'گزارش تازه رویداد {i}', claim_status='confirmed')
            for i in range(2)]

LINE = 'این دو گزارش از ادامه مذاکرات درباره توافق خبر می‌دهند.'


class Router:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def complete(self, prompt, stage):
        self.calls.append((prompt, stage))
        return LlmResult(ok=True, status='ok', text=json.dumps(self.payload))


def payload(events, line=LINE):
    return {'synthesis': line, 'evidence': [
        {'event_id': e.event_key, 'quote': e.summary} for e in events]}


def run(data, events=None):
    events = _events() if events is None else events
    ctx = SimpleNamespace(router=Router(data), synthesis_evidence_keys=['stale'])
    return build_synthesis(ctx, events, TPL, _Log()), ctx


def test_valid_evidence_exposes_exact_keys_and_json_untrusted_input():
    events = [replace(e, prior_context='پیشینه جداگانه خبر', why_matters='زمینه ساخته شده')
              for e in _events()]
    result, ctx = run(payload(events), events)
    assert result == LINE
    assert ctx.synthesis_evidence_keys == [e.event_key for e in events]
    prompt = ctx.router.calls[0][0]
    assert 'untrusted' in prompt.lower()
    data = json.loads(prompt[prompt.index('['):])
    assert data == [{'event_id': e.event_key, 'headline': e.headline,
                     'summary': e.summary, 'claim_status': e.claim_status,
                     'previous_context': e.prior_context} for e in events]
    assert 'why_matters' not in prompt
    assert 'زمینه ساخته شده' not in prompt


@pytest.mark.parametrize('case', ['unknown', 'wrong', 'single', 'duplicate', 'why', 'prior',
    'numeric', 'list', 'string', 'null', 'line_type', 'quote_type', 'too_short', 'oversize'])
def test_bad_evidence_is_omitted_and_context_reset(case):
    events = [replace(e, why_matters='زمینه ساخته شده', prior_context='پیشینه قدیمی خبر')
              for e in _events()]
    data = payload(events)
    if case == 'unknown': data['evidence'][0]['event_id'] = 'unknown'
    if case == 'wrong': data['evidence'][0]['quote'] = events[1].summary
    if case == 'single': data['evidence'] = data['evidence'][:1]
    if case == 'duplicate': data['evidence'][1] = data['evidence'][0]
    if case == 'why': data['evidence'][0]['quote'] = events[0].why_matters
    if case == 'prior': data['evidence'][0]['quote'] = events[0].prior_context
    if case == 'numeric': data['synthesis'] = LINE + ' ۷'
    if case == 'list': data = []
    if case == 'string': data['evidence'] = 'invalid'
    if case == 'null': data['evidence'] = None
    if case == 'line_type': data['synthesis'] = ['گزارش تازه رویداد امروز']
    if case == 'quote_type': data['evidence'][0]['quote'] = 123
    if case == 'too_short': data['evidence'][0]['quote'] = 'گزارش تازه'
    if case == 'oversize': data['evidence'] *= 20
    line, ctx = run(data, events)
    assert line == ''
    assert ctx.synthesis_evidence_keys == []


@pytest.mark.parametrize('number,accepted', [('۷۰', True), ('٧٠', True), ('70', True),
    ('7', False), ('۷', False), ('700', False), ('۰۷۰', False)])
def test_unicode_numerals_compare_whole_values(number, accepted):
    events = [replace(e, summary='مذاکرات با حضور 70 نماینده ادامه یافت') for e in _events()]
    line, _ = run(payload(events, LINE + ' ' + number), events)
    assert bool(line) == accepted


def test_numeral_must_be_in_cited_current_evidence():
    events = [replace(e, summary='گزارش تازه مذاکرات ادامه یافت', headline='حضور ۷ نماینده')
              for e in _events()]
    line, _ = run(payload(events, LINE + ' ۷'), events)
    assert line == ''


def test_whitespace_normalized_quotes_and_uncertainty_attribution():
    events = [replace(e, summary='گزارش تازه\n  مذاکرات ادامه یافت', claim_status='rumour')
              for e in _events()]
    data = payload(events)
    for item in data['evidence']:
        item['quote'] = 'گزارش تازه مذاکرات'
    line, ctx = run(data, events)
    assert line == 'بر پایه گزارش‌های تأییدنشده: ' + LINE
    assert all(e.claim_status == 'rumour' for e in events)


def test_skipped_call_resets_evidence_keys():
    ctx = SimpleNamespace(router=None, synthesis_evidence_keys=['stale'])
    assert build_synthesis(ctx, _events(), TPL, _Log()) == ''
    assert ctx.synthesis_evidence_keys == []


@pytest.mark.parametrize('line', ['', 'Today negotiations continue across the region.', 'کوتاه'])
def test_uncertainty_prefix_cannot_make_invalid_line_pass_language_or_word_gate(line):
    events = [replace(e, claim_status='unconfirmed') for e in _events()]
    result, ctx = run(payload(events, line), events)
    assert result == ''
    assert ctx.synthesis_evidence_keys == []


def test_non_string_transport_text_is_safely_omitted():
    ctx = SimpleNamespace(router=SimpleNamespace(complete=lambda *a, **k:
        LlmResult(ok=True, status='ok', text=None)))
    assert build_synthesis(ctx, _events(), TPL, _Log()) == ''
