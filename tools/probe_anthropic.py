"""Probe Anthropic API: validate key, list available Haiku models, measure latency."""
from __future__ import annotations

import json
import os
import sys
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

KEY = os.environ.get("ANTHROPIC_API_KEY", "")
if not KEY:
    print("FATAL: ANTHROPIC_API_KEY not set")
    sys.exit(1)

HEADERS = {
    "x-api-key": KEY,
    "Content-Type": "application/json",
    "anthropic-version": "2023-06-01",
}


def _call(payload: dict, label: str) -> tuple[int, dict | str, float]:
    start = time.monotonic()
    req = Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(payload).encode(),
        headers=HEADERS,
        method="POST",
    )
    try:
        resp = urlopen(req, timeout=30)
        body = json.loads(resp.read())
        elapsed = time.monotonic() - start
        return resp.status, body, elapsed
    except HTTPError as e:
        elapsed = time.monotonic() - start
        return e.code, e.read().decode(errors="replace"), elapsed
    except URLError as e:
        elapsed = time.monotonic() - start
        return 0, str(e.reason), elapsed


# Minimal extraction test (2-line Persian prompt, ~50 input tokens)
TEST_PROMPT = (
    "یک خبر کوتاه فارسی:\n"
    "زلزله ۴.۲ ریشتری در کرمان\n\n"
    "خلاصه یک خطی بنویس:"
)

MODELS = [
    ("claude-3.5-haiku-latest", "Claude 3.5 Haiku (latest)"),
    ("claude-3-haiku-20240307", "Claude 3 Haiku (legacy)"),
    ("claude-3.5-sonnet-latest", "Claude 3.5 Sonnet (reference)"),
]

print("=== Anthropic API Probe ===\n")
print(f"Key prefix: {KEY[:12]}...")
print()

for model_id, label in MODELS:
    payload = {
        "model": model_id,
        "max_tokens": 100,
        "temperature": 0,
        "messages": [{"role": "user", "content": TEST_PROMPT}],
    }
    status, body, elapsed = _call(payload, label)

    if status == 200 and isinstance(body, dict):
        tokens_in = body.get("usage", {}).get("input_tokens", 0)
        tokens_out = body.get("usage", {}).get("output_tokens", 0)
        text = (
            body.get("content", [{}])[0].get("text", "").strip()
            if body.get("content")
            else ""
        )
        text_preview = text[:80].replace("\n", " / ")
        print(
            f"[OK]    {label:40s} model={model_id:30s} "
            f"lat={elapsed:5.1f}s in={tokens_in:3d} out={tokens_out:3d} "
            f"→ {text_preview}"
        )
        # Cost estimate for the extraction call
        in_cost = tokens_in / 1_000_000 * 0.80
        out_cost = tokens_out / 1_000_000 * 4.00
        print(f"        cost: ${in_cost + out_cost:.4f} (in=${in_cost:.4f} out=${out_cost:.4f})")
    elif status == 200:
        print(f"[OK]    {label:40s} status=200 but body is not dict: {type(body)}")
    elif status in (401, 403):
        print(f"[FAIL]  {label:40s} status={status} — key invalid or unauthorized")
        if isinstance(body, str):
            print(f"        {body[:200]}")
        break  # same key — stop
    elif status == 429:
        print(f"[429]   {label:40s} rate-limited — wait and retry")
        break
    elif status == 404:
        print(f"[MISS]  {label:40s} model not found for this key")
    elif status == 0:
        print(f"[NET]   {label:40s} network error: {str(body)[:200]}")
    else:
        print(f"[?]     {label:40s} status={status}: {str(body)[:200]}")
    print()

print("Done.")