"""Offline tests for the historical re-scorer (prellm_historical.py).

Uses the FakeEmbedder (no torch) so the fate-join + row-emission logic is
proven locally before the CI job runs the real MiniLM path. Verifies:
  * singleton clusters get deterministic keys and join their historical fates
  * a cluster whose key is absent from chosen.csv is reported as unmatched
  * the injected on_mission guard lands in the on_mission column
"""

from __future__ import annotations

import csv
import hashlib
from pathlib import Path

from agent.pipeline.prellm_historical import score_run
from agent.pipeline.embed import FakeEmbedder


def _key(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def _write_read(path: Path, rows: list[dict]) -> None:
    fields = ["source_id", "url", "title", "body", "published_at_utc", "date_only", "lang"]
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({f: row.get(f, "") for f in fields})


def _write_chosen(path: Path, rows: list[dict]) -> None:
    fields = ["cluster_key", "fate"]
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({f: row.get(f, "") for f in fields})


def _mkrun(tmp_path: Path, urls: list[str], chosen: dict[str, str]) -> Path:
    run = tmp_path / "run_1" / "reports"
    run.mkdir(parents=True)
    items = [
        {"source_id": f"s{i}", "url": u, "title": f"T{i}", "body": f"B{i}",
         "published_at_utc": "2026-09-23T06:00:00+00:00", "date_only": "1", "lang": "en"}
        for i, u in enumerate(urls)
    ]
    _write_read(run / "read_20260923T060000Z.csv", items)
    _write_chosen(run / "chosen_20260923T060000Z.csv",
                  [{"cluster_key": k, "fate": v} for k, v in chosen.items()])
    return run


def test_singletons_join_fates(tmp_path: Path) -> None:
    urls = ["https://a.example/1", "https://a.example/2", "https://a.example/3"]
    chosen = {_key(urls[0]): "irrelevant", _key(urls[1]): "sent", _key(urls[2]): "cap_dropped"}
    run = _mkrun(tmp_path, urls, chosen)

    rows, stats = score_run(run, FakeEmbedder(), threshold=0.62, on_mission=lambda c: False)

    assert stats["items"] == 3
    assert stats["clusters"] == 3
    assert stats["matched"] == 3
    assert stats["unmatched"] == 0
    by_key = {r["cluster_key"]: r for r in rows}
    assert by_key[_key(urls[0])]["fate"] == "irrelevant"
    assert by_key[_key(urls[1])]["fate"] == "sent"
    assert by_key[_key(urls[2])]["fate"] == "cap_dropped"
    assert all(r["on_mission"] == 0 for r in rows)
    # score is a parseable float (max cosine to the anchors), never empty
    assert all(r["prellm_score"] for r in rows)
    for r in rows:
        float(r["prellm_score"])


def test_missing_key_reports_unmatched(tmp_path: Path) -> None:
    urls = ["https://a.example/1", "https://a.example/2"]
    chosen = {_key(urls[0]): "sent"}  # second key deliberately absent
    run = _mkrun(tmp_path, urls, chosen)

    rows, stats = score_run(run, FakeEmbedder(), threshold=0.62, on_mission=lambda c: False)

    assert stats["matched"] == 1
    assert stats["unmatched"] == 1
    unmatched = [r for r in rows if not r["fate"]]
    assert len(unmatched) == 1
    assert unmatched[0]["cluster_key"] == _key(urls[1])


def test_on_mission_guard_is_injected(tmp_path: Path) -> None:
    urls = ["https://a.example/1", "https://a.example/2"]
    guard_key = _key(urls[0])
    chosen = {_key(u): "sent" for u in urls}
    run = _mkrun(tmp_path, urls, chosen)

    rows, _ = score_run(
        run, FakeEmbedder(), threshold=0.62,
        on_mission=lambda c: c.key == guard_key,
    )

    by_key = {r["cluster_key"]: r for r in rows}
    assert by_key[guard_key]["on_mission"] == 1
    assert by_key[_key(urls[1])]["on_mission"] == 0
