"""Committed, append-only solver observations, independent of final results.

Each callback commits one observation. A killed worker may lose its current
uncommitted observation, but never the preceding accepted history. Terminal
job status and the normal/error completion observation share a transaction.
"""
import json
import math
import numbers
import time


JOB_FIELDS = "id,project_id,revision,status,created,started,finished,error"


def finite_json(value):
    """Keep unavailable numerical values explicit instead of emitting NaN/Inf."""
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, numbers.Integral):
        return int(value)
    if isinstance(value, numbers.Real):
        return float(value) if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(key): finite_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [finite_json(item) for item in value]
    raise TypeError(f"Unsupported monitor value: {type(value).__name__}")


def insert_event(db, job_id, event):
    """Insert using a caller-owned write transaction; never update old rows."""
    payload = finite_json(event)
    if not isinstance(payload, dict):
        raise TypeError("Monitor event must be an object")
    # Sequence and receipt time belong to the durable recorder, not the solver.
    payload.pop("seq", None)
    payload.pop("recorded", None)
    payload["schema_version"] = 1
    seq = db.execute("SELECT COALESCE(MAX(seq),0)+1 FROM job_monitor_events WHERE job_id=?",
                     (job_id,)).fetchone()[0]
    db.execute("INSERT INTO job_monitor_events(job_id,seq,recorded,event) VALUES(?,?,?,?)",
               (job_id, seq, time.time(), json.dumps(payload, ensure_ascii=False, allow_nan=False)))
    return seq


def append_event(store, job_id, event):
    """Serialize callbacks against cancellation; a terminal job is immutable."""
    with store.connect(immediate=True) as db:
        if not db.execute("SELECT 1 FROM jobs WHERE id=? AND kind='design' AND status='running'",
                          (job_id,)).fetchone():
            return None
        return insert_event(db, job_id, event)


def task_event(kind, *, status, reason=None):
    event = {"schema_version": 1, "kind": kind, "layer": "task", "run_id": "task",
             "status": status, "values": {}, "units": {}, "context": {}}
    if reason:
        event["termination_reason"] = reason
    return event


def monitor_page(store, job_id, *, after=0, limit=500):
    """A consistent cursor page, including terminal status from the same snapshot.

    The reader must drain has_more even after a terminal status. Old jobs with
    no observations are returned as empty; final summaries are never converted
    into synthetic iteration histories.
    """
    if isinstance(after, bool) or not isinstance(after, int) or not 0 <= after <= 9223372036854775807:
        raise ValueError("after must be a nonnegative SQLite integer")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 2000:
        raise ValueError("limit must be between 1 and 2000")
    with store.connect() as db:
        db.execute("BEGIN")
        row = db.execute(f"SELECT {JOB_FIELDS} FROM jobs WHERE id=? AND kind='design'", (job_id,)).fetchone()
        if row is None:
            return None
        rows = db.execute("SELECT seq,recorded,event FROM job_monitor_events "
                          "WHERE job_id=? AND seq>? ORDER BY seq LIMIT ?",
                          (job_id, after, limit + 1)).fetchall()
        events = [json.loads(item["event"]) | {"seq": item["seq"], "recorded": item["recorded"]}
                  for item in rows[:limit]]
        return {"schema_version": 1, "job": dict(row), "events": events,
                "next_seq": events[-1]["seq"] if events else after, "has_more": len(rows) > limit}
