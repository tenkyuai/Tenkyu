"""Postgres-backed event store: survives restarts and is shared by every server that uses the same database.

Same interface and semantics as MemoryStore and SQLiteStore. Needs the optional driver:
`pip install "tenkyu[postgres]"`. The driver (psycopg 3) is imported when a store is created, never at
`import tenkyu`, so the core package stays dependency-free.

Many customers can share one database: give each its own `prefix`. Every row carries its prefix and every
query filters on it with equality, so one customer can never read, count, sweep or lock another's events.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import threading
import time
from contextlib import contextmanager
from typing import Callable, Hashable

from .store import MAX_EVENTS_PER_KEY, SWEEP_MIN_ADDS, Blinder

_TABLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,40}$")
_MAX_PREFIX_CHARS = 200
_MAX_RAW_KEY_BYTES = 256  # longer keys are stored as a SHA-256 digest; B-tree entries are limited to ~2.7 kB


def _import_psycopg():
    try:
        import psycopg
        from psycopg import errors, sql
    except ImportError as e:
        raise ImportError(
            "PostgresStore needs the psycopg driver. Install it with: pip install \"tenkyu[postgres]\" "
            "(or pip install \"psycopg[binary]>=3.1\")") from e
    return psycopg, errors, sql


def _lock_id(*parts: str) -> int:
    """A stable signed 64-bit advisory-lock id for a name. Two names colliding only costs extra waiting."""
    digest = hashlib.sha256("\0".join(parts).encode("utf-8", "surrogatepass")).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


class PostgresStore:
    def __init__(self, dsn: str, prefix: str = "", clock: Callable[[], float] = time.time,
                 retention: float = 30 * 86400.0, timeout: float = 5.0, table: str = "tenkyu_events",
                 key_secret: bytes | None = None, connection=None, allow_raw_keys: bool = False):
        """`dsn`: a libpq connection string or URL. `prefix`: the namespace, e.g. one per customer (stores with
        different prefixes never see each other's events). `timeout`: seconds to wait to connect and for the
        store's lock before raising `psycopg.OperationalError`. `table`: name of the events table.

        `key_secret`: at least 16 secret bytes. When given, the variable part of every key and every value is
        stored as HMAC-SHA256(key_secret, ...) (hex, 32 characters), so the table holds no raw email, IP or device
        id; the kind before the first ":" stays readable. `values()` then returns blinded values, and
        `store.blind(raw)` maps a raw value to that form (Guard calls it). Keep the secret stable and out of the
        database. See `tenkyu.store.BlindStore`. Without `key_secret` the table would hold raw emails, IPs and
        device ids, so the store refuses to start unless you pass `allow_raw_keys=True` (for a throwaway database
        or a test). Generate a secret once with `python -c "import secrets; print(secrets.token_hex(32))"`, keep it
        in an environment variable or secret manager, and pass `key_secret=os.environ["TENKYU_KEY_SECRET"].encode()`.

        `connection`: borrow an open psycopg connection (for example from a `psycopg_pool` pool) instead of
        opening one; pass `dsn=None`. It must be in autocommit mode. The store neither creates the table nor
        sweeps on open (the table must exist), never reconnects, and `close()` leaves the connection open."""
        if key_secret is None and not allow_raw_keys:
            raise ValueError(
                "PostgresStore needs key_secret: without it the table stores raw emails, IP addresses and device ids "
                "as keys. Generate one with `python -c \"import secrets; print(secrets.token_hex(32))\"`, keep it in an "
                "environment variable, and pass key_secret=os.environ[\"TENKYU_KEY_SECRET\"].encode(). To store raw "
                "values anyway (a throwaway database, a test), pass allow_raw_keys=True.")
        if connection is not None:
            if dsn is not None:
                raise ValueError("pass either dsn or connection, not both")
            if not getattr(connection, "autocommit", False):
                raise ValueError("a borrowed connection must be in autocommit mode")
        elif not isinstance(dsn, str):
            raise TypeError(f"dsn must be a str, not {type(dsn).__name__}")
        elif not dsn.strip():
            raise ValueError("dsn must not be empty (libpq would silently use the PG* environment variables)")
        if not isinstance(prefix, str):
            raise TypeError(f"prefix must be a str, not {type(prefix).__name__}")
        if len(prefix) > _MAX_PREFIX_CHARS or "\x00" in prefix:
            raise ValueError(f"prefix must be at most {_MAX_PREFIX_CHARS} characters and contain no NUL")
        try:
            prefix.encode("utf-8")
        except UnicodeEncodeError:
            raise ValueError("prefix must be valid Unicode text (no lone surrogates)") from None
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be a positive number of seconds")
        if not isinstance(table, str) or not _TABLE_RE.match(table):
            raise ValueError("table must be a plain identifier: letters, digits and underscores, at most 41 characters")
        self._blinder = None if key_secret is None else Blinder(key_secret)
        self._pg, self._errors, self._sql = _import_psycopg()
        self.dsn = dsn
        self.prefix = prefix
        self.clock = clock
        self.retention = retention
        self.timeout = float(timeout)
        self.table = table
        self._lock = threading.RLock()
        self._tx_depth = 0
        self._adds_since_sweep = 0
        self._closed = False
        # One connection per store, guarded by an RLock like the SQLite store: threads sharing a store take
        # turns, processes and separate stores contend inside Postgres. Autocommit, so a read outside
        # transaction() is one statement and every transaction is explicit.
        self._tx_lock_id = _lock_id("tenkyu-tx", table, prefix)
        self._borrowed = connection is not None
        if self._borrowed:
            self._db = connection
            return
        self._db = self._connect()
        try:
            self._setup()
            self._sweep_quietly()
        except BaseException:
            self._db.close()  # do not leave a half-set-up connection open
            raise

    # --- connection and schema
    def _connect(self):
        return self._pg.connect(self.dsn, autocommit=True, connect_timeout=max(1, math.ceil(self.timeout)))

    def _connection(self):
        """The live connection. Between transactions a connection the server dropped (restart, failover,
        idle kill) is replaced; inside one it is not, so the transaction fails instead of half-running."""
        if self._closed:
            raise RuntimeError("PostgresStore is closed")
        if self._tx_depth == 0 and not self._borrowed and (self._db.closed or self._db.broken):
            self._db = self._connect()
        return self._db

    def _q(self, template: str):
        """Fill the table and index names (validated identifiers) into a SQL template."""
        sql = self._sql
        return sql.SQL(template).format(t=sql.Identifier(self.table),
                                        k=sql.Identifier(self.table + "_ns_key_ts"),
                                        s=sql.Identifier(self.table + "_ns_ts"))

    def _setup(self) -> None:
        """Create the table and indexes if they are missing. `CREATE TABLE IF NOT EXISTS` is not safe when
        many sessions run it at once on a new database: the loser can fail with a unique violation on the
        catalog. A transaction-scoped advisory lock serializes the DDL; the retry covers anything left
        (a lock timeout, a deadlock with another migration). Skipped when the table already exists, so a
        database provisioned by an admin works for a role that cannot create tables."""
        errors = self._errors
        retryable = (errors.UniqueViolation, errors.DuplicateTable, errors.DuplicateObject, errors.DeadlockDetected,
                     errors.SerializationFailure, errors.LockNotAvailable)
        deadline = time.monotonic() + self.timeout
        delay = 0.01
        while True:
            try:
                exists = self._db.execute("SELECT to_regclass(%s) IS NOT NULL", (self.table,)).fetchone()[0]
                if exists:
                    return
                with self._db.transaction():
                    self._db.execute("SELECT set_config('lock_timeout', %s, true)", (f"{int(self.timeout * 1000)}ms",))
                    self._db.execute("SELECT pg_advisory_xact_lock(%s)", (_lock_id("tenkyu-setup", self.table),))
                    self._db.execute(self._q(
                        "CREATE TABLE IF NOT EXISTS {t} (id bigserial PRIMARY KEY, ns text NOT NULL, "
                        "key bytea NOT NULL, ts double precision NOT NULL, value text NOT NULL)"))
                    self._db.execute(self._q("CREATE INDEX IF NOT EXISTS {k} ON {t} (ns, key, ts)"))
                    self._db.execute(self._q("CREATE INDEX IF NOT EXISTS {s} ON {t} (ns, ts)"))
                return
            except retryable:
                if time.monotonic() + delay > deadline:
                    raise
                time.sleep(delay)
                delay = min(delay * 2, 0.25)

    # --- helpers
    def _t(self, ts):
        try:
            ts = float(ts)
        except (TypeError, ValueError, OverflowError):
            return self.clock()
        return ts if math.isfinite(ts) else self.clock()

    def blind(self, value):
        """Map a raw value to the form this store keeps and `values()` returns: the identity without
        `key_secret`, a keyed hash with it. Guard uses it before comparing a raw value with `values()` results."""
        return value if self._blinder is None else self._blinder.value(value)

    @staticmethod
    def _enc(value) -> str:
        # ensure_ascii (the default) escapes NUL and lone surrogates, which a Postgres text column rejects
        return json.dumps(value, default=str, sort_keys=True)

    def _key(self, key) -> bytes:
        """Keys are stored as bytes so any string is safe: NUL and lone surrogates, which Postgres text
        cannot hold, round-trip exactly. Long keys (an attacker controls email and user-agent length) are
        replaced by their SHA-256, so an index entry never exceeds the B-tree size limit. The leading tag
        byte keeps the two forms from colliding. With `key_secret` the key is blinded first."""
        if self._blinder is not None:
            key = self._blinder.key(key)
        raw = (key if isinstance(key, str) else str(key)).encode("utf-8", "surrogatepass")
        if len(raw) <= _MAX_RAW_KEY_BYTES:
            return b"k" + raw
        return b"h" + hashlib.sha256(raw).digest()

    def _sweep_quietly(self) -> None:
        """Housekeeping on open must never stop startup or wait for anyone."""
        try:
            self._sweep()
        except (self._errors.LockNotAvailable, self._errors.DeadlockDetected):
            pass

    def _sweep(self) -> None:
        """Delete this prefix's expired rows, including keys that never come back (trimming only the key being
        written would let the table grow forever). Runs on open and every SWEEP_MIN_ADDS adds. Only this
        prefix is touched: another customer may use a different retention. SKIP LOCKED so two sweeps (or a
        sweep and an add) never wait on or deadlock with each other. Uses the clock, not one event's timestamp,
        so a single odd timestamp cannot wipe the history."""
        self._adds_since_sweep = 0
        self._connection().execute(self._q(
            "DELETE FROM {t} WHERE id IN (SELECT id FROM {t} WHERE ns = %s AND ts < %s FOR UPDATE SKIP LOCKED)"),
            (self.prefix, float(self.clock() - self.retention)))

    @contextmanager
    def transaction(self):
        """Run a whole read-then-write sequence as one Postgres transaction that holds a per-prefix advisory
        lock, so other threads, processes and servers using the same prefix wait instead of reading the same
        state. Re-entrant. Raises `psycopg.errors.LockNotAvailable` (an OperationalError) if the lock is not
        free within `timeout` seconds; nothing is recorded in that case.

        Why a transaction-scoped advisory lock per prefix, not row locks and not one lock per key:
        - SELECT ... FOR UPDATE locks rows that exist. The race Guard closes is two requests that both see
          *no* row for an email and then both insert one, so there is nothing to lock. A lock must exist
          before the data does, which is what an advisory lock is.
        - Guard.check reads many keys (email, ip, device, aliases, links) and then writes several. Which keys
          it will touch is not known when the transaction starts, so per-key locks could not be taken up
          front, and taking them as they are found invites lock-order deadlocks. One lock per prefix is
          deadlock-free (it is the only lock a transaction takes) and serializes exactly the requests whose
          reads can overlap.
        - Customers with different prefixes never block each other, only requests of one customer do. Their
          checks take milliseconds, and a signup stream is not a hot write path.
        - pg_advisory_xact_lock is released by COMMIT or ROLLBACK, including when the connection dies, so a
          crashed process cannot leave the lock held (a session-level lock could).
        The transaction is READ COMMITTED on purpose, and the lock is its first statement: every later
        statement then takes a new snapshot that includes whatever the previous lock holder committed. Under
        REPEATABLE READ the snapshot would be taken before the wait and the check would read stale data."""
        with self._lock:
            if self._tx_depth:
                self._tx_depth += 1
                try:
                    yield
                finally:
                    self._tx_depth -= 1
                return
            conn = self._connection()
            with conn.transaction():
                conn.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
                conn.execute("SELECT set_config('lock_timeout', %s, true)", (f"{int(self.timeout * 1000)}ms",))
                conn.execute("SELECT pg_advisory_xact_lock(%s)", (self._tx_lock_id,))
                self._tx_depth = 1
                try:
                    yield
                finally:
                    self._tx_depth = 0

    # --- interface
    def add(self, key: str, value: Hashable = None, ts: float | None = None) -> None:
        ts = self._t(self.clock() if ts is None else ts)
        k = self._key(key)
        with self.transaction():
            db = self._db
            db.execute(self._q("INSERT INTO {t} (ns, key, ts, value) VALUES (%s, %s, %s, %s)"),
                       (self.prefix, k, ts, self._enc(self.blind(value))))
            db.execute(self._q("DELETE FROM {t} WHERE ns = %s AND key = %s AND ts < %s"),
                       (self.prefix, k, ts - self.retention))
            db.execute(self._q(
                "DELETE FROM {t} WHERE id IN (SELECT id FROM {t} WHERE ns = %s AND key = %s "
                "ORDER BY ts DESC, id DESC OFFSET %s)"), (self.prefix, k, MAX_EVENTS_PER_KEY))
            self._adds_since_sweep += 1
            if self._adds_since_sweep >= SWEEP_MIN_ADDS:
                self._sweep()

    def _bounds(self, window, now):
        now = self._t(self.clock() if now is None else now)
        try:
            low = float(now - window)
        except OverflowError:  # an absurdly large int window
            low = -math.inf
        return low, now

    def _rows(self, key: str, window: float, now):
        low, now = self._bounds(window, now)
        with self._lock:
            cur = self._connection().execute(
                self._q("SELECT value FROM {t} WHERE ns = %s AND key = %s AND ts >= %s AND ts <= %s"),
                (self.prefix, self._key(key), low, now))
            return [r[0] for r in cur.fetchall()]

    def count(self, key: str, window: float, now: float | None = None) -> int:
        low, now = self._bounds(window, now)
        with self._lock:
            cur = self._connection().execute(
                self._q("SELECT count(*) FROM {t} WHERE ns = %s AND key = %s AND ts >= %s AND ts <= %s"),
                (self.prefix, self._key(key), low, now))
            return cur.fetchone()[0]

    def distinct(self, key: str, window: float, now: float | None = None) -> int:
        return len(set(self._rows(key, window, now)))

    def values(self, key: str, window: float, now: float | None = None) -> set:
        return {json.loads(v) for v in self._rows(key, window, now)}

    def close(self) -> None:
        with self._lock:
            self._closed = True
            if not self._borrowed:
                self._db.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
