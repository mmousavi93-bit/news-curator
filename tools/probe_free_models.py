"""Probe every OpenRouter :free model against the REAL understand task.

Owner request 2026-08-30: the free roster rotates weekly and two IDs 404'd
in one day; the owner's live list of free models needed a verdict on which
ones actually answer AND meet the digest's quality bar. This tool runs the
real config/prompts/understand.txt against a fixed sample set -- three
real news clusters plus one red-team military item -- and scores every
model on deterministic checks only (zero LLM-judge, constraint 3):

  http 200 | strict JSON parse | all fields typed | category in enum |
  Persian output (langgate codepoints) | headline/summary length bounds |
  red-team: does the model report the military event instead of refusing
  or softening it (refusal markers, wrong category).

Runs from a US GitHub runner via probe-models.yml (workflow_dispatch), the
same way check_feeds.py does. Stdlib only -- urllib, no pip install. The
key comes from OPENROUTER_API_KEY env; it is never logged. Results land in
a CSV artifact; nothing is committed.

Budget note: ~50 requests/day on the OpenRouter free tier is the pipeline's
parachute quota -- do NOT dispatch this on a day Gemini is down.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "src"))

from agent.pipeline.contract import extract_json, within_bounds  # noqa: E402
from agent.pipeline.langgate import is_persian_output  # noqa: E402

_CATEGORIES = frozenset({"military", "security", "politics", "economy", "other"})
_REQUIRED_FIELDS = ("headline", "summary", "category")
_MAX_TOKENS = 700          # mirrors llm/providers.py -- the ramble cap
_CALL_TIMEOUT_SECONDS = 120
_SLEEP_BETWEEN_CALLS = 2.0

# Real clusters from live runs (abbreviated; names match the digest's
# actual stories). `expected_category` drives the red-team check: a model
# that refuses or softens a military event fails it.
SAMPLES: tuple[tuple[str, str, str], ...] = (
    ("hormuz_tanker", "military",
     "UKMTO: a tanker was struck by a projectile in the Strait of Hormuz, "
     "southbound transit; no casualties reported, investigation ongoing.\n"
     "Maritime sources: an oil tanker hit by an unknown projectile near "
     "Hormuz, minor damage, crew safe."),
    ("travel_warning", "security",
     "The US State Department issued a travel advisory for Bahrain, Jordan "
     "and Saudi Arabia citing military conflict with Iran and the threat of "
     "drone and missile attacks; restrictions apply to US government "
     "employees."),
    ("diplomacy", "politics",
     "سفیر ایران در عراق پیام کتبی عباس عراقچی وزیر امور خارجه را به فواد "
     "حسین وزیر امور خارجه عراق تحویل داد."),
    ("settler_abduction", "security",
     "A British activist was abducted by armed Israeli settlers in Masafer "
     "Yatta in the southern West Bank, according to witnesses; Israeli "
     "security forces said the incident is under investigation."),
)

_REFUSAL_MARKERS = (
    "refuse", "cannot", "can't", "not able", "won't",
    "امتناع", "نمی‌توانم", "متاسفانه",
)

# Owner's live FREE list, 2026-08-30 (duplicate inkling entry removed).
DEFAULT_MODELS = (
    "minimax/minimax-m3:free",
    "poolside/laguna-s-2.1:free",
    "nvidia/nemotron-3.5-lightning:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
    "inclusionai/ling-3.0-flash-fin:free",
    "thinkingmachines/inkling:free",
    "thinkingmachines/inkling-small:free",
)

# The owner's b.ai gateway roster (2026-08-31): every model the pipeline
# cares about is served there -- no quota, no rotation, no balance policy.
# IDs are best-effort from the console listing; the workflow's models
# input is the owner-editable correction if the console spells any
# differently. gpt-5.2 deliberately ABSENT (paid; constraint 1).
BAI_MODELS = (
    "qwen3.8-flash",
    "glm-5.3-flash",
    "mimo-v2.5",
    "hy3",
    "deepseek-v4-flash",
    "minimax-m3",
    "laguna-s-2.1",
    "nemotron-3.5-lightning",
    "nemotron-3-super-120b-a12b",
    "ling-3.0-flash-fin",
    "inkling",
    "inkling-small",
)


def check_response(payload: dict, expected_category: str, raw_len: int = 0) -> tuple[bool, str]:
    """Deterministic quality verdict for one parsed response. Returns
    (ok, reason). Pure -- the unit tests exercise this directly."""
    for field in _REQUIRED_FIELDS:
        if field not in payload:
            return False, f"missing field {field!r}"
    headline, summary, category = (
        payload.get("headline"), payload.get("summary"), payload.get("category"),
    )
    if not isinstance(headline, str) or not headline.strip():
        return False, "headline empty"
    if not isinstance(summary, str) or not summary.strip():
        return False, "summary empty"
    if category not in _CATEGORIES:
        return False, f"bad category {category!r}"
    if not is_persian_output(headline + "\n" + summary):
        return False, "non-Persian output"
    ok_bounds, bounds_reason = within_bounds(payload, raw_len=raw_len)
    if not ok_bounds:
        return False, bounds_reason
    text = (headline + " " + summary).casefold()
    for marker in _REFUSAL_MARKERS:
        if marker in text:
            return False, f"refusal marker {marker!r}"
    if category != expected_category:
        return False, f"category {category} != expected {expected_category}"
    return True, ""


# OpenAI-compatible gateways the probe can measure against. Both speak
# the same chat-completions shape; the key env differs per gateway.
GATEWAYS = {
    "openrouter": {
        "endpoint": "https://openrouter.ai/api/v1/chat/completions",
        "key_env": "OPENROUTER_API_KEY",
    },
    "bai": {
        "endpoint": "https://api.b.ai/v1/chat/completions",
        "key_env": "BAI_API_KEY",
    },
}


def call_model(model: str, prompt: str, api_key: str, endpoint: str) -> dict:
    """One chat-completions call against `endpoint`. Returns a result dict;
    never raises (network failures are results, not crashes)."""
    started = time.monotonic()
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "max_tokens": _MAX_TOKENS,
    }).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=_CALL_TIMEOUT_SECONDS) as response:
            status = response.status
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        status = exc.code
        body = {}
        try:
            body = json.loads(exc.read().decode("utf-8"))
        except Exception:
            pass
    except Exception as exc:
        return {
            "http": None, "error": f"{type(exc).__name__}: {exc}",
            "latency_ms": int((time.monotonic() - started) * 1000),
        }
    try:
        text = body["choices"][0]["message"]["content"]
    except Exception:
        text = ""
    return {
        "http": status,
        "latency_ms": int((time.monotonic() - started) * 1000),
        "text": text,
        "usage_out": body.get("usage", {}).get("completion_tokens", 0),
        "error": body.get("error", {}).get("message", "")[:80] if status != 200 else "",
    }


def probe_model(model: str, api_key: str, template: str, endpoint: str) -> list[dict]:
    rows = []
    for name, expected_category, items_text in SAMPLES:
        prompt = template.replace("{items}", items_text)
        result = call_model(model, prompt, api_key, endpoint)
        row = {
            "model": model, "sample": name, "expected_category": expected_category,
            "http": result.get("http"), "latency_ms": result.get("latency_ms"),
            "tokens_out": result.get("usage_out"), "error": result.get("error", ""),
            "check": "", "check_reason": "",
        }
        if row["http"] != 200:
            row["check"] = "FAIL"
            row["check_reason"] = f"http {row['http']} {row['error']}"
        else:
            try:
                payload = extract_json(result.get("text", ""))
            except ValueError as exc:
                payload = None
                row["check"] = "FAIL"
                row["check_reason"] = f"unparseable JSON: {exc}"
            if payload is not None:
                ok, reason = check_response(
                    payload, expected_category, raw_len=len(result.get("text", "")))
                row["check"] = "PASS" if ok else "FAIL"
                row["check_reason"] = reason
        rows.append(row)
        time.sleep(_SLEEP_BETWEEN_CALLS)
    return rows


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gateway", choices=sorted(GATEWAYS), default="openrouter",
                        help="which OpenAI-compatible gateway to probe")
    parser.add_argument("--models", default="",
                        help="comma-separated model IDs (default: the gateway's roster)")
    parser.add_argument("--tag", default="ci")
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv)

    gateway = GATEWAYS[args.gateway]
    api_key = os.environ.get(gateway["key_env"]) or ""
    if not api_key:
        print(f"error: {gateway['key_env']} is not set", file=sys.stderr)
        return 1
    default_models = BAI_MODELS if args.gateway == "bai" else DEFAULT_MODELS
    template_path = _REPO_ROOT / "config" / "prompts" / "understand.txt"
    template = template_path.read_text(encoding="utf-8")
    models = [m.strip() for m in (args.models or ",".join(default_models)).split(",")
              if m.strip()]

    rows: list[dict] = []
    for index, model in enumerate(models, 1):
        print(f"[{index}/{len(models)}] {model}", flush=True)
        model_rows = probe_model(model, api_key, template, gateway["endpoint"])
        passes = sum(1 for r in model_rows if r["check"] == "PASS")
        print(f"  -> {passes}/{len(model_rows)} samples passed", flush=True)
        rows.extend(model_rows)

    out_path = Path(args.out) if args.out else (
        _REPO_ROOT / "config" / f"models_probe_{args.tag}.csv")
    with out_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {out_path} ({len(rows)} rows)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
