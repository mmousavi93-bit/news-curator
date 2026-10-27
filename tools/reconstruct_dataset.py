"""Per-run exact hash recovery; partial results are never accepted datasets."""
import argparse
import json
from pathlib import Path
try:
    from tools.signal_membership import cluster_key, load_items, load_clusters, recover, report_run
    from tools.signal_dataset_inventory import read_manifest, write_json
except ModuleNotFoundError:
    from signal_membership import cluster_key, load_items, load_clusters, recover, report_run
    from signal_dataset_inventory import read_manifest, write_json


def reconstruct(manifest):
    runs = []
    for run in manifest['runs']:
        if not run.get('read') or not run.get('chosen'):
            continue
        items = load_items(Path(run['read']['path']))
        rows = load_clusters(Path(run['chosen']['path']))
        runs.append(report_run(run['run_key'], rows, items,
                               manifest.get('combination_budget', 1_000_000)))
    return {'version': 1, 'dataset_id': manifest['dataset_id'], 'runs': runs}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--allow-partial', action='store_true')
    args = p.parse_args(argv)
    try:
        result = reconstruct(read_manifest(args.manifest))
        write_json(args.out, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if args.allow_partial or all(not r['unrecovered_count'] for r in result['runs']) else 1
    except (ValueError, KeyError, OSError) as exc:
        p.error(str(exc))


if __name__ == '__main__':
    raise SystemExit(main())
