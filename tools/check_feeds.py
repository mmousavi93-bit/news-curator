"""Dev utility -- NOT pipeline code. Checks every URL in sources_candidates.csv.

Stdlib only, on purpose: this must run on a clean Windows Python with nothing
installed. Run it from the repo root:

    python tools/check_feeds.py

Writes config/sources_probe.csv. Nothing else. No network calls happen at
import time, so this file is safe to sit in the repo.

Columns out:
    name, type, url, http, items, newest, verdict, detail

verdict is one of: OK, EMPTY, NOT_FEED, HTTP_ERROR, DNS, TIMEOUT, TLS
Only rows with verdict=OK and items>0 belong in sources.yaml.
"""

from __future__ import annotations

import argparse
import csv
import pathlib
import re
import socket
import ssl
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

REPO = pathlib.Path(__file__).resolve().parents[1]
IN_CSV = REPO / "config" / "sources_candidates.csv"

# WHERE this probe runs changes the answer. Run it from Iran and Western feeds
# fail the TLS handshake; run it from a US GitHub runner and Iranian state media
# may geo-block instead. The pipeline runs on GitHub runners, so CI is the
# authoritative verdict -- but the local run is still worth having, because a
# disagreement between the two IS the finding. Hence --tag: never overwrite one
# environment's result with another's.

TIMEOUT = 20
# Some outlets 403 a bare urllib UA. This is a liveness probe, not scraping.
UA = "Mozilla/5.0 (compatible; news-curator-feedcheck/1.0)"

ITEM_RE = re.compile(rb"<(item|entry)[\s>]", re.I)
DATE_RE = re.compile(
    rb"<(pubDate|published|updated|dc:date)>(.{4,40}?)</", re.I | re.S
)
# t.me/s/<channel> preview pages -- HTML, not XML. Each post is a div with this class.
TG_POST_RE = re.compile(rb'class="tgme_widget_message[\s"]', re.I)


def fetch(url: str) -> tuple[int, bytes, str]:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as resp:
        return resp.status, resp.read(400_000), resp.headers.get("Content-Type", "")


def probe(row: dict) -> dict:
    url = (row.get("url") or "").strip()
    kind = (row.get("type") or "rss").strip()
    out = {
        "name": row.get("name", ""),
        "type": kind,
        "url": url,
        "http": "",
        "items": 0,
        "newest": "",
        "verdict": "",
        "detail": "",
    }
    try:
        status, body, ctype = fetch(url)
    except urllib.error.HTTPError as exc:
        out["http"] = exc.code
        out["verdict"] = "HTTP_ERROR"
        out["detail"] = exc.reason or ""
        return out
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        name = type(reason).__name__
        if isinstance(reason, socket.timeout):
            out["verdict"] = "TIMEOUT"
        elif isinstance(reason, ssl.SSLError):
            out["verdict"] = "TLS"
        else:
            out["verdict"] = "DNS"
        out["detail"] = f"{name}: {reason}"
        return out
    except socket.timeout:
        out["verdict"] = "TIMEOUT"
        return out
    except Exception as exc:  # noqa: BLE001 -- probe must never abort the sweep
        out["verdict"] = "ERROR"
        out["detail"] = f"{type(exc).__name__}: {exc}"
        return out

    out["http"] = status
    out["detail"] = ctype.split(";")[0]

    if kind == "telegram":
        count = len(TG_POST_RE.findall(body))
        out["items"] = count
        out["verdict"] = "OK" if count else "NOT_FEED"
        return out

    count = len(ITEM_RE.findall(body))
    out["items"] = count
    if count == 0:
        # HTML landing page served instead of XML is the classic dead-feed signature.
        out["verdict"] = "NOT_FEED" if b"<html" in body[:2000].lower() else "EMPTY"
        return out

    match = DATE_RE.search(body)
    if match:
        out["newest"] = match.group(2).decode("utf-8", "replace").strip()[:40]
    out["verdict"] = "OK"
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="check_feeds")
    parser.add_argument(
        "--tag", default="local",
        help="where this ran, e.g. 'local' or 'ci'. Output goes to "
             "config/sources_probe_<tag>.csv and is recorded in a 'probe' column.",
    )
    args = parser.parse_args(argv)
    out_csv = REPO / "config" / f"sources_probe_{args.tag}.csv"

    if not IN_CSV.is_file():
        print(f"missing {IN_CSV}", file=sys.stderr)
        return 2
    with IN_CSV.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    print(f"probing {len(rows)} sources from '{args.tag}', {TIMEOUT}s timeout each ...")
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(probe, rows))
    for res in results:
        res["probe"] = args.tag

    fields = ["probe", "name", "type", "url", "http", "items", "newest", "verdict", "detail"]
    with out_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(results)

    tally: dict[str, int] = {}
    for res in results:
        tally[res["verdict"]] = tally.get(res["verdict"], 0) + 1
    for verdict in sorted(tally):
        print(f"  {verdict:<10} {tally[verdict]}")
    # TLS/DNS/TIMEOUT are network-environment verdicts, not source verdicts.
    # Printing them next to a 404 invites cutting a live feed for being blocked.
    blocked = [r["name"] for r in results if r["verdict"] in ("TLS", "DNS", "TIMEOUT")]
    broken = [r["name"] for r in results if r["verdict"] in ("HTTP_ERROR", "NOT_FEED", "EMPTY", "ERROR")]
    if broken:
        print("\nURL is wrong (server answered, feed was not there) -- fix or cut:")
        for name in broken:
            print(f"  - {name}")
    if blocked:
        print(f"\nUNREACHABLE FROM '{args.tag}' -- verdict unknown, do NOT cut on this alone:")
        for name in blocked:
            print(f"  - {name}")
    print(f"\nwrote {out_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
