"""Conformance tests for event stores (stdlib only: unittest, works under pytest too).

Writing your own store (Redis, Postgres, ...)? Subclass `StoreConformance` next to `unittest.TestCase`
and implement `make_store`:

    import unittest
    from tenkyu.testing import StoreConformance

    class TestMyRedisStore(StoreConformance, unittest.TestCase):
        def make_store(self, clock, retention):
            return MyRedisStore(clock=clock, retention=retention)

A store needs `add(key, value=None, ts=None)`, `count(key, window, now=None)`, `distinct(...)`,
`values(...)` and, to make `Guard.check` safe against parallel requests, `transaction()`: a re-entrant
context manager that holds a lock (or an atomic script) across the read-then-write.
A store that hides what it keeps (HMAC'd keys and values) also provides `blind(value)`: the form of a raw value as
`values()` returns it. Stores that keep raw values need no `blind`; the tests then use the identity.
"""
from __future__ import annotations

import threading
import time

T0 = 1_700_000_000.0


class StoreConformance:
    """Mixin of behavior every store must have. Mix with unittest.TestCase and define make_store."""

    def make_store(self, clock, retention):  # pragma: no cover - supplied by the subclass
        raise NotImplementedError("return a fresh, empty store that uses `clock` and `retention`")

    @staticmethod
    def blind_of(store):
        """`store.blind` (raw value -> the form `values()` returns); the identity for a store that keeps raw values."""
        return getattr(store, "blind", None) or (lambda v: v)

    def fresh(self, retention=30 * 86400.0, now=T0):
        self.now = [now]
        return self.make_store(lambda: self.now[0], retention)

    # --- windows
    def test_count_window_includes_both_edges_and_excludes_future(self):
        s = self.fresh()
        for i in range(3):
            s.add("k", "v", ts=T0 - i)
        s.add("k", "future", ts=T0 + 100)
        self.assertEqual(s.count("k", 10, T0), 3)
        self.assertEqual(s.count("k", 1, T0), 2)  # T0 and T0-1: both edges inclusive
        self.assertEqual(s.count("other", 10, T0), 0)

    def test_distinct_and_values_handle_none_str_and_int(self):
        s = self.fresh()
        for v in ["a", "b", "a", None, 7]:
            s.add("k", v, ts=T0)
        self.assertEqual(s.distinct("k", 10, T0), 4)
        b = self.blind_of(s)
        self.assertEqual(s.values("k", 10, T0), {b("a"), b("b"), b(None), b(7)})

    def test_default_now_is_the_clock(self):
        s = self.fresh()
        s.add("k", "x")
        self.assertEqual(s.count("k", 5), 1)
        self.now[0] += 100
        self.assertEqual(s.count("k", 5), 0)

    def test_non_finite_timestamps_fall_back_to_the_clock(self):
        s = self.fresh()
        for bad in (float("nan"), float("inf"), float("-inf")):
            s.add("k", bad, ts=bad)
        self.assertEqual(s.count("k", 5, float("nan")), 3)
        self.assertEqual(s.count("k", 5), 3)

    # --- retention and caps
    def test_old_events_are_dropped_after_retention(self):
        s = self.fresh(retention=100)
        s.add("k", 1, ts=T0)
        s.add("k", 2, ts=T0 + 500)
        self.assertEqual(s.count("k", 10_000, T0 + 500), 1)

    def test_one_key_keeps_at_most_2000_events(self):
        s = self.fresh()
        for i in range(2100):
            s.add("k", i, ts=T0)
        self.assertEqual(s.count("k", 10, T0), 2000)
        b = self.blind_of(s)
        self.assertNotIn(b(0), s.values("k", 10, T0))  # the oldest were dropped, not the newest
        self.assertIn(b(2099), s.values("k", 10, T0))

    # --- concurrency
    def test_parallel_adds_are_all_kept(self):
        s = self.fresh()
        threads = [threading.Thread(target=lambda i=i: [s.add(f"k{i}", n, ts=T0) for n in range(50)]) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sum(s.count(f"k{i}", 10, T0) for i in range(8)), 400)

    def test_transaction_makes_read_then_write_atomic(self):
        s = self.fresh()
        if not hasattr(s, "transaction"):
            self.skipTest("store has no transaction(): Guard only protects one process at a time")
        winners, bar = [], threading.Barrier(12)

        def claim(i):
            bar.wait()
            with s.transaction():
                if s.count("claim", 60, T0) == 0:
                    time.sleep(0.002)  # widen the window between the read and the write
                    s.add("claim", i, ts=T0)
                    winners.append(i)

        threads = [threading.Thread(target=claim, args=(i,)) for i in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(winners), 1, f"{len(winners)} of 12 parallel claims won; the read-then-write is not atomic")

    def test_transaction_is_reentrant_and_released_after_an_error(self):
        s = self.fresh()
        if not hasattr(s, "transaction"):
            self.skipTest("store has no transaction()")
        with s.transaction():
            with s.transaction():  # add() inside a transaction opens one of its own
                s.add("k", 1, ts=T0)
        with self.assertRaises(RuntimeError):
            with s.transaction():
                raise RuntimeError("boom")
        done = []
        # a fresh thread must be able to enter: the failed transaction released its lock
        t = threading.Thread(target=lambda: self._enter_and_leave(s, done))
        t.start()
        t.join(5)
        self.assertEqual(done, [1], "the lock was still held after an exception inside transaction()")

    @staticmethod
    def _enter_and_leave(store, done):
        with store.transaction():
            done.append(1)

    def test_guards_sharing_one_store_let_only_the_first_parallel_alias_through(self):
        from . import Guard, Signup
        s = self.fresh()
        out, bar = [], threading.Barrier(10)

        def signup(i):
            guard = Guard(store=s, clock=lambda: self.now[0])  # one per worker, like separate processes
            bar.wait()
            out.append(guard.check(Signup(email=f"j.o.h.n+{i}@gmail.com", ip=f"198.51.100.{i}",
                                          user_agent="Mozilla/5.0 Chrome/120")).action)

        threads = [threading.Thread(target=signup, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(out.count("allow"), 1, out)
