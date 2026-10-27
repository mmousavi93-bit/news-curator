"""Real composer, formatter and SQLite; only the LLM response is simulated."""
import json
import logging
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from agent.config import Config
from agent.llm.errors import LlmResult
from agent.memory.db import open_db
from agent.memory.event_models import Event, mark_delivered
from agent.pipeline.compose import ComposeStage
from agent.pipeline.queue import store_pending
from agent.settings import Settings

NOW = datetime(2026, 8, 20, 12, tzinfo=timezone.utc)
LINE = 'این دو گزارش از ادامه مذاکرات درباره توافق خبر می‌دهند.'
LOG = logging.getLogger(__name__)


class Router:
    def __init__(self, line=LINE):
        self.line = line
        self.inputs = []

    def complete(self, prompt, stage):
        # The production input is a JSON array at the end of the template.
        events = json.loads(prompt[prompt.index('['):])
        self.inputs.append(events)
        data = {'synthesis': self.line, 'evidence': [
            {'event_id': e['event_id'], 'quote': e['summary']} for e in events[:2]]}
        return LlmResult(ok=True, status='ok', text=json.dumps(data))


def context(tmp_path, max_chars=4096, lengths=(8, 8, 8), router=None):
    settings = Settings.from_dict(yaml.safe_load(
        (Path(__file__).parents[1] / 'fixtures/settings_minimal.yaml').read_text(encoding='utf-8')))
    settings = replace(settings,
        delivery=replace(settings.delivery, telegram_max_chars=max_chars),
        digest_rank=replace(settings.digest_rank, max_messages=1))
    events = [Event(event_key=f'key{i}', headline='عنوان مشترک خبر',
                    summary=('گزارش تازه مذاکرات ادامه یافت ' * n).strip(),
                    category='military', significance='escalation',
                    claim_status='confirmed', independent_count=3,
                    first_seen_at=NOW, last_updated_at=NOW)
              for i, n in enumerate(lengths)]
    ctx = SimpleNamespace(config=Config(settings=settings, credibility={}), events=events,
        clusters=[], now=NOW, counters={}, router=router,
        db=open_db(tmp_path / 's.db', create_if_absent=True))
    return ctx


def compose(ctx, template=''):
    ComposeStage(LOG, synthesis_template=template).run(ctx)


def identity(ctx):
    return (ctx.messages, ctx.message_event_keys, ctx.compose_render_forms,
            [e.event_key for e in ctx.compose_truncated], ctx.compose_kept_keys)


@pytest.mark.parametrize('excluded', [False, True])
def test_synthesis_only_sees_base_full_items_and_never_evicted_news(tmp_path, excluded):
    ctx = context(tmp_path, max_chars=1100, lengths=(8, 8, 80), router=Router())
    if excluded:
        ctx.events[2] = replace(ctx.events[2], headline='عنوان بلند خبر ' * 100)
    compose(ctx)
    base = identity(ctx)
    full = {key for key, form in ctx.compose_render_forms.items() if form == 'full'}
    assert len(full) == 2
    assert ctx.compose_render_forms.get('key2') != 'full'
    assert ('key2' not in ctx.compose_render_forms) == excluded
    compose(ctx, '{events}')
    assert len(ctx.router.inputs) == 1
    assert {e['event_id'] for e in ctx.router.inputs[0]} == full
    assert full <= {key for key, form in ctx.compose_render_forms.items() if form == 'full'}
    assert set(ctx.synthesis_evidence_keys) <= full
    assert ctx.db.execute('SELECT COUNT(*) FROM pending').fetchone()[0] == 3
    assert ctx.db.execute('SELECT COUNT(*) FROM delivered').fetchone()[0] == 0


def test_optional_line_cannot_displace_any_base_full_item(tmp_path):
    ctx = context(tmp_path, max_chars=4096, lengths=(8, 8), router=Router(LINE * 2))
    compose(ctx)
    # Leave a single unit of slack after the exact production base pack.
    from agent.delivery.budget import utf16_len
    cap = utf16_len(ctx.messages[0]) + 1
    ctx.config = replace(ctx.config, settings=replace(ctx.config.settings,
        delivery=replace(ctx.config.settings.delivery, telegram_max_chars=cap)))
    compose(ctx)
    base = identity(ctx)
    assert list(ctx.compose_render_forms.values()) == ['full', 'full']
    compose(ctx, '{events}')
    assert identity(ctx) == base
    assert len(ctx.router.inputs) == 1
    assert ctx.synthesis_evidence_keys == []


def test_html_sentence_is_escaped_once_and_exact_keys_survive(tmp_path):
    ctx = context(tmp_path, lengths=(3, 3), router=Router('<a href="evil">' + LINE + '</a> &'))
    compose(ctx, '{events}')
    assert '&lt;a href="evil"&gt;' in ctx.messages[0]
    assert '<a href="evil">' not in ctx.messages[0]
    assert '&amp;lt;' not in ctx.messages[0]
    assert ctx.message_event_keys == [['key0', 'key1']]
    assert ctx.synthesis_evidence_keys == ['key0', 'key1']


def test_one_full_item_skips_synthesis_and_resets_stale_context(tmp_path):
    ctx = context(tmp_path, max_chars=550, lengths=(8, 80, 80), router=Router())
    ctx.synthesis_evidence_keys = ['stale']
    compose(ctx, '{events}')
    assert list(ctx.compose_render_forms.values()).count('full') == 1
    assert ctx.router.inputs == []
    assert ctx.synthesis_evidence_keys == []


def test_pending_only_degraded_drain_uses_rendered_current_events(tmp_path):
    ctx = context(tmp_path, lengths=(3, 3), router=Router())
    store_pending(ctx, [(e, 20) for e in ctx.events], NOW)
    ctx.events = []
    ctx.llm_failed = True
    compose(ctx, '{events}')
    assert ctx.compose_kept_keys == ['key0', 'key1']
    assert ctx.synthesis_evidence_keys == ['key0', 'key1']
    assert len(ctx.router.inputs) == 1
    assert ctx.db.execute('SELECT COUNT(*) FROM pending').fetchone()[0] == 2
    mark_delivered(ctx.db, ctx.compose_kept_keys, NOW)
    compose(ctx, '{events}')
    assert ctx.compose_kept_keys == []
    assert ctx.synthesis_evidence_keys == []
    assert len(ctx.router.inputs) == 1
