"""Assemble source-only excerpt inputs, retaining every historical fate and gap."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analysis.signal_dataset_validation import item_issue, timestamp, validate
from tools.signal_dataset_inventory import read_manifest, write_json, sha256
from tools.signal_membership import load_items, load_clusters, valid_members


def assemble(manifest, memberships):
    if memberships['dataset_id'] != manifest['dataset_id']:
        raise ValueError('membership dataset identity mismatch')
    reports = {r['run_key']: r for r in memberships['runs']}
    if len(reports) != len(memberships['runs']):
        raise ValueError('duplicate membership runs')
    if set(reports)-{r['run_key'] for r in manifest['runs']}:
        raise ValueError('unknown membership run')
    runs, unavailable = [], []
    for source in manifest['runs']:
        key = source['run_key']
        if not source.get('chosen') or not source.get('cutoff'):
            unavailable.append(dict(run_key=key, reason='paired_report_or_cutoff_unavailable'))
            continue
        cutoff = timestamp(source['cutoff'])
        items = load_items(Path(source['read']['path']))
        rows = load_clusters(Path(source['chosen']['path']))
        report = reports.get(key)
        if not report:
            raise ValueError('membership report missing: '+key)
        recovered = {c['cluster_key']: c for c in report['memberships']}
        if len(recovered) != len(report['memberships']) or set(recovered) != {c['cluster_key'] for c in rows}:
            raise ValueError('membership row coverage mismatch')
        gaps = list(source.get('gaps', []))
        sources = source.get('sources', {})
        if not source.get('source_snapshot') or not source.get('settings_snapshot'):
            gaps.append('historical_config_unavailable')
        clusters, used = [], set()
        for row in rows:
            member = recovered[row['cluster_key']]
            for field in ('n_members', 'sources', 'fate'):
                if member[field] != row[field]:
                    raise ValueError('historical row metadata conflict')
            urls = member['urls']
            if urls and (not valid_members(row, urls, items) or used.intersection(urls)):
                raise ValueError('invalid or overlapping membership')
            used.update(urls)
            data = [{k: v for k, v in items[u].items() if not k.startswith('_')} for u in urls]
            problems = []
            for item in data:
                issue = item_issue(item, cutoff)
                if issue:
                    problems.append(issue)
                if item['source_id'] not in sources:
                    problems.append('unknown_source_join')
                if not item['published_at']:
                    gaps.append('item_date_unavailable')
                if not item['body'] or item['body'].endswith(('…', '...')):
                    gaps.append('empty_or_truncated_evidence')
            fate = 'not_extracted' if urls else 'membership_unresolved'
            if problems:
                fate = 'evidence_quarantined'
            if fate != 'not_extracted':
                gaps.append(fate)
            clusters.append(dict(cluster_key=row['cluster_key'], language=data[0]['lang'] if data else 'und',
                                 items=data if not problems else [], fate=fate, historical_fate=row['fate'],
                                 membership=dict(urls=urls, method=member['method'], n_members=row['n_members'],
                                                 sources=row['sources']),
                                 quarantine=dict(reasons=sorted(set(problems)), items=data) if problems else None,
                                 provenance=[dict(url=u, csv_rows=items[u]['_csv_rows'],
                                                  excerpt_sha256=sha256(items[u]['body'].encode('utf-8')),
                                                  raw_hash_status='unavailable:csv-export') for u in urls]))
        if set(items)-used:
            gaps.append('unassigned_items')
        if not source.get('collection_healthy'):
            gaps.append('collection_health_unverified')
        if not source.get('history_sufficient'):
            gaps.append('history_unverified')
        if not source.get('market_inputs_verified'):
            gaps.append('market_inputs_unavailable')
        runs.append(dict(run_key=key, cutoff=source['cutoff'], sources=sources, complete=not gaps,
                         stream_id=source.get('stream_id', 'default'), body_chars=4000, clusters=clusters,
                         gaps=sorted(set(gaps)), unassigned_urls=sorted(set(items)-used),
                         text_provenance='csv-export-excerpts'))
    runs.sort(key=lambda r: (timestamp(r['cutoff']), r['run_key']))
    dataset = dict(version=1, dataset_id=manifest['dataset_id'], runs=runs, unavailable_runs=unavailable,
                   selection=dict(evaluation=manifest.get('selected_run_keys', []),
                                  warmup=manifest.get('warmup_run_keys', []), windows=manifest.get('windows', [])))
    dataset['validation'] = validate(manifest, dataset)
    if not dataset['validation']['structural_pass']:
        raise ValueError(str(dataset['validation']['errors']))
    return dataset


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest', type=Path, required=True)
    p.add_argument('--memberships', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    args = p.parse_args(argv)
    try:
        dataset = assemble(read_manifest(args.manifest), json.loads(args.memberships.read_text(encoding='utf-8-sig')))
        write_json(args.out, dataset)
        print(json.dumps(dataset['validation'], indent=2))
        return 0 if dataset['validation']['complete'] else 1
    except (ValueError, KeyError, OSError) as exc:
        p.error(str(exc))


if __name__ == '__main__':
    raise SystemExit(main())
