"""CI-only paginated report fetcher with immutable per-run artifact directories."""
import argparse
import io
import json
import os
import urllib.request
import urllib.error
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
try:
    from tools.signal_dataset_inventory import write_json, sha256
except ModuleNotFoundError:
    from signal_dataset_inventory import write_json, sha256

_API = 'https://api.github.com/repos/{repo}/actions/artifacts'


def _headers(token):
    return {'Authorization': 'Bearer '+token, 'Accept': 'application/vnd.github+json',
            'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'signal-report-inventory'}


def _get_json(url, token):
    with urllib.request.urlopen(urllib.request.Request(url, headers=_headers(token)), timeout=60) as response:
        return json.load(response)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _download(url, token):
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        response = opener.open(urllib.request.Request(url, headers=_headers(token)), timeout=120)
    except urllib.error.HTTPError as exc:
        if exc.code not in (301, 302, 307, 308) or not exc.headers.get('Location', '').startswith('https://'):
            raise
        # Signed blob URL authenticates itself; never forward the GitHub token.
        response = urllib.request.urlopen(exc.headers['Location'], timeout=120)
    with response:
        return response.read()


def fetch(repo, token, start, end, out, run_ids=None):
    if start >= end:
        raise ValueError('start must precede end')
    records, seen, page = [], set(), 1
    while True:
        batch = _get_json(_API.format(repo=repo)+f'?per_page=100&page={page}', token).get('artifacts', [])
        if not batch:
            break
        for artifact in batch:
            created = datetime.fromisoformat(artifact['created_at'].replace('Z', '+00:00'))
            run_id = str(artifact['workflow_run']['id'])
            if artifact['name'] != 'run-reports' or not start <= created < end:
                continue
            if run_ids and run_id not in run_ids:
                continue
            seen.add(run_id)
            run = _get_json(f'https://api.github.com/repos/{repo}/actions/runs/{run_id}', token)
            record = dict(run_id=run_id, artifact_id=artifact['id'], created_at=artifact['created_at'],
                          head_sha=run['head_sha'], run_metadata=run, expired=artifact.get('expired', False), files=[])
            records.append(record)
            if record['expired']:
                record['availability'] = 'expired'
                continue
            data = _download(f"{_API.format(repo=repo)}/{artifact['id']}/zip", token)
            record['zip_sha256'] = sha256(data)
            digest = artifact.get('digest')
            if digest and digest != 'sha256:'+record['zip_sha256']:
                raise ValueError('artifact digest mismatch')
            target = Path(out)/run_id/str(artifact['id'])
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                names = set()
                for info in archive.infolist():
                    name = info.filename
                    dest = target/name
                    if (chr(92) in name or ':' in name or name in names
                            or not dest.resolve().is_relative_to(target.resolve())
                            or (info.external_attr >> 16) & 0o170000 == 0o120000):
                        raise ValueError('unsafe or duplicate ZIP member: '+name)
                    names.add(name)
                    if info.is_dir():
                        continue
                    payload = archive.read(info)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    if dest.exists() and dest.read_bytes() != payload:
                        raise ValueError('immutable artifact conflict')
                    dest.write_bytes(payload)
                    if sha256(dest.read_bytes()) != sha256(payload):
                        raise ValueError('file checksum mismatch')
                    record['files'].append(dict(path=str(dest.relative_to(out)), sha256=sha256(payload)))
            record['availability'] = 'downloaded'
        page += 1
    result = dict(version=1, interval=dict(start=start.isoformat(), end=end.isoformat()),
                  artifacts=records, missing_run_ids=sorted(set(run_ids or [])-seen))
    write_json(Path(out)/'manifest.json', result)
    return result


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo', default='mmousavi93-bit/news-curator')
    p.add_argument('--days', type=int, default=7)
    p.add_argument('--start')
    p.add_argument('--end')
    p.add_argument('--run-ids', type=Path)
    p.add_argument('--out', type=Path, default=Path('calibration/runs'))
    args = p.parse_args(argv)
    token = os.environ.get('GITHUB_TOKEN') or os.environ.get('GH_TOKEN')
    if not token:
        p.error('GITHUB_TOKEN not set')
    def aware(value):
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp
    end = aware(args.end) if args.end else datetime.now(timezone.utc)
    start = aware(args.start) if args.start else end-timedelta(days=args.days)
    ids = None
    if args.run_ids:
        text = args.run_ids.read_text(encoding='utf-8-sig').strip()
        ids = set(map(str, json.loads(text))) if text.startswith('[') else set(text.split())
        if any(not value.isdigit() for value in ids):
            p.error('run IDs must be numeric')
    try:
        result = fetch(args.repo, token, start, end, args.out, ids)
        return 0 if result['artifacts'] and not result['missing_run_ids'] else 1
    except (ValueError, OSError, zipfile.BadZipFile) as exc:
        p.error(str(exc))


if __name__ == '__main__':
    raise SystemExit(main())
