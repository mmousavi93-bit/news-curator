"""One LLM attempt: transport call, classification, structured logging.

Split out of router.py to keep every file under the ~200-line cap
(CLAUDE.md constraint #12). The router owns the failover LOOP; this owns a
single attempt. No policy decisions about rotation live here -- the attempt
reports what happened, the router decides what it means for the queue.

Logging rule (PHASE_5_BRIEF §8): provider, stage, latency, call index,
token counts, outcome. Never the prompt, never a response body -- source
text is untrusted input and the logs are public (CLAUDE.md constraint #9).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Mapping, Sequence

from agent.llm.breaker import CircuitBreaker
from agent.llm.errors import FATAL, OK, UNAVAILABLE, LlmResult, SchemaError
from agent.llm.limits import ProviderBudget
from agent.llm.providers import DEFAULT_TIMEOUT, ImageInput, ProviderAdapter
from agent.llm.transport import HttpError, HttpTimeout, HttpTransport

# Outcomes of one attempt, interpreted by the router's loop.
_OK, _ROTATE, _SAME, _FATAL = "ok", "rotate", "same", "fatal"


@dataclass
class Provider:
    """One provider as the router's loop sees it: adapter + per-run state.
    `spend` is None for free providers (no guard rails configured).
    `timeout` is the (connect, read) pair used for this provider's HTTP
    calls -- per-provider override from settings, DEFAULT_TIMEOUT otherwise
    (2026-08-30 decision: the primary runs a tighter read timeout)."""

    name: str
    adapter: ProviderAdapter
    rpm: int | None
    spend: ProviderBudget | None = None
    timeout: tuple[float, float] = DEFAULT_TIMEOUT
    schema_retried: bool = False


def attempt(
    *,
    provider: Provider,
    prompt: str,
    images: Sequence[ImageInput],
    prompt_hash: str,
    stage: str,
    transport: HttpTransport,
    breaker: CircuitBreaker,
    clock: Callable[[], float],
    call_index: int,
    logger: logging.Logger,
) -> tuple[str, LlmResult]:
    """One request through one provider. Returns (outcome, result):

      _OK     -> 200 and parseable; result is the answer
      _ROTATE -> 429 / 5xx / timeout / connection; router rotates
      _SAME   -> 200 but schema-invalid; router retries this provider once
      _FATAL  -> 400/401/403 or a build_request programming error. THIS
                 PROVIDER is dead for the run (the breaker counts it), but
                 the CALL rotates on to the next one -- see the 2026-09-05
                 note below and in failover.py.
    """
    name = provider.name
    try:
        url, headers, payload = provider.adapter.build_request(prompt, images)
    except ValueError as exc:
        # images on a text-only adapter: programming error, never a provider
        # fault -- no rotation, nothing to retry.
        logger.error("llm: %s: %s", name, exc)
        return _FATAL, LlmResult(ok=False, status=FATAL, provider=name)

    start = clock()
    try:
        response = transport.post(url, headers, payload, provider.timeout)
    except (HttpTimeout, HttpError) as exc:
        breaker.failure(name)
        latency_ms = int((clock() - start) * 1000)
        log_call(logger, call_index, stage, provider, prompt_hash, "transport_error",
                 latency_ms, None)
        logger.warning("llm transport error: provider=%s %s", name, exc)
        return _ROTATE, LlmResult(ok=False, status=UNAVAILABLE, provider=name)
    latency_ms = int((clock() - start) * 1000)

    status = response.status_code
    if status == 200:
        try:
            text, usage = provider.adapter.parse(response.body)
        except SchemaError:
            breaker.failure(name)
            log_call(logger, call_index, stage, provider, prompt_hash, "schema_error",
                     latency_ms, None)
            return _SAME, LlmResult(ok=False, status=UNAVAILABLE, provider=name)
        log_call(logger, call_index, stage, provider, prompt_hash, "ok", latency_ms, usage)
        return _OK, LlmResult(
            ok=True,
            status=OK,
            text=text,
            provider=name,
            model=provider.adapter.model,
            prompt_hash=prompt_hash,
            call_index=call_index,
            usage=usage,
        )

    if status == 429:
        # 429 rotates WITHOUT counting toward the breaker: rate-limit
        # pacing means "slow down", not "this provider is broken". The
        # 2026-08-31 09:36 run lost Groq -- its only healthy provider --
        # to the breaker over TWO pacing 429s, and the whole cascade
        # collapsed into 24 skipped clusters. Groq carried 23 calls the
        # same morning with six scattered 429s: the signal is transient,
        # never sickness. The stats counter still records the failed
        # attempt (run.csv) -- the breaker just does not judge on it.
        log_call(logger, call_index, stage, provider, prompt_hash,
                 f"status_{status}", latency_ms, None)
        # http_status feeds the router's 429 COOLDOWN (2026-08-31): a wall
        # must cool the provider, not the budget.
        return _ROTATE, LlmResult(
            ok=False, status=UNAVAILABLE, provider=name, http_status=status)

    if status == 404 or status >= 500:
        # 404 rotates too: "this provider does not have this model" is
        # provider-SPECIFIC -- each provider has its own model id, so the
        # next one may well succeed. Learned 2026-08-29: gemini-2.5-flash
        # was discontinued (404) and the old fatal treatment cost a full
        # run while Groq sat unused.
        breaker.failure(name)
        log_call(logger, call_index, stage, provider, prompt_hash,
                 f"status_{status}", latency_ms, None)
        return _ROTATE, LlmResult(
            ok=False, status=UNAVAILABLE, provider=name, http_status=status)

    # 400/401/403 (and any other non-200 we did not classify above): this
    # PROVIDER cannot serve this request -- a rejected model id, a payload
    # shape it does not accept, a key or balance policy that says no.
    #
    # It is NOT a cross-provider fact. The old §3 doctrine claimed "wrong
    # identically on every provider" and stopped the loop; 2026-09-05
    # falsified it (bai_deepseek 400 / openrouter 403 on prompts groq and
    # gemini answered fine), so failover.py now rotates. The breaker still
    # counts the failure, which is what actually bounds the waste: after
    # two fatal responses the provider is skipped for the rest of the run
    # instead of re-burned once per cluster (2026-08-30: a 403-blocked
    # OpenRouter was called 34 times in one run, ~90s of dead air).
    breaker.failure(name)
    log_call(logger, call_index, stage, provider, prompt_hash,
             f"status_{status}", latency_ms, None)
    logger.error(
        "llm: provider-fatal status=%s from provider=%s -- retiring it for "
        "this run, rotating to the next provider", status, name
    )
    return _FATAL, LlmResult(
        ok=False, status=FATAL, provider=name, model=provider.adapter.model,
        http_status=status,
    )


def log_call(
    logger: logging.Logger,
    call_index: int,
    stage: str,
    provider: Provider,
    prompt_hash: str,
    outcome: str,
    latency_ms: int,
    usage: Mapping[str, int] | None,
) -> None:
    """Structured, bodiless: provider, stage, latency, call index, token
    counts, outcome. Never the prompt, never the body."""
    tokens_in = usage.get("in", 0) if usage else 0
    tokens_out = usage.get("out", 0) if usage else 0
    logger.info(
        "llm call #%d stage=%s provider=%s model=%s outcome=%s "
        "latency_ms=%d tokens_in=%d tokens_out=%d prompt_hash=%s",
        call_index, stage, provider.name, provider.adapter.model,
        outcome, latency_ms, tokens_in, tokens_out, prompt_hash,
    )
