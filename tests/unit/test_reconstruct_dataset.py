"""Exact historical recovery regressions and malformed-input boundaries."""
import csv
from pathlib import Path
import pytest
from tools.signal_membership import cluster_key, load_items, load_clusters, recover, report_run
from tools.reconstruct_dataset import reconstruct

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('stamp,total,expected,singles,small', [
    ('20260908T180302Z', 97, 86, 70, 16), ('20260908T225636Z', 128, 110, 87, 23)])
def test_real_regressions(stamp, total, expected, singles, small):
    items = load_items(ROOT/'outputs'/f'read_{stamp}.csv')
    rows = load_clusters(ROOT/'outputs'/f'chosen_{stamp}.csv')
    report = report_run(stamp, rows, items)
    assert report['clusters'] == total
    assert report['recovered_count'] == expected
    assert report['unrecovered_count'] == total-expected
    by_n = report['recovery_by_n']
    assert by_n[1] == singles
    assert by_n.get(2, 0)+by_n.get(3, 0) == small
    assert sum(bool(c['urls']) for c in report['memberships']) == expected
    assert all(c['fate'] for c in report['memberships'])


def test_budget_source_set_and_empty_metadata():
    items = {str(i): dict(source_id='a' if i < 4 else 'b') for i in range(8)}
    row = dict(cluster_key=cluster_key(['0', '1']), n_members=2, sources=['a'], fate='cap')
    recovered, missing = recover([row], items, combination_budget=1)
    assert not recovered and missing[0]['reason'] == 'search_budget_exceeded'
    assert recover([row], items)[0][row['cluster_key']] == ['0', '1']
    row['sources'] = ['a', 'b']
    assert not recover([row], items)[0]
    row['sources'] = []
    assert recover([row], items)[0]
    assert report_run('r', [row], {k: dict(v, _csv_rows=[1]) for k, v in items.items()})['memberships'][0]['source_match'] == 'unverified_all_run_search'


@pytest.mark.parametrize('n', [0, -1, True, 1.5])
def test_cardinality_rejected(n):
    with pytest.raises(ValueError):
        recover([dict(cluster_key='bad', n_members=n, sources=[])], {})


def test_overlap_and_duplicate_keys_rejected():
    items = {'a': dict(source_id='s'), 'b': dict(source_id='s')}
    rows = [dict(cluster_key=cluster_key(u), n_members=len(u), sources=['s']) for u in [['a'], ['a','b']]]
    with pytest.raises(ValueError, match='overlapping'):
        recover(rows, items)
    with pytest.raises(ValueError, match='duplicate'):
        recover([rows[0], rows[0]], items)


def test_duplicate_csv_provenance_and_conflict(tmp_path):
    read = tmp_path/'read.csv'
    header = 'source_id,url,title,body,published_at_utc,date_only,lang\n'
    read.write_text(header+'s,u,t,b,,0,en\ns,u,t,b,,0,en\n', encoding='utf-8-sig')
    assert load_items(read)['u']['_csv_rows'] == [2, 3]
    read.write_text(header+'s,u,t,b,,0,en\ns,u,t,changed,,0,en\n')
    with pytest.raises(ValueError, match='duplicate URL'):
        load_items(read)
    chosen = tmp_path/'chosen.csv'
    chosen.write_text('cluster_key,n_members,sources,fate\nk,1,s,cap\nk,2,s,cap\n')
    with pytest.raises(ValueError, match='duplicate cluster'):
        load_clusters(chosen)


def test_manifest_keeps_same_url_snapshots_per_run(tmp_path):
    runs = []
    for key in ('first', 'second'):
        read, chosen = tmp_path/f'{key}.csv', tmp_path/f'{key}-chosen.csv'
        read.write_text(f'source_id,url,title,body\ns,u,{key},body\n')
        chosen.write_text(f'cluster_key,n_members,sources,fate\n{cluster_key(["u"])},1,s,cap\n')
        runs.append(dict(run_key=key, read=dict(path=str(read)), chosen=dict(path=str(chosen))))
    result = reconstruct(dict(dataset_id='test', runs=runs))
    assert [r['run_key'] for r in result['runs']] == ['first', 'second']
    assert all(r['recovered_count'] == 1 for r in result['runs'])
