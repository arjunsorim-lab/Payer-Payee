"""Transactional review decisions and audit events for a single deployment."""
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connection(path):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Create the file with restrictive permissions before SQLite opens it.
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    os.close(fd)
    os.chmod(path, 0o600)
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript("""
            CREATE TABLE IF NOT EXISTS reviews (
                review_id TEXT PRIMARY KEY, version INTEGER NOT NULL, payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS audit (
                id INTEGER PRIMARY KEY, at TEXT NOT NULL, actor TEXT NOT NULL,
                event TEXT NOT NULL, resource TEXT NOT NULL, detail TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS audit_lookup ON audit(event, resource, at);
        """)
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def audit(db, actor, event, resource, detail=None):
    db.execute("INSERT INTO audit(at,actor,event,resource,detail) VALUES(?,?,?,?,?)",
               (now(), actor, event, resource, json.dumps(detail or {})))


def read_reviews(path, ids):
    if not ids:
        return {}
    with connection(path) as db:
        rows = db.execute(f"SELECT review_id,payload FROM reviews WHERE review_id IN ({','.join('?' for _ in ids)})", ids)
        return {row["review_id"]: json.loads(row["payload"]) for row in rows}


TRANSITIONS = {
    "pending": {"accepted", "rejected", "deferred"},
    "accepted": {"accepted", "rejected", "deferred", "completed"},
    "deferred": {"accepted", "rejected", "deferred"},
    "rejected": {"accepted", "deferred", "rejected"},
    "completed": {"outcome_recorded"},
    "outcome_recorded": {"outcome_recorded"},
}


def save_review(path, review_id, data, actor, plan):
    allowed = {"version", "status", "reason", "assignee", "due_date", "completed_date", "outcome_date", "outcome", "outcome_notes"}
    if not isinstance(data, dict) or set(data) - allowed:
        raise ValueError("Unsupported review fields.")
    for key in allowed - {"version"}:
        if key in data and (not isinstance(data[key], str) or len(data[key]) > 2000):
            raise ValueError("Review fields must be text of at most 2,000 characters.")
    if type(data.get("version")) is not int:
        raise ValueError("Supply the current review version.")
    status = data.get("status")
    reason = data.get("reason", "").strip()
    if len(reason) < 3:
        raise ValueError("Record a reason for this decision.")
    with connection(path) as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT version,payload FROM reviews WHERE review_id=?", (review_id,)).fetchone()
        current = json.loads(row["payload"]) if row else {"status": "pending", "version": 0}
        if data["version"] != current["version"]:
            raise RuntimeError("This review changed. Reload before saving.")
        if status not in TRANSITIONS[current["status"]]:
            raise ValueError("This status transition is not allowed.")
        if status in {"accepted", "completed"} and not plan["available"]:
            raise ValueError("This plan needs additional clinical evidence before acceptance.")
        updated = {**current, **data, "reason": reason}
        if status in {"accepted", "deferred"} and not updated.get("assignee", "").strip():
            raise ValueError("Assign the review to a responsible person.")
        if status == "deferred" and not updated.get("due_date"):
            raise ValueError("Set a date to revisit the deferred decision.")
        for key in ("due_date", "completed_date", "outcome_date"):
            value = updated.get(key)
            if value:
                try:
                    parsed = date.fromisoformat(value)
                except ValueError:
                    raise ValueError("Dates must use YYYY-MM-DD.") from None
                if key != "due_date" and parsed > date.today():
                    raise ValueError("Completed care and observed outcomes cannot be future-dated.")
        if status == "completed" and not updated.get("completed_date"):
            raise ValueError("Record when the intervention was completed.")
        if updated.get("completed_date") and updated["completed_date"] < (plan.get("anchor_service_date") or ""):
            raise ValueError("Completion cannot precede the source claim.")
        if status == "outcome_recorded":
            if updated.get("outcome") not in {"improved", "unchanged", "worsened", "unknown"}:
                raise ValueError("Select the observed outcome.")
            if not updated.get("outcome_notes", "").strip() or not updated.get("outcome_date"):
                raise ValueError("Record outcome evidence and its observation date.")
            if updated["outcome_date"] < updated.get("completed_date", ""):
                raise ValueError("The outcome cannot precede intervention completion.")
        # A reviewer observation is not causal proof or a verified savings claim.
        updated.update(version=current["version"] + 1, updated_by=actor, updated_at=now(),
                       financial_status="unverified", verified_savings=None,
                       evidence_type="reviewer_reported", source_data_type=plan.get("source_data_type"))
        db.execute("INSERT INTO reviews VALUES(?,?,?) ON CONFLICT(review_id) DO UPDATE SET version=excluded.version,payload=excluded.payload",
                   (review_id, updated["version"], json.dumps(updated)))
        audit(db, actor, "review_updated", review_id, {"before": current, "after": updated})
        return updated
