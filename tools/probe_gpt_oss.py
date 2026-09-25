"""Probe groq2's gpt-oss models for the understand task (quality, not liveness).

Follows the provider-probing.md gate: a 200 is LIVENESS, not QUALITY. A model
must return a body that parses to the understand contract's strict JSON and
produces clean Persian fields before it is worth wiring. This script calls each
candidate with a FAITHFUL single-cluster understand prompt (the real
config/prompts/understand.txt template + one representative English cluster),
captures the raw body AND the x-ratelimit-* headers, and reports a parse check.

Run via .github/workflows/probe-gpt-oss.yml (keys live in CI secrets).
"""
from __future__ import annotations

import json
import os
import time
import traceback
from urllib.request import Request, urlopen
from urllib.error import HTTPError

KEY = os.environ["GROQ_API_KEY_2"]
BASE = "https://api.groq.com/openai/v1"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

# One representative English Middle-East cluster -> Persian output required.
CLUSTER = (
    "- [reuters | 2026-09-24 18:30] Oil tanker struck near Strait of Hormuz, "
    "crew safe\n"
    "  A commercial oil tanker was hit by a projectile near the Strait of "
    "Hormuz on Wednesday, shipping sources said. No group immediately claimed "
    "responsibility and the crew were reported safe. Shipping insurers raised "
    "risk premiums for Gulf transits."
)
TEMPLATE = open("config/prompts/understand.txt", encoding="utf-8").read()
PROMPT = TEMPLATE.replace("{items}", CLUSTER)

MODELS = ["openai/gpt-oss-20b", "openai/gpt-oss-120b"]

# Fields the understand contract requires (config/prompts/understand.txt).
REQUIRED = [
    "headline", "summary", "entities", "category",
    "clickbait", "irrelevant", "significance", "why_matters",
]


def req(path: str, method: str = "GET", body: bytes | None = None):
    r = Request(f"{BASE}{path}", data=body, method=method)
    r.add_header("Authorization", f"Bearer {KEY}")
    r.add_header("Content-Type", "application/json")
    r.add_header("User-Agent", UA)
    try:
        resp = urlopen(r, timeout=180)
        return resp.status, dict(resp.headers), resp.read().decode()
    except HTTPError as e:
        return e.code, dict(e.headers), e.read().decode()
    except Exception as e:  # noqa: BLE001 - probe wants the raw error, not a crash
        return 0, {}, f"EXCEPTION: {type(e).__name__}: {e}"


def parse_check(content: str) -> None:
    """Best-effort parse of the model's output against the contract shape."""
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.lstrip("`").lstrip("json").strip("`\n ").strip()
    try:
        parsed = json.loads(cleaned)
    except Exception as e:  # noqa: BLE001 - probe wants the raw error
        print(f"   JSON parse FAILED: {e}")
        return
    if not isinstance(parsed, dict):
        print(f"   parsed but NOT a dict: {type(parsed).__name__}")
        return
    missing = [f for f in REQUIRED if f not in parsed]
    print(f"   keys={list(parsed.keys())}")
    print(f"   missing={missing if missing else 'NONE'}")
    if not missing:
        print(f"   headline={parsed.get('headline')!r}")
        print(f"   summary={parsed.get('summary')!r}")
        print(f"   category={parsed.get('category')!r} "
              f"significance={parsed.get('significance')!r}")
        print(f"   clickbait={parsed.get('clickbait')!r} "
              f"irrelevant={parsed.get('irrelevant')!r}")


def main() -> None:
    for model in MODELS:
        print(f"\n===== {model} =====", flush=True)
        payload = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": PROMPT}],
            "max_tokens": 600,
            "temperature": 0.2,
        }).encode()
        t0 = time.monotonic()
        status, headers, body = req("/chat/completions", "POST", payload)
        elapsed = time.monotonic() - t0
        print(f"HTTP {status} ({elapsed:.1f}s)")
        print("   --- x-ratelimit-* headers ---")
        for k in sorted(headers):
            if k.lower().startswith("x-ratelimit"):
                print(f"   {k}: {headers[k]}")
        if status == 200:
            resp = json.loads(body)
            content = resp["choices"][0]["message"]["content"]
            usage = resp.get("usage", {})
            print(f"   prompt_tokens={usage.get('prompt_tokens')} "
                  f"completion_tokens={usage.get('completion_tokens')}")
            print("   --- RAW CONTENT ---")
            print(content)
            print("   --- PARSE CHECK ---")
            parse_check(content)
        else:
            print(f"   Body: {body[:500]}")
    print("\n=== DONE ===")


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001 - always flush output and exit 0
        traceback.print_exc()
        print("\n=== DONE (with top-level error above) ===")
