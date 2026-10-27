"""Mocked artifact pagination, safety, provenance and offline bridge inventory."""
import io
import zipfile
from datetime import datetime, timezone
from pathlib import Path
import pytest
from tools import download_run_reports as downloader
from tools.signal_dataset_inventory import inventory, export_ref, file_record, read_manifest, write_json, sha256


def zip_bytes(name='read_x.csv', payload=b'original\xef\xbb\xbf'):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as archive:
        archive.writestr(name, payload)
    return out.getvalue()


def test_paginated_interval_expired_missing_and_hashes(tmp_path, monkeypatch):
    calls = []
    def get(url, token):
        calls.append(url)
        if '/runs/' in url:
            return dict(head_sha='abc', created_at='2026-09-01T00:00:00Z')
        page = int(url.split('page=')[-1])
        return dict(artifacts=[dict(id=page, name='run-reports', created_at='2026-09-01T00:00:00Z',
                                   expired=page == 2, workflow_run=dict(id=page))] if page < 3 else [])
    monkeypatch.setattr(downloader, '_get_json', get)
    monkeypatch.setattr(downloader, '_download', lambda *a: zip_bytes())
    result = downloader.fetch('r', 'fake', datetime(2026,8,1,tzinfo=timezone.utc),
                              datetime(2026,10,1,tzinfo=timezone.utc), tmp_path, {'1','2','3'})
    assert len(result['artifacts']) == 2
    assert result['missing_run_ids'] == ['3']
    assert result['artifacts'][1]['availability'] == 'expired'
    assert result['artifacts'][0]['head_sha'] == 'abc'
    assert result['artifacts'][0]['files'][0]['sha256'] == sha256((tmp_path/'1/1/read_x.csv').read_bytes())
    assert any('page=3' in c for c in calls)


@pytest.mark.parametrize('name', ['../escape', '/absolute', 'C:/escape', 'dir/../../escape'])
def test_zip_traversal_blocked(name, tmp_path, monkeypatch):
    monkeypatch.setattr(downloader, '_get_json', lambda url, token:
                        dict(head_sha='a') if '/runs/' in url else dict(artifacts=[dict(
                            id=1, name='run-reports', created_at='2026-09-01T00:00:00Z', workflow_run=dict(id=1))]))
    monkeypatch.setattr(downloader, '_download', lambda *a: zip_bytes(name))
    with pytest.raises(ValueError, match='unsafe'):
        downloader.fetch('r', 'fake', datetime(2026,8,1,tzinfo=timezone.utc),
                         datetime(2026,10,1,tzinfo=timezone.utc), tmp_path)


def test_manifest_immutable_and_input_checksum(tmp_path):
    csv = tmp_path/'read.csv'
    csv.write_bytes(b'original')
    path = tmp_path/'manifest.json'
    manifest = dict(runs=[dict(run_key='r', read=file_record(csv))])
    write_json(path, manifest)
    write_json(path, manifest)
    with pytest.raises(ValueError, match='immutable'):
        write_json(path, dict(runs=[]))
    csv.write_bytes(b'changed')
    with pytest.raises(ValueError, match='checksum'):
        read_manifest(path)


def test_four_starting_sets_and_byte_exact_git_export(tmp_path):
    root = Path(__file__).resolve().parents[2]
    value = inventory(root/'outputs', root/'fixtures/extraction_eval/items_20260928.csv',
                      'run-reports', tmp_path)
    assert len(value['runs']) == 4
    assert next(r for r in value['runs'] if r.get('run_id') == 36123832881)['cutoff_evidence']
    orphan = next(r for r in value['runs'] if r['run_key'].startswith('development:'))
    assert orphan['chosen'] is None
    assert value['selected_run_keys'] == []
    assert value == inventory(root/'outputs', root/'fixtures/extraction_eval/items_20260928.csv',
                              'run-reports', tmp_path)


def test_downloaded_manifest_checksums_are_verified_on_import(tmp_path):
    report = tmp_path/'1/read_x.csv'
    report.parent.mkdir()
    report.write_text('source_id,url\ns,u\n')
    manifest = dict(artifacts=[dict(run_id='1', artifact_id=9, head_sha=None,
                                   files=[dict(path='1/read_x.csv', sha256='bad')])])
    write_json(tmp_path/'manifest.json', manifest)
    with pytest.raises(ValueError, match='bridge artifact checksum'):
        inventory(tmp_path)


def test_historical_snapshots_are_loaded_from_recorded_git_sha(tmp_path):
    import subprocess
    from tools.signal_dataset_inventory import historical_config
    sha = subprocess.check_output(['git', 'rev-parse', 'HEAD']).decode().strip()
    snapshots = historical_config(sha, tmp_path)
    assert snapshots['source_snapshot']['sha256']
    assert snapshots['sources']
    assert snapshots['embedding']['revision'] is None  # no invented historical checkpoint
    manifest = dict(runs=[dict(run_key='r', **snapshots)])
    path = tmp_path/'manifest.json'
    write_json(path, manifest)
    assert read_manifest(path)['runs'][0]['sources'] == snapshots['sources']
    manifest['runs'][0]['sources'] = {}
    other = tmp_path/'tampered.json'
    write_json(other, manifest)
    with pytest.raises(ValueError, match='source joins'):
        read_manifest(other)
