"""SQLite-backed event store: survives restarts, shared by processes on one machine.

Same interface as MemoryStore. Not meant for multi-server deployments (use one
store per machine, or wait for a networked store).
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Callable, Hashable

from .store import MAX_EVENTS_PER_KEY, SWEEP_MIN_ADDS


class SQLiteStore:
    def __init__(self, path: str = ":memory:", clock: Callable[[], float] = time.time, retention: float = 30 * 86400.0,
                 timeout: float = 5.0):
        if not isinstance(path, (str, os.PathLike)):
            raise TypeError(f"path must be a str or path, not {type(path).__name__}")
        if not str(path).strip():
            raise ValueError("path must not be empty (SQLite would silently use a throwaway database); use ':memory:' on purpose")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be a positive number of seconds")
        self.clock = clock
        self.retention = retention
        self._lock = threading.RLock()
        self._adds_since_sweep = 0
        self._tx_depth = 0
        self._db = sqlite3.connect(str(path), timeout=float(timeout), check_same_thread=False, isolation_level=None)
        try:
            with self._lock:
                self._setup(str(path) != ":memory:", float(timeout))
                self._db.execute("PRAGMA busy_timeout=0")  # housekeeping never waits for another writer
                try:
                    self._sweep()
                except sqlite3.OperationalError as e:  # ...and must not stop startup
                    if "locked" not in str(e) and "busy" not in str(e):
                        raise
                finally:
                    self._db.execute(f"PRAGMA busy_timeout={int(timeout * 1000)}")
        except BaseException:
            self._db.close()  # do not leave a half-set-up connection open
            raise

    # --- helpers
    def _setup(self, wal: bool, timeout: float) -> None:
        """Switch to WAL and create the schema. SQLite does not wait on its busy handler while changing the
        journal mode, so several processes opening a brand-new file at once could fail with "database is
        locked" (found 2026-11-26: about 1 in 27 at 16 simultaneous opens). Retry until `timeout` runs out."""
        deadline = time.monotonic() + timeout
        delay = 0.005
        while True:
            try:
                self._db.execute(f"PRAGMA busy_timeout={int(timeout * 1000)}")
                if wal:
                    self._db.execute("PRAGMA journal_mode=WAL")
                self._db.execute("CREATE TABLE IF NOT EXISTS events (key TEXT NOT NULL, ts REAL NOT NULL, value TEXT)")
                self._db.execute("CREATE INDEX IF NOT EXISTS events_key_ts ON events (key, ts)")
                self._db.execute("CREATE INDEX IF NOT EXISTS events_ts ON events (ts)")
                return
            except sqlite3.OperationalError as e:
                if ("locked" not in str(e) and "busy" not in str(e)) or time.monotonic() + delay > deadline:
                    raise
                time.sleep(delay)
                delay = min(delay * 2, 0.25)

    def blind(self, value):
        """The identity: this store keeps raw values (see `BlindStore` to hide them)."""
        return value

    def _t(self, ts):
        try:
            ts = float(ts)
        except (TypeError, ValueError, OverflowError):
            return self.clock()
        return ts if math.isfinite(ts) else self.clock()

    @staticmethod
    def _enc(value) -> str:
        return json.dumps(value, default=str, sort_keys=True)

    def _sweep(self) -> None:
        """Delete every expired row, including keys that never come back (trimming only the key being
        written would let the file grow forever). Runs on open and every SWEEP_MIN_ADDS adds. Uses the
        clock, not one event's timestamp, so a single odd timestamp cannot wipe the history."""
        self._adds_since_sweep = 0
        self._db.execute("DELETE FROM events WHERE ts < ?", (self.clock() - self.retention,))

    @contextmanager
    def transaction(self):
        """Take the file's write lock for a whole read-then-write sequence, so other threads and other
        processes sharing the file wait instead of reading the same state. Re-entrant."""
        with self._lock:
            outer = self._tx_depth == 0
            if outer:
                self._db.execute("BEGIN IMMEDIATE")
            self._tx_depth += 1
            try:
                yield
            except BaseException:
                self._tx_depth -= 1
                if outer:
                    self._db.execute("ROLLBACK")
                raise
            else:
                self._tx_depth -= 1
                if outer:
                    self._db.execute("COMMIT")

    # --- interface
    def add(self, key: str, value: Hashable = None, ts: float | None = None) -> None:
        ts = self._t(self.clock() if ts is None else ts)
        with self.transaction():
            db = self._db
            db.execute("INSERT INTO events (key, ts, value) VALUES (?, ?, ?)", (key, ts, self._enc(value)))
            db.execute("DELETE FROM events WHERE key = ? AND ts < ?", (key, ts - self.retention))
            db.execute(
                "DELETE FROM events WHERE key = ? AND rowid NOT IN "
                "(SELECT rowid FROM events WHERE key = ? ORDER BY ts DESC, rowid DESC LIMIT ?)",
                (key, key, MAX_EVENTS_PER_KEY))
            self._adds_since_sweep += 1
            if self._adds_since_sweep >= SWEEP_MIN_ADDS:
                self._sweep()

    def _rows(self, key: str, window: float, now):
        now = self._t(self.clock() if now is None else now)
        with self._lock:
            return [r[0] for r in self._db.execute(
                "SELECT value FROM events WHERE key = ? AND ts >= ? AND ts <= ?", (key, now - window, now))]

    def count(self, key: str, window: float, now: float | None = None) -> int:
        return len(self._rows(key, window, now))

    def distinct(self, key: str, window: float, now: float | None = None) -> int:
        return len(set(self._rows(key, window, now)))

    def values(self, key: str, window: float, now: float | None = None) -> set:
        return {json.loads(v) for v in self._rows(key, window, now)}

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
