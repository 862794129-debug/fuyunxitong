"""Persistence for pre-check runs and the platform's real review outcomes.

The whole point of this module is the feedback loop: every pre-check result is
stored next to what the platform actually decided, so the rule list can be
calibrated with data instead of opinion. Stdlib sqlite3 only.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Iterable

from .engine import CheckResult, VERDICT_NONE

PLATFORM_STATUSES = ("approved", "rejected")

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    content_id    TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    rules_version TEXT NOT NULL,
    verdict       TEXT NOT NULL,
    categories    TEXT NOT NULL,
    result_json   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runs_content ON runs(content_id);
CREATE TABLE IF NOT EXISTS outcomes (
    outcome_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    content_id    TEXT NOT NULL,
    status        TEXT NOT NULL CHECK (status IN ('approved','rejected')),
    reasons       TEXT NOT NULL,
    recorded_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_outcomes_content ON outcomes(content_id);
"""


def connect(path: str = ":memory:") -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def save_run(conn: sqlite3.Connection, result: CheckResult) -> int:
    cur = conn.execute(
        "INSERT INTO runs (content_id, created_at, rules_version, verdict, categories, result_json) "
        "VALUES (?,?,?,?,?,?)",
        (
            result.content_id,
            result.checked_at,
            result.rules_version,
            result.verdict,
            json.dumps(result.categories(), ensure_ascii=False),
            json.dumps(result.to_dict(), ensure_ascii=False),
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def record_outcome(
    conn: sqlite3.Connection,
    content_id: str,
    status: str,
    reasons: Iterable[str] = (),
    recorded_at: str | None = None,
) -> int:
    if status not in PLATFORM_STATUSES:
        raise ValueError(f"status must be one of {PLATFORM_STATUSES}, got {status!r}")
    reasons = [r.strip() for r in reasons if r and r.strip()]
    if status == "approved" and reasons:
        raise ValueError("an approved outcome cannot carry rejection reasons")
    cur = conn.execute(
        "INSERT INTO outcomes (content_id, status, reasons, recorded_at) VALUES (?,?,?,?)",
        (
            content_id,
            status,
            json.dumps(reasons, ensure_ascii=False),
            recorded_at or datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def _ratio(num: int, den: int):
    return round(num / den, 4) if den else None


def agreement_report(conn: sqlite3.Connection) -> dict:
    """Compare the latest pre-check run with the latest platform outcome per content_id.

    'flagged' = verdict other than no_issues_found. Only content that has BOTH a
    run and an outcome is counted. With few labelled items the numbers are noise;
    the report says how many it used so nobody over-reads it.
    """
    rows = conn.execute(
        """
        SELECT r.content_id, r.verdict, r.categories, o.status, o.reasons
        FROM runs r
        JOIN (SELECT content_id, MAX(run_id) AS run_id FROM runs GROUP BY content_id) lr
          ON r.run_id = lr.run_id
        JOIN outcomes o ON o.content_id = r.content_id
        JOIN (SELECT content_id, MAX(outcome_id) AS outcome_id FROM outcomes GROUP BY content_id) lo
          ON o.outcome_id = lo.outcome_id
        """
    ).fetchall()

    tp = fp = fn = tn = 0
    per_reason: dict[str, dict] = {}
    for row in rows:
        flagged = row["verdict"] != VERDICT_NONE
        rejected = row["status"] == "rejected"
        if flagged and rejected:
            tp += 1
        elif flagged and not rejected:
            fp += 1
        elif not flagged and rejected:
            fn += 1
        else:
            tn += 1
        if rejected:
            predicted = set(json.loads(row["categories"]))
            for reason in json.loads(row["reasons"]):
                bucket = per_reason.setdefault(reason, {"rejected": 0, "caught_same_category": 0})
                bucket["rejected"] += 1
                if reason in predicted:
                    bucket["caught_same_category"] += 1

    n = tp + fp + fn + tn
    for reason, b in per_reason.items():
        b["catch_rate"] = _ratio(b["caught_same_category"], b["rejected"])

    return {
        "labelled_items": n,
        "platform_rejection_rate": _ratio(tp + fn, n),
        "flagged_and_rejected": tp,
        "flagged_but_approved": fp,
        "not_flagged_but_rejected": fn,
        "not_flagged_and_approved": tn,
        "precision": _ratio(tp, tp + fp),
        "recall": _ratio(tp, tp + fn),
        "per_platform_reason": per_reason,
        "warning": "Small samples are noise. Do not tune rules on fewer than ~100 labelled items per category.",
    }
