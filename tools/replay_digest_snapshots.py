"""Offline post-ranking replay; not a source-fact or model-quality benchmark.

CSV snapshots omit original admission times, prior_context and full score
precision. The fixture documents those limits. Reconstruct the first observed
queue, admit only subsequent fresh snapshots, and use the production composer
and SQLite store. Never imports delivery/Telegram or loads credentials.
"""
from __future__ import annotations

import argparse
import json
import logging
import socket
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace


def replay(source: Path, fixture: Path, database: Path, output: Path) -> dict:
    if database.exists():
        raise ValueError("refusing to overwrite an existing state database")
    database.parent.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(source.resolve() / "src"))
    import agent.pipeline.compose as compose
    from agent.config import load_all
    from agent.llm.errors import LlmResult
    from agent.memory.db import open_db
    from agent.memory.event_models import Event, mark_delivered
    from agent.pipeline.queue import load_pending, store_pending

    assert Path(compose.__file__).resolve().is_relative_to(source.resolve())
    # An accidental I/O path must fail rather than reach a real service.
    def blocked(*args, **kwargs):
        raise RuntimeError("network forbidden in snapshot replay")
    data = json.loads(fixture.read_text(encoding="utf-8"))
    config = load_all(base=source / "config")
    db = open_db(database, create_if_absent=True)
    assert Path(db.execute("PRAGMA database_list").fetchone()[2]).resolve() == database.resolve()
    log = logging.getLogger("snapshot-replay")
    reports, previous_deferred = [], set()
    previous_connect = socket.socket.connect
    socket.socket.connect = blocked
    try:
        for index, snap in enumerate(data["snapshots"]):
            now = datetime.fromisoformat(snap["at"])
            ctx = SimpleNamespace(config=config, db=db, now=now, events=[],
                                  clusters=[], counters={}, router=None, dry_run=True)
            def event(row):
                return Event(event_key=row["event_key"], headline=row["headline"],
                             summary=row["summary"], category=row["category"],
                             significance=row["significance"], claim_status=row["claim_status"],
                             independent_count=row["independent_count"],
                             first_seen_at=now, last_updated_at=now)
            pending = {r["event_key"] for r in snap["admissions"] if r["origin"] == "pending"}
            if index == 0:
                store_pending(ctx, [(event(r), r["score"]) for r in snap["admissions"]
                                    if r["origin"] == "pending"], now)
            else:
                assert {e.event_key for e, _ in load_pending(ctx, now)} == pending
                assert previous_deferred == pending
            store_pending(ctx, [(event(r), r["score"]) for r in snap["admissions"]
                                if r["origin"] == "fresh"], now)
            class FixtureRouter:
                calls = 0
                def complete(self, prompt, *, stage):
                    assert stage == "synthesis"
                    self.calls += 1
                    assert self.calls <= 1
                    return LlmResult(ok=True, status="ok", text=json.dumps(
                        snap["curated_overview_response"], ensure_ascii=False))
            ctx.router = FixtureRouter()
            template = (source / "config/prompts/synthesis.txt").read_text(encoding="utf-8")
            compose.ComposeStage(log, synthesis_template=template).run(ctx)
            expected = set(snap["expected_acknowledged_keys"])
            selected = {e.event_key for e in ctx.queue_selected}
            assert selected == expected and 0 < len(selected) <= 5
            admitted = {r["event_key"] for r in snap["admissions"]}
            deferred = {e.event_key for e in ctx.queue_deferred}
            assert deferred == admitted - selected
            assert {e.event_key for e, _ in load_pending(ctx, now)} == admitted
            assert set(ctx.compose_kept_keys) == selected
            assert set(ctx.compose_render_forms) == selected
            assert all(form == "full" for form in ctx.compose_render_forms.values())
            assert set(k for page in ctx.message_event_keys for k in page) == selected
            if hasattr(ctx, "synthesis_evidence_keys"):
                assert len(ctx.synthesis_evidence_keys) >= 2
                assert set(ctx.synthesis_evidence_keys) <= selected
            # Replay the HISTORICALLY OBSERVED receipts; no Telegram call.
            mark_delivered(db, expected, now)
            assert {e.event_key for e, _ in load_pending(ctx, now)} == deferred
            previous_deferred = deferred
            reports.append({"run_id": snap["run_id"], "admitted": len(admitted),
                            "selected_keys": sorted(selected), "deferred": len(deferred),
                            "overview_fixture_calls": ctx.router.calls,
                            "full_form_keys": sorted(ctx.compose_render_forms),
                            "messages": ctx.messages})
        result = {"fixture_kind": data["kind"], "source_tree": str(source.resolve()),
                  "database": str(database.resolve()), "network_forbidden": True,
                  "limitations": data["limitations"], "snapshots": reports}
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return result
    finally:
        socket.socket.connect = previous_connect
        db.close()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--fixture", type=Path, required=True)
    p.add_argument("--database", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    report = replay(args.source, args.fixture, args.database, args.output)
    print(json.dumps({"snapshots": len(report["snapshots"]), "network_forbidden": True,
                      "counts": [{k: v for k, v in row.items() if k in
                                  ("run_id", "admitted", "deferred", "overview_fixture_calls")}
                                 for row in report["snapshots"]]}, indent=2))


if __name__ == "__main__":
    main()
