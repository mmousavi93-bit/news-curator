"""UI labels and category icons for the composer.

Split out of compose.py to keep that file under the ~200-line cap.
`output_language` selects the set: "fa" is the owner's choice
(2026-08-29); anything else falls back to English rather than failing --
the strings are cosmetic, not scoring. Category icons are the "related
symbols" the owner asked for: one glance tells the reader what KIND of
news each line is before reading a word.
"""

from __future__ import annotations

from typing import Mapping

CATEGORY_ICONS: Mapping[str, str] = {
    "military": "⚔️",
    "security": "🛡️",
    "politics": "🏛️",
    "economy": "💰",
    "other": "🌐",
}

CATEGORY_NAMES: Mapping[str, Mapping[str, str]] = {
    "fa": {
        "military": "نظامی",
        "security": "امنیتی",
        "politics": "سیاسی",
        "economy": "اقتصادی",
        "other": "سایر",
    },
    "en": {
        "military": "military",
        "security": "security",
        "politics": "politics",
        "economy": "economy",
        "other": "other",
    },
}

LABELS: Mapping[str, Mapping[str, str]] = {
    "fa": {
        "header": "مرور اخبار",
        "digest_marker": "مرور روزانه",
        "tehran": "تهران",
        "lang_dropped": (
            "در این اجرا خبر جمع شد اما به فارسی آماده نشد؛ "
            "خبرها ذخیره شدند و در اجراهای بعدی پوشش داده می‌شوند."
        ),
        "nothing_new": "چیز تازهای نسبت به اجرای قبلی نیامده.",
        "raw_fallback": "⚠️ خلاصه خودکار در دسترس نیست — عناوین خام منابع:",
        "ai_unavailable": (
            "هوش مصنوعی در این اجرا در دسترس نبود؛ خبرها جمعآوری و "
            "ذخیره شدند اما خلاصهای ساخته نشد."
        ),
        "time_not_stated": "زمان اعلام نشده",
        "date_unknown": "تاریخ نامعلوم",
        "rumour": "شایعه",
        "lead_header": "کانال سرنخها (تأییدنشده، وزن صفر)",
        "lead_prefix": "سرنخ",
    },
    "en": {
        "header": "News Curator",
        "digest_marker": "daily digest",
        "tehran": "Tehran",
        "nothing_new": "Nothing new since the last run.",
        "raw_fallback": "⚠️ Automated summaries unavailable — raw source titles:",
        "ai_unavailable": (
            "AI unavailable this run -- items were collected and stored, "
            "but nothing could be summarised."
        ),
        "lang_dropped": (
            "Events were collected this run but could not be rendered in "
            "Persian; they are stored and will be covered by later runs."
        ),
        "time_not_stated": "time not stated",
        "date_unknown": "date unknown",
        "rumour": "RUMOUR",
        "lead_header": "News Curator - lead channel (unverified, weight 0)",
        "lead_prefix": "LEAD",
    },
}


def labels_for(language: str) -> Mapping[str, str]:
    return LABELS.get(language, LABELS["en"])


def category_name(language: str, category: str) -> str:
    names = CATEGORY_NAMES.get(language, CATEGORY_NAMES["en"])
    return names.get(category, names["other"])


def category_icon(category: str) -> str:
    return CATEGORY_ICONS.get(category, CATEGORY_ICONS["other"])
