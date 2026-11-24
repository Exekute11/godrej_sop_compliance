"""SQLite persistence (thread-safe: one short-lived connection per call, global write lock)."""
import json
import os
import sqlite3
import threading
from contextlib import contextmanager

_WRITE_LOCK = threading.RLock()
DB_PATH = os.environ.get("SOP_DB_PATH") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "sop_monitor.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL, full_name TEXT,
  password_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'operator', active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT, last_login TEXT);
CREATE TABLE IF NOT EXISTS shifts(
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, start_ts TEXT NOT NULL, end_ts TEXT,
  started_by TEXT, summary TEXT);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, camera_id TEXT, etype TEXT NOT NULL,
  severity TEXT NOT NULL, message TEXT, cycle_no INTEGER, shift_id INTEGER, screenshot TEXT);
CREATE INDEX IF NOT EXISTS ix_events_cam ON events(camera_id, id);
CREATE TABLE IF NOT EXISTS cycles(
  id INTEGER PRIMARY KEY AUTOINCREMENT, camera_id TEXT, shift_id INTEGER, cycle_no INTEGER,
  start_ts TEXT, end_ts TEXT, duration REAL, result TEXT, violations TEXT);
CREATE TABLE IF NOT EXISTS audit(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, username TEXT, action TEXT NOT NULL,
  details TEXT, ip TEXT);
"""


@contextmanager
def connect():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with _WRITE_LOCK, connect() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(SCHEMA)


def execute(sql, params=()):
    with _WRITE_LOCK, connect() as c:
        cur = c.execute(sql, params)
        return cur.lastrowid


def query(sql, params=()):
    with connect() as c:
        return [dict(r) for r in c.execute(sql, params).fetchall()]


def query_one(sql, params=()):
    rows = query(sql, params)
    return rows[0] if rows else None


def dumps(o):
    return json.dumps(o, ensure_ascii=False)
