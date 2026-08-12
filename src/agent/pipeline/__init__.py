"""Stage protocol and the ordered stage list.

The pipeline is a fixed linear sequence -- no branching, no agent framework
(CLAUDE.md constraint #7: no LangGraph/LangChain/CrewAI in v1).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from agent.run import RunContext


class Stage(Protocol):
    name: str

    def run(self, ctx: "RunContext") -> None: ...


STAGES: tuple[str, ...] = (
    "collect", "filter", "vision", "embed", "cluster",
    "understand", "validate", "score", "compose", "deliver",
)
