"""Typed outcomes for the LLM router package.

The policy these classes encode (PHASE_5_BRIEF §3, §6):

  - Retryable  -> rotate to the next provider: 429, 5xx, timeout, connection.
  - Fatal      -> 400, 401, 403: THIS provider cannot serve the request (bad
                  model id for its gateway, payload shape, key or balance
                  policy). Retire the provider for the run via the breaker,
                  but ROTATE the call onward.
                  Revised 2026-09-05: the original rule said "fails identically
                  on every provider, so STOP". False -- bai_deepseek 400'd and
                  openrouter 403'd on prompts groq and gemini answered seconds
                  later, and stopping destroyed three clusters including the
                  run's most-corroborated story. Same reasoning already applied
                  to 404 in call.py. Do not re-add the early return.
  - SchemaError-> HTTP 200 but the body is not the shape the adapter expects.
                  A model failure, not a transport one: retry once on the same
                  provider, then rotate.
  - Unavailable-> every candidate provider failed or is breaker-open. The run
                  degrades (collect + store, deliver an "AI unavailable"
                  notice) instead of crashing.

No exception from this package reaches a caller: the router converts every
outcome into an LlmResult (PHASE_5_BRIEF §6). These classes exist for the
failover machinery inside this package only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

# LlmResult.status values. Callers branch on these -- never on provider
# identity (PHASE_5_BRIEF: "No caller ever learns which provider answered").
OK = "ok"
REFUSED_CAP = "refused_cap"    # per-run call budget exhausted
UNAVAILABLE = "unavailable"    # every provider failed / breaker-open / no key
FATAL = "fatal"                # 400/401/403: the request is wrong, retrying is wrong


class LlmError(Exception):
    """Base for every error this package raises internally. Carries the
    provider name for structured logging -- messages must stay bodyless and
    URL-free (PHASE_5_BRIEF §7, §8)."""

    def __init__(self, message: str, *, provider: str) -> None:
        super().__init__(message)
        self.provider = provider


class RetryableError(LlmError):
    """429, 5xx, timeout, connection failure. Rotate to the next provider."""


class FatalError(LlmError):
    """400, 401, 403. The request or key is wrong; no provider will fix it."""


class SchemaError(LlmError):
    """HTTP 200 but unparseable as the expected response shape."""


class UnavailableError(LlmError):
    """Every candidate provider is breaker-open or unconfigured. Raised
    internally; callers receive LlmResult(status=UNAVAILABLE) instead."""


@dataclass(frozen=True, slots=True)
class LlmResult:
    """The only type that leaves this package.

    `provider` / `model` / `prompt_hash` are PROVENANCE, not control: callers
    must not branch on them. They exist so a Phase 7 disagreement over an
    extraction can be reproduced (PHASE_5_BRIEF §9) -- the model id and a
    hash of the prompt that produced each answer are unrecoverable later.
    """
    ok: bool
    status: str
    text: str = ""
    provider: str | None = None
    model: str | None = None
    prompt_hash: str = ""
    call_index: int = 0
    usage: Mapping[str, int] = field(default_factory=dict)
    # The HTTP status behind a failed attempt, when the transport answered
    # one (429/5xx/4xx). The router uses it for the 429 cooldown -- a wall
    # must cool the provider, not the budget.
    http_status: int | None = None
