"""The synthesis line (Session 25): ONE extra LLM call that turns the digest's
top events into a single 'big picture' sentence for an Iranian reader.

OPTIONAL and cosmetic: any failure -- LLM call, parse, word bounds, non-Persian
-- omits the line and never sinks the digest. Grounding is enforced in
config/prompts/synthesis.txt; the word bound + Persian-script gate here are
the deterministic backstop. No new content is invented by THIS module -- it
only trims/omits the model's answer.
"""

from __future__ import annotations

import logging

from agent.memory.event_models import Event
from agent.pipeline.contract import SYNTHESIS_WORD_BOUNDS, extract_json
from agent.pipeline.langgate import is_persian_output


def _format_event(event: Event) -> str:
    parts = [f"- [{event.category} | {event.significance}] {event.headline}"]
    if event.summary:
        parts.append(f"  {event.summary}")
    if event.why_matters:
        parts.append(f"  چرا مهم است: {event.why_matters}")
    return "\n".join(parts)


def _has_persian_script(text: str) -> bool:
    """True when the line carries at least one Arabic-script codepoint --
    the cheap guard against a fully English answer slipping past the
    Arabic-marker gate (is_persian_output only proves 'not Arabic drift')."""
    return any("\u0600" <= ch <= "\u06ff" for ch in text)


def build_synthesis(
    ctx, events: list[Event], template: str, logger: logging.Logger,
) -> str:
    """Return the synthesis sentence, or '' when it cannot be produced.

    Skips on: fewer than two events, no router (dry-run/mock), or an empty
    template. A failed call, unparseable answer, out-of-bounds length, or a
    non-Persian line all omit the line (logged, never raised)."""
    if not template or len(events) < 2 or getattr(ctx, "router", None) is None:
        return ""
    prompt = template.replace(
        "{events}", "\n\n".join(_format_event(e) for e in events)
    )
    result = ctx.router.complete(prompt, stage="synthesis")
    if not result.ok:
        logger.info(
            "synthesis: LLM unavailable (status=%s) -- line omitted", result.status
        )
        return ""
    try:
        parsed = extract_json(result.text)
    except ValueError as exc:
        logger.info("synthesis: unparseable (%s) -- line omitted", exc)
        return ""
    line = str(parsed.get("synthesis") or "").strip()
    words = len(line.split())
    if not (SYNTHESIS_WORD_BOUNDS[0] <= words <= SYNTHESIS_WORD_BOUNDS[1]):
        logger.info(
            "synthesis: %d words outside %s -- line omitted",
            words, SYNTHESIS_WORD_BOUNDS,
        )
        return ""
    if not is_persian_output(line) or not _has_persian_script(line):
        logger.info("synthesis: not Persian output -- line omitted")
        return ""
    return line
