"""Probe Gemini's flash roster for LIVENESS (generateContent 200 vs 503).

Owner problem 2026-09-18: the free-tier saturation that hit 3.7/3.8-flash
has marched DOWN to 3.6-flash (503 x2, slow 9.5-13.4s latency, breaker
opened in run 35335314225). The models LIST endpoint still lists
3.5/3.6/3.7/3.8-flash, but "listed" != "serves 200" -- the
gemini-flash-latest alias died exactly that way (config/settings.yaml
gemini block documents it). This tool answers, per candidate id, whether
generateContent actually returns 200 RIGHT NOW, and captures latency plus
the error body for the 503s.

Runs from a US GitHub runner (probe-gemini.yml, workflow_dispatch), for
the same reason every probe here is CI-only: an Iran-side fetch measures
Iran's network, not the provider. Stdlib only -- urllib, no pip install.
The key comes from GEMINI_API_KEY env; it is never logged. Results land
in a text artifact; nothing is committed.

Budget note: each candidate id costs one generateContent call of the
20-RPD free tier. Probe only the plausible pins, not the whole roster.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

_API = "https://generativelanguage.googleapis.com/v1beta"

# Newest-first candidate order. 3.8/3.7 are KNOWN saturated (config
# history), so the default probes only the current pin (3.6) and the two
# downgrade candidates (3.5, 3.5-lite). 3 calls = 15% of the daily 20.
DEFAULT_MODELS = (
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
)

# Tiny but REAL: Persian + strict-JSON ask, mirroring the understand task
# shape. Liveness is the goal, so maxOutputTokens is 100 and content is a
# fixed sample -- not a real cluster body.
_SAMPLE_PROMPT = (
    "Extract strict JSON {headline, summary, category, significance} from "
    "these news items. Items: UKMTO reports a tanker struck by a projectile "
    "in the Strait of Hormuz; no casualties. Respond in Persian, JSON only."
)


def _request(method: str, url: str, key: str, payload: dict | None = None) -> dict:
    headers = {"x-goog-api-key": key}
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
    except Exception as exc:
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
    parser.add_argument("--models", default=",".join(DEFAULT_MODELS),
                        help="comma-separated model ids to probe")
    parser.add_argument("--out", default="config/gemini_probe.txt")
    args = parser.parse_args(argv)

    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        print("error: GEMINI_API_KEY is not set", file=sys.stderr)
        return 1

    models = [m.strip() for m in args.models.split(",") if m.strip()]

    # 1. Ground-truth roster: what the models endpoint lists right now.
    roster = _request("GET", f"{_API}/models?pageSize=200", key)
    print(f"== models list: http {roster['status']} ({roster['latency_ms']}ms)")
    listed: set[str] = set()
    if roster["status"] == 200:
        try:
            listed = {m["name"].split("/")[-1]
                      for m in json.loads(roster["snippet"]).get("models", [])}
        except Exception:
            pass
    print(f"   flash-family listed: {sorted(m for m in listed if 'flash' in m)}")

    # 2. Liveness: does generateContent return 200 for each candidate?
    lines = [
        "# gemini flash liveness probe\n",
        f"# at {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n",
        "# verdict http lat_ms listed model\n",
    ]
    for model in models:
        url = f"{_API}/models/{model}:generateContent"
        payload = {
            "contents": [{"role": "user", "parts": [{"text": _SAMPLE_PROMPT}]}],
            "generationConfig": {"maxOutputTokens": 100, "temperature": 0.0},
        }
        r = _request("POST", url, key, payload)
        listed_mark = "listed" if model in listed else "NOT-listed"
        verdict = "OK" if r["status"] == 200 else "FAIL"
        line = (f"{verdict:4} http={r['status']} lat={r['latency_ms']:>6}ms "
                f"{listed_mark:10} {model}")
        print(line)
        lines.append(line + "\n")
        if r["status"] != 200:
            snippet = (r.get("error") or r["snippet"])[:200].replace("\n", " ")
            lines.append(f"        err: {snippet}\n")
        time.sleep(2.0)

    with open(args.out, "w", encoding="utf-8") as fh:
        fh.writelines(lines)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
