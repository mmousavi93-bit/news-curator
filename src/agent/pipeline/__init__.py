"""Stage protocol, the ordered stage list, and real stage wiring.

The pipeline is a fixed linear sequence -- no branching, no agent framework
(CLAUDE.md constraint #7). build_stages() wires the Phase 6 stages (filter,
embed, cluster, understand) and leaves vision/validate/score/compose/deliver
as no-ops until their phases.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Mapping, Protocol

from agent.config import Config, ConfigError, load_yaml
from agent.util.logging import get_logger

if TYPE_CHECKING:
    from agent.run import RunContext


class Stage(Protocol):
    name: str

    def run(self, ctx: "RunContext") -> None: ...


STAGES: tuple[str, ...] = (
    "collect", "filter", "vision", "embed", "cluster",
    "understand", "validate", "score", "compose", "deliver",
)


def _load_prompt(name: str, base: Path | None) -> str:
    """Prompt text from config/prompts/. Missing file is a loud ConfigError
    -- a silently empty prompt would send an untrained model a blank page."""
    # src/agent/pipeline/__init__.py -> parents[3] is the repo root.
    directory = base if base is not None else Path(__file__).resolve().parents[3] / "config"
    path = directory / "prompts" / name
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"prompt file not found: {name} ({path})") from exc


def build_stages(
    config: Config,
    env: Mapping[str, str] | None = None,
    *,
    base: Path | None = None,
    force_mock: bool = False,
    provider_health: Mapping[str, Mapping] | None = None,
) -> tuple[tuple[Stage, ...], object, object]:
    """Wire the real Phase 6 stages. `base` overrides the config dir (tests).
    Returns (stages, router, embedder) -- the caller attaches the last two
    to RunContext, which is how stages reach them.

    Mock mode (ops.mock_mode or --dry-run via force_mock): no keys, no
    network, no model download -- the router gets zero providers (every call
    degrades to UNAVAILABLE) and the embedder is the deterministic fake.
    Mock mode is a property of the WIRING, not a stub of the stage logic
    (PHASE_5_BRIEF §11)."""
    from agent.collectors import registry
    from agent.llm.router import Router
    from agent.llm.transport import MockHttpTransport
    from agent.llm.wiring import build_router
    from agent.pipeline._noop import NoopStage
    from agent.pipeline.cluster import ClusterStage, EmbedStage
    from agent.pipeline.collect import CollectStage
    from agent.pipeline.compose import ComposeStage
    from agent.pipeline.deliver import DeliverStage
    from agent.pipeline.embed import FakeEmbedder, MiniLmEmbedder
    from agent.pipeline.filter import TopicGateStage, validate_topics
    from agent.pipeline.understand import UnderstandStage
    from agent.pipeline.validate import ValidateStage

    logger = get_logger("agent.pipeline")
    environment = os.environ if env is None else env

    topics_raw = load_yaml("topics.yaml", base=base)
    topics = validate_topics(topics_raw)
    sources = registry.load_sources(base=base)
    registry.validate_join(sources, config.credibility)
    gated_ids = {s.id for s in sources if s.topic_gate}

    # Startup coverage check (session-5 decision 6): warns by default, can be
    # promoted to a hard failure in settings. Needs risk_weights.yaml, which
    # Phase 8 wrote from the backtest's canonical values.
    from agent.coverage import run_startup_check
    run_startup_check(
        sources,
        load_yaml("risk_weights.yaml", base=base),
        config.credibility,
        require_check=config.settings.ops.require_signal_coverage_check,
        fail_on_warnings=config.settings.ops.coverage_check_fails_build,
        logger=logger,
    )

    if config.settings.ops.mock_mode or force_mock:
        router = Router([], transport=MockHttpTransport(),
                        max_calls=config.settings.llm.max_calls_per_run,
                        clock=lambda: 0.0, sleep=lambda s: None, logger=logger)
        embedder = FakeEmbedder()
    else:
        router = build_router(config.settings.llm, environment, logger=logger,
                              health=provider_health)
        embedder = MiniLmEmbedder(config.settings.pipeline.embed_model)

    understand_prompt = _load_prompt("understand.txt", base)

    stages = (
        CollectStage(sources, config.settings, logger),
        TopicGateStage(topics, gated_ids, logger),
        NoopStage("vision"),  # no collector extracts images in v1
        EmbedStage(),
        ClusterStage(config, logger),
        UnderstandStage(
            understand_prompt, config.settings.pipeline.item_body_chars, logger
        ),
        ValidateStage(config.credibility, logger),
        NoopStage("score"),     # v1.5 (Phase 11)
        ComposeStage(logger),
        DeliverStage(environment, logger),
    )
    return stages, router, embedder
