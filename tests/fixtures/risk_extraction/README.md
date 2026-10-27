# Risk extraction replay fixtures

This directory intentionally contains no labeled histories or invented articles.
Human source collection, independent labeling, review and adjudication are pending.
Controlled unit fixtures are mechanical tests, never historical gold.

Run from the repository root:

```powershell
py -3.12 -B analysis/eval_signal_extraction.py
py -3.12 -B analysis/eval_signal_extraction.py --dataset path/to/history.json --labels path/to/labels.json
```

Without a label file (or with `{"runs": []}`), the report says `no labels` and
`accuracy_gate: incomplete`. Without a dataset, zero historical clusters are
reported. Five synthetic scorer checks run separately and do not imply accuracy.

## Source dataset schema (JSON)

The root contains `runs`, ordered chronologically. Each run has:

- `run_key`: stable unique run ID; `cutoff`: aware ISO timestamp.
- `sources`: source ID to original configured `tier` and `group` snapshots.
- `complete`: collection/extraction health, explicitly supplied, default false.
- `clusters`: all sampled clusters, including negatives and skipped clusters.
- Optional `body_chars` (default 4000), `linked_consequences` (occurrence-key pairs).
- Optional `market_events`: explicit G1/G2 fixtures with six event fields and
  explicit corroboration counts. Omission is reported as missing markets.

Each cluster has `cluster_key`, `language`, and original `items`. Each item has
the `agent.collectors.base.Item` fields: `source_id`, `url` (stable item ID),
`title`, `body`, `published_at` (aware ISO timestamp or null), `lang`, `raw_hash`,
and optional `date_only`. Keep original texts, dates, URLs and collection coverage.
Only evidence available at the cutoff may be included. Future items are rejected.

An extracted cluster includes `capture`: `raw_response`, `provider`,
`model_version`, `prompt_version`, `schema_version`, optional `ok`, `status`,
`prompt_hash` (SHA-256 of the exact rendered prompt), and `usage` token counts.
The response passes through `pipeline.signals.extract`, never around validation.
Freeze the prompt file with the dataset; hash mismatches halt replay. Optional
`history_context` contains only source evidence available at this cutoff.
Clusters without captures have `fate`, such as `refused_cap`, `unavailable`,
`omitted`, or `truncated`; they remain unknown rather than correct-none.

## Independent label schema (JSON; do not copy model output)

The root contains `runs` and `coverage`. Each labeled run has `run_key`,
`clusters`, and `accepted`. Each labeled cluster has `cluster_key`, `language`,
and `candidates` (a list of independently assigned canonical occurrence keys;
an empty list labels a negative). `accepted` maps occurrence keys to all six
derived fields: `signal_id`, `event_date` (ISO date), `source_tier`,
`independent_source_count`, `novelty`, `state_end_date` (ISO date or null).
These represent the expected retained score inputs at that cutoff, not only new
firings. Keys use `signal_history.occurrence_key`: JSON array of normalized
signal/actor/object/location/occurrence/episode identity, with the A2 theater rule.

Store supporting item IDs, exact source-language quotes, state transitions,
C1/soothing/schedule side channels, baseline references, collection availability,
reviewers and adjudication notes alongside each label. `coverage` must describe
missing sources/history, the five scenario cutoffs, the quiet week, and which
classes have no examples. Extra annotation fields are retained by the human
dataset; this mechanical harness does not judge semantic entailment.

Labels must be written independently of model output and reviewed/adjudicated
before accuracy claims. Missing captures on gold positives count as false
negatives. Use a separate development corpus; freeze acceptance labels and
versions before running acceptance histories. No examples here are gold data.
