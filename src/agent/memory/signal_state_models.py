"""Atomic occurrence/episode and calendar repository writes."""
def persist_signal_history(conn, history, *, run_key, observed_at, raw_records=(),
                           calendar=None, source_snapshots=None, calendar_items=None):
    """Atomic offline state/evidence/checkpoint write. A replay is a no-op.
    Reusing a run key with different state or provenance halts. Older runs
    cannot overwrite newer state. No clock or classifier is consulted here.
    Source snapshots must include configured tier/group, not today's config
    substituted later on reload.
    """
    from agent.memory import signal_models as sm
    from agent.memory.db import StateError
    from agent.memory.models import to_utc_iso
    from agent.pipeline.signal_history import occurrence_key
    stamp = to_utc_iso(observed_at)
    if not run_key or stamp is None:
        raise StateError("signal run requires key and observation timestamp")
    sources = source_snapshots or {}
    if any(not isinstance(v, dict) or not {"tier", "group"} <= v.keys() for v in sources.values()):
        raise StateError("source snapshots require configured tier and group")
    header = sm.history_header(history)
    prior = conn.execute("SELECT header, calendar FROM signal_history_meta").fetchone()
    cal = sm.unpack(prior[1]) if calendar is None and prior else sm.calendar_payload(calendar)
    baselines = {k: (v, history.baseline_evidence[k])
                 for k, v in history.baselines.items()}
    raw_records = tuple(sorted(raw_records, key=lambda r: r.key))
    evidence_items = dict(calendar_items or {})
    for raw in raw_records:
        evidence_items.update(raw.items)
    for record in history.records.values():
        evidence_items.update(record.items)
    for _, (_, baseline_items, _) in baselines.values():
        evidence_items.update(baseline_items)
    if prior:
        evidence_items = {**sm.unpack(prior[0]).get("calendar_items", {}), **evidence_items}
    calendar_supports = {s.item_id for kind in ("deadlines", "schedules", "statements")
        for entry in cal[kind].values() for s in
        (entry[1] if kind == "statements" else entry.candidate).supports}
    if calendar_supports - evidence_items.keys():
        raise StateError("missing calendar supporting item snapshot")
    header["calendar_items"] = {k: evidence_items[k] for k in sorted(calendar_supports)}
    if any(i.source_id not in sources for i in header["calendar_items"].values()):
        raise StateError("missing calendar source snapshot")
    header["source_snapshots"] = sources
    payload = sm.pack((header, history.records, history.ledger, baselines, cal,
                       raw_records_payload(raw_records), sources))
    state_hash = sm.digest(payload)
    # Validate before beginning a transaction; even a failed input is harmless.
    for key, record in history.records.items():
        if occurrence_key(record.candidate) != key:
            raise StateError("corrupt occurrence key")
        for item in record.items.values():
            if item.source_id not in sources:
                raise StateError(f"missing source snapshot: {item.source_id}")
    for raw in raw_records:
        if raw.run_key != run_key or to_utc_iso(raw.observed_at) != stamp:
            raise StateError("raw record run/observation mismatch")
        if not all((raw.input_hash, raw.prompt_version, raw.model_version,
                    raw.provider, raw.schema_version)):
            raise StateError("raw record missing provenance version")
        if any(i.source_id not in raw.sources for i in raw.items.values()):
            raise StateError("raw record missing source snapshot")
    conn.execute("SAVEPOINT signal_write")
    try:
        old = conn.execute("SELECT state_hash FROM signal_runs WHERE run_key=?",
                           (run_key,)).fetchone()
        if old:
            if old[0] != state_hash:
                raise StateError("conflicting replay of signal run")
            conn.execute("RELEASE signal_write")
            return
        latest = conn.execute("SELECT observed_at FROM signal_history_meta").fetchone()
        if latest and latest[0] > stamp:
            raise StateError("out-of-order signal run")
        for raw in raw_records:
            conn.execute("INSERT INTO signal_raw_candidates VALUES (?, ?, ?, ?, ?)",
                         (raw.key, run_key, raw.cluster_key, stamp, sm.pack(vars(raw))))
            for item_key, item in sorted(raw.items.items()):
                conn.execute("INSERT INTO signal_supports VALUES (?, ?, ?, ?, ?)",
                             (raw.key, item_key, sm.pack(item),
                              sm.pack(raw.sources[item.source_id]),
                              sm.pack(sm.raw_quotes(raw.raw_response, item_key))))
        for key, record in sorted(history.records.items()):
            e = record.event
            old = conn.execute("SELECT signal_event_id FROM signal_occurrences "
                               "WHERE occurrence_key=?", (key,)).fetchone()
            end = e.state_end_date.isoformat() if e.state_end_date else None
            if old:
                event_id = old[0]
                conn.execute("UPDATE [signal_events] SET state_ended_at=?, tier=? WHERE id=?",
                             (end, e.source_tier, event_id))
            else:
                event_id = conn.execute(
                    "INSERT INTO [signal_events] (signal_id, observed_at, state_ended_at, tier) "
                    "VALUES (?, ?, ?, ?)", (e.signal_id, stamp, end, e.source_tier)).lastrowid
            snapshot = {i.source_id: sources[i.source_id] for i in record.items.values()}
            conn.execute(
                "INSERT INTO signal_occurrences VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(occurrence_key) DO UPDATE SET "
                "state_end_date=excluded.state_end_date, "
                "last_confirmed_at=excluded.last_confirmed_at, "
                "last_checked_at=excluded.last_checked_at, observed_at=excluded.observed_at, "
                "payload=excluded.payload",
                (key, event_id, e.event_date.isoformat(), end,
                 record.last_confirmed_at.isoformat(), record.last_checked_at.isoformat(),
                 stamp, sm.pack((record, snapshot))))
        for sid, cycle in sorted(history.ledger.items()):
            conn.execute("INSERT INTO signal_novelty VALUES (?, ?) ON CONFLICT(signal_id) "
                         "DO UPDATE SET payload=excluded.payload", (sid, sm.pack(cycle)))
        for key, (baseline, evidence) in sorted(baselines.items()):
            candidate, items, _ = evidence
            old = conn.execute("SELECT statement_id FROM signal_baselines WHERE baseline_key=?",
                               (key,)).fetchone()
            statement_id = old[0] if old else None
            if statement_id is None:
                source_id = items[candidate.supports[0].item_id].source_id
                statement_id = conn.execute(
                    "INSERT INTO [speaker_statements] (speaker, source_id, said_at, tone, text_hash) "
                    "VALUES (?, ?, ?, ?, ?)", (baseline.actor, source_id,
                    baseline.observed.isoformat(), baseline.kind, sm.digest(candidate.claim))).lastrowid
            snapshot = {i.source_id: sources[i.source_id] for i in items.values()}
            conn.execute("INSERT INTO signal_baselines VALUES (?, ?, ?, ?) "
                         "ON CONFLICT(baseline_key) DO UPDATE SET observed_day=excluded.observed_day, "
                         "payload=excluded.payload",
                         (key, statement_id, baseline.observed.isoformat(),
                          sm.pack(((baseline, evidence), snapshot))))
        _persist_signal_calendar(conn, cal, stamp, evidence_items)
        conn.execute("INSERT INTO signal_history_meta VALUES (1, ?, ?, ?) "
                     "ON CONFLICT(singleton) DO UPDATE SET header=excluded.header, "
                     "calendar=excluded.calendar, observed_at=excluded.observed_at",
                     (sm.pack(header), sm.pack(cal), stamp))
        conn.execute("INSERT INTO signal_runs VALUES (?, ?, ?)", (run_key, state_hash, stamp))
        conn.execute("RELEASE signal_write")
    except Exception:
        conn.execute("ROLLBACK TO signal_write")
        conn.execute("RELEASE signal_write")
        raise
def raw_records_payload(records):
    return tuple(vars(r) for r in records)
def _persist_signal_calendar(conn, calendar, stamp, items):
    """Mirror side channels into legacy tables with stable companion keys."""
    from agent.memory.signal_models import digest
    from agent.memory.models import to_utc_iso
    for kind in ("deadlines", "schedules", "statements"):
        for identity, entry in sorted(calendar[kind].items()):
            key = kind + ":" + identity
            old = conn.execute("SELECT schedule_id, statement_id FROM signal_calendar_rows "
                               "WHERE calendar_key=?", (key,)).fetchone()
            if kind == "statements":
                observed, candidate = entry
                if not old:
                    row_id = conn.execute(
                        "INSERT INTO [speaker_statements] (speaker, source_id, said_at, tone, text_hash) "
                        "VALUES (?, ?, ?, 'soothing', ?)", (candidate.actor,
                        items[candidate.supports[0].item_id].source_id, to_utc_iso(observed),
                        digest(candidate.claim))).lastrowid
                    conn.execute("INSERT INTO signal_calendar_rows VALUES (?, NULL, ?)", (key, row_id))
            else:
                status = "cancelled" if entry.cancelled else "active"
                if old:
                    conn.execute("UPDATE scheduled_events SET due_at=?, status=?, resolved_at=? "
                                 "WHERE id=?", (entry.due.isoformat(), status,
                                 stamp if entry.cancelled else None, old[0]))
                else:
                    row_id = conn.execute(
                        "INSERT INTO scheduled_events (title, kind, due_at, status, first_seen_at) "
                        "VALUES (?, ?, ?, ?, ?)", (entry.candidate.claim, kind,
                        entry.due.isoformat(), status, stamp)).lastrowid
                    conn.execute("INSERT INTO signal_calendar_rows VALUES (?, ?, NULL)", (key, row_id))
