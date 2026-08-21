"""Gate 8, plus the two scope rules for this package, asserted by grep.

Grepping your own source in a test looks crude. It is also the only thing that
actually holds: every rule below is one an editor would break by writing code
that works perfectly, and each would then be discovered months later as a data
bug rather than immediately as a test failure. Cheap to run, and these are
exactly the kind of thing that creeps back in.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

MEMORY_DIR = Path(__file__).resolve().parents[2] / "src" / "agent" / "memory"
PY_FILES = sorted(MEMORY_DIR.glob("*.py"))


def test_the_package_is_where_we_think_it_is() -> None:
    """Without this, every assertion below passes vacuously against an empty
    glob -- which is how a grep test quietly stops testing anything."""
    assert {p.name for p in PY_FILES} >= {
        "db.py", "models.py", "dedup.py", "retention.py", "crypto.py"
    }


@pytest.mark.parametrize("path", PY_FILES, ids=lambda p: p.name)
def test_no_tehran_anywhere_under_memory(path: Path) -> None:
    """State is UTC. `dates.to_tehran()` is display-only, and converting at
    storage time would make dedup windows, retention boundaries and trend deltas
    depend on a display preference -- which is how a timezone bug becomes a data
    bug that no later fix can undo."""
    assert "tehran" not in path.read_text(encoding="utf-8").lower()


@pytest.mark.parametrize("path", PY_FILES, ids=lambda p: p.name)
def test_no_wall_clock_reads_under_memory(path: Path) -> None:
    """`now` is injected. A pruner that reads the clock internally cannot be
    tested at its boundary and will be trusted anyway."""
    source = path.read_text(encoding="utf-8")
    assert not re.search(r"datetime\.now\s*\(", source)
    assert not re.search(r"\butcnow\s*\(", source)
    assert not re.search(r"\btime\.time\s*\(", source)


@pytest.mark.parametrize("path", PY_FILES, ids=lambda p: p.name)
def test_no_numpy_or_llm_in_the_storage_layer(path: Path) -> None:
    """Layer 4 (semantic cosine) is Phase 6 and the LLM router is Phase 5.
    Either import appearing here means a phase boundary was crossed."""
    source = path.read_text(encoding="utf-8")
    assert not re.search(r"^\s*(import|from)\s+numpy", source, re.M)
    assert not re.search(r"^\s*from\s+agent\.llm", source, re.M)


@pytest.mark.parametrize("path", PY_FILES, ids=lambda p: p.name)
def test_files_stay_under_the_size_cap(path: Path) -> None:
    """Constraint 12. Breached twice this month (`dates.py` at 211,
    `check_feeds.py` at 217), so it is asserted rather than remembered."""
    lines = len(path.read_text(encoding="utf-8").splitlines())
    assert lines <= 200, f"{path.name} is {lines} lines"


def test_no_python_writes_to_the_deferred_scoring_tables() -> None:
    """Requirement 5. The scoring tables are CREATED so Phase 7 needs no
    migration against encrypted state, and they are NOT written, because
    session-3 decision 1 deferred the scorer. `retention.py` names them for
    pruning and that is the only mention permitted."""
    deferred = ("signal_events", "speaker_statements", "risk_history", "market_metrics")
    for path in PY_FILES:
        if path.name == "retention.py":
            continue
        source = path.read_text(encoding="utf-8")
        statements = re.findall(r"(INSERT INTO|UPDATE)\s+(\w+)", source, re.I)
        assert not [t for _verb, t in statements if t in deferred], path.name
