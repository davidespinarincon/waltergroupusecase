"""SQLite persistence: emails, threads (open requests) and order history."""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS emails (
    message_id   TEXT PRIMARY KEY,
    thread_id    TEXT NOT NULL,
    sender       TEXT NOT NULL,
    subject      TEXT,
    body         TEXT NOT NULL,
    received_at  TEXT NOT NULL,
    in_reply_to  TEXT,
    raw_json     TEXT NOT NULL,
    status       TEXT NOT NULL,          -- processed | pending_retry
    decision     TEXT,
    result_json  TEXT,
    processed_at TEXT
);
CREATE TABLE IF NOT EXISTS threads (
    thread_id         TEXT PRIMARY KEY,
    customer          TEXT NOT NULL,
    state             TEXT NOT NULL,     -- awaiting_customer | pending_retry | quoted | handed_over | closed
    requests_json     TEXT,              -- current (possibly partial) transport requests
    language          TEXT,
    first_failure_at  TEXT,              -- first pricing failure (for the 30 min rule)
    updated_at        TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS orders (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    customer    TEXT NOT NULL,
    origin      TEXT NOT NULL,
    destination TEXT NOT NULL,
    weight_kg   REAL,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS deliveries (   -- every inbound delivery, incl. duplicates (= the inbox)
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id   TEXT NOT NULL,
    duplicate    INTEGER NOT NULL,
    result_json  TEXT,
    at           TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id  TEXT,
    kind        TEXT NOT NULL,
    detail      TEXT,
    at          TEXT NOT NULL
);
"""


class Store:
    def __init__(self, path: str = ":memory:"):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.conn.executescript(SCHEMA)

    # -- generic helpers --
    def _one(self, sql, args=()):
        with self.lock:
            row = self.conn.execute(sql, args).fetchone()
        return dict(row) if row else None

    def _all(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def _exec(self, sql, args=()):
        with self.lock, self.conn:
            self.conn.execute(sql, args)

    # -- emails --
    def get_email(self, message_id: str):
        return self._one("SELECT * FROM emails WHERE message_id=?", (message_id,))

    def add_delivery(self, message_id: str, duplicate: bool, result: dict | None, at: str):
        self._exec("INSERT INTO deliveries (message_id, duplicate, result_json, at) VALUES (?,?,?,?)",
                   (message_id, int(duplicate), json.dumps(result, ensure_ascii=False) if result else None, at))

    def inbox(self):
        """One row per delivery; originals show the email's current decision, duplicates show IGNORE."""
        return self._all(
            """SELECT d.id AS delivery_id, d.duplicate, d.at AS delivered_at, e.*,
                      CASE WHEN d.duplicate=1 THEN d.result_json ELSE e.result_json END AS shown_result,
                      CASE WHEN d.duplicate=1 THEN 'IGNORE' ELSE e.decision END AS shown_decision
               FROM deliveries d JOIN emails e ON e.message_id = d.message_id
               ORDER BY d.id""")

    def save_email(self, email, thread_id: str, status: str, decision: str | None,
                   result: dict | None, processed_at: str):
        self._exec(
            """INSERT INTO emails (message_id, thread_id, sender, subject, body, received_at, in_reply_to,
                                   raw_json, status, decision, result_json, processed_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(message_id) DO UPDATE SET status=excluded.status, decision=excluded.decision,
                   result_json=excluded.result_json, processed_at=excluded.processed_at""",
            (email.message_id, thread_id, email.sender, email.subject, email.body,
             email.received_at.isoformat(), email.in_reply_to, email.model_dump_json(),
             status, decision, json.dumps(result, ensure_ascii=False) if result else None, processed_at))

    def list_emails(self):
        return self._all("SELECT * FROM emails ORDER BY received_at, rowid")

    def pending_retry(self):
        return self._all("SELECT * FROM emails WHERE status='pending_retry' ORDER BY received_at")

    def thread_of(self, message_ids: list[str]) -> str | None:
        for mid in message_ids:
            row = self._one("SELECT thread_id FROM emails WHERE message_id=?", (mid,))
            if row:
                return row["thread_id"]
        return None

    # -- threads --
    def get_thread(self, thread_id: str):
        t = self._one("SELECT * FROM threads WHERE thread_id=?", (thread_id,))
        if t and t["requests_json"]:
            t["requests"] = json.loads(t["requests_json"])
        return t

    def upsert_thread(self, thread_id: str, customer: str, state: str, requests: list[dict] | None,
                      language: str | None, updated_at: str, first_failure_at: str | None = None):
        self._exec(
            """INSERT INTO threads (thread_id, customer, state, requests_json, language, first_failure_at, updated_at)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(thread_id) DO UPDATE SET state=excluded.state, requests_json=excluded.requests_json,
                   language=excluded.language, first_failure_at=excluded.first_failure_at,
                   updated_at=excluded.updated_at""",
            (thread_id, customer, state, json.dumps(requests, ensure_ascii=False) if requests is not None else None,
             language, first_failure_at, updated_at))

    # -- orders --
    def last_order(self, customer: str):
        return self._one("SELECT * FROM orders WHERE lower(customer)=lower(?) ORDER BY created_at DESC, id DESC LIMIT 1",
                         (customer,))

    def add_order(self, customer: str, origin: str, destination: str, weight_kg: float | None, created_at: str):
        self._exec("INSERT INTO orders (customer, origin, destination, weight_kg, created_at) VALUES (?,?,?,?,?)",
                   (customer, origin, destination, weight_kg, created_at))

    # -- events --
    def log(self, message_id: str | None, kind: str, detail: str, at: str):
        self._exec("INSERT INTO events (message_id, kind, detail, at) VALUES (?,?,?,?)", (message_id, kind, detail, at))

    def events(self, limit: int = 200):
        return self._all("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))

    def reset(self):
        with self.lock, self.conn:
            for t in ("emails", "deliveries", "threads", "events"):  # order history (master data) is kept
                self.conn.execute(f"DELETE FROM {t}")
