"""SQLite: наборы, записи, планы и аудит."""

import json
import os
import sqlite3
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def utcnow() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def db_path() -> Path:
    raw = os.environ.get("DATABASE_PATH", "data/scout.db")
    path = Path(raw)
    if not path.is_absolute():
        path = ROOT / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS datasets (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                name TEXT NOT NULL,
                source TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                dataset_id TEXT NOT NULL,
                record_json TEXT NOT NULL,
                FOREIGN KEY (dataset_id) REFERENCES datasets(id)
            );

            CREATE TABLE IF NOT EXISTS agent_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                query TEXT NOT NULL,
                plan_json TEXT,
                needs_review INTEGER NOT NULL,
                error TEXT
            );

            CREATE TABLE IF NOT EXISTS audit_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                action TEXT NOT NULL,
                input TEXT,
                output TEXT,
                status TEXT NOT NULL,
                error TEXT,
                duration_ms INTEGER NOT NULL
            );
            """
        )


def _dump(value) -> str:
    text = json.dumps(value, ensure_ascii=False)
    for name in ("GNEWS_API_KEY", "OPENAI_API_KEY"):
        secret = os.environ.get(name, "").strip()
        if len(secret) >= 8:
            text = text.replace(secret, "***")
    return text


def insert_dataset(name: str, source: str) -> str:
    dataset_id = str(uuid.uuid4())
    with connect() as conn:
        conn.execute(
            "INSERT INTO datasets (id, created_at, name, source) VALUES (?, ?, ?, ?)",
            (dataset_id, utcnow(), name, source),
        )
    return dataset_id


def list_datasets() -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, created_at, name, source FROM datasets ORDER BY created_at DESC"
        ).fetchall()
    return [
        {
            "dataset_id": row["id"],
            "created_at": row["created_at"],
            "name": row["name"],
            "source": row["source"],
        }
        for row in rows
    ]


def get_dataset(dataset_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT id, created_at, name, source FROM datasets WHERE id = ?",
            (dataset_id,),
        ).fetchone()
    if row is None:
        return None
    return {
        "dataset_id": row["id"],
        "created_at": row["created_at"],
        "name": row["name"],
        "source": row["source"],
    }


def insert_records(dataset_id: str, records: list[dict]) -> int:
    now = utcnow()
    with connect() as conn:
        conn.executemany(
            "INSERT INTO records (created_at, dataset_id, record_json) VALUES (?, ?, ?)",
            [(now, dataset_id, _dump(item)) for item in records],
        )
    return len(records)


def list_records(dataset_id: str, limit: int) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT id, created_at, dataset_id, record_json
            FROM records
            WHERE dataset_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (dataset_id, limit),
        ).fetchall()
    result = []
    for row in rows:
        result.append(
            {
                "id": row["id"],
                "created_at": row["created_at"],
                "dataset_id": row["dataset_id"],
                "record_json": json.loads(row["record_json"]),
            }
        )
    return result


def insert_agent_run(query: str, plan: dict, needs_review: bool, error: str | None) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO agent_runs (created_at, query, plan_json, needs_review, error)
            VALUES (?, ?, ?, ?, ?)
            """,
            (utcnow(), query, _dump(plan), 1 if needs_review else 0, error),
        )


def list_agent_runs(limit: int) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT id, created_at, query, plan_json, needs_review, error
            FROM agent_runs
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    result = []
    for row in rows:
        result.append(
            {
                "id": row["id"],
                "created_at": row["created_at"],
                "query": row["query"],
                "plan_json": json.loads(row["plan_json"]) if row["plan_json"] else None,
                "needs_review": bool(row["needs_review"]),
                "error": row["error"],
            }
        )
    return result


def write_audit(action: str, payload, output, status: str, error: str | None, started: float) -> None:
    duration_ms = max(0, int((time.perf_counter() - started) * 1000))
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO audit_runs
                (created_at, action, input, output, status, error, duration_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (utcnow(), action, _dump(payload), _dump(output), status, error, duration_ms),
        )


def list_audit(limit: int) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT id, created_at, action, input, output, status, error, duration_ms
            FROM audit_runs
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    result = []
    for row in rows:
        result.append(
            {
                "id": row["id"],
                "created_at": row["created_at"],
                "action": row["action"],
                "input": json.loads(row["input"]) if row["input"] else None,
                "output": json.loads(row["output"]) if row["output"] else None,
                "status": row["status"],
                "error": row["error"],
                "duration_ms": row["duration_ms"],
            }
        )
    return result
