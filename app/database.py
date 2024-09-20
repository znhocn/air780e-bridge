"""SQLite 存储层"""

import json
import os
import sqlite3
import threading
from contextlib import contextmanager

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    direction  TEXT NOT NULL,                    -- in / out
    sender     TEXT,                             -- 接收时号码
    receiver   TEXT,                             -- 发送目标号码
    content    TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'stored',  -- in: stored; out: queued/sending/sent/failed
    raw        TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- 通知配置（钉钉/企微/飞书/Email 走 Apprise；Webhook 走独立 HTTP POST）
CREATE TABLE IF NOT EXISTS notify_configs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT NOT NULL,
    type           TEXT NOT NULL,               -- dingtalk/wecom/feishu/email/webhook
    enabled        INTEGER NOT NULL DEFAULT 1,
    match_from     TEXT NOT NULL DEFAULT '',
    match_contains TEXT NOT NULL DEFAULT '',
    params         TEXT NOT NULL DEFAULT '{}',  -- JSON，随渠道类型不同
    created_at     TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS forward_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id  INTEGER,
    sender      TEXT,
    content     TEXT,
    rule_name   TEXT,          -- 通知配置名称
    webhook_url TEXT,          -- 目标地址（脱敏展示）
    channel     TEXT,          -- 渠道类型
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
"""

MIGRATIONS = [
    (
        "api_keys",
        "last_used",
        "ALTER TABLE api_keys ADD COLUMN last_used TEXT",
    ),
    (
        "forward_logs",
        "channel",
        "ALTER TABLE forward_logs ADD COLUMN channel TEXT",
    ),
]


def _migrate(con: sqlite3.Connection):
    for table, col, ddl in MIGRATIONS:
        cols = [r[1] for r in con.execute(f"PRAGMA table_info({table})").fetchall()]
        if col not in cols:
            con.execute(ddl)
    # 旧版 forward_rules → 迁移为 webhook 类型的通知配置
    has_old = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='forward_rules'"
    ).fetchone()
    if has_old:
        legacy = con.execute("SELECT * FROM forward_rules").fetchall()
        cur = con.execute("SELECT COUNT(*) FROM notify_configs").fetchone()[0]
        if cur == 0:
            for r in legacy:
                params = json.dumps({"url": r["webhook_url"]}, ensure_ascii=False)
                con.execute(
                    "INSERT INTO notify_configs "
                    "(name, type, enabled, match_from, match_contains, params) "
                    "VALUES (?,?,?,?,?,?)",
                    (r["name"], "webhook", r["active"], r["match_from"], r["match_contains"], params),
                )
        con.execute("DROP TABLE forward_rules")


class Database:
    def __init__(self, path: str):
        self.path = path
        self._local = threading.local()
        self._write_lock = threading.Lock()
        parent = os.path.dirname(os.path.abspath(path))
        os.makedirs(parent, exist_ok=True)
        with sqlite3.connect(path) as con:
            con.row_factory = sqlite3.Row
            con.executescript(SCHEMA)
            _migrate(con)
            con.commit()

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn = conn
        return conn

    @contextmanager
    def cursor(self, *, write: bool = False):
        """串行化写操作，读操作并发安全（WAL）。"""
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