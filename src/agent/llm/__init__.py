"""LLM router package: one complete() and one see() behind which providers
fail over, rate limits are paced, budgets are enforced, and total provider
collapse degrades the run instead of ending it (PHASE_5_BRIEF).

The pipeline builds a Router with wiring.build_router(); tests construct one
by hand with adapters and a MockHttpTransport. Callers see only LlmResult.
"""

from agent.llm.errors import (
    FATAL,
    OK,
    REFUSED_CAP,
    UNAVAILABLE,
    LlmResult,
)
from agent.llm.providers import ImageInput
from agent.llm.router import Router
from agent.llm.wiring import build_router

__all__ = [
    "FATAL",
    "OK",
    "REFUSED_CAP",
    "UNAVAILABLE",
    "ImageInput",
    "LlmResult",
    "Router",
    "build_router",
]
