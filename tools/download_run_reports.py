"""CI helper: download the last N days of `run-reports` artifacts.

    python tools/download_run_reports.py --days 7 --out calibration/runs

Lists the repo's workflow artifacts, keeps the ones named `run-reports`
whose created_at falls inside the window, downloads each zip (Bearer
GITHUB_TOKEN, provided by the Actions runner) and extracts it into its own
`<out>/<artifact_id>/` dir -- one dir per run, so the scorer can treat each
run independently and the analyzer can bucket scores per run.

CI-only: this machine's VPN-gated network cannot reach api.github.com, but
the Actions runner can.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

_API = "https://api.github.com/repos/{repo}/actions/artifacts"


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "hermes-prellm-calibration",
    }


def _get_json(url: str, token: str):
    request = urllib.request.Request(url, headers=_headers(token))
    with urllib.request.urlopen(request, timeout=60) as resp:
        return json.load(resp)


def _download(url: str, token: str) -> bytes:
    request = urllib.request.Request(url, headers=_headers(token))
    with urllib.request.urlopen(request, timeout=120) as resp:
        return resp.read()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default="mmousavi93-bit/news-curator")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--out", default="calibration/runs")
    args = parser.parse_args(argv)

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        print("GITHUB_TOKEN not set", file=sys.stderr)
        return 1

    artifacts = _get_json(_API.format(repo=args.repo), token).get("artifacts", [])
    cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)

    def created(artifact) -> datetime:
        return datetime.fromisoformat(artifact["created_at"].replace("Z", "+00:00"))

    recent = [
        a for a in artifacts
        if a.get("name") == "run-reports" and created(a) >= cutoff
    ]
    print(f"{len(artifacts)} artifacts; {len(recent)} run-reports within {args.days} days",
          file=sys.stderr)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for artifact in recent:
        target = out / str(artifact["id"])
        if target.exists():
            print(f"have {artifact['id']} ({created(artifact):%Y-%m-%d}), skip", file=sys.stderr)
            continue
        target.mkdir(parents=True, exist_ok=True)
        data = _download(f"{_API.format(repo=args.repo)}/{artifact['id']}/zip", token)
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            zf.extractall(target)
        print(f"downloaded {artifact['id']} ({created(artifact):%Y-%m-%d})", file=sys.stderr)

    return 0 if recent else 1


if __name__ == "__main__":
    raise SystemExit(main())
