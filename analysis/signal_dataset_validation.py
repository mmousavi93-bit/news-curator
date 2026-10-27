"""Structural assembly validation and explicit evidence-completeness dossier."""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent.collectors.base import Item

ROLES = {'tact_pre_strike', 'strat_accumulation', 'strike_onset', 'second_tact_spike', 'quiet_week'}


def timestamp(value):
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if stamp.tzinfo is None:
        raise ValueError('cutoff must be timezone-aware')
    return stamp


def item_issue(item, cutoff):
    for field in ('source_id', 'url', 'title', 'body', 'lang', 'raw_hash'):
        if not isinstance(item.get(field), str) or (field in ('source_id', 'url', 'raw_hash') and not item[field]):
            raise ValueError('invalid Item field: '+field)
    if type(item.get('date_only')) is not bool:
        raise ValueError('date_only must be boolean')
    value = item.get('published_at')
    converted = dict(item)
    if value:
        try:
            published = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            return 'ambiguous_timestamp'
        converted['published_at'] = published
        if item['date_only']:
            if published.date() > cutoff.astimezone(ZoneInfo('Asia/Tehran')).date():
                return 'future_date_only'
        elif published.tzinfo is None:
            return 'ambiguous_timestamp'
        elif published > cutoff:
            return 'future_timestamp'
    Item(**converted)
    return None


def validate(manifest, dataset):
    from tools.signal_membership import load_clusters, load_items, valid_members
    errors, gaps, keys = [], list(manifest.get('gaps', [])), set()
    if dataset.get('dataset_id') != manifest['dataset_id']:
        errors.append('dataset identity mismatch')
    originals = {r['run_key']: r for r in manifest['runs']}
    selected = set(manifest.get('selected_run_keys', []))
    warmup = set(manifest.get('warmup_run_keys', []))
    if (len(selected) != len(manifest.get('selected_run_keys', []))
            or len(warmup) != len(manifest.get('warmup_run_keys', []))):
        errors.append('duplicate selection keys')
    if not selected:
        gaps.append('evaluation_selection_unavailable')
    if selected & warmup:
        errors.append('warmup/evaluation overlap')
    if (selected | warmup)-originals.keys():
        errors.append('selection references unknown runs')
    previous = {}
    for run in dataset['runs']:
        key = run['run_key']
        if key in keys or key not in originals:
            errors.append('duplicate/unknown run: '+key)
        keys.add(key)
        try:
            cutoff = timestamp(run['cutoff'])
            if run['cutoff'] != originals[key]['cutoff'] or run['sources'] != originals[key].get('sources', {}):
                errors.append('run cutoff/source provenance mismatch')
            original_rows = {c['cluster_key']: c for c in load_clusters(Path(originals[key]['chosen']['path']))}
            original_items = load_items(Path(originals[key]['read']['path']))
            stream = run.get('stream_id', 'default')
            if stream in previous and cutoff < previous[stream]:
                errors.append('nonchronological stream')
            previous[stream] = cutoff
            if not originals[key].get('cutoff_evidence'):
                gaps.append(key+':cutoff_evidence_missing')
            seen, used = set(), set()
            for cluster in run['clusters']:
                ck = cluster['cluster_key']
                if ck in seen:
                    errors.append('duplicate cluster key')
                seen.add(ck)
                original = original_rows.get(ck)
                if not original or cluster.get('historical_fate') != original['fate']:
                    errors.append('historical row/fate mismatch')
                membership = cluster.get('membership', {})
                urls = membership.get('urls', [])
                if used.intersection(urls):
                    errors.append('overlapping membership including quarantine')
                used.update(urls)
                if urls and (not original or not valid_members(original, urls, original_items)):
                    errors.append('invalid exact membership')
                quarantine = cluster.get('quarantine') or {}
                evidence = quarantine.get('items', []) if quarantine else cluster['items']
                if [i['url'] for i in evidence] != urls:
                    errors.append('membership/evidence mismatch')
                for data in evidence:
                    expected = original_items.get(data['url'], {})
                    if any(expected.get(k) != v for k, v in data.items()):
                        errors.append('original Item changed')
                if 'capture' in cluster or not cluster.get('fate') or 'historical_fate' not in cluster:
                    errors.append('invalid unextracted fate/capture')
                for item in cluster['items']:
                    issue = item_issue(item, cutoff)
                    if issue:
                        errors.append('unquarantined '+issue)
                    if item['source_id'] not in run['sources']:
                        errors.append('unknown source join')
                if not urls and cluster['fate'] != 'membership_unresolved':
                    errors.append('unresolved membership fate missing')
                if urls and not quarantine and cluster['fate'] != 'not_extracted':
                    errors.append('invalid fresh extraction fate')
            if seen != original_rows.keys():
                errors.append('historical cluster coverage mismatch')
            for source in run['sources'].values():
                if source.get('tier') not in (1, 2, 3, 'lead') or 'group' not in source:
                    errors.append('invalid source snapshot')
            if not run['complete']:
                gaps.append(key+':run_incomplete')
            if any(c['fate'] != 'not_extracted' for c in run['clusters']):
                gaps.append(key+':unresolved_or_quarantined_evidence')
            if not originals[key].get('collection_healthy') or not originals[key].get('history_sufficient'):
                gaps.append(key+':collection_or_history_unverified')
            if not originals[key].get('source_snapshot') or not originals[key].get('settings_snapshot'):
                gaps.append(key+':historical_config_unavailable')
            if not originals[key].get('market_inputs_verified'):
                gaps.append(key+':market_inputs_unavailable')
            gaps.extend(key+':'+g for g in run.get('gaps', []))
        except (ValueError, KeyError, TypeError) as exc:
            errors.append(key+':'+str(exc))
    if (selected | warmup)-keys:
        gaps.append('selected_or_warmup_run_unavailable')
    windows = manifest.get('windows', [])
    if len(windows) != 5 or {w.get('role') for w in windows} != ROLES:
        gaps.append('five_evidence_roles_unverified')
    for window in windows:
        if not window.get('evidence_verified') or not window.get('run_keys'):
            gaps.append('window_evidence_unverified')
        if not set(window.get('run_keys', [])) <= selected:
            errors.append('window runs outside evaluation selection')
        if window.get('role') == 'quiet_week':
            by_key = {r['run_key']: r for r in dataset['runs']}
            days = sorted({timestamp(by_key[k]['cutoff']).astimezone(ZoneInfo('Asia/Tehran')).date()
                           for k in window.get('run_keys', []) if k in by_key})
            if len(days) < 7 or not any(all((days[i+j]-days[i]).days == j for j in range(7))
                                      for i in range(max(0, len(days)-6))):
                gaps.append('seven_observed_quiet_days_missing')
    # Semantic class counts are HUMAN-owned, never inferred by assembly.
    coverage = manifest.get('human_verified_coverage', {})
    if not all(coverage.get(c, 0) > 0 for c in ('B1', 'B2', 'B6', 'E1', 'E2')):
        gaps.append('human_class_coverage_unavailable')
    if not manifest.get('e2_incidents_verified'):
        gaps.append('two_e1_incidents_within_seven_days_unverified')
    if len(previous) > 1:
        gaps.append('independent_streams_require_separate_replay_state')
    return dict(structural_pass=not errors, complete=not errors and not gaps,
                errors=errors, gaps=sorted(set(gaps)), runs=len(keys), selected_runs=len(selected))


def main(argv=None):
    from tools.signal_dataset_inventory import read_manifest
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest', type=Path, required=True)
    p.add_argument('--dataset', type=Path, required=True)
    args = p.parse_args(argv)
    result = validate(read_manifest(args.manifest),
                      json.loads(args.dataset.read_text(encoding='utf-8-sig')))
    print(json.dumps(result, indent=2))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
