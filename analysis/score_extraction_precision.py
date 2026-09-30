"""Compute per-provider extraction precision/recall against hand labels.

Hand labels (ground truth) for the 12 clusters, judged against the STRAT/TACT
signal catalog in config/prompts/signal_extraction.txt. Rules applied:
- Rule 1 (catalog-only, "fits nothing IS nothing"): diplomacy, market moves,
  diplomatic spats, domestic politics, single-source rumours, and out-of-scope
  conflict (Ethiopia) all fire NO signal_id.
- E5 ("officials disclose interception of a major attack") fires for the two
  RAF Fairford clusters: UK police disclosed a foiled terror plot on a US
  airbase used to strike Iran. E3 does NOT (no attack occurred; UK is not the
  Gulf region). The plot was intercepted -> E5.
- The oil-price spike (cluster 5) belongs in `economic_events` (MSTRESS), a
  field this probe does not capture -> no signal_id here.
"""
import json

GT = {
    "8f763544d3366e5a": set(),   # Iran nukes/ceasefire/talks -> diplomacy, none
    "edcf4ad4bea1bc32": {"E5"},  # RAF Fairford terror probe -> interception
    "2ef0e7ca85c68928": set(),   # Netanyahu-MBZ secret meeting -> none
    "50ff502e79a7a2ee": {"E5"},  # RAF Fairford 5 arrests -> interception
    "92bbf001df76e071": set(),   # oil-price spike -> economic_events (not captured)
    "27a28822ae5134c9": set(),   # Israel-Dutch diplomatic spat -> none
    "b283f7119422e41d": set(),   # mixed/garbage cluster -> none
    "fe5be7afec93cad0": set(),   # Ben Gvir prisoner threat -> none (borderline D4)
    "0671a90d04650f47": set(),   # car-scrappage scheme -> none
    "13f061bc31994d1f": set(),   # Hormuz "card burning" rumour -> none (unconfirmed)
    "1867962347f1d572": set(),   # Rouhani commentary -> none
    "1f185d2589cf7c7a": set(),   # Ethiopia-Tigray -> out of scope (E4 wrong)
}

rows = [json.loads(l) for l in open(
    "probe_results/extraction_predictions.jsonl", encoding="utf-8")]

providers = {}
for r in rows:
    p = r["provider"]
    providers.setdefault(p, {"usable": 0, "unusable": 0, "tp": 0, "fp": 0, "fn": 0})
    true = GT[r["cluster_key"]]
    pred = {s["signal_id"] for s in r["signals"]}
    if r["status"] in ("unavailable", "unparseable"):
        providers[p]["unusable"] += 1
        # no signal predicted; every true signal in this cluster is missed
        providers[p]["fn"] += len(true)
    else:
        providers[p]["usable"] += 1
        tp = len(pred & true)
        fp = len(pred - true)
        fn = len(true - pred)
        providers[p]["tp"] += tp
        providers[p]["fp"] += fp
        providers[p]["fn"] += fn

print(f"{'provider':9s} {'avail%':>7s} {'TP':>3s} {'FP':>3s} {'FN':>3s} "
      f"{'prec':>6s} {'recall':>7s}")
for p, m in sorted(providers.items()):
    total = m["usable"] + m["unusable"]
    avail = m["usable"] / total * 100 if total else 0
    prec = m["tp"] / (m["tp"] + m["fp"]) if (m["tp"] + m["fp"]) else float("nan")
    rec = m["tp"] / (m["tp"] + m["fn"]) if (m["tp"] + m["fn"]) else float("nan")
    prec_s = f"{prec:.3f}" if prec == prec else "n/a"
    rec_s = f"{rec:.3f}" if rec == rec else "n/a"
    print(f"{p:9s} {avail:6.1f}% {m['tp']:3d} {m['fp']:3d} {m['fn']:3d} "
          f"{prec_s:>6s} {rec_s:>7s}")
