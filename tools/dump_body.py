"""Dev utility -- NOT pipeline code. Fetches one URL and reports how its dates
are actually laid out, plus optionally dumps the raw bytes as evidence.

Written 2026-08-19 to settle g1: state_dept_travel returns 89-122 parsed items
and ZERO per-item timestamps, while tools/check_feeds.py has read a perfectly
ordinary RFC-822 date off that same feed in ci2, ci3 and ci4. The two are not
in conflict -- the probe takes the FIRST date anywhere in the body, and the
collector takes one per <item> slice. So a feed carrying <pubDate> only at
channel level satisfies the probe and starves the collector.

Two candidate causes, and CLAUDE.md is explicit that they must not be guessed
between -- the undeclared-gzip hypothesis was guessed in session 4 and ci4
falsified it after a wasted round:
  (a) channel-level <pubDate> only, items genuinely undated -> a feed property,
      needs a policy decision for undated items, NOT a collector fix;
  (b) a per-item date tag that rss.py's _DATE_RE does not match -> a collector
      bug, fix the regex.
This tool distinguishes them mechanically by counting dates inside vs outside
item slices and listing the tag names actually present in the first few items.

Separate file rather than a flag on check_feeds.py because that file is already
217 lines, over the ~200 cap in CLAUDE.md constraint 12, and this is a
different job: check_feeds answers "is it alive", this answers "why is a live
feed's data shaped wrong".

Stdlib only, same contract as check_feeds.py -- probe-feeds.yml deliberately
has no pip install step.
"""

from __future__ import annotations

import argparse
import gzip
import pathlib
import re
import ssl
import sys
import urllib.error
import urllib.request

REPO = pathlib.Path(__file__).resolve().parents[1]

# Byte-identical to tools/check_feeds.py:48 and to settings.yaml's user_agent.
# A probe that a bot filter blocks measures the filter, not the feed, and a
# diagnosis run under a DIFFERENT UA than the collector could produce a body
# the collector never sees. Session 5 flagged UA drift as the single item most
# likely to burn a phase; do not let it drift here either.
TIMEOUT = 20
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
ACCEPT = ("application/rss+xml, application/atom+xml, application/xml;q=0.9, "
          "text/xml;q=0.9, text/html;q=0.8, */*;q=0.7")

# COPIED VERBATIM from src/agent/collectors/rss.py. They are duplicated rather
# than imported because tools/ must run with no PYTHONPATH and no install, but
# that means they can DRIFT. The tool prints both patterns at the end of every
# run so a reader can diff them against rss.py by eye in one place.
ENTRY_RE = re.compile(rb"<(item|entry)[\s>].*?</\1\s*>", re.I | re.S)
DATE_RE = re.compile(rb"<(pubDate|published|updated|dc:date)[^>]*>(.*?)</\1>", re.I | re.S)

# Deliberately WIDER than DATE_RE: anything that smells like a date-bearing tag.
# The gap between what this finds inside an item and what DATE_RE finds inside
# the same item IS the answer to cause (b).
ANY_TAG_RE = re.compile(rb"<([a-zA-Z][\w:.-]*)[\s>/]")
DATEISH_RE = re.compile(
    rb"<([a-zA-Z][\w:.-]*(?:date|time|updated|published|created|modified)[\w:.-]*)"
    rb"[^>]*>(.{4,60}?)</", re.I | re.S
)


def fetch(url: str) -> tuple[int, bytes, str]:
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


def analyse(body: bytes, sample: int) -> None:
    entries = list(ENTRY_RE.finditer(body))
    print(f"item/entry slices found: {len(entries)}")

    inside = bytearray()
    for m in entries:
        inside += m.group(0)
    outside = ENTRY_RE.sub(b"", body)

    in_hits = DATE_RE.findall(bytes(inside))
    out_hits = DATE_RE.findall(outside)
    print(f"DATE_RE matches INSIDE item slices : {len(in_hits)}")
    print(f"DATE_RE matches OUTSIDE (channel)  : {len(out_hits)}")
    for tag, val in out_hits[:3]:
        print(f"    channel-level <{tag.decode('ascii','replace')}> = "
              f"{val.decode('utf-8','replace').strip()[:50]}")

    print()
    if not entries:
        print("VERDICT: no item slices at all -- not an RSS/Atom body, or ENTRY_RE is wrong.")
    elif in_hits:
        print("VERDICT: per-item dates DO exist and DATE_RE matches them. If the")
        print("         collector still yields None, the bug is in parse_date, not")
        print("         in tag selection -- check the value format below.")
    else:
        loose = DATEISH_RE.findall(bytes(inside))
        if loose:
            print("VERDICT: CAUSE (b) -- COLLECTOR BUG. Items carry date-bearing tags")
            print("         that DATE_RE does not list. Add them to rss.py:")
            seen = []
            for tag, val in loose:
                name = tag.decode("ascii", "replace")
                if name not in seen:
                    seen.append(name)
                    print(f"    <{name}> = {val.decode('utf-8','replace').strip()[:50]}")
        else:
            print("VERDICT: CAUSE (a) -- FEED PROPERTY, NOT A BUG. Items carry no")
            print("         date-bearing tag of any kind. The channel-level date above")
            print("         is all there is. This needs a POLICY decision for undated")
            print("         items, not a regex change.")

    print()
    print(f"tag inventory of the first {sample} item slice(s):")
    for i, m in enumerate(entries[:sample], 1):
        tags = []
        for t in ANY_TAG_RE.findall(m.group(0)):
            name = t.decode("ascii", "replace")
            if name not in tags:
                tags.append(name)
        print(f"  item {i}: {', '.join(tags)}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="dump_body")
    p.add_argument("--url", required=True, help="URL to fetch and analyse")
    p.add_argument("--out", default=None,
                   help="write the raw bytes here, relative to repo root. "
                        "Upload as a CI artifact; never commit a feed body.")
    p.add_argument("--sample", type=int, default=3,
                   help="how many item slices to inventory (default 3)")
    args = p.parse_args(argv)

    print(f"fetching {args.url}")
    try:
        status, body, ctype = fetch(args.url)
    except urllib.error.HTTPError as exc:
        print(f"HTTP {exc.code} {exc.reason or ''}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 -- a dev tool reports, never raises
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(f"HTTP {status}, {len(body)} bytes, content-type: {ctype}")
    if len(body) == 400_000:
        print("WARNING: hit the 400 KB cap -- analysis covers a TRUNCATED body.")
    print()
    analyse(body, args.sample)

    if args.out:
        dest = REPO / args.out
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(body)
        print(f"\nraw body written to {args.out}")

    print(f"\npatterns used (diff against rss.py if this looks wrong):")
    print(f"  ENTRY_RE {ENTRY_RE.pattern!r}")
    print(f"  DATE_RE  {DATE_RE.pattern!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
