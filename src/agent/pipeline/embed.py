"""Local embeddings: the Embedder protocol, the real MiniLM embedder, and a
deterministic fake for tests.

sentence-transformers is an OPTIONAL dependency ([embeddings] extra): torch
is ~2 GB and the owner's Windows pytest suite plus --dry-run/--collect-only
must run without it (the requests lesson, PHASE_5_BRIEF §1, now for a
bigger package). `MiniLmEmbedder` imports it lazily inside __init__ and
fails at construction with an instruction, never later out of .embed().

Embedding quality is NOT what the offline suite tests: the fake embedder
exists so clustering ASSIGNMENT logic is fully testable offline. Real-model
behaviour is exercised on CI (and tuned in Phase 10, when the 0.62
threshold finally gets measured).
"""

from __future__ import annotations

import hashlib
import random
from typing import Protocol, Sequence


class Embedder(Protocol):
    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class MiniLmEmbedder:
    """Multilingual MiniLM via sentence-transformers, normalised vectors
    (cosine similarity becomes a dot product). Model name comes from
    settings -- owner-editable, no code edit to swap."""

    def __init__(self, model_name: str, batch_size: int = 32) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise ImportError(
                "MiniLmEmbedder needs the 'sentence-transformers' package "
                "(pip install -e '.[embeddings]'); mock mode and the offline "
                "test suite do not need it, so this only matters in a real run."
            ) from exc
        self.model_name = model_name
        self._model = SentenceTransformer(model_name)
        self._batch_size = batch_size

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = self._model.encode(
            list(texts), batch_size=self._batch_size, normalize_embeddings=True
        )
        return [vec.tolist() for vec in vectors]


class FakeEmbedder:
    """Deterministic hash-derived unit vectors. Same text -> same vector on
    every machine and every run, which is what makes clustering tests
    reproducible. Deliberately UNRELATED to real semantics -- it exercises
    the clustering logic, not the model."""

    def __init__(self, dim: int = 64) -> None:
        self.dim = dim

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            digest = hashlib.sha256(text.encode("utf-8")).digest()
            rng = random.Random(int.from_bytes(digest[:8], "big"))
            vec = [rng.uniform(-1.0, 1.0) for _ in range(self.dim)]
            norm = sum(v * v for v in vec) ** 0.5
            if norm > 0:
                vec = [v / norm for v in vec]
            vectors.append(vec)
        return vectors
