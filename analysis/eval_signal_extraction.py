"""Offline captured-response replay. No network, generated gold, or CSV writes."""
from __future__ import annotations

import argparse
from copy import deepcopy
from collections import Counter
from dataclasses import asdict
from datetime import date, datetime
import json
import logging
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import yaml
from agent.collectors.base import Item
from agent.llm.errors import LlmResult
from agent.memory.signal_models import digest, dumps
from agent.pipeline.cluster import Cluster
from agent.pipeline.signal_attribution import local_day
from agent.pipeline.signal_calendar import SignalCalendar
from agent.pipeline.signal_contract import EvidenceError
from agent.pipeline.signal_history import SignalHistory, occurrence_key
from agent.pipeline.signals import extract, attribute_run, signal_catalog
from agent.risk.engine import RiskEngine, SignalEvent


def event_fields(event):
    return {k: v.isoformat() if isinstance(v, date) else v for k, v in asdict(event).items()}


def read_event(row):
    row = dict(row)
    row["event_date"] = date.fromisoformat(row["event_date"])
    if row.get("state_end_date"):
        row["state_end_date"] = date.fromisoformat(row["state_end_date"])
    # Explicit corroboration is required, including at the market boundary.
    if "independent_source_count" not in row:
        raise ValueError("event fixture requires explicit independent_source_count")
    return SignalEvent(**row)


class CapturedRouter:
    def __init__(self, capture):
        self.capture, self.calls = capture, 0

    def complete(self, prompt, *, stage):
        self.calls += 1
        expected = self.capture.get("prompt_hash")
        if expected and expected != digest(prompt):
            raise ValueError("captured response prompt hash mismatch")
        return LlmResult(ok=self.capture.get("ok", True),
                         status=self.capture.get("status", "ok"),
                         text=self.capture["raw_response"],
                         provider=self.capture["provider"], model=self.capture["model_version"])


def scores(engine, events, now, kwargs):
    result = engine.evaluate(events, local_day(now), **kwargs)
    return {"STRAT": result.strat.capped, "TACT": result.tact.capped}


def replay(dataset, weights, template):
    """Replay chronological runs, retaining incomplete/failed cluster fates."""
    history, calendar = SignalHistory(signal_catalog(weights)), SignalCalendar()
    engine, output = RiskEngine(weights), []
    previous = None
    for run in dataset.get("runs", []):
        now = datetime.fromisoformat(run["cutoff"])
        local_day(now)
        if previous is not None and now < previous:
            raise ValueError("runs must be chronological")
        previous = now
        credibility = {k: SimpleNamespace(**v) for k, v in run["sources"].items()}
        candidates, items, clusters, fates, calendar_actions = [], {}, [], [], []
        for row in run["clusters"]:
            members = []
            for data in row["items"]:
                data = dict(data)
                if data.get("published_at"):
                    published = datetime.fromisoformat(data["published_at"])
                    if not data.get("date_only") and (published.tzinfo is None or published > now):
                        raise ValueError("future/ambiguous replay item timestamp")
                    if data.get("date_only") and published.date() > local_day(now):
                        raise ValueError("future date-only replay item")
                    data["published_at"] = published
                members.append(Item(**data))
            cluster = Cluster(row["cluster_key"], members)
            # Missing captures remain unknown; they are never converted to none.
            capture = row.get("capture")
            result = None
            if capture:
                for field in ("provider", "model_version", "schema_version", "prompt_version"):
                    if not capture.get(field):
                        raise ValueError(f"capture missing {field}")
                result = extract(CapturedRouter(capture), cluster, template=template,
                    body_chars=run.get("body_chars", 4000), catalog=history.catalog,
                    credibility=credibility, logger=logging.getLogger(__name__),
                    history_context=row.get("history_context", ""))
                candidates.extend(result.signals)
                for candidate in result.countdowns:
                    calendar_actions.append(("deadline", candidate))
                for candidate in result.soothing_statements:
                    calendar_actions.append(("soothing", candidate))
                for candidate in result.scheduled_events:
                    calendar_actions.append(("schedule", candidate))
            fate = result.status if result else row.get("fate", "missing_capture")
            fates.append(fate)
            clusters.append({"cluster_key": cluster.key, "language": row.get("language", "unknown"),
                "status": fate, "none": bool(result and result.none),
                "candidates": [occurrence_key(c) for c in result.signals] if result else [],
                "rejected": list(result.rejected) if result else [],
                "invalid_ids": list(result.dropped_ids) if result else []})
            items.update({i.url: i for i in members})
        complete = run.get("complete", False) and all(f in {"ok", "none"} for f in fates)
        error = None
        previous_history, previous_calendar = deepcopy(history), deepcopy(calendar)
        try:
            for kind, candidate in calendar_actions:
                if kind == "deadline":
                    calendar.update_deadline(candidate, items, credibility, now=now)
                elif kind == "soothing":
                    calendar.add_soothing(candidate, items, credibility, now=now)
                else:
                    calendar.update_schedule(candidate.occurrence_id, candidate, items, credibility, now=now)
                    history.add_baseline(candidate.occurrence_id, "schedule", candidate,
                                         items, credibility, now=now, due=candidate.dates[0])
            candidate_map = {occurrence_key(c): c for c in candidates}
            for update in run.get("baseline_updates", []):
                candidate = candidate_map[update["candidate_key"]]
                due = date.fromisoformat(update["due"]) if update.get("due") else None
                history.add_baseline(update["key"], update["kind"], candidate, items,
                                     credibility, now=now, due=due)
            events = attribute_run(candidates, items, credibility, now=now, history=history,
                                   weights=weights, complete=complete,
                                   linked_consequences=run.get("linked_consequences", ()))
        except EvidenceError as exc:
            error = str(exc)
            history, calendar = previous_history, previous_calendar
            events = attribute_run([], {}, credibility, now=now, history=history,
                                   weights=weights, complete=False)
        markets = [read_event(e) for e in run.get("market_events", [])]
        if any(e.signal_id not in {"G1", "G2"} for e in markets):
            raise ValueError("market boundary accepts G1/G2 only")
        output.append({"run_key": run["run_key"], "cutoff": run["cutoff"],
            "complete": complete and error is None, "clusters": clusters, "attribution_error": error,
            "accepted": {k: event_fields(e) for (k, _), e in
                zip(sorted(history.records.items()), history.materialize())
                if e.independent_source_count >= 2},
            "score": scores(engine, list(events) + markets, now, calendar.kwargs(now=now)),
            "market_events": [event_fields(e) for e in markets],
            "markets_missing": "market_events" not in run,
            "calls": sum("capture" in c for c in run["clusters"]),
            "tokens": dict(sum((Counter(c.get("capture", {}).get("usage", {}))
                                for c in run["clusters"]), Counter()))})
    return output


def prf(predicted, gold):
    tp, fp, fn = len(predicted & gold), len(predicted - gold), len(gold - predicted)
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    return {"tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall,
            "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None}


def measure(results, labels=None, weights=None):
    clusters = [c for r in results for c in r["clusters"]]
    report = {"label_status": "no labels", "accuracy_gate": "incomplete",
        "runs": len(results), "clusters": len(clusters),
        "statuses": dict(Counter(c["status"] for c in clusters)),
        "unknown_abstentions": sum(c["status"] not in {"ok", "none"} for c in clusters),
        "parse_success": sum(c["status"] in {"ok", "none", "partial", "rejected"} for c in clusters),
        "invalid_ids": sum(len(c["invalid_ids"]) for c in clusters),
        "evidence_rejections": sum(len(c["rejected"]) for c in clusters),
        "complete_runs": sum(r["complete"] for r in results),
        "field_accuracy": None, "candidate_metrics": None, "accepted_metrics": None,
        "keyword_baseline": "unmeasured: no independently frozen baseline supplied",
        "variability_latency_digest": "pending live measurements",
        "uncertainty": "unmeasured; report sample counts, no monthly false-alarm claim"}
    if not labels or not labels.get("runs"):
        return report
    report["label_status"] = "labels present"
    predictions = {r["run_key"]: r for r in results}
    candidate_p, candidate_g, accepted_p, accepted_g = set(), set(), set(), set()
    score_comparisons = []
    dimensions, exact, field_total, quiet, quiet_correct, critical_misses = {}, 0, 0, 0, 0, []
    for gold in labels["runs"]:
        key = gold["run_key"]
        pred = predictions.get(key, {"clusters": [], "accepted": {}})
        cluster_map = {c["cluster_key"]: c for c in pred["clusters"]}
        for cluster in gold["clusters"]:
            ck = cluster["cluster_key"]
            p = set(cluster_map.get(ck, {}).get("candidates", []))
            g = set(cluster["candidates"])
            candidate_p.update((key, ck, v) for v in p)
            candidate_g.update((key, ck, v) for v in g)
            if not g:
                quiet += 1
                quiet_correct += bool(cluster_map.get(ck, {}).get("none"))
            for dimension, value in (("language", cluster["language"]),):
                dp, dg = dimensions.setdefault((dimension, value), (set(), set()))
                dp.update((key, ck, v) for v in p)
                dg.update((key, ck, v) for v in g)
        accepted_p.update((key, v) for v in pred["accepted"])
        accepted_g.update((key, v) for v in gold["accepted"])
        for identity in set(pred["accepted"]) | set(gold["accepted"]):
            e = gold["accepted"].get(identity, pred["accepted"].get(identity))
            for dimension, value in (("signal_id", e["signal_id"]), ("category", e["signal_id"][0])):
                dp, dg = dimensions.setdefault((dimension, value), (set(), set()))
                if identity in pred["accepted"]:
                    dp.add((key, identity))
                if identity in gold["accepted"]:
                    dg.add((key, identity))
        for identity, fields in gold["accepted"].items():
            field_total += len(fields)
            exact += sum(pred["accepted"].get(identity, {}).get(f) == v for f, v in fields.items())
            if identity not in pred["accepted"] and fields["signal_id"] in {"B1", "B2", "B6", "E1", "E2"}:
                critical_misses.append([key, identity])
        if weights and "cutoff" in pred:
            now = datetime.fromisoformat(pred["cutoff"])
            events = [read_event(e) for e in gold["accepted"].values()]
            if any(e.event_date > local_day(now) or
                   (e.state_end_date and e.state_end_date > local_day(now)) for e in events):
                raise ValueError("future oracle label at replay cutoff")
            markets = [read_event(e) for e in pred.get("market_events", [])]
            kwargs = dict(gold.get("side_channels", {}))
            if kwargs.get("deadline"):
                kwargs["deadline"] = date.fromisoformat(kwargs["deadline"])
            oracle = scores(RiskEngine(weights), events + markets, now, kwargs)
            score_comparisons.append({"run_key": key, "oracle": oracle, "extracted": pred["score"],
                "missing_markets": pred["markets_missing"],
                "side_channels_labeled": "side_channels" in gold,
                "delta": {k: round(pred["score"][k] - oracle[k], 1) for k in oracle}})
    report.update(candidate_metrics=prf(candidate_p, candidate_g),
        accepted_metrics=prf(accepted_p, accepted_g),
        field_accuracy=exact / field_total if field_total else None,
        field_sample_size=field_total, labeled_runs=len(labels["runs"]),
        quiet_correct_none=quiet_correct / quiet if quiet else None,
        floor_critical_misses=critical_misses,
        breakdown={f"{k}:{v}": prf(p, g) for (k, v), (p, g) in sorted(dimensions.items())},
        evidence_validity="provenance validated mechanically; semantic entailment requires human review",
        source_history_coverage=labels.get("coverage", "missing"),
        score_comparisons=score_comparisons,
        unsupported_classes="unmeasured; absence of gold is not evidence of accuracy")
    # Semantic review, score coverage, repeated-live and cost gates remain human work.
    return report


def calibration(weights):
    from analysis.backtest_weights import SCENARIOS, score
    engine, rows = RiskEngine(weights), []
    for sc in SCENARIOS:
        events = [SignalEvent(e["id"], e["date"], e["tier"], 2,
                              e.get("novelty", 1.0), e.get("until")) for e in sc["events"]]
        actual = engine.score(events, sc["at"], sc["which"],
            deadline=sc.get("deadline"), deception=sc.get("deception", False)).capped
        reference = round(score(sc["events"], sc["at"], sc["which"], "state",
                               sc.get("deadline"), sc.get("deception", False)), 1)
        assert actual == reference
        assert sc["lo"] <= actual and (actual < sc["hi"] if sc["target"] == "<15" else actual <= sc["hi"])
        rows.append({"name": sc["name"], "score": actual, "reference": reference,
                     "band": sc["target"], "kind": "synthetic scorer fidelity only"})
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path)
    parser.add_argument("--labels", type=Path, default=ROOT / "tests/fixtures/risk_extraction/labels.json")
    args = parser.parse_args(argv)
    weights = yaml.safe_load((ROOT / "config/risk_weights.yaml").read_text(encoding="utf-8"))
    dataset = json.loads(args.dataset.read_text(encoding="utf-8")) if args.dataset else {"runs": []}
    labels = json.loads(args.labels.read_text(encoding="utf-8")) if args.labels.exists() else None
    template = (ROOT / "config/prompts/signal_extraction.txt").read_text(encoding="utf-8")
    results = replay(dataset, weights, template)
    print(dumps({"metrics": measure(results, labels, weights), "replay": results,
                 "calibration": calibration(weights)}))


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    main()
