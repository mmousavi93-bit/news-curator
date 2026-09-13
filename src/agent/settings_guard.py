"""Cross-section settings guards: relationships BETWEEN sections that the
per-leaf checks in settings.py cannot see.

Split out of settings.py 2026-09-09 (session 9s) when the batch-vs-tpm
guard pushed that file over the ~200-line cap (constraint 12). The two
older checks moved here verbatim -- the error TEXTS are load-bearing
(tests match on them), so nothing was reworded.

The batch-vs-tpm guard is the same class of guard as 9q's
`event_repeat_threshold < event_match_threshold` check (settings.py's
docstring, round-3 review fix 3): settings.yaml invites the owner to nudge
`llm.batch_size` and provider `tpm` values, and a config where a nominal
batch CANNOT fit one minute's token allowance is not slow -- it is
permanently impossible. Groq's free tier proves it: TPM 8,000, and a
request above it 429s forever regardless of pacing (evidence:
agents/briefs/SESSION_9S_BRIEF.md).

The estimate is NOMINAL, not worst-case: the prompt template is ~2,300
tokens and the measured average cluster contributes ~700 tokens of items
(the arithmetic table in the brief, reproduced next to `batch_size` in
settings.yaml). The RUNTIME TokenPacer books the ACTUAL rendered prompt
size, so a real 30-member cluster is paced correctly even though this
guard treated it as average. The guard exists so a config edit cannot
silently recreate the 2026-09-08 lockout.
"""

from __future__ import annotations

from typing import Any

# Nominal token costs for the guard's estimate -- see the module
# docstring and the table in settings.yaml. These are NOT the runtime
# estimate (that is chars/1.6 in llm/token_pacer.py, computed from the
# actual rendered prompt).
# The cluster cost is deliberately CONSERVATIVE: the brief's measured
# table used ~700, but actual batch-7 runs measured ~7,600 against the
# 7,200 nominal -- ~50 tokens/cluster of slop. 800 keeps batch 7 inside
# (2,300+5,600=7,900 <= 8,000) and refuses batch 8 (8,700) instead of
# letting a near-the-line config slip through the exact guard built to
# stop this defect (round-1 review, MINOR-1).
_NOMINAL_TEMPLATE_TOKENS = 2300
_NOMINAL_CLUSTER_TOKENS = 800


def cross_section_errors(built: dict[str, Any]) -> list[str]:
    """Every cross-section problem in one list; settings.py raises them
    all at once. Runs only after every leaf has validated clean, so every
    attribute read here is guaranteed present."""
    errors: list[str] = []
    pipeline = built["pipeline"]
    digest_rank = built["digest_rank"]
    llm = built["llm"]

    def _fraction(path: str, value: float) -> None:
        if not (0.0 <= value <= 1.0):
            errors.append(f"{path}: must be between 0.0 and 1.0, got {value!r}")

    _fraction("settings.pipeline.event_match_threshold", pipeline.event_match_threshold)
    _fraction("settings.digest_rank.event_repeat_threshold", digest_rank.event_repeat_threshold)
    _fraction(
        "settings.pipeline.samerun_pair_log_floor", pipeline.samerun_pair_log_floor
    )

    if (
        0.0 <= pipeline.event_match_threshold <= 1.0
        and 0.0 <= digest_rank.event_repeat_threshold <= 1.0
        and digest_rank.event_repeat_threshold < pipeline.event_match_threshold
    ):
        errors.append(
            "settings.digest_rank.event_repeat_threshold "
            f"({digest_rank.event_repeat_threshold!r}) must be >= "
            "settings.pipeline.event_match_threshold "
            f"({pipeline.event_match_threshold!r}) -- the repeat gate's "
            "HIGH ('same story') band cannot be looser than its MID "
            "('worth comparing') floor, see pipeline/repeats.py"
        )

    if digest_rank.max_messages < 1:
        errors.append(
            "settings.digest_rank.max_messages: must be at least 1, got "
            f"{digest_rank.max_messages!r} -- 0 crashes delivery/budget.py "
            "on an empty page list"
        )

    # Session 9s: batch_size must be a real batch size (0 would loop
    # forever in pipeline/batch.py's chunk) and the nominal batch must fit
    # the smallest declared tpm among the providers actually in the
    # cascade. Providers WITHOUT a tpm are unconstrained and never bind
    # the check (settings_llm.py documents missing tpm = unconstrained).
    if llm.batch_size < 1:
        errors.append(
            "settings.llm.batch_size: must be at least 1, got "
            f"{llm.batch_size!r} -- 1 is the rollback path "
            "(one call per cluster, pre-batching behaviour)"
        )
    if llm.batch_size > 1:
        declared_tpms = [
            cfg.tpm for cfg in llm.providers.values()
            if cfg.tpm is not None and cfg.tpm > 0
        ]
        ordered_tpms = [
            cfg.tpm for name in llm.order
            if (cfg := llm.providers.get(name)) is not None and cfg.tpm
        ]
        # The cascade order governs which providers actually serve, so the
        # binding ceiling is the smallest tpm among THOSE. Fall back to all
        # declared tpms if order lists nobody (defensive).
        tpms = ordered_tpms or declared_tpms
        if tpms:
            smallest = min(tpms)
            nominal = _NOMINAL_TEMPLATE_TOKENS + _NOMINAL_CLUSTER_TOKENS * llm.batch_size
            if nominal > smallest:
                errors.append(
                    "settings.llm.batch_size: a nominal batch of "
                    f"{llm.batch_size} clusters is ~{nominal} tokens "
                    f"(template ~{_NOMINAL_TEMPLATE_TOKENS} + ~"
                    f"{_NOMINAL_CLUSTER_TOKENS}/cluster), which exceeds the "
                    f"smallest tpm in llm.order ({smallest}) -- a request "
                    "above the per-minute allowance 429s permanently "
                    "regardless of pacing; lower batch_size or raise the "
                    "provider's tpm"
                )

    return errors
