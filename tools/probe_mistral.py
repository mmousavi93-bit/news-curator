"""Probe Mistral's free "Free mode" tier for LIVENESS (chat.completions 200).

Owner problem 2026-09-18: free-LLM capacity is the binding constraint (run
35343007845: 42 calls -> 10 ok, 40/90 clusters unavailable because groq 429
and gemini 503 walled simultaneously). Cerebras -- the first third-rung
candidate -- is BILLING-GATED: every live model (qwen-3.8-27b, gpt-oss-120b)
returns 402 payment_required, and the owner has no card. Mistral "Free mode"
is the no-card fallback: create API keys and use included monthly usage
(~$10/mo in API credits), no payment method required.

Candidate models: ministral-8b-2512 (the wired rung) and ministral-3b-2512
(the higher-RPS alternative). The mistral-small-latest / mistral-medium-latest
aliases are a trap: /models LISTS them but they 429 code 1300 on every call
(probes 35585072996/35586658168, 2026-09-21) -- only the DATED ministral-*
ids serve. "listed" != "serves 200" -- the gemini-alias trap
(config/settings.yaml) applies here too, so this tool
answers, per candidate id, whether chat.completions returns 200 RIGHT NOW,
plus the exact alias the /models list reports and the error body on failure.

A 429 here is NOT a hard failure -- it is the signal we are trying to
measure: Mistral's free tier rate limit (the exact RPM/TPM the wiring gate
needs). So on 429 this tool captures the Retry-After / ratelimitbysize-*
headers and retries with backoff, printing the numeric limit the server
reports. That converts "rate limit exceeded" into the RPM/TPM data the
CLAUDE.md gate requires before wiring.

Unlike Cerebras, api.mistral.ai is NOT Cloudflare-fronted: default python
client UAs reach the API (401 auth path, verified 2026-09-18). The browser
UA below is kept defensively only -- it costs nothing and rules out a
UA-fingerprint surprise on the runner.

Runs from a GitHub runner (probe-mistral.yml, workflow_dispatch) so the key
lives only in MISTRAL_API_KEY (the same secret the pipeline uses). Stdlib
only. Never logs the key. Results land in a text artifact.

Budget note: each candidate id costs one call (plus 429 retries); probe only
the two candidate ids, never the whole roster.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

_BASE = "https://api.mistral.ai/v1"

# Defensive only: api.mistral.ai is not Cloudflare-fronted (verified
# 2026-09-18: default UA -> 401, the auth path, not a bot wall). Kept so a
# UA-fingerprint surprise can't poison the liveness verdict.
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

DEFAULT_MODELS = ("ministral-8b-2512", "ministral-3b-2512")

# Mirrors the understand stage's ask (Persian strict JSON) so a 200 here is
# evidence the model serves the real task, not just any prompt.
_SAMPLE_PROMPT = (
    "Extract strict JSON {headline, summary, category, significance} from "
    "these news items. Items: UKMTO reports a tanker struck by a projectile "
    "in the Strait of Hormuz; no casualties. Respond in Persian, JSON only."
)

# Header names Mistral reports for size-based rate limits (docs: "Rate
# limits" page). Captured on every response so a 429 prints the number.
_RATE_HEADERS = (
    "retry-after",
    "ratelimitbysize-limit",
    "ratelimitbysize-remaining",
    "ratelimitbysize-reset",
    "ratelimitbysize-quota",
    "ratelimitbysize-query-cost",
    "x-ratelimitbysize-limit",
    "x-ratelimitbysize-remaining",
    "x-ratelimitbysize-reset",
    "x-ratelimit-limit",
    "x-ratelimit-remaining",
    "x-ratelimit-reset",
    "ratelimit-limit",
    "ratelimit-remaining",
    "ratelimit-reset",
)

_MAX_RETRIES = 4  # enough to ride out a 60s Retry-After window once or twice


def _headers_dict(msg) -> dict:
    """urllib HTTPMessage -> dict of lowercased header names."""
    out: dict = {}
    for k, v in msg.items():
        out[str(k).lower()] = v
    return out


def _request(method: str, url: str, key: str, payload: dict | None = None) -> dict:
    headers = {"Authorization": f"Bearer {key}", "User-Agent": _BROWSER_UA}
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    started = time.monotonic()
    resp_headers: dict = {}
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            status = resp.status
            body = resp.read().decode("utf-8", "replace")
            resp_headers = _headers_dict(resp.headers)
    except urllib.error.HTTPError as exc:
        status = exc.code
        body = exc.read().decode("utf-8", "replace")
        resp_headers = _headers_dict(exc.headers)
    except Exception as exc:  # noqa: BLE001 -- network failure is a verdict here
        return {
            "status": None,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "error": f"{type(exc).__name__}: {exc}",
            "snippet": "",
            "headers": {},
        }
    return {
        "status": status,
        "latency_ms": int((time.monotonic() - started) * 1000),
        "error": "",
        "snippet": body,
        "headers": resp_headers,
    }


def _rate_summary(headers: dict) -> str:
    parts = [f"{n}={headers[n]}" for n in _RATE_HEADERS if n in headers]
    return " ".join(parts) if parts else "(no rate headers)"


def _retry_after(headers: dict) -> int | None:
    for name in ("retry-after", "x-ratelimit-reset", "ratelimitbysize-reset"):
        v = headers.get(name)
        if v is not None and str(v).strip().isdigit():
            return int(str(v).strip())
    return None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", default=",".join(DEFAULT_MODELS))
    parser.add_argument("--out", default="config/mistral_probe.txt")
    args = parser.parse_args(argv)

    key = os.environ.get("MISTRAL_API_KEY", "")
    if not key:
        print("error: MISTRAL_API_KEY is not set", file=sys.stderr)
        return 1

    models = [m.strip() for m in (args.models or ",".join(DEFAULT_MODELS)).split(",")
              if m.strip()]

    lines = [
        "# mistral free-mode liveness probe\n",
        f"# at {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n",
        "# verdict http lat_ms try listed model\n",
    ]

    roster = _request("GET", f"{_BASE}/models", key)
    print(f"== models list: http {roster['status']} ({roster['latency_ms']}ms) "
          f"{_rate_summary(roster['headers'])}")
    listed: set[str] = set()
    if roster["status"] == 200:
        try:
            data = json.loads(roster["snippet"])
            listed = {m.get("id") for m in data.get("data", []) if m.get("id")}
        except (json.JSONDecodeError, AttributeError):
            pass
    lines.append(f"   free-mode models listed: {sorted(listed)}\n")

    for model in models:
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": _SAMPLE_PROMPT}],
            "temperature": 0.0,
            "max_tokens": 100,
        }
        listed_mark = "listed" if model in listed else "NOT-listed"
        r: dict = {}
        for attempt in range(1, _MAX_RETRIES + 1):
            r = _request("POST", f"{_BASE}/chat/completions", key, payload)
            verdict = "OK" if r["status"] == 200 else "FAIL"
            line = (f"{verdict:4} http={r['status']} lat={r['latency_ms']:>6}ms "
                    f"try={attempt} {listed_mark:10} {model}")
            print(line)
            lines.append(line + "\n")
            if r["status"] == 200:
                # Capture the parsed assistant text so liveness != quality:
                # a 200 can still return empty/garbage for strict Persian JSON.
                try:
                    body = json.loads(r["snippet"])
                    content = body["choices"][0]["message"].get("content", "")
                except (json.JSONDecodeError, KeyError, IndexError, AttributeError):
                    content = r["snippet"][:2000]
                lines.append(f"        body: {content[:800]!r}\n")
                print(f"        body: {content[:800]!r}")
                break
            if r["status"] == 429:
                rate = _rate_summary(r["headers"])
                print(f"        rate: {rate}")
                lines.append(f"        rate: {rate}\n")
                if attempt < _MAX_RETRIES:
                    wait = _retry_after(r["headers"])
                    if wait is None:
                        wait = 15 * attempt  # exponential-ish fallback
                    print(f"        retrying in {wait}s ...")
                    time.sleep(wait)
                    continue
            snippet = (r.get("error") or r["snippet"])[:200].replace("\n", " ")
            lines.append(f"        err: {snippet}\n")
            break

    with open(args.out, "w", encoding="utf-8") as fh:
        fh.writelines(lines)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
