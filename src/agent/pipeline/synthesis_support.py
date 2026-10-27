"""Structural grounding guard; this does not prove semantic entailment."""

import re
import unicodedata

from agent.delivery.formatter import format_split_tracked
from agent.delivery.message import Message

UNVERIFIED_PREFIX = "بر پایه گزارش‌های تأییدنشده: "
MAX_EVIDENCE = 10


def normalized(text):
    return " ".join(text.split())


def numerals(text):
    digits = "".join(str(unicodedata.decimal(c)) if c.isdecimal() else c for c in text)
    return set(re.findall(
        r"(?:(?<!\w)[+\-−])?\d+(?:[.,٫٬:/-]\d+)*(?:[eE][+\-−]?\d+)?", digits))


def grounded_line(parsed, events):
    """Return (line, exact keys), or an empty pair for invalid evidence."""
    if not isinstance(parsed, dict) or not isinstance(parsed.get("synthesis"), str):
        return "", []
    line = parsed["synthesis"].strip()
    evidence = parsed.get("evidence")
    if not isinstance(evidence, list) or not 2 <= len(evidence) <= MAX_EVIDENCE:
        return "", []
    known = {e.event_key: e for e in events}
    keys, quotes = [], []
    for item in evidence:
        if not isinstance(item, dict):
            return "", []
        key, quote = item.get("event_id"), item.get("quote")
        if not isinstance(key, str) or key not in known or not isinstance(quote, str):
            return "", []
        quote = normalized(quote)
        event = known[key]
        if (len(quote.split()) < 3 or not any(
                quote in normalized(text) for text in (event.headline, event.summary))):
            return "", []
        if key not in keys:
            keys.append(key)
        quotes.append(quote)
    if len(keys) < 2 or not numerals(line) <= numerals(" ".join(quotes)):
        return "", []
    return line, keys


def pack_digest(ctx, header, items, events, footer, template, labels, logger):
    from agent.pipeline.synthesis import build_synthesis

    settings = ctx.config.settings

    def pack(candidate_header):
        forms = []
        # The formatter HTML-escapes the entire header, including model text,
        # exactly once before inserting it into rendered HTML.
        messages, truncated = format_split_tracked(
            Message(header=candidate_header, items=tuple(items), footer=footer or None),
            max_units=settings.delivery.telegram_max_chars,
            max_messages=settings.digest_rank.max_messages, page_forms=forms)
        return messages, truncated, forms

    base = pack(header)
    full = {i for forms in base[2] for i, form in forms.items() if form == "full"}
    if not settings.digest_rank.synthesis_enabled or len(full) < 2 or not template:
        return base
    line = build_synthesis(ctx, [e for i, e in enumerate(events) if i in full],
                           template, logger)
    if not line:
        return base
    candidate = pack(f"{header}\n{labels['synthesis_header']}: {line}")
    candidate_full = {i for forms in candidate[2] for i, form in forms.items()
                      if form == "full"}
    if not full <= candidate_full:
        ctx.synthesis_evidence_keys = []
        return base
    return candidate
