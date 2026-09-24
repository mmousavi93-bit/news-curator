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
import re

_FENCE_RE_OPEN = "```"

# Output-contract bounds (the prompt asks for a ~15-word headline and 2-3
# sentence mini-brief summary). Enforced HERE, not at the API boundary.
HEADLINE_WORD_BOUNDS = (2, 25)
SUMMARY_WORD_BOUNDS = (2, 60)
# Trim-not-drop band (Session 22): a summary over the 60-word bound but at
# or under this ceiling is only SLIGHTLY over, not a ramble -- a 61-90-word
# mini-brief is a formatting miss (the prompt asks for "2 to 60 words";
# mistral/ministral overshoot anyway and 4-24 clusters/run were being
# DROPPED for it). `within_bounds` admits the band and `trim_summary` cuts
# it back to the bound in build_event. Past the ceiling, or past
# MAX_RESPONSE_CHARS, the answer is the ramble class and still drops.
SUMMARY_SOFT_CEILING_WORDS = 90
# Optional "why it matters" context line (owner 2026-09-06). Used as a TRIM
# in build_event, NOT a gate -- an over-long context line is dropped, never
# sinks the event (headline/summary gate the event; context degrades
# gracefully). Lower bound 1: anything non-empty is acceptable.
WHY_MATTERS_WORD_BOUNDS = (1, 30)
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
    if summary_words > SUMMARY_WORD_BOUNDS[1]:
        # Over the bound. Inside the soft ceiling with a usable sentence
        # boundary it is a trimmable over-run, not a ramble: ADMIT it and
        # let build_event cut it to SUMMARY_WORD_BOUNDS[1] (trim-not-drop,
        # Session 22). Everything else keeps the old fate.
        if (summary_words <= SUMMARY_SOFT_CEILING_WORDS
                and trim_summary(summary) is not None):
            return True, ""
        return False, f"summary {summary_words} words (bounds {SUMMARY_WORD_BOUNDS})"
    if summary_words < SUMMARY_WORD_BOUNDS[0]:
        return False, f"summary {summary_words} words (bounds {SUMMARY_WORD_BOUNDS})"
    return True, ""


# Sentence end + whatever legitimately trails it (closing quote, bracket,
# space) so a cut lands AFTER the full stop, never inside it.
_SENTENCE_END_RE = re.compile(r"[.!?؟…]+[\s»\"')\]]*")


def _sentence_prefixes(text: str) -> list[str]:
    """Whole-sentence prefixes: "a. b. c." -> ["a.", "a. b.", "a. b. c."].
    A run-on with no terminator yields one prefix, the whole text -- it has
    no sentence boundary to cut on, which is exactly what `trim_summary`
    must not paper over by slicing mid-thought."""
    prefixes: list[str] = []
    start = 0
    for match in _SENTENCE_END_RE.finditer(text):
        if match.end() <= start:
            continue
        prefixes.append(text[:match.end()].strip())
        start = match.end()
    tail = text[start:].strip()
    if tail:
        prefixes.append(text.strip())
    return [prefix for prefix in prefixes if prefix]


def trim_summary(text: str, max_words: int = SUMMARY_WORD_BOUNDS[1]) -> str | None:
    """The trim-not-drop cut (Session 22). Returns:

      * `text` unchanged when it already fits `max_words`;
      * else the LONGEST whole-sentence prefix that fits `max_words` and
        keeps at least `max_words // 2` words -- the prompt asks for a 2-3
        sentence mini-brief, so in practice this is "the first two
        sentences", and a shorter cut is a truncation artifact;
      * else None: no sentence boundary fits inside the bound (a single
        unterminated 61+ word run-on) or the text is past
        SUMMARY_SOFT_CEILING_WORDS -- the ramble class, which still drops.

    Pure and deterministic, zero LLM calls: same input, same cut, and
    `text` is never mutated -- callers apply the return value (build_event
    does). Words are whitespace-split, the same count `within_bounds` uses.
    """
    if len(text.split()) <= max_words:
        return text
    if len(text.split()) > SUMMARY_SOFT_CEILING_WORDS:
        return None
    best: str | None = None
    keep_min = max_words // 2  # a cut keeping less than half the budget is
    # a truncation artifact (e.g. a 2-word first sentence), not a mini-brief.
    for prefix in _sentence_prefixes(text):
        count = len(prefix.split())
        if keep_min <= count <= max_words:
            best = prefix
    return best


def _strip_fences(text) -> str:
    """The model may wrap JSON in markdown fences. Strip them. None
    arrives when a provider answers 200 with `content: null` (an
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
    return stripped


def extract_json(text: str) -> dict:
    """Parse one response into a JSON OBJECT. Raises ValueError on
    anything unparseable -- the caller skips the cluster, because feeding
    a half-parse downstream invents content."""
    parsed = json.loads(_strip_fences(text))
    if not isinstance(parsed, dict):
        raise ValueError("response is not a JSON object")
    return parsed


# Keys a model may use when it wraps the batch array in an object. Only a
# list OF DICTS carrying a "key" field qualifies as the array -- an element's
# own list fields (e.g. `entities`, a list of strings) must never be mistaken
# for it (constraint 11).
_WRAPPER_KEY_HINTS = (
    "results", "clusters", "items", "summaries", "events", "entries", "data", "output",
)


def _is_element_array(value) -> bool:
    """The batch array's elements are dicts carrying the echoed `key` field
    (`map_results` maps by it). Anything else is not the array."""
    return isinstance(value, list) and all(
        isinstance(e, dict) and "key" in e for e in value
    )


def _unwrap_wrapped_array(obj: dict) -> list | None:
    """Return the array a model wrapped in an object, or None when there is
    no unambiguous one. Prefer a single element-array value; fall back to a
    single element-array under a known wrapper key. The single-object case
    (a dict whose fields ARE one cluster) has no element-array value and
    returns None -- it stays unparseable, since guessing a per-cluster
    mapping invents content (constraint 11)."""
    arrays = [v for v in obj.values() if _is_element_array(v)]
    if len(arrays) == 1:
        return arrays[0]
    hinted = [v for k, v in obj.items()
              if _is_element_array(v) and k.lower() in _WRAPPER_KEY_HINTS]
    if len(hinted) == 1:
        return hinted[0]
    return None


def _scan_first_array(text: str) -> list | None:
    """Bracket-scan the first balanced `[...]` that parses to a list.

    Handles a model that prefixes prose before the array or appends trailing
    text. Deterministic and bounded -- it re-parses text the model already
    produced, never invents content (constraint 11)."""
    start = text.find("[")
    while start != -1:
        depth = 0
        in_str = False
        escaped = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    candidate = text[start:i + 1]
                    try:
                        parsed = json.loads(candidate)
                    except ValueError:
                        break  # span isn't valid JSON; advance to the next '['
                    return parsed if isinstance(parsed, list) else None
        start = text.find("[", start + 1)
    return None


def extract_json_array(text: str) -> list:
    """The batch contract (session 9s): the response is a JSON ARRAY, one
    object per cluster.

    Repair ladder before failing (zero LLM cost): (1) an object wrapping the
    array -> unwrap its element-array value; (2) prose-wrapped / trailing
    junk -> scan the first balanced `[...]`. Only an unambiguous ARRAY is
    accepted; a dict whose fields ARE the clusters (the single-object case)
    still raises, because guessing the per-cluster mapping invents content
    (constraint 11)."""
    stripped = _strip_fences(text)
    try:
        parsed = json.loads(stripped)
    except ValueError:
        arr = _scan_first_array(stripped)
        if arr is not None:
            return arr
        raise ValueError("response is not valid JSON") from None
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        arr = _unwrap_wrapped_array(parsed)
        if arr is not None:
            return arr
        raise ValueError("response is a JSON object, not an array") from None
    raise ValueError("response is not a JSON array (scalar)")
