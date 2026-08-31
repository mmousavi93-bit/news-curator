"""Per-run per-provider attempt counters (owner request 2026-08-30).

run.csv gains calls_<provider> / fails_<provider> columns so the owner
can watch bai's real rate-limit ceiling emerge (as Groq's ~13-call
ceiling did), trend Gemini's degradation, and detect OpenRouter's
resurrection -- from the run-reports artifact, without log-diving.

Counted: every attempt the router makes against a provider (rotations
and same-provider retries included). Failed: any attempt that did not
return an ok result -- 429/5xx/timeout/connection AND HTTP-200 answers
whose shape is schema garbage (the provider answered, the pipeline
could not use it).
"""

from __future__ import annotations

from typing import Iterable, Mapping


class ProviderStats:
    def __init__(self, names: Iterable[str]) -> None:
        self._stats: dict[str, dict[str, int]] = {
            name: {"calls": 0, "failed": 0} for name in names
        }

    def record(self, name: str, ok: bool) -> None:
        entry = self._stats[name]
        entry["calls"] += 1
        if not ok:
            entry["failed"] += 1

    def as_dict(self) -> Mapping[str, dict[str, int]]:
        """A copy -- callers can mutate it without touching the counters."""
        return {name: dict(entry) for name, entry in self._stats.items()}
