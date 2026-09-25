# Agent orchestration — responsibility ladder + failure-review-reuse

Status: **awaiting approval**. No change to `PORTABLE_AGENT_PACK.md` until this is
signed off.

## Why

The current loop routes agents by MODEL TIER (Architect=strongest, Implementer=mid/light,
Verifier=light, Scout=light) and, on a gate failure, "Reject costs one more Implementer run"
— the SAME role retries in a fresh context while the failed diff is thrown away. Two gaps:

1. **No responsibility ranking.** Model strength is a *capability* axis, not an
   *accountability* axis. "Who is responsible when this breaks" should be explicit, not
   inferred from "which model was cheapest to run."
2. **Failed work is discarded.** A phase that fails its gate can still contain correct,
   salvageable work (a right module, a right test, one wrong edge case). Discarding the whole
   diff forces a blind rebuild that repeats the same mistake.

## Responsibility ladder (low → high)

| Rank | Agent | Accountable for | Failure means |
|---|---|---|---|
| 1 | Scout | data is accurate | an invented URL / limit / feed |
| 2 | Implementer | diff meets the brief | the gate fails |
| 3 | Verifier | verdict is correct | a missed finding (caught at review) |
| 4 | Architect | the phase is correct | the owner sees wrong output |

Rule: **a failure is reviewed by the next-higher-responsibility agent, never by the agent
that produced it.** Model tier is a separate axis and only decides how *strong* the reviewer
is, not *who* reviews. It does not change which model runs each role — it adds the
accountability ordering the loop is currently missing.

## Failure-review-reuse loop

On gate FAILURE, replace "one more Implementer run" with an escalation + salvage:

1. **Verifier reports FAIL** with findings (file:line), never a summary.
2. **Architect reviews the failed work** — reads the diff + findings, partitions it:
   - `salvage` — files / tests / functions that are correct and reused.
   - `discard` — the parts that caused the failure.
3. **Fresh Implementer** (new context; escalate to a stronger model only when the failure
   was unexplained) retries with: the original brief + the `salvage` list + the Verifier
   findings. It starts from the salvage, not from scratch.
4. **One retry only.** Second failure → the Architect implements directly (or narrows
   scope). Never a third blind Implementer run.

Non-negotiable: the Implementer never reviews its own failed work; the `salvage` list is
written by the Architect, not by the failed Implementer.

Scout sits at the bottom of the ladder: its failures surface as `UNVERIFIED` entries that a
human resolves (already in `PORTABLE_AGENT_PACK.md`), and it never blocks the build loop.

## Patch to PORTABLE_AGENT_PACK.md

Replace the loop line:

> Architect writes brief → Implementer builds → Verifier attacks → Architect reviews the
> verdict and approves or rejects the gate → commit → **discard Implementer context** →
> next phase.

with the success/failure split:

> Architect writes brief → Implementer builds → Verifier attacks → Architect reviews the
> verdict.
> - **Gate PASS** → commit → discard Implementer context → next phase.
> - **Gate FAIL** → Architect partitions the diff into `salvage` / `discard` → fresh
>   Implementer (new context) retries from the salvage + findings → **one retry** → second
>   failure → Architect implements.

## Gate (how we verify the protocol)

The retry brief is machine-checkable: it must name (a) the `salvage` list and (b) the
Verifier findings. The phase brief template (`PORTABLE_AGENT_PACK.md` "Phase brief template")
gains a `FAILURE REUSE` section. A retry brief missing either field is rejected before code
is written.
