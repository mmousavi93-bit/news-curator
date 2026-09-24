# SESSION_23_BRIEF — Pre-LLM drop calibration, CI route

## TASK
Move the past-7-days pre-LLM calibration onto GitHub Actions (local GitHub
API is dead behind the VPN), and prove the offline analyzer against a
FakeEmbedder join test. No threshold is enabled; this lands the *evidence
machinery*, not a behaviour change.

## GATE (machine-checkable, must pass before commit)
- `python -m pytest tests/unit/test_prellm_calibration.py tests/unit/test_prellm_drop.py tests/unit/test_prellm_historical.py -q` → all pass, exit 0.
- `python -m pytest -q` (full suite) → 0 failures.
- `python tools/calibrate_prellm_threshold.py <synth> --json-out curve.json` → exit 0 and prints a VERDICT.
- `git diff --check` clean; workflow YAML parses (`python -c "import yaml;yaml.safe_load(open('.github/workflows/calibrate-prellm.yml'))"`).

## CONTEXT
- HEAD `5227036`; drop gate + analyzer already implemented and unit-tested
  (25 tests green). This brief covers the remaining pieces: historical
  re-scorer, artifact downloader, CI workflow, and their tests.
- Production scoring path that must be reproduced: `EmbedStage` →
  `f"{title}\n{body}"` normalized MiniLM → `cluster_items` (key =
  sha256 of sorted member urls, deterministic) → `anchor_vectors` →
  `relevance_score` (max cosine). `is_on_mission` injected from a fully
  loaded Config in the CLI only.
- Embeddings are CI-only (torch ~2GB; user chose CI over local torch).

## SCOPE (files)
NEW:
- `src/agent/pipeline/prellm_historical.py` (scoring core, no torch/config)
- `tools/score_historical_prellm.py` (CLI, real MiniLM + config)
- `tools/download_run_reports.py` (artifact downloader, stdlib only)
- `.github/workflows/calibrate-prellm.yml` (workflow_dispatch + weekly cron)
- `tests/unit/test_prellm_historical.py` (FakeEmbedder join tests)
- `agents/briefs/SESSION_23_BRIEF.md` (this file)
EDIT:
- `tools/calibrate_prellm_threshold.py` (return 0 on valid verdict, so CI's
  `pipefail` only trips on a true no-rows error)

## CONSTRAINTS
- No LLM calls, no network, no torch in the scoring core.
- Drop gate stays `prellm_drop_enabled: false` — zero behaviour change.
- Workflow needs `permissions: actions: read` to list/download artifacts;
  `if: always()` on the upload step so the evidence lands even on a "do not
  enable" verdict.
- Report the historical-key match rate, not a silent join — body truncation
  (400 chars) means a few clusters legitimately split/merge and go unmatched.
- Never print or persist the token; use `GITHUB_TOKEN` from the runner env.

## REPORT
- Offline: FakeEmbedder test proves singletons join fates, unmatched keys
  surface, and the on_mission guard column is wired.
- CI: the workflow is the real measurement — it must produce
  `calibration_rows.csv` + `curve.json` + `report.txt`; the threshold decision
  is a human reading the report, not this commit.
