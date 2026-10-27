"""Capacity acceptance: real router, simulated HTTP/clock, zero network."""
import logging

from agent.llm.providers import GroqAdapter, Groq2Adapter
from agent.llm.router import Router
from agent.llm.transport import HttpResponse


class Clock:
    def __init__(self):
        self.now = 0.0
        self.waits = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        assert seconds >= 0
        self.waits.append(seconds)
        self.now += seconds


class SuccessfulHttp:
    """An explicit simulation, NOT recorded model output or live timing."""
    def __init__(self, clock):
        self.clock = clock
        self.calls = []

    def post(self, url, headers, payload, timeout):
        self.calls.append((self.clock(), payload["model"]))
        self.clock.now += 1.0
        return HttpResponse(200, {
            "choices": [{"message": {"content": "fixture response"}}],
            "usage": {"prompt_tokens": 4500, "completion_tokens": 1000},
        })


def capacity_trace(requests=24, *, include_overflow=True):
    clock = Clock()
    http = SuccessfulHttp(clock)
    model = "qwen/qwen3.8-27b"
    adapters = [GroqAdapter(model, "offline-test-primary")]
    if include_overflow:
        adapters.append(Groq2Adapter(model, "offline-test-overflow"))
    router = Router(
        adapters, transport=http, clock=clock, sleep=clock.sleep,
        rpm_by_provider={"groq": 30, "groq2": 30},
        tpm_by_provider={"groq": 8000, "groq2": 8000},
        max_calls=requests, logger=logging.getLogger(__name__),
    )
    results = [router.complete("x" * 13500) for _ in range(requests)]
    assert all(r.ok for r in results)
    assert len(http.calls) == requests
    assert {m for _, m in http.calls} == {model}
    assert sum(p["calls"] for p in router.stats.as_dict().values()) == requests
    return {"elapsed_seconds": clock(), "requests": requests,
            "pacing_seconds": sum(clock.waits), "provider_stats": router.stats.as_dict()}


def test_identical_model_overflow_halves_wait_without_reducing_coverage():
    single = capacity_trace(include_overflow=False)
    pooled = capacity_trace(include_overflow=True)
    assert pooled["elapsed_seconds"] <= single["elapsed_seconds"] * 0.60
    assert pooled["requests"] == single["requests"] == 24
