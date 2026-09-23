"""Pre-LLM relevance pre-screen: REORDER clusters, never drop any.

Measured problem (agents/briefs/SESSION_22_BRIEF.md, 9 runs 2026-09-22/23):
`irrelevant` = 58-70 clusters/run, i.e. ~40% of the 150-cluster LLM budget is
spent on content the model then throws away, while `cap_dropped` = 32-37
clusters are dropped for lack of budget -- some of them relevant. The cap
budget is the scarce resource; this module makes it land on the right
clusters by putting on-mission clusters FIRST and off-mission clusters LAST
in the list the run spends its calls on.

Contract, deliberately narrow:

* REORDER-ONLY. The output is a PERMUTATION of the input -- same objects,
  same count, `sorted(keys)` identical. There is no drop path in this file,
  by construction: adding one would silently re-introduce the coverage loss
  (the `cap_dropped` half of the measured problem) with no LLM in the loop
  to argue.
* ZERO LLM, ZERO network, ZERO new dependencies. Scoring is a max cosine
  against a handful of PRE-COMPUTED anchor embeddings (war / security /
  Iran / Israel / Middle-East). torch and sentence-transformers are NOT
  imported here -- they are the [embeddings] extra, CI-only, and this module
  must work in the offline Windows suite where clustering runs on
  FakeEmbedder.
* DETERMINISTIC. Same input -> same output, every machine, every run. Ties
  keep input order (the sort key carries the input index explicitly, so
  determinism does not even rely on sort stability). No dict/set iteration
  feeds the order.

Cosine convention: embed.py's vectors are already L2-normalised
(`normalize_embeddings=True`), so cosine IS the dot product -- cluster.py's
`_cosine` relies on that. This module does NOT: it accepts any float
vectors and divides by the norms, because an un-normalised caller (a
hand-built anchor, a test double, a future embedder without the flag) must
not silently produce a magnitude ordering instead of a direction one.
Unscorable input (no vector, empty centroid, wrong dimensionality, a bad
anchor) is NEUTRAL: never dropped, never guessed at, it sinks below every
scored item in input order. With no usable anchors the input order is
returned unchanged, so a missing anchor file degrades to today's behaviour.

Consumer contract: this order only matters where input order is preserved.
`priority.split_at_cap` re-sorts with its own key (on_mission binary from
keyword relevance, tier, corroboration, recency), so it OVERRIDES this
order except for full key ties -- the caller should apply the reorder where
the list order is what decides spend (the batch/LLM call order), not expect
split_at_cap to honour it.
"""

from __future__ import annotations

import math
from typing import Any, Callable, Iterable, Protocol, Sequence, runtime_checkable


@runtime_checkable
class HasCentroid(Protocol):
    """What an item must expose to be scored. `pipeline.cluster.Cluster`
    satisfies this as-is (`centroid: list[float]`). Text is NOT required:
    nothing in the scoring path is lexical (that is `relevance.py`'s job,
    and mixing the two would double-count the same evidence)."""

    centroid: Sequence[float]


# Default anchor set for the wiring layer to embed ONCE per process (MiniLM,
# normalise_embeddings=True) and reuse for every run. Unused by the
# functions below -- they take vectors, never text -- so they cost nothing
# and stay honest: no model call happens in this module.
DEFAULT_ANCHOR_TEXTS: tuple[str, ...] = (
    "war, military conflict and armed escalation in the Middle East",
    "security and defense affairs of Iran",
    "Iran: government, nuclear program, sanctions, IRGC",
    "Israel, Gaza and the Israel-Iran confrontation",
    "Middle East geopolitics: Persian Gulf, Strait of Hormuz, Yemen, Syria, Lebanon, Iraq",
)

Similarity = Callable[[Sequence[float], Sequence[float]], float]


def _as_floats(value: object) -> list[float] | None:
    """A usable vector, or None -- never raises. A non-numeric sequence
    (text), a string, an empty sequence or None is not a vector."""
    if value is None or isinstance(value, (str, bytes)):
        return None
    try:
        numbers = [float(entry) for entry in value]  # type: ignore[union-attr]
    except (TypeError, ValueError):
        return None
    return numbers or None


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity of two arbitrary float vectors, norms included.

    Mismatched dimensions return 0.0 (neutral) rather than raising: this
    module sits in the middle of a run and a reorder must never be the thing
    that breaks it. Non-finite inputs (nan/inf) also score 0.0 -- a nan
    would otherwise make the sort order depend on comparison quirks.
    """
    left, right = _as_floats(a), _as_floats(b)
    if left is None or right is None or len(left) != len(right):
        return 0.0
    dot = left_norm = right_norm = 0.0
    for x, y in zip(left, right):
        dot += x * y
        left_norm += x * x
        right_norm += y * y
    if left_norm <= 0.0 or right_norm <= 0.0:
        return 0.0
    value = dot / (math.sqrt(left_norm) * math.sqrt(right_norm))
    if not math.isfinite(value):
        return 0.0
    return max(-1.0, min(1.0, value))


def vector_of(item: Any) -> list[float] | None:
    """The item's centroid vector, or None if it has none.

    Accepted shapes: an object exposing `.centroid` (Cluster), a bare
    numeric sequence (the vector itself), or a `(text, vector)` /
    `(vector, text)` pair -- the text side is metadata and is not scored.
    """
    direct = _as_floats(item)
    if direct is not None:
        return direct
    centroid = _as_floats(getattr(item, "centroid", None))
    if centroid is not None:
        return centroid
    if isinstance(item, (tuple, list)) and len(item) == 2:
        for part in item:
            vector = _as_floats(part)
            if vector is not None:
                return vector
    return None


def relevance_score(
    item: Any, anchor_vectors: Iterable[Sequence[float]], similarity: Similarity = cosine
) -> float | None:
    """MAX cosine to the anchors, or None when the item (or the anchor set)
    cannot be scored. Max, not mean: an item is on-mission if it is close to
    ANY single mission, and averaging would dilute a strong single-topic
    match with four unrelated anchors."""
    vector = vector_of(item)
    if vector is None:
        return None
    best: float | None = None
    for anchor in anchor_vectors or ():
        anchor_vector = _as_floats(anchor)
        if anchor_vector is None:
            continue
        score = similarity(vector, anchor_vector)
        best = score if best is None else max(best, score)
    return best


def relevance_scores(
    items: Iterable[Any], anchor_vectors: Iterable[Sequence[float]], similarity: Similarity = cosine
) -> list[tuple[Any, float | None]]:
    """(item, score) pairs in INPUT order -- the observability half of the
    pre-screen (which clusters scored low, and by how much). Pure and
    side-effect free; scoring is not affected by the reorder."""
    anchors = [a for a in (_as_floats(entry) for entry in anchor_vectors or ()) if a is not None]
    return [(item, relevance_score(item, anchors, similarity)) for item in items or ()]


def reorder_for_relevance(
    items: Iterable[Any], anchor_vectors: Iterable[Sequence[float]], *, similarity: Similarity = cosine
) -> list[Any]:
    """Same items, on-mission first, off-mission last, unscorable last of all.

    Returns a NEW list; the input sequence is never mutated. Ties keep input
    order, so a caller that already ranked its clusters keeps that ranking
    WITHIN equal scores -- this is a pre-screen, not a re-rank. With no
    usable anchors this is the identity reorder.
    """
    ordered = list(items or ())
    if len(ordered) < 2:
        return ordered
    anchors = [a for a in (_as_floats(entry) for entry in anchor_vectors or ()) if a is not None]
    if not anchors:
        return ordered
    decorated: list[tuple[tuple[int, float], int, Any]] = []
    for index, item in enumerate(ordered):
        score = relevance_score(item, anchors, similarity)
        # (0, -score) sorts scored items by descending score; (1, 0.0) sinks
        # every unscorable item below them. The index makes the order
        # explicit rather than a property of sort stability.
        rank = (1, 0.0) if score is None else (0, -score)
        decorated.append((rank, index, item))
    decorated.sort(key=lambda entry: (entry[0], entry[1]))
    return [item for _, _, item in decorated]
