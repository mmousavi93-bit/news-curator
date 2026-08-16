"""Dev utility -- NOT pipeline code. Checks every URL in sources_candidates.csv.

Stdlib only, on purpose: this must run on a clean Windows Python with nothing
installed. Run it from the repo root:

    python tools/check_feeds.py

Writes config/sources_probe_<tag>.csv. Nothing else. No network calls happen at
import time, so this file is safe to sit in the repo.

Columns out: probe, id, name, type, url, http, items, newest, verdict, detail
verdict is one of: OK, EMPTY, NOT_FEED, HTTP_ERROR, DNS, TIMEOUT, TLS, ERROR
Only rows with verdict=OK and items>0 belong in sources.yaml.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import pathlib
import re
import socket
import ssl
import sys
import urllib.error
import urllib.parse
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
# Round-2 CI probe: 9 of 15 "broken" rows were 403, including BOTH URL variants
# for ISW and for UKMTO. Two paths on one host returning the same 403 is a
# host-level bot filter, not a wrong path -- the old
# "compatible; news-curator-feedcheck/1.0" reads as a crawler. A probe a bot
# filter blocks measures the filter, not the feed. The production collector
# needs these same headers.
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
ACCEPT = ("application/rss+xml, application/atom+xml, application/xml;q=0.9, "
          "text/xml;q=0.9, text/html;q=0.8, */*;q=0.7")

ITEM_RE = re.compile(rb"<(item|entry)[\s>]", re.I)
DATE_RE = re.compile(
    rb"<(pubDate|published|updated|dc:date)>(.{4,40}?)</", re.I | re.S
)
# t.me/s/<channel> preview pages -- HTML, not XML. Each post is a div with this class.
TG_POST_RE = re.compile(rb'class="tgme_widget_message[\s"]', re.I)


def fetch(url: str) -> tuple[int, bytes, str]:
    # No Accept-Encoding: gzip -- a 400 KB partial read of a gzip stream cannot
    # be decompressed. Some servers gzip anyway and urllib will not decode it,
    # so the body arrives binary and every item regex misses: a live feed scores
    # EMPTY. Decode when it happens; tolerate a truncated stream.
    req = urllib.request.Request(
        url,
        headers={"User-Agent": UA, "Accept": ACCEPT,
                 "Accept-Language": "en-US,en;q=0.9"},
    )
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as resp:
        body = resp.read(400_000)
        ctype = resp.headers.get("Content-Type", "")
        if "gzip" in (resp.headers.get("Content-Encoding") or "").lower():
            try:
                body = gzip.decompress(body)
            except Exception:  # noqa: BLE001 -- truncated stream, keep raw bytes
                ctype += "; gzip-undecodable"
    return resp.status, body, ctype


def probe(row: dict) -> dict:
    url = (row.get("url") or "").strip()
    kind = (row.get("type") or "rss").strip()
    out = {
        # `id` is the join key into credibility.yaml. Carried through the probe
        # so the surviving rows can be pasted straight into sources.yaml.
        "id": row.get("id", ""),
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
    parser.add_argument(
        "--input", default=None,
        help="candidates CSV to probe, relative to repo root. Defaults to "
             "config/sources_candidates.csv. Earlier rounds are kept on disk as "
             "evidence, so point this at whichever round you are testing.",
    )
    args = parser.parse_args(argv)
    in_csv = (REPO / args.input) if args.input else IN_CSV
    out_csv = REPO / "config" / f"sources_probe_{args.tag}.csv"

    if not in_csv.is_file():
        print(f"missing {in_csv}", file=sys.stderr)
        return 2
    with in_csv.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    print(f"probing {len(rows)} sources from '{args.tag}', {TIMEOUT}s timeout each ...")
    # One host at a time, hosts in parallel. Round 2 probed amwaj.media/feed and
    # /rss concurrently and the second got 429 -- a rate limit we inflicted on
    # ourselves, recorded as the source's verdict. Every a/b variant pair shares
    # a host, so this hit exactly the rows that are hardest to read.
    buckets: dict[str, list[dict]] = {}
    for row in rows:
        host = urllib.parse.urlsplit((row.get("url") or "").strip()).netloc.lower()
        buckets.setdefault(host, []).append(row)

    def probe_host(bucket: list[dict]) -> list[dict]:
        return [probe(row) for row in bucket]

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = [r for out in pool.map(probe_host, buckets.values()) for r in out]
    for res in results:
        res["probe"] = args.tag

    fields = ["probe", "id", "name", "type", "url", "http", "items", "newest", "verdict", "detail"]
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
