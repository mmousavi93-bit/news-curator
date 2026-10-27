# SESSION_10J_BRIEF — execute S1–S4 (reconstruction + dataset assembly)

## TASK
Execute steps S1–S4 of `analysis/SESSION_10I_PLAN.md` — READ IT FIRST, it is the
spec (especially §3 steps S1–S4 and §4 reconstruction/assembly spec). This is the
first SOL block, gated by HUMAN gate H1.

## SCOPE (per the plan §3, steps S1–S4 — ONLY these files)
- S1: refine `tools/download_run_reports.py` + `.github/workflows/fetch-run-reports.yml`; add `tools/signal_dataset_inventory.py` + `tests/unit/test_signal_dataset_inventory.py`.
- S2: refine `tools/reconstruct_dataset.py`; add `tools/signal_membership.py` + `tests/unit/test_reconstruct_dataset.py`.
- S3: add `tools/recluster_signal_dataset.py` + `.github/workflows/reconstruct-signal-dataset.yml` + `tests/unit/test_recluster_signal_dataset.py`.
- S4: add `tools/assemble_signal_dataset.py` + `analysis/signal_dataset_validation.py` + `tests/unit/test_signal_dataset.py`.

## GATES (plan §3 G1–G4; make the LOCAL-testable ones pass)
- G2: the known regressions reproduce — 86/97 (`outputs/*20260908T180302Z.csv`)
  and 110/128 (`outputs/*20260908T225636Z.csv`) exact recoveries.
- G4: assembly validates Item fields, source joins, timestamps, all fates.
- G1/G3: inventory + recluster tools have mocked unit tests passing. The ACTUAL
  CI fetch and re-cluster need `workflow_dispatch` — do NOT dispatch them, do NOT
  make network calls; just write the code + workflows + tests.

## CONSTRAINTS (hard)
- Hash-first: `sha256("\n".join(sorted(urls)))[:16]`; NO embeddings for
  singletons/small clusters (re-cluster is CI fallback ONLY, sizes >=4).
- Do NOT touch `config/risk_weights.yaml`, `src/agent/risk/engine.py`,
  `analysis/backtest_weights.py`.
- Do NOT rebuild stages 2/3a. Do NOT generate labels (HUMAN H2).
- Do NOT git commit (Hermes does). No workflow dispatches.

## REPORT
Files changed + `git diff --stat` + pytest pass count + which of G1–G4 pass
locally, and any gate that is CI-only.
