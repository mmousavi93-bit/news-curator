"""Quality comparison: same cluster through all three free models."""
from __future__ import annotations

import json
import os
import sys
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError

CLUSTER_FILE = "tools/riyadh_cluster.txt"
PROMPT_TEMPLATE = "config/prompts/understand_batch.txt"

# Model configs
MODELS = {
    "gemini-flash": {
        "key_env": "GEMINI_API_KEY",
        "url": "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.6-flash:generateContent",
        "adapter": "gemini",
    },
    "groq-qwen": {
        "key_env": "GROQ_API_KEY",
        "url": "https://api.groq.com/openai/v1/chat/completions",
        "adapter": "openai",
        "model": "qwen/qwen3.8-27b",
    },
    "groq2-mixtral": {
        "key_env": "GROQ_API_KEY_2",
        "url": "https://api.groq.com/openai/v1/chat/completions",
        "adapter": "openai",
        "model": "mixtral-8x7b-32768",
    },
}

def build_prompt() -> str:
    with open(CLUSTER_FILE, encoding="utf-8") as f:
        items = f.read().strip()
    with open(PROMPT_TEMPLATE, encoding="utf-8") as f:
        template = f.read()
    return template.replace("{items}", items)

def call_gemini(prompt: str, key: str) -> dict:
    payload = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 2000},
    }).encode()
    r = Request(MODELS["gemini-flash"]["url"] + f"?key={key}", data=payload)
    r.add_header("Content-Type", "application/json")
    return _do_request(r)

def call_openai(prompt: str, key: str, model: str, base_url: str) -> dict:
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
        "max_tokens": 2000,
    }).encode()
    r = Request(base_url, data=payload)
    r.add_header("Authorization", f"Bearer {key}")
    r.add_header("Content-Type", "application/json")
    return _do_request(r)

def _do_request(r: Request) -> dict:
    try:
        resp = urlopen(r, timeout=120)
        return json.loads(resp.read().decode())
    except HTTPError as e:
        return {"error": e.code, "body": e.read().decode()[:500]}

def extract_output(model_name: str, raw: dict) -> dict:
    if "error" in raw:
        return {"model": model_name, "error": raw["error"], "body_preview": raw.get("body", "")}
    try:
        if model_name == "gemini-flash":
            text = raw["candidates"][0]["content"]["parts"][0]["text"]
        else:
            text = raw["choices"][0]["message"]["content"]
        # Strip JSON from markdown fences
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("\n```", 1)[0]
        parsed = json.loads(text)
        if isinstance(parsed, list) and len(parsed) > 0:
            return {"model": model_name, "output": parsed[0]}
        return {"model": model_name, "parse_error": "not a non-empty list", "raw": text[:300]}
    except (json.JSONDecodeError, KeyError) as e:
        return {"model": model_name, "parse_error": str(e), "raw_raw": str(raw)[:500]}

def main():
    prompt = build_prompt()
    print(f"Prompt length: {len(prompt):,} chars\n")

    results = {}
    for name, cfg in MODELS.items():
        key = os.environ.get(cfg["key_env"], "")
        if not key:
            results[name] = {"model": name, "error": f"missing env {cfg['key_env']}"}
            print(f"  {name}: SKIP (no key)")
            continue

        print(f"  {name} ...", end=" ", flush=True)
        t0 = time.monotonic()
        if cfg["adapter"] == "gemini":
            raw = call_gemini(prompt, key)
        else:
            raw = call_openai(prompt, key, cfg["model"], cfg["url"])
        elapsed = time.monotonic() - t0

        parsed = extract_output(name, raw)
        parsed["latency_s"] = round(elapsed, 1)
        results[name] = parsed
        status = "✅" if "output" in parsed else f"❌ {parsed.get('error','?')}"
        print(f"{status} ({elapsed:.1f}s)")

    # Comparison table
    print("\n=== COMPARISON ===")
    fields = ["headline", "category", "significance", "claim_status", "best_tier", "independent_count"]
    rows = []
    for name in MODELS:
        r = results.get(name, {})
        o = r.get("output", {})
        if not o:
            rows.append(f"| {name} | {' | '.join(['—']*len(fields))} | {r.get('error','parse fail')} |")
            continue
        vals = [str(o.get(f, "?")) for f in fields]
        rows.append(f"| {name} | {' | '.join(vals)} | {r.get('latency_s','?')}s |")

    header = f"| Model | {' | '.join(fields)} | Latency |"
    sep = f"|{'---|' * (len(fields)+2)}"
    print(header)
    print(sep)
    for row in rows:
        print(row)

    # Also dump full JSON for comparison
    print("\n=== FULL OUTPUT ===")
    for name in MODELS:
        r = results.get(name, {})
        print(f"\n--- {name} ---")
        print(json.dumps(r, ensure_ascii=False, indent=2))

    # Exit non-zero if any model failed
    failures = [n for n, r in results.items() if "output" not in r]
    if failures:
        print(f"\n❌ Failures: {failures}")
        sys.exit(1)

if __name__ == "__main__":
    main()