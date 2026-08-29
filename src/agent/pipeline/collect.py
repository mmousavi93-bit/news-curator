"""The collect stage: fetch every enabled source, store what survives
dedup, hand the NEW items forward to the filter.

Contract (PHASE_7_BRIEF §1): a real run without a database is a silent
no-store -- the exact failure constraint 14 exists to prevent -- so that
combination raises. Dry runs and mock runs skip the stage entirely: they
are offline by definition. Per-source failures are recorded by registry
(Phase 3 contract) and never crash the run.
"""

from __future__ import annotations

import logging
from typing import Callable

from agent.collectors import registry
from agent.collectors.base import SourceSpec
from agent.memory import dedup, retention
from agent.memory.source_health import (
    SourceHealthRow,
    read_degraded,
    upsert_source_health,
)
from agent.settings import Settings


class CollectStage:
    name = "collect"

    def __init__(
        self,
        sources: list[SourceSpec],
        settings: Settings,
        logger: logging.Logger,
        collect_fn: Callable | None = None,
    ) -> None:
        self._sources = sources
        self._settings = settings
        self._logger = logger
        # Injectable so tests stub the network while the store logic stays
        # the real code (mock mode is wiring, not a stub of the stage).
        self._collect_fn = collect_fn or registry.collect_all

    def run(self, ctx) -> None:
        ctx.counters.setdefault("collect", 0)
        if getattr(ctx, "dry_run", False) or self._settings.ops.mock_mode:
            self._logger.info("collect: skipped (dry-run/mock mode)")
            return
        if getattr(ctx, "db", None) is None:
            raise ValueError(
                "collect: no database on RunContext in a real run -- a run "
                "that cannot store state must not run (constraint 14)"
            )
        report = self._collect_fn(self._sources, self._settings, ctx.now)
        items = [item for res in report.results.values() for item in res.items]
        stored = dedup.store_new(ctx.db, items, ctx.now)
        pruned = retention.prune(ctx.db, self._settings.retention, ctx.now)
        ctx.items = list(stored.new)
        ctx.counters["collect"] = len(stored.new)
        self._logger.info(
            "collect: fetched=%d new=%d dup(url/norm/title)=%d/%d/%d pruned=%d",
            len(items), len(stored.new), stored.by_url, stored.by_norm_url,
            stored.by_title, sum(pruned.values()),
        )

        # Phase 10: per-source health, persisted every run. A source is
        # healthy when it returned items with no error; empty feeds and
        # errors both climb the consecutive-empty counter.
        health_rows = [
            SourceHealthRow(
                source_id=source_id,
                ok=res.error is None and res.kept > 0,
                error=res.error,
                now=ctx.now,
            )
            for source_id, res in report.results.items()
        ]
        current = upsert_source_health(ctx.db, health_rows)
        degraded = read_degraded(ctx.db, self._settings.collection.degraded_after_empty_runs)
        for source_id, count in degraded:
            self._logger.warning(
                "source health: %s degraded (%d consecutive non-ok runs)", source_id, count
            )
