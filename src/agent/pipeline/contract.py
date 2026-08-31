"""The understand stage's response contract: parse and bounds.

Split out of pipeline/understand.py 2026-08-30 when the stage crossed the
~200-line cap (constraint 12). Deterministic, zero LLM calls. Shared with
tools/probe_free_models.py so the probe measures exactly the contract the
pipeline enforces.

The bounds exist because gateways can ignore max_tokens: 2026-08-30, bai
returned 6,587 / 3,114 / 2,626 output tokens on a ~400-token task and the
JSON parsed anyway. Field bounds alone were not enough -- the ramble can
hide in ANY field -- so the raw response length is capped too.
"""

from __future__ import annotations

import json

_FENCE_RE_OPEN = "```"

# Output-contract bounds (the prompt asks for a ~15-word headline and 1-2
# sentence summary). Enforced HERE, not at the API boundary.
HEADLINE_WORD_BOUNDS = (2, 25)
SUMMARY_WORD_BOUNDS = (2, 60)
# A compliant digest answer is ~120 words ≈ well under 1,000 chars of JSON.
MAX_RESPONSE_CHARS = 2000


def within_bounds(payload: dict, raw_len: int = 0) -> tuple[bool, str]:
    """(ok, reason) -- the digest's output contract on one parsed response.
    `raw_len` is the length of the raw response text; when it exceeds
    MAX_RESPONSE_CHARS the answer is a ramble wherever it hid."""
    if raw_len > MAX_RESPONSE_CHARS:
        return False, f"response too long ({raw_len} chars > {MAX_RESPONSE_CHARS})"
    headline = payload.get("headline") or ""
    summary = payload.get("summary") or ""
    if not isinstance(headline, str) or not isinstance(summary, str):
        return False, "headline/summary not strings"
    headline_words = len(headline.split())
    if not (HEADLINE_WORD_BOUNDS[0] <= headline_words <= HEADLINE_WORD_BOUNDS[1]):
        return False, f"headline {headline_words} words (bounds {HEADLINE_WORD_BOUNDS})"
    summary_words = len(summary.split())
    if not (SUMMARY_WORD_BOUNDS[0] <= summary_words <= SUMMARY_WORD_BOUNDS[1]):
        return False, f"summary {summary_words} words (bounds {SUMMARY_WORD_BOUNDS})"
    return True, ""


def extract_json(text: str) -> dict:
    """The model may wrap JSON in markdown fences. Strip them, then parse.
    Raises ValueError on anything unparseable -- the caller skips the
    cluster, because feeding a half-parse downstream invents content.
    None arrives when a provider answers 200 with `content: null` (an
    empty/refusal answer); it is unparseable by definition, never a crash
    (2026-08-30: exactly this None crashed a whole run mid-pipeline)."""
    if not isinstance(text, str):
        raise ValueError("response content is missing (null) -- provider did not answer")
    stripped = text.strip()
    if stripped.startswith(_FENCE_RE_OPEN):
        first_newline = stripped.find("\n")
        stripped = stripped[first_newline + 1:] if first_newline != -1 else stripped[3:]
    if stripped.endswith(_FENCE_RE_OPEN):
        stripped = stripped[: stripped.rfind(_FENCE_RE_OPEN)].strip()
    parsed = json.loads(stripped)
    if not isinstance(parsed, dict):
        raise ValueError("response is not a JSON object")
    return parsed
