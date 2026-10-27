"""Source-only assembly: fates, quarantine, timestamps and incomplete coverage."""
import copy
from pathlib import Path
import pytest
from tools.signal_dataset_inventory import inventory, read_manifest, file_record, write_json
from tools.signal_membership import cluster_key
from tools.reconstruct_dataset import reconstruct
from tools.assemble_signal_dataset import assemble
from analysis.signal_dataset_validation import validate, item_issue, timestamp


@pytest.fixture
def manifest(tmp_path):
    read, chosen, run = [tmp_path/name for name in ('read_x.csv', 'chosen_x.csv', 'run_x.csv')]
    read.write_text('source_id,url,title,body,published_at_utc,date_only,lang\n'
                    's,a,Title,Literal excerpt…,2026-09-01T01:00:00+00:00,0,fa\n'
                    's,b,Title,Body,,0,en\n', encoding='utf-8-sig')
    chosen.write_text('cluster_key,n_members,sources,fate\n'
                      f'{cluster_key(["a"])},1,s,cap_dropped\n'
                      f'{cluster_key(["b"])},1,s,llm_failed\n'
                      'missing,4,s,unavailable\n')
    run.write_text('run_at_utc\n2026-09-01T02:00:00+00:00\n')
    value = inventory(tmp_path)
    value['runs'][0]['sources'] = {'s': dict(tier=1, group=None)}
    return value


def test_all_fates_raw_hash_literal_excerpt_and_unresolved(manifest):
    dataset = assemble(manifest, reconstruct(manifest))
    run = dataset['runs'][0]
    assert [c['historical_fate'] for c in run['clusters']] == ['cap_dropped','llm_failed','unavailable']
    assert run['clusters'][0]['items'][0]['body'] == 'Literal excerpt…'
    assert run['clusters'][0]['items'][0]['raw_hash'] == 'unavailable:csv-export'
    assert run['clusters'][2]['fate'] == 'membership_unresolved'
    assert run['clusters'][2]['items'] == []
    assert all('capture' not in c for c in run['clusters'])
    assert dataset['validation']['structural_pass']
    assert not dataset['validation']['complete']
    assert not run['complete']
    assert 'market_events' not in run
    assert 'item_date_unavailable' in run['gaps']


@pytest.mark.parametrize('date,reason', [
    ('2026-09-02T01:00:00+00:00', 'future_timestamp'),
    ('2026-09-01T01:00:00', 'ambiguous_timestamp'), ('nonsense', 'ambiguous_timestamp')])
def test_quarantine_preserves_original_membership_and_evidence(manifest, date, reason):
    read = Path(manifest['runs'][0]['read']['path'])
    read.write_text(read.read_text(encoding='utf-8-sig').replace('2026-09-01T01:00:00+00:00', date), encoding='utf-8')
    dataset = assemble(manifest, reconstruct(manifest))
    cluster = dataset['runs'][0]['clusters'][0]
    assert cluster['fate'] == 'evidence_quarantined'
    assert cluster['items'] == []
    assert reason in cluster['quarantine']['reasons']
    assert cluster['quarantine']['items'][0]['published_at'] == date
    assert cluster['cluster_key'] == cluster_key(['a'])
    assert cluster['membership']['urls'] == ['a']


def test_unknown_source_is_quarantined(manifest):
    manifest['runs'][0]['sources'] = {}
    dataset = assemble(manifest, reconstruct(manifest))
    assert 'unknown_source_join' in dataset['runs'][0]['clusters'][0]['quarantine']['reasons']
    assert dataset['validation']['structural_pass']
    assert not dataset['validation']['complete']


def test_date_only_uses_tehran_day_and_null_is_unavailable():
    item = dict(source_id='s', url='u', title='', body='', lang='fa', raw_hash='sentinel',
                published_at='2026-09-02', date_only=True)
    assert item_issue(item, timestamp('2026-09-01T21:00:00Z')) is None
    assert item_issue(item, timestamp('2026-09-01T20:00:00Z')) == 'future_date_only'
    item['published_at'] = None
    assert item_issue(item, timestamp('2026-09-01T20:00:00Z')) is None
    del item['raw_hash']
    with pytest.raises(ValueError, match='raw_hash'):
        item_issue(item, timestamp('2026-09-01T20:00:00Z'))


def test_selection_overlap_and_tampering_rejected(manifest):
    key = manifest['runs'][0]['run_key']
    manifest.update(selected_run_keys=[key], warmup_run_keys=[key])
    with pytest.raises(ValueError, match='overlap'):
        assemble(manifest, reconstruct(manifest))
    manifest['warmup_run_keys'] = []
    memberships = reconstruct(manifest)
    memberships['runs'][0]['memberships'].pop()
    with pytest.raises(ValueError, match='coverage'):
        assemble(manifest, memberships)


def test_real_runs_assemble_structurally_with_missing_sources_explicit(tmp_path):
    root = Path(__file__).resolve().parents[2]
    value = inventory(root/'outputs')
    result = assemble(value, reconstruct(value))
    assert result['validation']['structural_pass']
    assert not result['validation']['complete']
    assert len(result['runs']) == 2
    assert [len(r['clusters']) for r in result['runs']] == [97, 128]
    assert sum(c['fate'] == 'membership_unresolved' for r in result['runs'] for c in r['clusters']) == 29
    assert all(not r['complete'] for r in result['runs'])
    path = tmp_path/'dataset.json'
    write_json(path, result)
    assert path.exists()


def test_standalone_validation_rejects_changed_text_and_missing_fates(manifest):
    result = assemble(manifest, reconstruct(manifest))
    result['runs'][0]['clusters'][0]['items'][0]['body'] = 'invented body'
    assert 'original Item changed' in validate(manifest, result)['errors']
    result = assemble(manifest, reconstruct(manifest))
    result['runs'][0]['clusters'].pop()
    assert 'historical cluster coverage mismatch' in validate(manifest, result)['errors']


def test_cutoff_requires_aware_evidence_and_quiet_week_is_observed(manifest):
    key = manifest['runs'][0]['run_key']
    manifest['selected_run_keys'] = [key]
    manifest['windows'] = [dict(role='quiet_week', run_keys=[key], evidence_verified=True)]
    result = assemble(manifest, reconstruct(manifest))
    assert 'seven_observed_quiet_days_missing' in result['validation']['gaps']
    manifest['runs'][0]['cutoff'] = '2026-09-01T02:00:00'
    with pytest.raises(ValueError, match='aware'):
        assemble(manifest, reconstruct(manifest))


def test_cli_writes_incomplete_dossier_and_reports_nonzero(manifest, tmp_path):
    from tools.reconstruct_dataset import main as recover_main
    from tools.assemble_signal_dataset import main as assemble_main
    from analysis.signal_dataset_validation import main as validate_main
    manifest_path = tmp_path/'manifest.json'
    membership_path = tmp_path/'memberships.json'
    dataset_path = tmp_path/'dataset.json'
    write_json(manifest_path, manifest)
    assert recover_main(['--manifest', str(manifest_path), '--out', str(membership_path)]) == 1
    assert recover_main(['--manifest', str(manifest_path), '--out', str(membership_path), '--allow-partial']) == 0
    assert assemble_main(['--manifest', str(manifest_path), '--memberships', str(membership_path), '--out', str(dataset_path)]) == 1
    assert dataset_path.exists()
    assert validate_main(['--manifest', str(manifest_path), '--dataset', str(dataset_path)]) == 1
