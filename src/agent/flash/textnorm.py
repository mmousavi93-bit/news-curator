"""Text normalization shared by the flash matcher and the config loader
(reviewer finding 2026-08-31: keywords were stored RAW while item text
was normalized — ZWNJ-carrying keywords like «آتش‌بازی» could never
match). One function, one direction, applied to BOTH sides."""

from __future__ import annotations

import re
import unicodedata

_ARABIC_TO_PERSIAN = str.maketrans(
    {"ي": "ی", "ى": "ی", "ك": "ک", "أ": "ا", "إ": "ا", "ة": "ه"}
)
_TOKEN_RE = re.compile(r"[^\w]+", re.UNICODE)


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    text = text.replace("‌", "")  # strip ZWNJ
    text = text.translate(_ARABIC_TO_PERSIAN)
    return text.lower()


def tokens(text: str) -> set[str]:
    return {t for t in _TOKEN_RE.split(text) if t}
