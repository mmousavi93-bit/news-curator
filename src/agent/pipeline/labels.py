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
        # Session 9s: the raw-title section is gone -- raw untranslated
        # source titles violate the Persian-only tone contract. A count is
        # a fact (constraint 11); {count} arrives pre-formatted in Persian
        # digits from render.py.
        "raw_fallback": "⚠️ {count} خبر بدون خلاصه ماند",
        "ai_unavailable": (
            "هوش مصنوعی در این اجرا در دسترس نبود؛ خبرها جمعآوری و "
            "ذخیره شدند اما خلاصهای ساخته نشد."
        ),
        # Flash-monitor watchdog (9r). Stated as a fact about the monitor,
        # not as an emergency -- the tone contract forbids drama, and the
        # thing that is broken is the plumbing, not the world.
        "flash_stale": (
            "⚠️ پایش هشدار فوری پاسخ نمی‌دهد — "
            "آخرین ثبت وضعیت حدود {hours} ساعت پیش."
        ),
        "flash_missing": "⚠️ پایش هشدار فوری هیچ وضعیتی ثبت نکرده است.",
        "time_not_stated": "زمان اعلام نشده",
        "date_unknown": "تاریخ نامعلوم",
        "rumour": "شایعه",
        "unconfirmed": "تک‌منبع",
        "lead_header": "کانال سرنخها (تأییدنشده، وزن صفر)",
        "lead_prefix": "سرنخ",
        "follow_up": "پیگیری",
    },
    "en": {
        "header": "News Curator",
        "digest_marker": "daily digest",
        "tehran": "Tehran",
        "nothing_new": "Nothing new since the last run.",
        "raw_fallback": "⚠️ {count} stories shipped without a summary",
        "ai_unavailable": (
            "AI unavailable this run -- items were collected and stored, "
            "but nothing could be summarised."
        ),
        "lang_dropped": (
            "Events were collected this run but could not be rendered in "
            "Persian; they are stored and will be covered by later runs."
        ),
        "flash_stale": (
            "⚠️ Flash monitor is not reporting — last state about "
            "{hours}h ago."
        ),
        "flash_missing": "⚠️ Flash monitor has never reported state.",
        "time_not_stated": "time not stated",
        "date_unknown": "date unknown",
        "rumour": "RUMOUR",
        "unconfirmed": "unconfirmed",
        "lead_header": "News Curator - lead channel (unverified, weight 0)",
        "lead_prefix": "LEAD",
        "follow_up": "FOLLOW-UP",
    },
}


def labels_for(language: str) -> Mapping[str, str]:
    return LABELS.get(language, LABELS["en"])


def category_name(language: str, category: str) -> str:
    names = CATEGORY_NAMES.get(language, CATEGORY_NAMES["en"])
    return names.get(category, names["other"])


def category_icon(category: str) -> str:
    return CATEGORY_ICONS.get(category, CATEGORY_ICONS["other"])
