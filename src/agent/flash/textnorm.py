"""Text normalization shared by the flash matcher, the config loader and
pipeline/relevance.py (reviewer finding 2026-08-31: keywords were stored
RAW while item text was normalized — ZWNJ-carrying keywords like
«آتش‌بازی» could never match). One function, one direction, applied to
BOTH sides.

Extended 2026-09-06 (round-3 review, fix 1): relevance.py was matching
Arabic-orthography source text against Persian-only keywords with plain
casefold() -- «إيران»/«غزة»/«إسرائيل» never matched «ایران»/«غزه»/«اسرائیل»,
measured at 9% Arabic on-mission vs 83% Persian on the same run. Added
«آ» (Alef madda) -> ا, alongside the existing «أ»/«إ», plus Arabic-Indic
(٠-٩) and Persian (۰-۹) digit folding to ASCII -- Hebrew has no case and
no orthographic variants against this table so it passes through
unchanged (still lowercased, which is a no-op for Hebrew)."""

from __future__ import annotations

import re
import unicodedata

_ARABIC_TO_PERSIAN = str.maketrans(
    {
        "ي": "ی", "ى": "ی", "ك": "ک", "أ": "ا", "إ": "ا", "آ": "ا", "ة": "ه",
        **{chr(0x0660 + i): str(i) for i in range(10)},  # Arabic-Indic digits
        **{chr(0x06F0 + i): str(i) for i in range(10)},  # Persian digits
    }
)
_TOKEN_RE = re.compile(r"[^\w]+", re.UNICODE)


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    text = text.replace("‌", "")  # strip ZWNJ
    text = text.translate(_ARABIC_TO_PERSIAN)
    return text.lower()


def tokens(text: str) -> set[str]:
    return {t for t in _TOKEN_RE.split(text) if t}
