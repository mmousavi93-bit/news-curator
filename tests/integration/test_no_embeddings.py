"""Regression guard for the Phase 6 dependency rule: sentence-transformers
is an OPTIONAL extra ([embeddings]), imported lazily -- the owner's Windows
pytest suite, --dry-run and --collect-only must run without torch installed
(the Phase 2 requests lesson, now for a 2 GB package).

Same mechanism as test_no_requests.py: sys.modules sentinel makes the import
raise even if the package happens to be installed in the test sandbox.
"""

from __future__ import annotations

import contextlib
import importlib
import sys

import pytest

PIPELINE_MODULES = (
    "agent.pipeline",
    "agent.pipeline.filter",
    "agent.pipeline.embed",
    "agent.pipeline.cluster",
    "agent.pipeline.understand",
)


@contextlib.contextmanager
def embeddings_unimportable():
    saved = dict(sys.modules)
    for name in [n for n in sys.modules if n == "agent" or n.startswith("agent.")]:
        del sys.modules[name]
    sys.modules["sentence_transformers"] = None  # sentinel: import -> ImportError
    try:
        yield
    finally:
        sys.modules.clear()
        sys.modules.update(saved)


def test_sentinel_actually_blocks_the_import():
    with embeddings_unimportable():
        with pytest.raises(ImportError):
            import sentence_transformers  # noqa: F401


def test_pipeline_modules_import_without_sentence_transformers():
    with embeddings_unimportable():
        for name in PIPELINE_MODULES:
            importlib.import_module(name)  # raises -> test fails, which is the point


def test_minilm_construction_fails_with_actionable_message():
    with embeddings_unimportable():
        embed = importlib.import_module("agent.pipeline.embed")
        with pytest.raises(ImportError) as excinfo:
            embed.MiniLmEmbedder("paraphrase-multilingual-MiniLM-L12-v2")
    message = str(excinfo.value)
    assert "sentence-transformers" in message
    assert "pip install" in message
