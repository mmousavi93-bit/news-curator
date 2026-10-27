"""Deterministic, versioned rows for the offline extraction store.

Only allowlisted dataclasses are decoded; no executable serialization is used.
Evidence snapshots outlive the short-lived collector items table.
"""
from dataclasses import dataclass, fields, is_dataclass
from datetime import date, datetime
import hashlib
import json

from agent.collectors.base import Item
from agent.pipeline.signal_attribution import AttributedSignal
from agent.pipeline.signal_calendar import Deadline, SignalCalendar
from agent.pipeline.signal_contract import Candidate, Support
from agent.pipeline.signal_history import Baseline, Cycle, Record, SignalHistory


_TYPES = {c.__name__: c for c in
          (Item, AttributedSignal, Candidate, Support, Baseline, Cycle, Record, Deadline)}


def encode(value):
    if isinstance(value, datetime):
        # A date-only Item's calendar date must survive offset normalization.
        return {"type": "datetime", "value": value.isoformat()}
    if isinstance(value, date):
        return {"type": "date", "value": value.isoformat()}
    if is_dataclass(value):
        name = type(value).__name__
        if name not in _TYPES:
            raise ValueError(f"unsupported state type: {name}")
        return {"type": name, "value": {f.name: encode(getattr(value, f.name))
                                       for f in fields(value)}}
    if isinstance(value, dict):
        rows = [[encode(k), encode(v)] for k, v in value.items()]
        return {"type": "dict", "value": sorted(rows, key=lambda r: dumps(r[0]))}
    if isinstance(value, (tuple, list, set, frozenset)):
        rows = [encode(v) for v in value]
        if isinstance(value, (set, frozenset)):
            rows.sort(key=dumps)
        return {"type": type(value).__name__, "value": rows}
    if value is None or type(value) in (str, int, float, bool):
        return value
    raise ValueError(f"unsupported state value: {type(value).__name__}")


def decode(value):
    if not isinstance(value, dict):
        return value
    kind, data = value["type"], value["value"]
    if kind == "datetime":
        return datetime.fromisoformat(data)
    if kind == "date":
        return date.fromisoformat(data)
    if kind == "dict":
        return {decode(k): decode(v) for k, v in data}
    containers = {"tuple": tuple, "list": list, "set": set, "frozenset": frozenset}
    if kind in containers:
        return containers[kind](decode(v) for v in data)
    return _TYPES[kind](**{k: decode(v) for k, v in data.items()})


def dumps(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False)


def pack(value):
    return dumps(encode(value))


def unpack(value):
    return decode(json.loads(value))


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RawCandidateRecord:
    """One immutable captured cluster response, including failures/explicit none.

    input_hash covers the exact rendered prompt and supplied input snapshot.
    Versions are mandatory caller-supplied provenance, never inferred.
    """
    run_key: str
    cluster_key: str
    input_hash: str
    prompt_version: str
    model_version: str
    provider: str
    schema_version: str
    raw_response: str
    observed_at: datetime
    items: dict
    sources: dict
    status: str

    @property
    def key(self):
        return digest(pack((self.run_key, self.cluster_key)))


def history_header(history):
    history.materialize()  # Missing/corrupt ledgers must halt before a write.
    return {"version": 1, "catalog": history.catalog,
            "last_checkpoint": history.last_checkpoint,
            "checkpoints": history.checkpoints,
            "consequence_days": history.consequence_days}


def calendar_payload(calendar):
    return vars(calendar or SignalCalendar()).copy()


def restore_history(header, records, ledger, baselines):
    if header["version"] != 1:
        raise ValueError("unknown signal history format")
    history = SignalHistory(header["catalog"])
    history.records, history.ledger = records, ledger
    history.baselines = {k: v[0] for k, v in baselines.items()}
    history.baseline_evidence = {k: v[1] for k, v in baselines.items()}
    for name in ("last_checkpoint", "checkpoints", "consequence_days"):
        setattr(history, name, header[name])
    history.materialize()
    return history


def raw_quotes(raw_response, item_key):
    """Capture quoted spans verbatim; validation belongs to the extractor."""
    from agent.pipeline.contract import extract_json
    try:
        envelope = extract_json(raw_response)
    except ValueError:
        return []
    quotes = set()
    for kind in ("signals", "countdowns", "soothing_statements", "scheduled_events"):
        for row in envelope.get(kind, []) if isinstance(envelope.get(kind), list) else []:
            for support in row.get("supports", []) if isinstance(row, dict) else []:
                if isinstance(support, dict) and support.get("item_id") == item_key:
                    quote = support.get("quote")
                    if isinstance(quote, str):
                        quotes.add(quote)
    return sorted(quotes)


def load_signal_history(conn, catalog):
    """Load required state; absence is an error, never an implicit new ledger.

    Returns (history, calendar, source snapshots). A new empty history must be
    constructed explicitly by the offline caller and persisted once.
    """
    import sqlite3
    from agent.memory.db import StateError
    from agent.memory import signal_models as sm
    from agent.pipeline.signal_calendar import SignalCalendar

    try:
        row = conn.execute("SELECT header, calendar FROM signal_history_meta WHERE singleton=1").fetchone()
        if row is None:
            raise StateError("missing required signal history")
        header = sm.unpack(row[0])
        if header["catalog"] != frozenset(catalog):
            raise StateError("signal history catalog mismatch")
        records, sources, baselines = {}, dict(header.get("source_snapshots", {})), {}
        for r in conn.execute("SELECT occurrence_key, payload FROM signal_occurrences ORDER BY occurrence_key"):
            records[r[0]], snapshot = sm.unpack(r[1])
            sources.update(snapshot)
        ledger = {r[0]: sm.unpack(r[1]) for r in conn.execute("SELECT signal_id, payload FROM signal_novelty")}
        for r in conn.execute("SELECT baseline_key, payload FROM signal_baselines ORDER BY baseline_key"):
            baselines[r[0]], snapshot = sm.unpack(r[1])
            sources.update(snapshot)
        history = sm.restore_history(header, records, ledger, baselines)
        calendar = SignalCalendar()
        calendar.__dict__.update(sm.unpack(row[1]))
        return history, calendar, sources
    except StateError:
        raise
    except (ValueError, KeyError, TypeError, sqlite3.DatabaseError) as exc:
        raise StateError(f"corrupt required signal history: {exc}") from exc
