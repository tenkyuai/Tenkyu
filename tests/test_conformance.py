"""Run the shared store conformance suite against every store, plus a deliberately broken one."""
import math
import threading
import unittest
from contextlib import contextmanager

from tenkyu import MemoryStore
from tenkyu.sqlite_store import SQLiteStore
from tenkyu.testing import StoreConformance

MAX = 2000


class DictStore:
    """A tiny third-party style store: a dict of lists and one re-entrant lock."""

    def __init__(self, clock, retention, lock=True):
        self.clock, self.retention = clock, retention
        self.data = {}
        self._lock = threading.RLock() if lock else None

    @contextmanager
    def transaction(self):
        if self._lock is None:  # the bug a conformance suite exists to catch
            yield
            return
        with self._lock:
            yield

    def _ts(self, ts):
        try:
            ts = float(ts)
        except (TypeError, ValueError, OverflowError):
            return self.clock()
        return ts if math.isfinite(ts) else self.clock()

    def add(self, key, value=None, ts=None):
        with self.transaction():
            ts = self._ts(self.clock() if ts is None else ts)
            rows = self.data.setdefault(key, [])
            rows.append((ts, value))
            del rows[:-MAX]
            cutoff = ts - self.retention
            rows[:] = [r for r in rows if r[0] >= cutoff]

    def _rows(self, key, window, now):
        now = self._ts(self.clock() if now is None else now)
        return [v for t, v in self.data.get(key, []) if now - window <= t <= now]

    def count(self, key, window, now=None):
        return len(self._rows(key, window, now))

    def distinct(self, key, window, now=None):
        return len(set(self._rows(key, window, now)))

    def values(self, key, window, now=None):
        return set(self._rows(key, window, now))


class TestMemoryStore(StoreConformance, unittest.TestCase):
    def make_store(self, clock, retention):
        return MemoryStore(clock=clock, retention=retention)


class TestSQLiteStore(StoreConformance, unittest.TestCase):
    def make_store(self, clock, retention):
        return SQLiteStore(":memory:", clock=clock, retention=retention)


class TestDictStore(StoreConformance, unittest.TestCase):
    def make_store(self, clock, retention):
        return DictStore(clock, retention)


class TestSuiteCatchesBrokenStores(unittest.TestCase):
    """The suite is only useful if it fails for a store that is wrong."""

    def run_suite(self, make):
        class Broken(StoreConformance, unittest.TestCase):
            def make_store(self, clock, retention):
                return make(clock, retention)

        result = unittest.TestResult()
        unittest.defaultTestLoader.loadTestsFromTestCase(Broken).run(result)
        return {t.id().rsplit(".", 1)[-1] for t, _ in result.failures + result.errors}

    def test_a_transaction_without_a_lock_fails_the_atomicity_tests(self):
        failed = self.run_suite(lambda c, r: DictStore(c, r, lock=False))
        self.assertIn("test_transaction_makes_read_then_write_atomic", failed)

    def test_a_store_that_ignores_the_per_key_cap_fails(self):
        class NoCap(DictStore):
            def add(self, key, value=None, ts=None):
                ts = self._ts(self.clock() if ts is None else ts)
                self.data.setdefault(key, []).append((ts, value))

        self.assertIn("test_one_key_keeps_at_most_2000_events", self.run_suite(lambda c, r: NoCap(c, r)))

    def test_a_store_that_never_expires_events_fails(self):
        class NoRetention(DictStore):
            def add(self, key, value=None, ts=None):
                ts = self._ts(self.clock() if ts is None else ts)
                self.data.setdefault(key, []).append((ts, value))
                del self.data[key][:-MAX]

        self.assertIn("test_old_events_are_dropped_after_retention", self.run_suite(lambda c, r: NoRetention(c, r)))

    def test_a_store_that_trusts_nan_timestamps_fails(self):
        class TrustsNaN(DictStore):
            def _ts(self, ts):
                return float(ts)

        self.assertIn("test_non_finite_timestamps_fall_back_to_the_clock",
                      self.run_suite(lambda c, r: TrustsNaN(c, r)))
