"""Mocked whole-run fallback joins; never loads MiniLM locally."""
import copy
from pathlib import Path
import pytest
from tools.signal_dataset_inventory import inventory
from tools.reconstruct_dataset import reconstruct
from tools.recluster_signal_dataset import recluster, real_embedder
from tools.signal_membership import cluster_key


def inputs(tmp_path, n=4):
    (tmp_path/'read_x.csv').write_text('source_id,url,title,body,published_at_utc,lang\n'+
                                     ''.join(f's,{i},title{i},body{i},2026-09-01T00:00:00Z,en\n' for i in range(n+1)))
    (tmp_path/'chosen_x.csv').write_text('cluster_key,n_members,sources,fate\n'+
                                       f'{cluster_key(list(map(str, range(n))))},{n},s,cap\n'+
                                       f'{cluster_key([str(n)])},1,s,repeat_dropped\n')
    value = inventory(tmp_path)
    value['runs'][0].update(pipeline_code_sha='historical', embedding=dict(model='test', revision='a'*40, threshold=.8))
    return value, reconstruct(value)


class MockEmbedder:
    def __init__(self, split=False):
        self.texts = []
        self.split = split

    def embed(self, texts):
        self.texts = texts
        if self.split:
            return [[int(i == j) for j in range(len(texts))] for i in range(len(texts))]
        return [[1,0] for _ in texts[:-1]]+[[0,1]]


def test_whole_run_and_exact_fallback(tmp_path):
    manifest, memberships = inputs(tmp_path)
    embedder = MockEmbedder()
    result = recluster(manifest, memberships, lambda settings: embedder)
    report = result['runs'][0]
    assert len(embedder.texts) == 5  # includes already hash-recovered singleton
    assert embedder.texts[0] == 'title0\nbody0'
    assert report['unrecovered_count'] == 0
    assert report['memberships'][0]['method'] == 'ci_recluster_exact'
    assert report['memberships'][1] == memberships['runs'][0]['memberships'][1]
    assert memberships['runs'][0]['unrecovered_count'] == 1


def test_split_merge_never_substitutes_nearest_key(tmp_path):
    manifest, memberships = inputs(tmp_path)
    result = recluster(manifest, memberships, lambda settings: MockEmbedder(split=True))
    assert result['runs'][0]['unrecovered_count'] == 1
    assert result['runs'][0]['memberships'][0]['urls'] == []
    assert len(result['runs'][0]['fallback_candidates']) == 5


def test_small_clusters_never_embed_even_when_unresolved(tmp_path):
    manifest, memberships = inputs(tmp_path, n=3)
    row = memberships['runs'][0]['memberships'][0]
    row.update(urls=[], method='unresolved')
    def forbidden(settings):
        pytest.fail('embedding invoked for small cluster')
    result = recluster(manifest, memberships, forbidden)
    assert result['runs'][0]['memberships'][0]['urls'] == []


def test_missing_revision_discloses_gap(tmp_path):
    manifest, memberships = inputs(tmp_path)
    manifest['runs'][0]['embedding']['revision'] = None
    result = recluster(manifest, memberships, lambda settings: pytest.fail('must not embed'))
    assert result['runs'][0]['fallback_gap'] == 'historical_model_revision_missing'
    assert result['runs'][0]['unrecovered_count'] == 1


def test_tampered_authoritative_membership_rejected(tmp_path):
    manifest, memberships = inputs(tmp_path)
    memberships['runs'][0]['memberships'][1]['urls'] = ['0']
    with pytest.raises(ValueError, match='authoritative'):
        recluster(manifest, memberships, lambda settings: MockEmbedder())


def test_real_model_prohibited_locally(monkeypatch):
    monkeypatch.delenv('CI', raising=False)
    with pytest.raises(ValueError, match='CI-only'):
        real_embedder(dict(model='test', revision='a'*40))


def test_incorrect_source_set_cannot_join(tmp_path):
    manifest, memberships = inputs(tmp_path)
    # Historical source metadata requires a second source absent from the run.
    chosen = Path(manifest['runs'][0]['chosen']['path'])
    chosen.write_text(chosen.read_text().replace(',4,s,cap', ',4,s|other,cap'))
    memberships = reconstruct(manifest)
    result = recluster(manifest, memberships, lambda settings: MockEmbedder())
    assert result['runs'][0]['memberships'][0]['urls'] == []
    assert result['runs'][0]['unrecovered_count'] == 1


def test_workflow_yaml_and_embedded_python_are_valid():
    import yaml
    root = Path(__file__).resolve().parents[2]
    for name in ('fetch-run-reports.yml', 'reconstruct-signal-dataset.yml'):
        workflow = yaml.safe_load((root/'.github/workflows'/name).read_text(encoding='utf-8-sig'))
        assert workflow['concurrency']['cancel-in-progress'] is False
        assert workflow['permissions'].get('contents') == 'write'
        for job in workflow['jobs'].values():
            for step in job['steps']:
                script = step.get('run', '')
                if "python - <<'PY'" in script:
                    code = script.split("python - <<'PY'\n", 1)[1].split('\nPY', 1)[0]
                    compile(code, name, 'exec')
