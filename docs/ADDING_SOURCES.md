# Adding or removing a source

The system fetches 50 sources. Adding one is four steps, in order, each
verifying the previous one. Skipping a step means the run halts (join rule)
or the source silently reads as tier 3 (credibility default).

## 1. Probe the URL first

Every URL in `sources.yaml` was answered 200 with parseable items by a
GitHub US runner — the network the pipeline actually runs on. Your home
network in Iran is NOT the same network (TLS failures on western feeds,
possible geoblocks the other direction), so a local check is not evidence.

Add the candidate to `config/sources_candidates.csv` (same columns as the
existing rows), then:

    Actions → probe-feeds → Run workflow → tag: ci

Read the output CSV artifact: verdict must be OK and items > 0. 403/429 on
a live site is a host-level bot filter (the Google News `site:` proxy is
the known workaround — see existing ap_gnews/reuters_gnews rows).

## 2. Add to sources.yaml AND credibility.yaml in the same edit

```yaml
  - id: my_new_source            # kebab-case, unique
    name: My New Source
    url: https://...             # https ONLY, verified by the probe
    type: rss                    # rss | telegram
    lang: en                     # en | fa | ar | he
    topic: regional              # free-form label
    enabled: true
    topic_gate: false            # true only for general-interest feeds
    signals_covered: [B3, E1]    # which catalog signals it can witness
                                 # (config/risk_weights.yaml; [] for leads)
```

And in `credibility.yaml`:

```yaml
  my_new_source:
    tier: 2         # 1 = government/official, 2 = established newsroom,
                    # 3 = OSINT/channel, lead = untrusted early-warning
    group: my_group # independence: two sources confirm ONLY if groups differ.
                    # omit to make it its own group
```

## 3. Run the pipeline once, watch the log

Actions → pipeline → Run workflow. The run must not print a coverage
warning for your id and must not halt on the join check.

## 4. Removing a source

Set `enabled: false` and leave the row + its credibility entry. Rows are
kept deliberately: the reasoning survives and re-enabling is one flag.
Only delete a row when the id is also deleted from credibility.yaml.
