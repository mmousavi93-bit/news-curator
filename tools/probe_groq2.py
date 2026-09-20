"""Probe groq2: validate key, confirm mixtral model, capture RPM/TPM headers."""
from __future__ import annotations

import json
import os
import sys
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError

KEY = os.environ["GROQ_API_KEY_2"]
BASE = "https://api.groq.com/openapi/v1"
MODEL = "mixtral-8x7b-32768"

def req(path: str, method: str = "GET", body: bytes | None = None) -> tuple[int, dict, str]:
    r = Request(f"{BASE}{path}", data=body, method=method)
    r.add_header("Authorization", f"Bearer {KEY}")
    r.add_header("Content-Type", "application/json")
    try:
        resp = urlopen(r, timeout=30)
        return resp.status, dict(resp.headers), resp.read().decode()
    except HTTPError as e:
        return e.code, dict(e.headers), e.read().decode()

print("=== groq2 probe ===\n")

# 1. List models
print("1. GET /models ...")
status, headers, body = req("/models")
print(f"   HTTP {status}")
if status == 200:
    data = json.loads(body)
    mixtral_models = [m["id"] for m in data.get("data", []) if "mixtral" in m["id"].lower()]
    print(f"   Mixtral models found: {mixtral_models}")
else:
    print(f"   Body: {body[:500]}")
    print("   FAIL: key invalid or endpoint unreachable")
    sys.exit(1)

# 2. Minimal chat completion
print(f"\n2. POST /chat/completions with {MODEL} ...")
payload = json.dumps({
    "model": MODEL,
    "messages": [{"role": "user", "content": "Say 'probe ok' in Persian."}],
    "max_tokens": 50,
}).encode()
t0 = time.monotonic()
status, headers, body = req("/chat/completions", "POST", payload)
elapsed = time.monotonic() - t0

print(f"   HTTP {status} ({elapsed:.2f}s)")
print(f"   Rate headers: x-ratelimit-remaining-requests={headers.get('x-ratelimit-remaining-requests','?')}, x-ratelimit-remaining-tokens={headers.get('x-ratelimit-remaining-tokens','?')}")

if status == 200:
    resp = json.loads(body)
    content = resp["choices"][0]["message"]["content"]
    usage = resp.get("usage", {})
    print(f"   Response: {content[:200]}")
    print(f"   Usage: prompt={usage.get('prompt_tokens')}, completion={usage.get('completion_tokens')}, total={usage.get('total_tokens')}")
    print("\n✅ groq2 probe PASSED")
    sys.exit(0)
elif status == 429:
    print(f"   Retry-After: {headers.get('Retry-After', 'none')}")
    print(f"   Body: {body[:300]}")
    print("   ⚠️ Rate limited — retry after backoff")
    sys.exit(2)
else:
    print(f"   Body: {body[:500]}")
    print(f"   FAIL: HTTP {status}")
    sys.exit(1)