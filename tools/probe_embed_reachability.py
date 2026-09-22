#!/usr/bin/env python3
"""Embedding reachability probe: does the HIGH band (sim >= 0.80) stay reachable
for a *developed* story, or does adding a new fact push the similarity into the
MID band?

Background (Session 21 finding): the `follow_up_high` compact-line path is
unit-tested but has never been observed in production. Production data shows:
  - distinct stories       -> cosine 0.40..0.63  (MID / below floor)
  - same story, reworded   -> cosine 0.84..0.85  (HIGH band, but `no_development`,
                             correctly dropped as a verbatim repeat)
  - same story + NEW FACT  -> NO SAMPLE (this probe)

If a developed story stays >= 0.80, the HIGH-band developed-follow-up line is
live. If it drops below 0.80, every "development" lands in the MID band and
renders as a full entry with the "peygiri" marker instead -- i.e. the compact
`follow_up_high` branch is effectively unreachable. Either way this settles the
question with the real production embedder.

Mirrors MiniLmEmbedder.embed() exactly: SentenceTransformer(model) +
encode(normalize_embeddings=True), so cosine == dot product. Model name is
config/settings.yaml `embed_model` (owner-editable -- update here if changed).
"""

from __future__ import annotations

import sys

MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"  # settings.yaml embed_model

# Real summaries lifted from run 35719159597 (chosen_*.csv). BASE_HORMOZ and
# REWORDED_HORMOZ are the pair that production measured at sim=0.85 (row
# bc0819a2 vs 6f91c577) -- reproduced here as a calibration anchor.
BASE_HORMOZ = (
    "مقام‌های ایرانی اعلام کردند که در صورت کاهش تحریم‌ها و فشار نظامی آمریکا، "
    "تنگه هرمز ظرف هفت روز بازگشایی خواهد شد. این پیشنهاد از طریق میانجی‌ها به "
    "واشنگتن منتقل شده و هیئت ایرانی در نیویورک اختیار مذاکره دارد."
)
REWORDED_HORMOZ = (
    "ایران اعلام کرد در صورت کاهش فشار نظامی و رفع محاصره بنادر، آماده بازگشایی "
    "تنگه هرمز در هفت روز آینده است. هیئت ایرانی در نیویورک اختیار مذاکره از "
    "طریق میانجی‌ها را دارد."
)
BASE_IRAQ = (
    "علی الزیدی، رئیس‌جمهور عراق، تعهد کرد گروه‌های مسلح وفادار به ایران را تا "
    "۳۰ ژوئن ۲۰۲۷ منحل و سلاح‌های آن‌ها را جمع‌آوری کند. این تصمیم بخشی از "
    "تلاش‌های بغداد برای تثبیت امنیت داخلی است."
)

# The two "developed" cases: same story, one materially NEW fact appended
# (new number, then new entity) -- the exact trigger for `_content_novelty`.
DEVELOPED_NUMBER = BASE_HORMOZ + (
    " در همین حال وزارت خارجه ایران این پیشنهاد را مشروط به لغو ۱۲ تحریم مشخص کرد."
)
DEVELOPED_ENTITY = BASE_HORMOZ + (
    " قطر نیز برای میانجی‌گری میان تهران و واشنگتن اعلام آمادگی کرد."
)

PAIRS = [
    ("verbatim_control", BASE_HORMOZ, BASE_HORMOZ),
    ("reworded_same_fact", BASE_HORMOZ, REWORDED_HORMOZ),
    ("developed_new_number", BASE_HORMOZ, DEVELOPED_NUMBER),
    ("developed_new_entity", BASE_HORMOZ, DEVELOPED_ENTITY),
    ("different_story", BASE_HORMOZ, BASE_IRAQ),
]

HIGH = 0.80
MID = 0.40


def dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def band(sim: float) -> str:
    if sim >= HIGH:
        return "HIGH"
    if sim >= MID:
        return "MID"
    return "BELOW_FLOOR"


def main() -> int:
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        print("sentence-transformers not installed -- run: pip install -e '.[embeddings]'")
        return 2

    model = SentenceTransformer(MODEL_NAME)
    texts = [t for pair in PAIRS for t in pair[1:]]
    vectors = model.encode(texts, normalize_embeddings=True)

    print(f"model: {MODEL_NAME}")
    print(f"normalize_embeddings: True (cosine == dot product)")
    print(f"bands: HIGH >= {HIGH}, MID >= {MID}\n")
    print(f"{'pair':24} {'sim':>6}  band           verdict")
    print("-" * 72)

    verdicts = []
    for i, (label, _, _) in enumerate(PAIRS):
        sim = dot(vectors[2 * i].tolist(), vectors[2 * i + 1].tolist())
        b = band(sim)
        if label.startswith("developed"):
            verdict = "HIGH-band follow-up REACHABLE" if b == "HIGH" else "lands in MID -> full entry, not compact line"
        elif label == "reworded_same_fact":
            verdict = "expect ~0.84-0.85 (calibration)"
        elif label == "verbatim_control":
            verdict = "expect ~1.000 (sanity)"
        else:
            verdict = "expect < 0.80 (floor control)"
        verdicts.append((label, sim, b, verdict))
        print(f"{label:24} {sim:>6.3f}  {b:13} {verdict}")

    print("\n---")
    high_dev = [v for v in verdicts if v[0].startswith("developed") and v[2] == "HIGH"]
    print(f"RESULT: developed pairs in HIGH band: {len(high_dev)}/{sum(1 for p in PAIRS if p[0].startswith('developed'))}")
    if high_dev:
        print("-> `follow_up_high` compact line is reachable in production.")
    else:
        print("-> `follow_up_high` compact line is UNREACHABLE; developments render as MID-band full entries.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
