"""In-memory sliding-window event store. Stdlib only, injectable clock."""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import threading
import time
from contextlib import contextmanager
from collections import defaultdict, deque
from typing import Callable, Deque, Dict, Hashable, Tuple

MAX_EVENTS_PER_KEY = 2000
SWEEP_MIN_ADDS = 1000  # sweep expired keys at most this often


BLIND_DIGEST_CHARS = 32  # hex characters kept from each HMAC-SHA256 (128 bits)
MIN_BLIND_SECRET_BYTES = 16


class Blinder:
    """The keyed one-way mapping behind BlindStore and PostgresStore(key_secret=...).

    `key("email:maya@gmail.com")` -> `"email:<32 hex>"`: the kind before the first ":" stays readable (so pruning and
    debugging by kind keep working) and everything after it becomes HMAC-SHA256(secret, whole key), hex, truncated
    to 32 characters. A key with no ":" becomes `"_:<32 hex>"`. `value(v)` -> 32 hex characters for any value
    except None, which stays None (it says nothing). Keys and values use different HMAC domains, and a str and an int
    with the same text never collide. Equal inputs always give equal outputs for one secret, so counting, windows
    and set union keep working on blinded data; different secrets give unrelated outputs."""

    def __init__(self, secret: bytes):
        if isinstance(secret, bool) or not isinstance(secret, (bytes, bytearray)) or len(secret) < MIN_BLIND_SECRET_BYTES:
            raise ValueError(f"secret must be bytes, at least {MIN_BLIND_SECRET_BYTES} bytes long")
        self._secret = bytes(secret)

    def _mac(self, domain: bytes, data: bytes) -> str:
        return hmac.new(self._secret, domain + data, hashlib.sha256).hexdigest()[:BLIND_DIGEST_CHARS]

    def key(self, key) -> str:
        raw = (key if isinstance(key, str) else str(key)).encode("utf-8", "surrogatepass")
        kind, sep, _ = (key if isinstance(key, str) else str(key)).partition(":")
        if not sep:
            return "_:" + self._mac(b"n\0", raw)
        return kind + ":" + self._mac(b"k\0", raw)

    def value(self, value):
        if value is None:
            return None
        if isinstance(value, str):
            data = b"s" + value.encode("utf-8", "surrogatepass")
        else:
            data = b"j" + json.dumps(value, default=str, sort_keys=True).encode("utf-8")
        return self._mac(b"v\0", data)


class MemoryStore:
    """Remembers (timestamp, value) events per key and answers window queries."""

    def blind(self, value):
        """Map a raw value to the form this store keeps and `values()` returns. The identity here; blinded stores
        (BlindStore, PostgresStore with key_secret) return a keyed hash. Guard applies it to any raw value it
        compares with `values()` results."""
        return value

    def __init__(self, clock: Callable[[], float] = time.time, retention: float = 30 * 86400.0):
        self.clock = clock
        self.retention = retention
        self._events: Dict[str, Deque[Tuple[float, Hashable]]] = defaultdict(deque)
        self._lock = threading.RLock()
        self._adds_since_sweep = 0
        self._sweep_after = SWEEP_MIN_ADDS

    @contextmanager
    def transaction(self):
        """Hold the store's lock so a read-then-write sequence cannot interleave with another thread."""
        with self._lock:
            yield

    def _t(self, ts):
        """A usable timestamp: finite float, else the clock."""
        try:
            ts = float(ts)
        except (TypeError, ValueError, OverflowError):
            return self.clock()
        return ts if math.isfinite(ts) else self.clock()

    def add(self, key: str, value: Hashable = None, ts: float | None = None) -> None:
        ts = self._t(self.clock() if ts is None else ts)
        with self._lock:
            q = self._events[key]
            q.append((ts, value))
            while len(q) > MAX_EVENTS_PER_KEY:
                q.popleft()
            cutoff = ts - self.retention
            while q and q[0][0] < cutoff:
                q.popleft()
            self._adds_since_sweep += 1
            if self._adds_since_sweep >= self._sweep_after:
                self._sweep()

    def _sweep(self) -> None:
        """Drop keys whose events have all expired. Trimming only the key being written would keep
        every one-off email, IP and device in memory forever. Runs after as many adds as keys were
        left by the last sweep (at least 1000), so the cost per add stays constant. Uses the clock, not one event's timestamp, so a
        single odd timestamp cannot wipe the history."""
        self._adds_since_sweep = 0
        cutoff = self.clock() - self.retention
        for key in [k for k, q in self._events.items() if not q or q[-1][0] < cutoff]:
            del self._events[key]
        self._sweep_after = max(SWEEP_MIN_ADDS, len(self._events))

    def _window(self, key: str, window: float, now: float | None):
        now = self._t(self.clock() if now is None else now)
        with self._lock:
            q = self._events.get(key)
            if not q:
                return []
            return [v for t, v in q if now - window <= t <= now]

    def count(self, key: str, window: float, now: float | None = None) -> int:
        return len(self._window(key, window, now))

    def distinct(self, key: str, window: float, now: float | None = None) -> int:
        return len(set(self._window(key, window, now)))

    def values(self, key: str, window: float, now: float | None = None) -> set:
        return set(self._window(key, window, now))


class BlindStore:
    """Wrap any store so it never holds a raw key part or value: the variable part of every key and every value is
    HMAC-SHA256'd with `secret` (hex, 32 characters) before it reaches `inner`; the kind before the first ":" stays
    readable. Use it when the backing store is shared or hosted and must not contain emails, IPs or device ids:

        Guard(store=BlindStore(SQLiteStore("tenkyu.db"), secret))

    `values()` returns blinded values; call `store.blind(raw)` to compare a raw value with them (Guard does this
    itself). Keep `secret` stable and private: a new secret orphans the history, and with the secret an attacker
    can confirm a guessed email, so it must not be stored beside the data. Counts, windows, caps and
    transactions are those of `inner`."""

    def __init__(self, inner, secret: bytes):
        self.inner = inner
        self._blinder = Blinder(secret)
        if hasattr(inner, "transaction"):  # Guard only takes a transaction when the store has one
            self.transaction = inner.transaction

    def blind(self, value):
        return self._blinder.value(value)

    def add(self, key: str, value: Hashable = None, ts: float | None = None) -> None:
        self.inner.add(self._blinder.key(key), self._blinder.value(value), ts)

    def count(self, key: str, window: float, now: float | None = None) -> int:
        return self.inner.count(self._blinder.key(key), window, now)

    def distinct(self, key: str, window: float, now: float | None = None) -> int:
        return self.inner.distinct(self._blinder.key(key), window, now)

    def values(self, key: str, window: float, now: float | None = None) -> set:
        return self.inner.values(self._blinder.key(key), window, now)

    def __getattr__(self, name):  # close(), clock, ... come from the wrapped store
        if name in ("inner", "_blinder") or name.startswith("__"):
            raise AttributeError(name)
        return getattr(self.inner, name)
