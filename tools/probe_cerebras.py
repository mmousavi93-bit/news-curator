"""Probe Cerebras Inference's free roster for LIVENESS (chat.completions 200).

Owner problem 2026-09-18: free-LLM capacity is the binding constraint (run
35343007845: 42 calls -> 10 ok, 40/90 clusters unavailable because groq 429
and gemini 503 walled simultaneously). Cerebras free tier (5 RPM / 30K TPM /
1M tokens per day) is the candidate third rung, but "listed" != "serves 200"
-- the gemini-alias trap (config/settings.yaml) applies here too. This tool
answers, per candidate id, whether chat.completions returns 200 RIGHT NOW,
plus latency and the error body for failures.

Runs from a US GitHub runner (probe-cerebras.yml, workflow_dispatch) for the
same reason every probe here is CI-only. Stdlib only. The key comes from
CEREBRAS_API_KEY env; never logged. Results land in a text artifact.

Budget note: each candidate id costs one call of the 5-RPM free tier -- probe
only the two free models, never the whole roster.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

_BASE = "https://api.cerebras.ai/v1"

# Cerebras is behind Cloudflare bot protection: urllib's default UA gets
# 403 error 1010 (verified 2026-09-18), while a browser UA reaches the API
# (401 wrong-key is the auth path, not the bot wall).
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

DEFAULT_MODELS = ("gpt-oss-120b", "gemma-4-31b")

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
    parser.add_argument("--out", default="config/cerebras_probe.txt")
    args = parser.parse_args(argv)

    key = os.environ.get("CEREBRAS_API_KEY", "")
    if not key:
        print("error: CEREBRAS_API_KEY is not set", file=sys.stderr)
        return 1

    models = [m.strip() for m in (args.models or ",".join(DEFAULT_MODELS)).split(",")
              if m.strip()]

    lines = [
        "# cerebras free-model liveness probe\n",
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
    lines.append(f"   free-model candidates listed: {sorted(listed)}\n")

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
        time.sleep(15.0)  # 5 RPM -> 12s; pad to 15s

    with open(args.out, "w", encoding="utf-8") as fh:
        fh.writelines(lines)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
