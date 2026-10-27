"""CI fallback: whole-run embedding, exact historical joins only."""
import argparse
import copy
import importlib.metadata
import json
import os
import platform
import subprocess
from datetime import datetime
from pathlib import Path
from agent.collectors.base import Item
from agent.pipeline.cluster import cluster_items
from agent.pipeline.embed import MiniLmEmbedder
try:
    from tools.signal_dataset_inventory import read_manifest, write_json, sha256
    from tools.signal_membership import load_items, load_clusters, valid_members
except ModuleNotFoundError:
    from signal_dataset_inventory import read_manifest, write_json, sha256
    from signal_membership import load_items, load_clusters, valid_members


def make_item(data):
    fields = {k: v for k, v in data.items() if not k.startswith('_')}
    if fields['published_at']:
        fields['published_at'] = datetime.fromisoformat(fields['published_at'].replace('Z', '+00:00'))
        if fields['published_at'].tzinfo is None:
            raise ValueError('ambiguous item timestamp')
    return Item(**fields)


def real_embedder(settings):
    if os.environ.get('CI') != 'true':
        raise ValueError('real embedding is CI-only')
    # MiniLmEmbedder lacks a revision argument: resolve a pinned local checkpoint first.
    from huggingface_hub import snapshot_download
    revision = settings.get('revision')
    if not revision or len(revision) != 40:
        raise ValueError('immutable model revision required; historical fidelity gap')
    path = snapshot_download(settings['model'], revision=revision)
    return MiniLmEmbedder(path)


def recluster(manifest, memberships, embedder_factory=real_embedder):
    if memberships['dataset_id'] != manifest['dataset_id']:
        raise ValueError('dataset identity mismatch')
    result = copy.deepcopy(memberships)
    originals = {r['run_key']: r for r in manifest['runs']}
    if len({r['run_key'] for r in result['runs']}) != len(result['runs']):
        raise ValueError('duplicate membership run')
    for report in result['runs']:
        run = originals[report['run_key']]
        items = load_items(Path(run['read']['path']))
        historical = {c['cluster_key']: c for c in load_clusters(Path(run['chosen']['path']))}
        if (len({c['cluster_key'] for c in report['memberships']}) != len(report['memberships'])
                or {c['cluster_key'] for c in report['memberships']} != historical.keys()):
            raise ValueError('historical row coverage mismatch')
        used = set()
        for row in report['memberships']:
            if any(row[f] != historical[row['cluster_key']][f] for f in ('sources', 'n_members', 'fate')):
                raise ValueError('historical row metadata conflict')
            if row['urls']:
                if not valid_members(row, row['urls'], items) or used.intersection(row['urls']):
                    raise ValueError('invalid authoritative membership')
                used.update(row['urls'])
        pending = [c for c in report['memberships'] if not c['urls'] and c['n_members'] >= 4]
        if not pending:
            continue  # No embeddings for unresolved singletons/small clusters.
        settings = run.get('embedding')
        if not settings or not run.get('pipeline_code_sha'):
            report['fallback_gap'] = 'historical_embedding_or_code_provenance_missing'
            continue
        if not settings.get('revision'):
            report['fallback_gap'] = 'historical_model_revision_missing'
            continue
        embedder = embedder_factory(settings)
        ordered = [make_item(items[u]) for u in sorted(items)]
        vectors = embedder.embed([f'{i.title}\n{i.body}' for i in ordered])
        if len(vectors) != len(ordered):
            raise ValueError('embedding count mismatch')
        candidates = cluster_items(ordered, vectors, settings['threshold'])
        proposed = {c.key: sorted(i.url for i in c.members) for c in candidates}
        packages = {}
        for name in ('news-curator', 'sentence-transformers', 'transformers', 'torch', 'huggingface-hub'):
            try:
                packages[name] = importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError:
                packages[name] = 'unavailable'
        report['fallback_provenance'] = dict(settings, pipeline_code_sha=run['pipeline_code_sha'],
                                           executed_code_sha=subprocess.check_output(['git', 'rev-parse', 'HEAD'],
                                               cwd=Path(__file__).resolve().parents[1]).decode().strip(),
                                           packages=packages, python=platform.python_version(),
                                           text_boundary='csv-export-excerpts')
        report['fallback_fidelity_gaps'] = ['truncated_export_text', 'historical_package_versions_unavailable']
        if report['fallback_provenance']['executed_code_sha'] != run['pipeline_code_sha']:
            report['fallback_fidelity_gaps'].append('executed_code_differs_from_historical')
        report['fallback_candidates'] = proposed  # split/merge diagnostics, never relabel old keys
        for row in pending:
            urls = proposed.get(row['cluster_key'], [])
            if not valid_members(row, urls, items) or used.intersection(urls):
                continue
            row.update(urls=urls, method='ci_recluster_exact', source_match='exact' if row['sources'] else 'unverified_all_run_search')
            used.update(urls)
        unresolved = [c for c in report['memberships'] if not c['urls']]
        report.update(recovered_count=len(report['memberships'])-len(unresolved),
                      unrecovered_count=len(unresolved),
                      unrecovered_clusters=[dict(cluster_key=c['cluster_key'], n_members=c['n_members'],
                                                 reason='no_exact_fallback_join') for c in unresolved],
                      recovered_members=len(used), unassigned_urls=sorted(set(items)-used),
                      cluster_coverage=(len(report['memberships'])-len(unresolved))/len(report['memberships']),
                      member_coverage=len(used)/report['expected_members'])
    return result


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest', type=Path, required=True)
    p.add_argument('--memberships', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    args = p.parse_args(argv)
    try:
        result = recluster(read_manifest(args.manifest), json.loads(args.memberships.read_text(encoding='utf-8-sig')))
        write_json(args.out, result)
        return 0
    except (ValueError, KeyError, OSError) as exc:
        p.error(str(exc))


if __name__ == '__main__':
    raise SystemExit(main())
