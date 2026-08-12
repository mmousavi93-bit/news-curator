"""No-op stage used until real pipeline stages exist.

Phase 1 scaffolding only: every name in STAGES resolves to one of these so
run.py has something to execute in order and report zero counts against.
Later phases replace entries in run._build_stages() one at a time -- this file
does not change shape when that happens.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent.run import RunContext


class NoopStage:
    """A stage that does nothing and reports zero items collected under its name."""

    def __init__(self, name: str) -> None:
        self.name = name

    def run(self, ctx: "RunContext") -> None:
        ctx.counters.setdefault(self.name, 0)
