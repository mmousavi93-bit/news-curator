"""Unit tests for pipeline/embed.py: the deterministic fake. The real
MiniLM is exercised only on CI ([embeddings] extra); its offline contract
(construction fails with an instruction when the package is missing) is
asserted in tests/integration/test_no_embeddings.py."""

from __future__ import annotations

from agent.pipeline.embed import FakeEmbedder


def test_fake_embedder_is_deterministic_across_instances():
    a = FakeEmbedder().embed(["hello world"])
    b = FakeEmbedder().embed(["hello world"])
    assert a == b


def test_fake_embedder_vectors_are_unit_norm():
    vec = FakeEmbedder(dim=32).embed(["some text"])[0]
    norm = sum(v * v for v in vec) ** 0.5
    assert abs(norm - 1.0) < 1e-9


def test_fake_embedder_different_texts_get_different_vectors():
    vectors = FakeEmbedder().embed(["alpha", "beta"])
    assert vectors[0] != vectors[1]


def test_fake_embedder_empty_input_returns_empty():
    assert FakeEmbedder().embed([]) == []


def test_fake_embedder_returns_one_vector_per_text():
    vectors = FakeEmbedder(dim=16).embed(["a", "b", "c"])
    assert len(vectors) == 3
    assert all(len(v) == 16 for v in vectors)
