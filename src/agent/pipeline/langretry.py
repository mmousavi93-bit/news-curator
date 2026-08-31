"""Retry-once forced-Persian recovery for provider language drift.

The understand prompt (config/prompts/understand.txt) already demands
Persian output, but free-tier providers occasionally answer in the source
language anyway. The deterministic gate in compose (langgate.py) then
drops a REAL event from the message -- the 2026-08-30 clean run lost
exactly this way its best tier-2 item (Al Jazeera Arabic: Sudan/Kordofan
displacement, score 9.14, answered in Arabic).

This module retries the cluster ONCE with a trailing forced-Persian
instruction. On any failure the original event survives untouched:
memory keeps the fact, compose's gate keeps the message clean. Zero cost
on compliant answers; one extra router call per drifting cluster
(measured ~1 per quiet run, worst realistic day ~3-5, inside the 51-call
budget the router enforces).
"""

from __future__ import annotations

from agent.pipeline.contract import extract_json, within_bounds
from agent.pipeline.langgate import is_persian_output

FORCE_PERSIAN_LINE = (
    "IMPORTANT -- answer entirely in PERSIAN using only Persian letters. "
    "Never use Arabic letters (ي ك ة ى إ) or Hebrew script."
)


def drifts_from_persian(headline: str, summary: str) -> bool:
    """True when the text compose gates on carries Arabic-only or Hebrew
    markers. Same join as compose.split_persian, so this predicts the
    gate's verdict exactly."""
    return not is_persian_output(f"{headline}\n{summary}")


def retry_persian(router, prompt: str):
    """One extra router call with the force line appended. Returns the
    LlmResult; the router's budget and breaker still govern it. Never
    raises -- provider errors are router business (Phase 5 contract)."""
    return router.complete(prompt + "\n" + FORCE_PERSIAN_LINE, stage="understand")


def recovery_payload(router, prompt: str):
    """(parsed, status) -- run the retry and the full output contract on
    the answer. A payload is returned only when it is parseable,
    in-bounds, kept by the content filter, AND Persian; otherwise
    (None, reason) and the caller keeps the original event."""
    result = retry_persian(router, prompt)
    if not result.ok:
        return None, result.status
    try:
        parsed = extract_json(result.text)
    except ValueError:
        return None, "unparseable"
    ok_bounds, _reason = within_bounds(parsed, len(result.text or ""))
    if not ok_bounds:
        return None, "oversized"
    if parsed.get("clickbait") or parsed.get("irrelevant"):
        return None, "filtered"
    headline = str(parsed.get("headline") or "").strip()
    summary = str(parsed.get("summary") or parsed.get("headline") or "")
    if drifts_from_persian(headline, summary):
        return None, "still_non_persian"
    return parsed, "ok"
