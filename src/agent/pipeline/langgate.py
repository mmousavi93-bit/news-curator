"""Deterministic Persian output gate -- zero LLM calls (constraint 3).

Owner contract (2026-08-30): the digest is Persian whatever the source
language. The live sample the same day showed the drift this guards
against: an Arabic-source cluster produced an Arabic headline and a fully
Arabic summary. The prompt (config/prompts/understand.txt) asks for
Persian; this module is the deterministic backstop that keeps non-Persian
text out of the MESSAGE. Stored events are untouched -- memory keeps the
fact, the display contract stays clean.

Detection is script markers, not a language model, split into two tiers
(session 12, 2026-09-14):

STRONG (any occurrence proves non-Persian -> drop):
- \u0629 taa marbuta and \u0649 alef maqsura: Arabic codepoints Persian
  never uses (Persian writes \u06cc and \u06a9).
- \u0625 hamza-below-alef.
- Hebrew occupies U+0590-U+05FF.

SOFT (ambiguous, only drop at count >= 2):
- \u064a and \u0643. These Arabic forms are NOT unambiguous: Persian LLM
  output (groq) routinely emits one stray \u064a/\u0643 in transliterated
  foreign names. Dropping on a single occurrence silently killed the run's
  only corroborated, "likely" item (2026-09-14: B'Tselem "\u0628\u062a\u0633\u064a\u0644\u0645",
  score 12.04, 2 sources) -- a systematic bias against exactly the
  international, multi-source stories the digest exists for. Two or more
  soft markers in one headline+summary is genuine Arabic drift, not a
  transliteration.

Persian-only letters (\u067e \u0686 \u0698 \u06af \u0622 \u0626 \u0621) are NOT markers --
they must pass.
\u0623 (hamza-above-alef) is DELIBERATELY not a marker: Persian
productively uses it (\u062a\u0623\u06cc\u06cc\u062f, \u062a\u0623\u062b\u06cc\u0631, \u0645\u0623\u0645\u0648\u0631\u06cc\u062a), and
dropping events on it would silently lose exactly the stories the digest
exists for (review finding 2026-08-30). The fully-Arabic drift observed in
the live sample carries \u0629 and \u064a, so it is still caught (by \u0629 alone).
English/Latin drift is NOT detected here: Persian text legitimately
contains Latin (numbers, acronyms, names). The prompt covers that class;
this gate only enforces what it can prove.
"""

from __future__ import annotations

from typing import Sequence

from agent.memory.event_models import Event

# Spelled as escapes so the source cannot silently change script if a tool
# mangles the file's encoding (same convention as the test file).
_STRONG = frozenset("\u0629\u0649\u0625")  # ة ى إ  -- any occurrence = drop
_SOFT = frozenset("\u064a\u0643")          # ي ك  -- count >= 2 = drop
_SOFT_MIN = 2
_HEBREW_BLOCK = (0x0590, 0x05FF)


def is_persian_output(text: str) -> bool:
    """True when `text` carries no STRONG Arabic/Hebrew markers and fewer
    than _SOFT_MIN soft markers (ي/ك)."""
    soft = 0
    for ch in text:
        codepoint = ord(ch)
        if ch in _STRONG or _HEBREW_BLOCK[0] <= codepoint <= _HEBREW_BLOCK[1]:
            return False
        if ch in _SOFT:
            soft += 1
    return soft < _SOFT_MIN


def split_persian(events: Sequence[Event]) -> tuple[list[Event], list[Event]]:
    """(kept, dropped): dropped = events whose headline+summary carry a
    strong Arabic/Hebrew marker or >= 2 soft ي/ك markers. Gates the message,
    never the memory."""
    kept: list[Event] = []
    dropped: list[Event] = []
    for event in events:
        text = f"{event.headline}\n{event.summary}"
        (kept if is_persian_output(text) else dropped).append(event)
    return kept, dropped
