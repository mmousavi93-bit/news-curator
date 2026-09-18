"""Probe a SECOND Groq account (GROQ_API_KEY_2) for LIVENESS before wiring
a groq2 cascade rung.

Owner problem 2026-09-18: groq's daily token pool (TPD) is the pipeline's
binding constraint -- settings.yaml groq block is RPM 30 / RPD 1,000 /
TPM 8,000 / TPD 200,000, and a busy day (90 clusters) exhausts it, leaving
clusters dropped as "no summary". Cerebras is 402-billing-gated and
Mistral's free mode 429s on every chat call (shared-pool saturation, zero
rate headers), so a SECOND groq account is the cleanest no-card capacity
play on the table: it doubles the daily pool with zero new integration risk
(groq is already wired and validated for Persian output). ToS caveat is on
record (SESSION_19_BRIEF): two free accounts can get both keys killed if
detected -- treat as capacity you can lose, not capacity you own.

"new account" != "same roster": Groq rotates free models, so a fresh key may
not serve the pinned qwen/qwen3.8-27b. This tool answers, per candidate id,
whether chat.completions returns 200 RIGHT NOW with the second key, plus the
exact x-ratelimit-* headers groq reports (the RPM/TPM data the wiring gate
needs). A 429 here is NOT a hard failure -- it is the limit being measured.

Runs from a GitHub runner (probe-groq2.yml) so the key lives only in the
GROQ_API_KEY_2 secret. Stdlib only. Never logs the key. On 429 captures
groq's rate headers and retries with backoff.

Budget: each probed id costs one call of the second account's free pool.
Probe only the pinned model, never the whole roster.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

_BASE = "https://api.groq.com/openai/v1"
_KEY_ENV = "GROQ_API_KEY_2"

DEFAULT_MODELS = ("qwen/qwen3.8-27b",)

# Mirrors the understand stage's ask (Persian strict JSON) so a 200 here is
# evidence the model serves the real task, not just any prompt.
_SAMPLE_PROMPT = (
    "Extract strict JSON {headline, summary, category, significance} from "
    "these news items. Items: UKMTO reports a tanker struck by a projectile "
    "in the Strait of Hormuz; no casualties. Respond in Persian, JSON only."
)

# Groq reports per-minute and (on some models) per-day rate limits as
# x-ratelimit-* headers; captured on every response so a 429 prints the
# number the CLAUDE.md wiring gate needs.
_RATE_HEADERS = (
    "retry-after",
    "x-ratelimit-limit-requests",
    "x-ratelimit-remaining-requests",
    "x-ratelimit-reset-requests",
    "x-ratelimit-limit-tokens",
    "x-ratelimit-remaining-tokens",
    "x-ratelimit-reset-tokens",
    "x-ratelimit-limit-requests-day",
    "x-ratelimit-remaining-requests-day",
    "x-ratelimit-reset-requests-day",
    "x-ratelimit-limit-tokens-day",
    "x-ratelimit-remaining-tokens-day",
    "x-ratelimit-reset-tokens-day",
)

_MAX_RETRIES = 4  # enough to ride out a short Retry-After window


def _headers_dict(msg) -> dict:
    """urllib HTTPMessage -> dict of lowercased header names."""
    out: dict = {}
    for k, v in msg.items():
        out[str(k).lower()] = v
    return out


def _request(method: str, url: str, key: str, payload: dict | None = None) -> dict:
    headers = {"Authorization": f"Bearer {key}"}
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
    for name in ("retry-after", "x-ratelimit-reset-requests"):
        v = headers.get(name)
        if v is not None and str(v).strip().isdigit():
            return int(str(v).strip())
    return None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", default=",".join(DEFAULT_MODELS))
    parser.add_argument("--key-env", default=_KEY_ENV)
    parser.add_argument("--out", default="config/groq2_probe.txt")
    args = parser.parse_args(argv)

    key = os.environ.get(args.key_env, "")
    if not key:
        print(f"error: {args.key_env} is not set", file=sys.stderr)
        return 1

    models = [m.strip() for m in (args.models or ",".join(DEFAULT_MODELS)).split(",")
              if m.strip()]

    lines = [
        "# groq2 (second-account) liveness probe\n",
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
    lines.append(f"   models listed: {sorted(listed)}\n")

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
