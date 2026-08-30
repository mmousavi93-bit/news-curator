"""Deterministic Persian output gate -- zero LLM calls (constraint 3).

Owner contract (2026-08-30): the digest is Persian whatever the source
language. The live sample the same day showed the drift this guards
against: an Arabic-source cluster produced an Arabic headline and a fully
Arabic summary. The prompt (config/prompts/understand.txt) asks for
Persian; this module is the deterministic backstop that keeps non-Persian
text out of the MESSAGE. Stored events are untouched -- memory keeps the
fact, the display contract stays clean.

Detection is script markers, not a language model:
- Arabic has codepoints Persian never uses: ة U+0629, ى U+0649, ي U+064A,
  ك U+0643, إ U+0625. Persian writes ی U+06CC and ک U+06A9.
- Hebrew occupies U+0590-U+05FF.
Persian-only letters (پ چ ژ گ آ ئ ء) are NOT markers -- they must pass.
أ U+0623 (hamza-above-alef) is DELIBERATELY not a marker: Persian
productively uses it (تأیید، تأثیر، مأموریت), and dropping events on it
would silently lose exactly the stories the digest exists for
(review finding 2026-08-30). The fully-Arabic drift observed in the live
sample carries ة and ي, so it is still caught.
English/Latin drift is NOT detected here: Persian text legitimately
contains Latin (numbers, acronyms, names). The prompt covers that class;
this gate only enforces what it can prove.
"""

from __future__ import annotations

from typing import Sequence

from agent.memory.event_models import Event

_ARABIC_ONLY = frozenset("ةىيكإ")
_HEBREW_BLOCK = (0x0590, 0x05FF)


def is_persian_output(text: str) -> bool:
    """True when `text` carries no Arabic-only or Hebrew codepoints."""
    for ch in text:
        codepoint = ord(ch)
        if ch in _ARABIC_ONLY or _HEBREW_BLOCK[0] <= codepoint <= _HEBREW_BLOCK[1]:
            return False
    return True


def split_persian(events: Sequence[Event]) -> tuple[list[Event], list[Event]]:
    """(kept, dropped): dropped = events whose headline+summary carry
    Arabic-only or Hebrew markers. Gates the message, never the memory."""
    kept: list[Event] = []
    dropped: list[Event] = []
    for event in events:
        text = f"{event.headline}\n{event.summary}"
        (kept if is_persian_output(text) else dropped).append(event)
    return kept, dropped
