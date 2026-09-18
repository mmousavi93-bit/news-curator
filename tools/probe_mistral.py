"""Probe Mistral's free "Free mode" tier for LIVENESS (chat.completions 200).

Owner problem 2026-09-18: free-LLM capacity is the binding constraint (run
35343007845: 42 calls -> 10 ok, 40/90 clusters unavailable because groq 429
and gemini 503 walled simultaneously). Cerebras -- the first third-rung
candidate -- is BILLING-GATED: every live model (qwen-3.8-27b, gpt-oss-120b)
returns 402 payment_required, and the owner has no card. Mistral "Free mode"
is the no-card fallback: create API keys and use included monthly usage
(~$10/mo in API credits), no payment method required.

Candidate model: mistral-small-latest (cost-sensitive, Apache-2.0). The
quality fallback is mistral-medium-latest (frontier). "listed" != "serves
200" -- the gemini-alias trap (config/settings.yaml) applies here too, so
this tool answers, per candidate id, whether chat.completions returns 200
RIGHT NOW, plus the exact alias the /models list reports and the error body
on failure.

Unlike Cerebras, api.mistral.ai is NOT Cloudflare-fronted: default python
client UAs reach the API (401 auth path, verified 2026-09-18). The browser
UA below is kept defensively only -- it costs nothing and rules out a
UA-fingerprint surprise on the runner.

Runs from a GitHub runner (probe-mistral.yml, workflow_dispatch) so the key
lives only in MISTRAL_API_KEY (the same secret the pipeline uses). Stdlib
only. Never logs the key. Results land in a text artifact.

Budget note: each candidate id costs one call; probe only the two candidate
ids, never the whole roster.
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

DEFAULT_MODELS = ("mistral-small-latest",)

# Mirrors the understand stage's ask (Persian strict JSON) so a 200 here is
# evidence the model serves the real task, not just any prompt.
_SAMPLE_PROMPT = (
    "Extract strict JSON {headline, summary, category, significance} from "
    "these news items. Items: UKMTO reports a tanker struck by a projectile "
    "in the Strait of Hormuz; no casualties. Respond in Persian, JSON only."
)


def _request(method: str, url: str, key: str, payload: dict | None = None) -> dict:
    headers = {"Authorization": f"Bearer {key}", "User-Agent": _BROWSER_UA}
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            status = resp.status
            body = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        status = exc.code
        body = exc.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001 -- network failure is a verdict here
        return {
            "status": None,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "error": f"{type(exc).__name__}: {exc}",
            "snippet": "",
        }
    return {
        "status": status,
        "latency_ms": int((time.monotonic() - started) * 1000),
        "error": "",
        "snippet": body,
    }


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
        "# verdict http lat_ms listed model\n",
    ]

    roster = _request("GET", f"{_BASE}/models", key)
    print(f"== models list: http {roster['status']} ({roster['latency_ms']}ms)")
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
        r = _request("POST", f"{_BASE}/chat/completions", key, payload)
        listed_mark = "listed" if model in listed else "NOT-listed"
        verdict = "OK" if r["status"] == 200 else "FAIL"
        line = (f"{verdict:4} http={r['status']} lat={r['latency_ms']:>6}ms "
                f"{listed_mark:10} {model}")
        print(line)
        lines.append(line + "\n")
        if r["status"] != 200:
            snippet = (r.get("error") or r["snippet"])[:200].replace("\n", " ")
            lines.append(f"        err: {snippet}\n")
        time.sleep(15.0)  # free-mode RPM unknown; pad to be safe

    with open(args.out, "w", encoding="utf-8") as fh:
        fh.writelines(lines)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
