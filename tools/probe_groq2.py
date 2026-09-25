"""Probe groq2: validate key, scan available models, test top candidates."""
from __future__ import annotations

import json
import os
import sys
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError

KEY = os.environ["GROQ_API_KEY_2"]
BASE = "https://api.groq.com/openai/v1"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

def req(path: str, method: str = "GET", body: bytes | None = None) -> tuple[int, dict, str]:
    r = Request(f"{BASE}{path}", data=body, method=method)
    r.add_header("Authorization", f"Bearer {KEY}")
    r.add_header("Content-Type", "application/json")
    r.add_header("User-Agent", UA)
    try:
        resp = urlopen(r, timeout=30)
        return resp.status, dict(resp.headers), resp.read().decode()
    except HTTPError as e:
        return e.code, dict(e.headers), e.read().decode()

print("=== groq2 probe ===\n")

# 1. List all models
print("1. GET /models ...")
status, headers, body = req("/models")
print(f"   HTTP {status}")
if status != 200:
    print(f"   Body: {body[:500]}")
    print("   FAIL: key invalid or endpoint unreachable")
    sys.exit(1)

data = json.loads(body)
all_models = data.get("data", [])
print(f"   Total models: {len(all_models)}")
for m in all_models:
    print(f"   - {m['id']} {'(active)' if m.get('active') else '(inactive)'}")

# 2. Try top free-tier candidates
CANDIDATES = [
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
    "gemma2-9b-it",
    "qwen-2.5-32b",
    "deepseek-r1-distill-qwen-32b",
]

print("\n2. Testing candidates...")
for model in CANDIDATES:
    print(f"\n   {model}: ", end="", flush=True)
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "Say 'ok' in one word."}],
        "max_tokens": 10,
    }).encode()
    t0 = time.monotonic()
    status, headers, body = req("/chat/completions", "POST", payload)
    elapsed = time.monotonic() - t0
    if status == 200:
        resp = json.loads(body)
        content = resp["choices"][0]["message"]["content"]
        usage = resp.get("usage", {})
        rpm = headers.get("x-ratelimit-remaining-requests", "?")
        tpm = headers.get("x-ratelimit-remaining-tokens", "?")
        print(f"✅ {elapsed:.1f}s | response={content.strip()[:30]} | RPM_rem={rpm} TPM_rem={tpm} | prompt_tok={usage.get('prompt_tokens')} completion_tok={usage.get('completion_tokens')}")
    elif status == 404 or "decommissioned" in body:
        print(f"❌ decommissioned or not found")
    elif status == 429:
        print(f"⏳ rate limited")
    else:
        err_preview = body[:120].replace("\n", " ")
        print(f"❌ HTTP {status}: {err_preview}")

# 3. Real call to the ACTUAL production model + per-account rate-limit headers.
# The production model qwen/qwen3.8-27b was never exercised above (only
# decommissioned candidates), and settings.yaml copies groq2's limits from
# groq1's VERIFIED values (tpm 8000) -- groq2 is a SEPARATE account whose
# limits were assumed, never measured. The x-ratelimit-* LIMIT headers are the
# per-account truth; dump them all so settings.yaml can be corrected.
print("\n3. Production model qwen/qwen3.8-27b + rate-limit headers...")
payload = json.dumps({
    "model": "qwen/qwen3.8-27b",
    "messages": [{"role": "user", "content": "Say 'ok' in one word."}],
    "max_tokens": 10,
}).encode()
t0 = time.monotonic()
status, headers, body = req("/chat/completions", "POST", payload)
elapsed = time.monotonic() - t0
print(f"   HTTP {status} ({elapsed:.1f}s)")
if status == 200:
    resp = json.loads(body)
    usage = resp.get("usage", {})
    print(f"   prompt_tokens={usage.get('prompt_tokens')} "
          f"completion_tokens={usage.get('completion_tokens')}")
else:
    print(f"   Body: {body[:300]}")
print("   --- x-ratelimit-* headers (per-account limit truth) ---")
for k in sorted(headers):
    if k.lower().startswith("x-ratelimit"):
        print(f"   {k}: {headers[k]}")

print("\n=== DONE ===")