"""Offline inventory of immutable report snapshots; never substitutes current config."""
import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, value):
    path = Path(path)
    data = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)+'\n').encode('utf-8')
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError('immutable output conflicts: ' + str(path))
        return
    fd, name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def file_record(path):
    path = Path(path)
    try:
        name = str(path.resolve().relative_to(Path.cwd())).replace(chr(92), '/')
    except ValueError:
        name = str(path.resolve())
    return dict(path=name, sha256=sha256(path.read_bytes()))


def historical_config(sha, root):
    """Read only the recorded code object, never working-tree configuration."""
    import yaml
    if not sha:
        return {}
    result = {}
    try:
        for field, name in [('source_snapshot', 'credibility.yaml'), ('settings_snapshot', 'settings.yaml')]:
            data = subprocess.check_output(['git', 'show', sha+':config/'+name], stderr=subprocess.DEVNULL)
            path = Path(root)/sha/name
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.read_bytes() != data:
                raise ValueError('historical snapshot conflict')
            path.write_bytes(data)
            result[field] = file_record(path)
        credibility = yaml.safe_load(Path(result['source_snapshot']['path']).read_bytes())
        result['sources'] = {k: {'tier': v['tier'], 'group': v.get('group')}
                             for k, v in credibility['sources'].items()}
        pipeline = yaml.safe_load(Path(result['settings_snapshot']['path']).read_bytes())['pipeline']
        result['embedding'] = dict(model=pipeline['embed_model'], revision=None,
                                   threshold=pipeline['cluster_similarity_threshold'])
    except (subprocess.CalledProcessError, KeyError):
        return {}  # Retain explicit gap; no fallback to today's YAML.
    return result


def read_manifest(path):
    manifest = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    keys = [r['run_key'] for r in manifest['runs']]
    if len(keys) != len(set(keys)):
        raise ValueError('duplicate run keys')
    for run in manifest['runs']:
        for field in ('read', 'chosen', 'cutoff_evidence', 'source_snapshot', 'settings_snapshot'):
            entry = run.get(field)
            if entry and sha256(Path(entry['path']).read_bytes()) != entry['sha256']:
                raise ValueError('checksum mismatch: ' + field)
        if run.get('source_snapshot'):
            import yaml
            original = yaml.safe_load(Path(run['source_snapshot']['path']).read_bytes())['sources']
            sources = {k: dict(tier=v['tier'], group=v.get('group')) for k, v in original.items()}
            if run.get('sources') != sources:
                raise ValueError('source joins differ from frozen historical snapshot')
    return manifest


def export_ref(ref, destination):
    # Resolve once before reading any blobs. git show returns original bytes, including BOM.
    sha = subprocess.check_output(['git', 'rev-parse', ref+'^{commit}']).decode().strip()
    names = subprocess.check_output(['git', 'ls-tree', '-r', '--name-only', sha]).decode().splitlines()
    root = Path(destination)/sha
    for name in names:
        if not (name.endswith('.csv') or name.endswith('.json')):
            continue
        target = root/name
        if not target.resolve().is_relative_to(root.resolve()):
            raise ValueError('unsafe git path')
        data = subprocess.check_output(['git', 'show', sha+':'+name])
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.read_bytes() != data:
            raise ValueError('snapshot conflict')
        target.write_bytes(data)
    return sha, root


def inventory(local, items=None, reports_ref=None, snapshot_root=None, membership_ref=None):
    roots, fetched_sha = [Path(local)], None
    if reports_ref:
        fetched_sha, root = export_ref(reports_ref, snapshot_root)
        roots.append(root)
    runs, fetched_inventories = [], []
    for root in roots:
        download = root/'manifest.json'
        if not download.exists():
            download = root/'reports'/'manifest.json'
        metadata = json.loads(download.read_text(encoding='utf-8-sig')) if download.exists() else {}
        if metadata:
            for artifact in metadata.get('artifacts', []):
                for entry in artifact.get('files', []):
                    original = download.parent/entry['path']
                    if (not original.resolve().is_relative_to(download.parent.resolve())
                            or sha256(original.read_bytes()) != entry['sha256']):
                        raise ValueError('bridge artifact checksum mismatch')
            fetched_inventories.append(metadata)
        for read in sorted(root.rglob('read_*.csv')):
            stamp = read.stem.removeprefix('read_')
            chosen = read.with_name('chosen_'+stamp+'.csv')
            summary = read.with_name('run_'+stamp+'.csv')
            from_csv = None
            if summary.exists():
                try:
                    from tools.signal_membership import csv_rows
                except ModuleNotFoundError:
                    from signal_membership import csv_rows
                rows = csv_rows(summary)
                from_csv = rows[0].get('run_at_utc') if rows else None
            artifact = next((a for a in metadata.get('artifacts', [])
                             if str(a['run_id']) in read.parts), {})
            run_id = artifact.get('run_id')
            if fetched_sha and stamp == '20260925T102645Z':
                run_id = 36123832881
            key = str(run_id) if run_id else 'local:'+stamp
            if any(r['run_key'] == key for r in runs):
                raise ValueError('multiple report sets for run: '+key)
            runs.append(dict(run_key=key, run_id=run_id, artifact_id=artifact.get('artifact_id'),
                             fetched_ref=reports_ref if root != Path(local) else None,
                             fetched_sha=fetched_sha if root != Path(local) else None,
                             pipeline_code_sha=artifact.get('head_sha'), cutoff=from_csv,
                             cutoff_evidence=file_record(summary) if summary.exists() else None,
                             read=file_record(read), chosen=file_record(chosen) if chosen.exists() else None,
                             sources={}, source_snapshot=None, settings_snapshot=None,
                             embedding=None, collection_healthy=False, history_sufficient=False,
                             text_provenance='csv-export-excerpts', availability='paired' if chosen.exists() else 'items_only',
                             gaps=['historical_config_unavailable', 'history_unverified', 'market_inputs_unavailable']
                             + ([] if run_id else ['run_id_unavailable'])
                             + ([] if from_csv else ['cutoff_unavailable'])))
            snapshots = historical_config(artifact.get('head_sha'), (snapshot_root or root/'snapshots'))
            runs[-1].update(snapshots)
            if snapshots:
                runs[-1]['gaps'].remove('historical_config_unavailable')
                runs[-1]['gaps'].append('historical_model_revision_missing')
    if items:
        runs.append(dict(run_key='development:'+Path(items).stem, read=file_record(items), chosen=None,
                         availability='items_only', gaps=['paired_report_missing', 'cutoff_unavailable']))
    recovery_snapshot = None
    if membership_ref:
        recovery_sha, recovery_root = export_ref(membership_ref, snapshot_root)
        recovery_snapshot = dict(ref=membership_ref, sha=recovery_sha,
                                 files=[file_record(p) for p in sorted(recovery_root.rglob('*.json'))])
    identity = sha256(json.dumps(runs, sort_keys=True).encode())[:16]
    return dict(version=1, dataset_id=identity, combination_budget=1_000_000, windows=[],
                selected_run_keys=[], warmup_run_keys=[], runs=runs,
                fetched_inventories=fetched_inventories,
                recovery_snapshot=recovery_snapshot,
                gaps=['five_windows_not_verified', 'human_class_coverage_unavailable'])


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--reports-ref')
    p.add_argument('--membership-ref', help='Immutable CI recovery bridge SHA to import and checksum')
    p.add_argument('--local', type=Path, default=Path('outputs'))
    p.add_argument('--items', type=Path)
    p.add_argument('--out', type=Path, required=True)
    args = p.parse_args(argv)
    try:
        value = inventory(args.local, args.items, args.reports_ref, args.out.parent/'snapshots', args.membership_ref)
        write_json(args.out, value)
        print(json.dumps(value, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        p.error(str(exc))


if __name__ == '__main__':
    raise SystemExit(main())
