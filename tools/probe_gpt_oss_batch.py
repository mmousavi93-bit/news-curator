"""Probe gpt-oss on the PRODUCTION batch shape (understand_batch.txt, 5 clusters).

The single-cluster probe (probe_gpt_oss.py) proved QUALITY. This script proves
SHAPE: can gpt-oss hold the 5-cluster array under the production max_tokens=2000
without truncating (finish_reason="length"), echo every key, and return a
parseable list of 5 objects? gpt-oss is a REASONING model -- the thinking block
eats output budget, so a model that is fine single-object can truncate at batch 5.

The gate (provider-probing.md): wire only after BOTH quality and shape pass.
Run via .github/workflows/probe-gpt-oss-batch.yml (keys live in CI secrets).
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

# 5 faithful ME clusters in the EXACT format build_payload() renders:
#   ## cluster cN
#   - [source_id | YYYY-MM-DD HH:MM] title
#     body
# separated by one blank line. Values are realistic, not lifted from any
# live run -- the point is shape fidelity, not news accuracy.
CLUSTERS = [
    ("reuters", "2026-09-24 18:30",
     "Oil tanker struck near Strait of Hormuz, crew safe",
     "A commercial oil tanker was hit by a projectile near the Strait of Hormuz "
     "on Wednesday, shipping sources said. No group immediately claimed "
     "responsibility and the crew were reported safe. Shipping insurers raised "
     "risk premiums for Gulf transits."),
    ("ap", "2026-09-24 17:05",
     "Israeli strikes hit Hezbollah positions in southern Lebanon",
     "Israeli aircraft struck several Hezbollah positions in southern Lebanon "
     "overnight, the Israeli military said. There was no immediate word on "
     "casualties. The strikes followed cross-border rocket fire a day earlier."),
    ("reuters", "2026-09-24 15:40",
     "Iran nuclear talks resume in Vienna",
     "Negotiators from Iran and the remaining parties to the 2015 nuclear deal "
     "resumed talks in Vienna on Thursday. European officials described the "
     "atmosphere as constructive but said major gaps remain over sanctions relief."),
    ("bloomberg", "2026-09-24 14:20",
     "Oil prices rise on Gulf shipping fears",
     "Brent crude rose more than two percent on Thursday as attacks on shipping "
     "near the Strait of Hormuz stoked supply concerns. Analysts said the move "
     "reflected a repricing of transit risk rather than any actual supply cut."),
    ("ap", "2026-09-24 12:55",
     "US carrier group redeploys to eastern Mediterranean",
     "The United States has moved a carrier strike group to the eastern "
     "Mediterranean in what officials called a signal of support for regional "
     "allies. The Pentagon said the move was precautionary and defensive."),
]

TEMPLATE = open("config/prompts/understand_batch.txt", encoding="utf-8").read()

REQUIRED = [
    "headline", "summary", "entities", "category",
    "clickbait", "irrelevant", "significance", "why_matters",
]


def build_batch_prompt() -> str:
    blocks = []
    for i, (src, when, title, body) in enumerate(CLUSTERS, start=1):
        blocks.append(f"## cluster c{i}\n- [{src} | {when}] {title}\n  {body}")
    return TEMPLATE.replace("{items}", "\n\n".join(blocks))


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


def check(content: str) -> None:
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.lstrip("`").lstrip("json").strip("`\n ").strip()
    try:
        parsed = json.loads(cleaned)
    except Exception as e:  # noqa: BLE001 - probe wants the raw error
        print(f"   JSON parse FAILED: {e}")
        return
    if not isinstance(parsed, list):
        print(f"   parsed but NOT a list: {type(parsed).__name__}")
        return
    print(f"   parsed list len={len(parsed)} (expected 5)")
    for i, el in enumerate(parsed):
        if not isinstance(el, dict):
            print(f"   [{i}] NOT an object: {type(el).__name__}")
            continue
        echoed = el.get("key")
        missing = [f for f in REQUIRED if f not in el]
        print(f"   [{i}] key={echoed!r} missing={missing if missing else 'NONE'}")


def main() -> None:
    prompt = build_batch_prompt()
    print(f"prompt_chars={len(prompt)}", flush=True)
    for model in ["openai/gpt-oss-120b"]:
        print(f"\n===== {model} (batch 5, max_tokens=2000) =====", flush=True)
        payload = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 2000,   # production value (providers.py)
            "temperature": 0.0,   # production value
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
            choice = resp["choices"][0]
            msg = choice["message"]
            finish = choice.get("finish_reason")
            content = msg.get("content") or ""
            usage = resp.get("usage", {})
            reasoning = msg.get("reasoning") or msg.get("reasoning_content")
            print(f"   finish_reason={finish!r}  <-- 'length' = truncated by max_tokens")
            print(f"   prompt_tokens={usage.get('prompt_tokens')} "
                  f"completion_tokens={usage.get('completion_tokens')}")
            if reasoning:
                print(f"   reasoning_tokens={len(str(reasoning))} chars")
            print("   --- RAW CONTENT ---")
            print(content)
            print("   --- SHAPE CHECK ---")
            check(content)
        else:
            print(f"   Body: {body[:500]}")
    print("\n=== DONE ===")


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001 - always flush output and exit 0
        traceback.print_exc()
        print("\n=== DONE (with top-level error above) ===")
