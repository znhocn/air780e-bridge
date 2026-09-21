"""SQLite storage layer."""

import os
import sqlite3
import threading
from contextlib import contextmanager

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    direction  TEXT NOT NULL,                    -- in / out
    sender     TEXT,                             -- number at receive time
    receiver   TEXT,                             -- target number for outbound
    content    TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'stored',  -- in: stored; out: queued/sending/sent/failed
    raw        TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- Notify configs (DingTalk/WeCom/Feishu/Email go via Apprise; Webhook is a direct HTTP POST)
CREATE TABLE IF NOT EXISTS notify_configs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT NOT NULL,
    type           TEXT NOT NULL,               -- dingtalk/wecom/feishu/telegram/email/webhook/apprise
    enabled        INTEGER NOT NULL DEFAULT 1,
    match_from     TEXT NOT NULL DEFAULT '',
    match_contains TEXT NOT NULL DEFAULT '',
    params         TEXT NOT NULL DEFAULT '{}',  -- JSON, depends on channel type
    created_at     TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS forward_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id  INTEGER,
    sender      TEXT,
    content     TEXT,
    rule_name   TEXT,          -- notify config name
    webhook_url TEXT,          -- target address (masked)
    channel     TEXT,          -- channel type
    success     INTEGER NOT NULL DEFAULT 0,
    status_code INTEGER,
    error       TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS api_keys (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    key_hash   TEXT NOT NULL UNIQUE,
    key_prefix TEXT NOT NULL,
    active     INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    last_used  TEXT
);

CREATE TABLE IF NOT EXISTS admins (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    salt          TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- Scheduled SMS tasks (reference: interval-based periodic sending)
CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT NOT NULL,
    enabled        INTEGER NOT NULL DEFAULT 1,
    interval_days  INTEGER NOT NULL DEFAULT 7,
    phone          TEXT NOT NULL,
    content        TEXT NOT NULL,
    last_run_at    TEXT,
    last_status    TEXT NOT NULL DEFAULT 'never',  -- never / running / success / failed
    last_msg_id    INTEGER,
    created_at     TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- Contacts (chat names shown on the SMS page)
CREATE TABLE IF NOT EXISTS contacts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    phone      TEXT NOT NULL UNIQUE,
    note       TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS idx_messages_sender_content ON messages(sender, content);
CREATE INDEX IF NOT EXISTS idx_messages_receiver ON messages(receiver);
CREATE INDEX IF NOT EXISTS idx_messages_created_at ON messages(created_at);
CREATE INDEX IF NOT EXISTS idx_forward_logs_message_id ON forward_logs(message_id);
CREATE INDEX IF NOT EXISTS idx_forward_logs_created_at ON forward_logs(created_at);
"""


class Database:
    def __init__(self, path: str):
        self.path = path
        self._local = threading.local()
        self._write_lock = threading.Lock()
        parent = os.path.dirname(os.path.abspath(path))
        os.makedirs(parent, exist_ok=True)
        with sqlite3.connect(path) as con:
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA journal_mode=WAL")
            con.executescript(SCHEMA)
            con.commit()

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA synchronous=NORMAL")
            self._local.conn = conn
        return conn

    @contextmanager
    def cursor(self, *, write: bool = False):
        """Serializes write ops; reads are concurrency-safe under WAL."""
        if write:
            self._write_lock.acquire()
        try:
            cur = self._conn().cursor()
            yield cur
            if write:
                self._conn().commit()
        finally:
            if write:
                self._write_lock.release()

    def rows(self, sql: str, params=()):
        with self.cursor() as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]

    def row(self, sql: str, params=()):
        with self.cursor() as cur:
            cur.execute(sql, params)
            r = cur.fetchone()
            return dict(r) if r else None

    def execute(self, sql: str, params=()):
        with self.cursor(write=True) as cur:
            cur.execute(sql, params)
            return cur.lastrowid