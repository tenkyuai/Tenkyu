"""PostgresStore against a real Postgres.

Set TENKYU_TEST_PG_DSN (for example postgresql://user@host/db) to run them; without it, or when the server cannot
be reached, they skip. Every test uses its own random prefix, so tests never see each other's rows and the database
can be reused between runs. Tests that need an empty database create and drop one of their own.
"""
import importlib.util
import json
import os
import subprocess
import sys
import threading
import time
import unittest
import uuid
from pathlib import Path

import pytest

from tenkyu import BLOCK, Guard, MemoryStore, Signup
from tenkyu.testing import StoreConformance

T0 = 1_700_000_000.0
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/120.0 Safari/537.36"
DSN = os.environ.get("TENKYU_TEST_PG_DSN", "").strip()

from tenkyu import PostgresStore  # importing the class does not import the driver

try:
    import psycopg
except ImportError:  # the lazy-import tests at the bottom still run without the driver
    psycopg = None


def _reachable() -> bool:
    if not DSN or psycopg is None:
        return False
    try:
        psycopg.connect(DSN, connect_timeout=3).close()
        return True
    except Exception:
        return False


REACHABLE = _reachable()
needs_pg = pytest.mark.skipif(not REACHABLE, reason="set TENKYU_TEST_PG_DSN to a reachable Postgres (and install tenkyu[postgres]) to run these")


def test_postgres_is_reachable_when_ci_requires_it():
    """CI sets TENKYU_TEST_PG_REQUIRED so a broken service container fails the job instead of skipping every test."""
    if os.environ.get("TENKYU_TEST_PG_REQUIRED") != "1":
        pytest.skip("only enforced in CI")
    assert REACHABLE, "TENKYU_TEST_PG_DSN is unset or unreachable, or psycopg is not installed"


def _prefix() -> str:
    return "t" + uuid.uuid4().hex[:12]


def _forget(*prefixes):
    with psycopg.connect(DSN, autocommit=True) as c:
        for p in prefixes:
            c.execute("DELETE FROM tenkyu_events WHERE ns = %s", (p,))


def _store(prefix=None, **kw):
    kw.setdefault("clock", lambda: T0)
    kw.setdefault("allow_raw_keys", kw.get("key_secret") is None)  # these tests exercise the raw-key store too
    return PostgresStore(DSN, prefix=prefix or _prefix(), **kw)


@pytest.fixture
def pg():
    if not REACHABLE:
        pytest.skip("no reachable Postgres")
    """A factory for stores with a fresh random prefix; closes them and deletes their rows afterwards."""
    made, prefixes = [], []

    def make(prefix=None, **kw):
        s = _store(prefix=prefix, **kw)
        made.append(s)
        prefixes.append(s.prefix)
        return s

    yield make
    for s in made:
        s.close()
    _forget(*prefixes)


@pytest.fixture
def fresh_dsn():
    """The DSN of a brand-new empty database, dropped afterwards."""
    if not REACHABLE:
        pytest.skip("no reachable Postgres")
    from psycopg.conninfo import make_conninfo
    name = "tk_test_" + uuid.uuid4().hex[:10]
    with psycopg.connect(DSN, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    try:
        yield make_conninfo(DSN, dbname=name)
    finally:
        with psycopg.connect(DSN, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


# --- the shared contract: the same test functions tests/test_stores.py runs against Memory and SQLite
def _load_shared_suite():
    path = Path(__file__).with_name("test_stores.py")
    spec = importlib.util.spec_from_file_location("_tenkyu_shared_store_tests", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_shared = _load_shared_suite()


SECRET = b"pg-blind-secret-0123456789abcdef"


@pytest.fixture(params=["plain", "blinded"])
def store(pg, request):
    return pg(key_secret=SECRET if request.param == "blinded" else None)


# these take only the `store` fixture, which is the Postgres one in this module
for _name in ("test_count_within_window", "test_distinct_and_values", "test_window_excludes_old_and_future",
              "test_default_now_uses_clock", "test_per_key_cap", "test_nonfinite_ts_uses_clock",
              "test_nonfinite_now_uses_clock"):
    globals()[_name] = needs_pg(getattr(_shared, _name))
del _name


@needs_pg
class TestBlindedPostgresStoreConformance(StoreConformance, unittest.TestCase):
    """The same contract with key_secret: values come back blinded, everything else is unchanged."""

    def make_store(self, clock, retention):
        prefix = _prefix()
        s = PostgresStore(DSN, prefix=prefix, clock=clock, retention=retention, timeout=30, key_secret=SECRET)
        self.addCleanup(_forget, prefix)
        self.addCleanup(s.close)
        return s


@needs_pg
class TestPostgresStoreConformance(StoreConformance, unittest.TestCase):
    """tenkyu.testing.StoreConformance: windows, caps, retention, atomic transaction(), parallel Guards."""

    def make_store(self, clock, retention):
        prefix = _prefix()
        s = PostgresStore(DSN, prefix=prefix, clock=clock, retention=retention, timeout=30, allow_raw_keys=True)
        self.addCleanup(_forget, prefix)
        self.addCleanup(s.close)
        return s


@needs_pg
def test_retention_purges(pg):
    s = pg(retention=100)
    s.add("k", 1, ts=T0)
    s.add("k", 2, ts=T0 + 500)
    assert s.count("k", 10_000, T0 + 500) == 1


@needs_pg
def test_persists_across_reopen(pg):
    prefix = _prefix()
    a = pg(prefix)
    a.add("email:a@b.co", ts=T0)
    a.close()
    assert pg(prefix).count("email:a@b.co", 60, T0) == 1


@needs_pg
def test_two_handles_share_state(pg):
    prefix = _prefix()
    a, b = pg(prefix), pg(prefix)
    a.add("k", "from-a", ts=T0)
    assert b.values("k", 10, T0) == {"from-a"}


@needs_pg
def test_guard_remembers_alias_across_restart(pg):
    prefix = _prefix()
    g1 = Guard(store=pg(prefix), clock=lambda: T0)
    assert g1.check(Signup(email="john@gmail.com", ip="1.1.1.1", user_agent=UA, ts=T0)).allowed
    g1.store.close()
    g2 = Guard(store=pg(prefix), clock=lambda: T0)
    assert g2.check(Signup(email="j.o.h.n+x@gmail.com", ip="2.2.2.2", user_agent=UA, ts=T0)).action == BLOCK


@needs_pg
def test_one_server_blocks_an_alias_another_server_recorded(pg):
    """Two independent stores (two app servers, each with its own connection) share one history."""
    prefix = _prefix()
    server_a = Guard(store=pg(prefix))
    server_b = Guard(store=pg(prefix))
    assert server_a.check(Signup(email="john@gmail.com", ip="203.0.113.7", device_id="d1", user_agent=UA)).action == "allow"
    again = server_b.check(Signup(email="j.o.h.n+free2@gmail.com", ip="198.51.100.9", device_id="d2", user_agent=UA))
    assert again.action == BLOCK
    assert any("already signed up" in r for r in again.reasons)


@needs_pg
def test_guard_decisions_identical_on_memory_and_postgres(pg):
    def run(store):
        g = Guard(store=store, clock=lambda: T0)
        return [g.check(Signup(email=f"user{i}@acme.io", ip="3.3.3.3", device_id=f"d{i % 3}", user_agent=UA,
                               ts=T0 + i * 20)).to_dict() for i in range(8)]
    assert run(MemoryStore(clock=lambda: T0)) == run(pg())


@needs_pg
def test_default_retention_covers_device_window(pg):
    """device_reuse looks back 7 days; the default store must keep events that long."""
    day = 86400.0
    clock = lambda: T0 + 6 * day  # noqa: E731
    g = Guard(store=pg(clock=clock))
    for i, email in enumerate(("a@acme.io", "b@acme.io")):
        g.check(Signup(email=email, ip=f"203.0.113.{i}", device_id="dev-1", user_agent=UA, ts=T0))
    g.check(Signup(email="c@acme.io", ip="198.51.100.9", device_id="dev-1", user_agent=UA, ts=T0 + 6 * day))
    third = g.check(Signup(email="d@acme.io", ip="198.51.100.8", device_id="dev-1", user_agent=UA, ts=T0 + 6.1 * day))
    dev = [s for s in third.signals if s.name == "device_reuse"]
    assert dev and dev[0].score == 0.65


def _benchmark_replay(store, rows):
    now = [0.0]
    store.clock = lambda: now[0]
    g = Guard(store=store, clock=lambda: now[0], link_secret=b"link-secret-0123456789abcdef")
    out = []
    for r in rows:
        now[0] = r["ts"]
        links = {k: r[k] for k in ("phone", "card") if r.get(k)}
        try:
            out.append(g.check(Signup(email=r["email"] or None, ip=r["ip"] or None, device_id=r["device_id"] or None,
                                      user_agent=r["user_agent"] or None, ts=r["ts"], links=links or None)).to_dict())
        except ValueError:
            out.append("invalid")
    return out


@needs_pg
def test_blinded_postgres_decisions_match_plain_memory_on_the_benchmark(pg):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "benchmarks"))
    from heldout_gen import generate
    rows = generate(1)[:700]
    plain = _benchmark_replay(MemoryStore(), rows)
    blinded = _benchmark_replay(pg(key_secret=SECRET, timeout=30), rows)
    assert plain == blinded
    assert {d["action"] for d in plain if d != "invalid"} >= {"allow", "block"}


def _all_database_text(dsn):
    from psycopg import sql
    """Every value in every column of every public table, as text: strings as is, bytes as latin-1 text and hex,
    json dumped. Returns a list of (table, text)."""
    out = []
    with psycopg.connect(dsn, autocommit=True) as c:
        # a SQL_ASCII database (the default of a bare initdb) hands text columns back as bytes: handle both
        tables = [r[0].decode() if isinstance(r[0], bytes) else r[0]
                  for r in c.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'").fetchall()]
        for t in tables:
            cur = c.execute(sql.SQL("SELECT * FROM {}").format(sql.Identifier(t)))
            for row in cur.fetchall():
                for v in row:
                    if isinstance(v, (bytes, bytearray, memoryview)):
                        raw = bytes(v)
                        out.append((t, raw.decode("latin-1")))
                        out.append((t, raw.hex()))
                    elif isinstance(v, (dict, list)):
                        out.append((t, json.dumps(v)))
                    elif v is not None:
                        out.append((t, str(v)))
    return out


@needs_pg
def test_no_raw_email_ip_or_device_in_any_table_after_a_run(fresh_dsn):
    needles = ["maya.rivera@gmail.com", "mayarivera", "203.0.113.77", "2001:db8:abcd:12", "dev-secret-fingerprint-91",
               "+14155550177", "4155550177", "card-fp-secret-55", "Zorblax-UA/9.9", "ann.lee@acme-private.io"]
    prefix = _prefix()
    with PostgresStore(fresh_dsn, prefix=prefix, key_secret=SECRET) as st:
        g = Guard(store=st, link_secret=b"link-secret-0123456789abcdef")
        for email, ip, dev in [(needles[0], needles[2], needles[4]), ("m.a.y.a.rivera+x@gmail.com", needles[2], needles[4]),
                               (needles[9], "2001:db8:abcd:12::5", needles[4]), ("zz@acme-private.io", needles[2], "other-dev")]:
            g.check(Signup(email=email, ip=ip, device_id=dev, user_agent=needles[8],
                           links={"phone": needles[5], "card": needles[7]}))
        assert st.count("email:mayarivera@gmail.com", 60) == 2  # the data is really there (the alias counts too)
    dump = _all_database_text(fresh_dsn)
    assert len(dump) > 20
    for needle in needles:
        for table, text in dump:
            assert needle.lower() not in text.lower(), (needle, table)
        assert needle.encode().hex() not in "".join(t for _, t in dump)


@needs_pg
def test_borrowed_connection_store_works_and_is_left_open(fresh_dsn):
    PostgresStore(fresh_dsn, prefix="x", timeout=5, allow_raw_keys=True).close()  # creates the table
    with psycopg.connect(fresh_dsn, autocommit=True) as conn:
        st = PostgresStore(None, prefix="b", connection=conn, key_secret=SECRET)
        g = Guard(store=st, clock=lambda: T0)
        assert g.check(Signup(email="john@gmail.com", ip="1.1.1.1", user_agent=UA, ts=T0)).allowed
        assert g.check(Signup(email="j.o.h.n+x@gmail.com", ip="2.2.2.2", user_agent=UA, ts=T0)).action == BLOCK
        st.close()
        assert not conn.closed
        with pytest.raises(RuntimeError):
            st.count("k", 10)
    with psycopg.connect(fresh_dsn, autocommit=False) as conn:
        with pytest.raises(ValueError):
            PostgresStore(None, connection=conn, allow_raw_keys=True)
    with pytest.raises(ValueError):
        PostgresStore("postgresql://x@h/d", connection=object(), allow_raw_keys=True)


@pytest.mark.skipif(psycopg is None, reason="psycopg not installed")
@pytest.mark.parametrize("bad", [b"", b"short", "text-secret-0123456789abcdef", 5])
def test_key_secret_must_be_16_bytes(bad):
    with pytest.raises(ValueError):
        PostgresStore("postgresql://nobody@127.0.0.1:1/none", key_secret=bad)


def _row_count(prefix):
    with psycopg.connect(DSN, autocommit=True) as c:
        return c.execute("SELECT count(*) FROM tenkyu_events WHERE ns = %s", (prefix,)).fetchone()[0]


@needs_pg
def test_deletes_expired_rows_for_keys_that_never_return(pg):
    now = [0.0]
    st = pg(clock=lambda: now[0], retention=86400.0)
    for i in range(1100):
        st.add(f"k{i}")
    now[0] += 2 * 86400.0
    for i in range(1100):
        st.add(f"fresh{i}")
    assert _row_count(st.prefix) < 1500  # the 1100 expired rows are gone (was 2200)


@needs_pg
def test_cleans_expired_rows_when_reopened(pg):
    now = [0.0]
    prefix = _prefix()
    st = pg(prefix, clock=lambda: now[0], retention=86400.0)
    for i in range(50):
        st.add(f"k{i}")
    st.close()
    now[0] += 2 * 86400.0
    pg(prefix, clock=lambda: now[0], retention=86400.0)
    assert _row_count(prefix) == 0


# --- namespaces: many customers, one database
@needs_pg
def test_prefixes_are_isolated(pg):
    a, b = pg(), pg()
    a.add("email:x@acme.io", "va", ts=T0)
    assert b.count("email:x@acme.io", 60, T0) == 0
    assert b.values("email:x@acme.io", 60, T0) == set()
    b.add("email:x@acme.io", "vb", ts=T0)
    b.add("email:x@acme.io", "vb2", ts=T0)
    assert a.values("email:x@acme.io", 60, T0) == {"va"}
    assert b.values("email:x@acme.io", 60, T0) == {"vb", "vb2"}


@needs_pg
def test_a_prefix_that_starts_with_another_prefix_is_still_a_different_customer(pg):
    base = _prefix()
    outer, inner = pg(base), pg(base + ":inner")
    outer.add("k", "outer", ts=T0)
    inner.add("k", "inner", ts=T0)
    # also: a key that looks like "<other prefix>:k" does not reach the other customer's row
    assert outer.count(":inner:k", 60, T0) == 0
    assert outer.values("k", 60, T0) == {"outer"}
    assert inner.values("k", 60, T0) == {"inner"}


@needs_pg
def test_one_customers_sweep_and_cap_never_touch_another(pg):
    short, long_ = pg(retention=100), pg(retention=10**9)
    long_.add("k", "old", ts=T0 - 10_000)  # expired for `short`, fine for `long_`
    for i in range(2100):
        short.add("k", i, ts=T0)  # trims "k" to 2000 for `short` only
    short._sweep()
    assert long_.values("k", 100_000, T0) == {"old"}
    assert short.count("k", 10, T0) == 2000


@needs_pg
def test_guard_history_is_per_prefix(pg):
    one, two = Guard(store=pg(), clock=lambda: T0), Guard(store=pg(), clock=lambda: T0)
    assert one.check(Signup(email="john@gmail.com", ip="1.1.1.1", user_agent=UA, ts=T0)).allowed
    assert two.check(Signup(email="john@gmail.com", ip="1.1.1.1", user_agent=UA, ts=T0)).allowed  # not a repeat for customer two
    assert one.check(Signup(email="john@gmail.com", ip="1.1.1.1", user_agent=UA, ts=T0)).action == BLOCK


@needs_pg
def test_customers_do_not_wait_for_each_others_transactions(pg):
    a, b = pg(timeout=30), pg(timeout=30)
    holding, release = threading.Event(), threading.Event()

    def hold():
        with a.transaction():
            holding.set()
            release.wait(10)

    t = threading.Thread(target=hold)
    t.start()
    try:
        assert holding.wait(5)
        start = time.monotonic()
        with b.transaction():
            b.add("k", 1, ts=T0)
        assert time.monotonic() - start < 2.0
    finally:
        release.set()
        t.join()


@needs_pg
def test_same_customers_transactions_do_wait_and_time_out_cleanly(pg):
    prefix = _prefix()
    a, b = pg(prefix, timeout=30), pg(prefix, timeout=0.3)
    holding, release = threading.Event(), threading.Event()

    def hold():
        with a.transaction():
            holding.set()
            release.wait(10)

    t = threading.Thread(target=hold)
    t.start()
    try:
        assert holding.wait(5)
        start = time.monotonic()
        with pytest.raises(psycopg.OperationalError):
            Guard(store=b).check(Signup(email="a@x.io", ip="203.0.113.1", user_agent=UA))
        assert time.monotonic() - start < 3.0
    finally:
        release.set()
        t.join()
    # nothing half-recorded, and the store works again once the lock is gone
    assert b.count("email:a@x.io", 60) == 0
    assert Guard(store=b).check(Signup(email="a@x.io", ip="203.0.113.1", user_agent=UA)).action != BLOCK


needs_driver = pytest.mark.skipif(psycopg is None, reason="psycopg not installed")


@needs_driver
@pytest.mark.parametrize("bad", [None, 5, b"x"])
def test_prefix_must_be_a_str(bad):
    with pytest.raises(TypeError):
        PostgresStore("postgresql://nobody@127.0.0.1:1/none", prefix=bad, allow_raw_keys=True)


@needs_driver
@pytest.mark.parametrize("bad", ["a\x00b", "\ud800", "x" * 201])
def test_prefix_rejects_nul_surrogates_and_huge_values(bad):
    with pytest.raises(ValueError):
        PostgresStore("postgresql://nobody@127.0.0.1:1/none", prefix=bad, allow_raw_keys=True)


@needs_driver
@pytest.mark.parametrize("bad", [0, -1, "5", None, float("nan"), True])
def test_timeout_must_be_a_positive_number(bad):
    with pytest.raises((TypeError, ValueError)):
        PostgresStore("postgresql://nobody@127.0.0.1:1/none", timeout=bad, allow_raw_keys=True)


@needs_driver
@pytest.mark.parametrize("bad", ["", "   ", None, 5])
def test_dsn_must_be_a_non_empty_str(bad):
    with pytest.raises((TypeError, ValueError)):
        PostgresStore(bad, allow_raw_keys=True)


@needs_driver
@pytest.mark.parametrize("bad", ["", "a b", "a;drop table x", "1abc", "x" * 42, 'a"b'])
def test_table_must_be_a_plain_identifier(bad):
    with pytest.raises(ValueError):
        PostgresStore("postgresql://nobody@127.0.0.1:1/none", table=bad, allow_raw_keys=True)


@needs_driver
def test_unreachable_server_raises_instead_of_hanging():
    start = time.monotonic()
    with pytest.raises(psycopg.OperationalError):
        PostgresStore("postgresql://nobody@/none?host=/nonexistent-dir&port=1", timeout=1, allow_raw_keys=True)
    assert time.monotonic() - start < 5


# --- odd keys and values
HUGE = "x" * 1_000_000


@needs_pg
@pytest.mark.parametrize("key", [
    "\ud800", "a\udfffb", "nul\x00inside", "\x00", "", "é" * 5000, "😀" * 3000, HUGE, "email:" + "a" * 100_000 + "@x.io",
])
def test_surrogate_nul_empty_and_huge_keys_do_not_crash(pg, key):
    s = pg()
    s.add(key, "v", ts=T0)
    s.add(key, None, ts=T0)
    assert s.count(key, 10, T0) == 2
    assert s.values(key, 10, T0) == {"v", None}
    assert s.count(key + "?", 10, T0) == 0  # a different key is not confused with it


@needs_pg
def test_long_keys_that_differ_late_stay_different(pg):
    s = pg()
    s.add("x" * 300, "a", ts=T0)
    s.add("x" * 300 + "y", "b", ts=T0)
    s.add("x" * 255, "c", ts=T0)
    s.add("x" * 256, "d", ts=T0)
    assert s.values("x" * 300, 10, T0) == {"a"}
    assert s.values("x" * 300 + "y", 10, T0) == {"b"}
    assert s.values("x" * 255, 10, T0) == {"c"}
    assert s.values("x" * 256, 10, T0) == {"d"}


@needs_pg
@pytest.mark.parametrize("value", ["\ud800", "nul\x00", "😀", HUGE, 7, 2.5, True, {"b": 1, "a": 2}.__repr__()])
def test_odd_values_round_trip(pg, value):
    s = pg()
    s.add("k", value, ts=T0)
    assert s.values("k", 10, T0) == {value}
    assert s.distinct("k", 10, T0) == 1


@needs_pg
def test_unhashable_value_does_not_crash_add(pg):
    s = pg()
    s.add("k", object(), ts=T0)  # default=str fallback, like the SQLite store
    assert s.count("k", 5, T0) == 1


@needs_pg
def test_guard_survives_hostile_signup_fields(pg):
    g = Guard(store=pg())
    d = g.check(Signup(email="a\ud800\x00@" + "b" * 50_000 + ".io", ip="1.2.3.4", device_id="\x00" * 10 + "d" * 100_000,
                       user_agent="\ud83d" + "u" * 100_000))
    assert d.action in ("allow", "review", "block")


@needs_pg
def test_closed_store_raises_and_reconnects_after_a_dropped_connection(pg):
    s = pg()
    s.add("k", 1, ts=T0)
    with psycopg.connect(DSN, autocommit=True) as admin:  # kill the store's session from outside, like a failover
        admin.execute("SELECT pg_terminate_backend(%s)", (s._db.info.backend_pid,))
    time.sleep(0.2)
    with pytest.raises(psycopg.OperationalError):
        s.count("k", 10, T0)  # the first use of a dead connection fails loudly...
    assert s.count("k", 10, T0) == 1  # ...and the next one has a new connection
    s.close()
    with pytest.raises(RuntimeError):
        s.count("k", 10, T0)


# --- concurrency
def _parallel_aliases(guard_for, n):
    bar = threading.Barrier(n)
    out = [None] * n

    def work(i):
        guard = guard_for(i)
        bar.wait()
        try:
            out[i] = guard.check(Signup(email=f"j.o.h.n+{i}@gmail.com", ip=f"198.51.100.{i}", user_agent=UA)).action
        except Exception as e:  # report the real error instead of a None in the list
            out[i] = f"error: {e!r}"

    ts = [threading.Thread(target=work, args=(i,)) for i in range(n)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    return out


@needs_pg
def test_parallel_aliases_one_shared_store_allow_only_the_first(pg):
    for _ in range(5):
        guard = Guard(store=pg(timeout=60))
        out = _parallel_aliases(lambda i: guard, 16)
        assert out.count("allow") == 1, out


@needs_pg
def test_parallel_aliases_separate_connections_allow_only_the_first(pg):
    """One store (one connection) per thread: contention happens inside Postgres, as with many servers."""
    for _ in range(5):
        prefix = _prefix()
        guards = [Guard(store=pg(prefix, timeout=60)) for _ in range(16)]
        out = _parallel_aliases(lambda i: guards[i], 16)
        assert out.count("allow") == 1, out


@needs_pg
@pytest.mark.parametrize("separate_connections", [False, True])
def test_same_email_from_8_threads_keeps_counters_exact(pg, separate_connections):
    for _ in range(5):
        prefix = _prefix()
        shared = Guard(store=pg(prefix, timeout=60))
        guards = [Guard(store=pg(prefix, timeout=60)) if separate_connections else shared for _ in range(8)]
        bar, out = threading.Barrier(8), [None] * 8

        def work(i):
            bar.wait()
            try:
                out[i] = guards[i].check(Signup(email="john@gmail.com", ip=f"203.0.113.{i}", user_agent=UA)).action
            except Exception as e:
                out[i] = f"error: {e!r}"

        ts = [threading.Thread(target=work, args=(i,)) for i in range(8)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        assert out.count("allow") == 1, out
        assert out.count("block") == 7, out
        # the counter is exact: Guard records every attempt (blocked ones too), so 8 attempts leave exactly 8 rows
        # for the email: none lost, none doubled (Guard stamps events with real time)
        assert pg(prefix).count("email:john@gmail.com", 120, time.time()) == 8


@needs_pg
def test_concurrent_adds_to_one_key_respect_the_cap(pg):
    prefix = _prefix()
    stores = [pg(prefix, timeout=60) for _ in range(6)]
    bar = threading.Barrier(6)

    def work(s):
        bar.wait()
        for i in range(400):
            s.add("k", i, ts=T0 + i)

    ts = [threading.Thread(target=work, args=(s,)) for s in stores]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert stores[0].count("k", 10_000, T0 + 10_000) == 2000  # 2400 added, newest 2000 kept


_OPEN_CHILD = (
    "import sys, time\n"
    "from tenkyu import PostgresStore\n"
    "dsn, start = sys.argv[1], float(sys.argv[2])\n"
    "time.sleep(max(0.0, start - time.time()))\n"
    "try:\n"
    "    PostgresStore(dsn, prefix='p' + sys.argv[3], timeout=30, allow_raw_keys=True).close()\n"
    "except Exception as e:\n"
    "    print(repr(e)); sys.exit(1)\n"
)


def _child_env():
    import tenkyu
    src = str(Path(tenkyu.__file__).resolve().parent.parent)
    return {**os.environ, "PYTHONPATH": src + os.pathsep + os.environ.get("PYTHONPATH", "")}


@needs_pg
def test_many_threads_opening_a_new_database_at_once(fresh_dsn):
    # Mirrors the SQLite regression: CREATE TABLE IF NOT EXISTS run by many sessions at once on an empty database
    # can fail on a catalog unique violation. Setup serializes the DDL with an advisory lock and retries.
    errors = []
    for _ in range(10):
        with psycopg.connect(fresh_dsn, autocommit=True) as c:
            c.execute("DROP TABLE IF EXISTS tenkyu_events")
        bar = threading.Barrier(16)

        def opener(n):
            bar.wait()
            try:
                PostgresStore(fresh_dsn, prefix=f"p{n}", timeout=30, allow_raw_keys=True).close()
            except Exception as e:
                errors.append(repr(e))

        threads = [threading.Thread(target=opener, args=(n,)) for n in range(16)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
    assert not errors, errors[:3]


@needs_pg
def test_16_processes_opening_a_new_database_at_once(fresh_dsn):
    for r in range(3):
        with psycopg.connect(fresh_dsn, autocommit=True) as c:
            c.execute("DROP TABLE IF EXISTS tenkyu_events")
        start = time.time() + 2.0  # every child sleeps until this moment, then opens
        procs = [subprocess.Popen([sys.executable, "-c", _OPEN_CHILD, fresh_dsn, repr(start), str(n)],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=_child_env())
                 for n in range(16)]
        results = [(p.communicate(timeout=120), p.returncode) for p in procs]
        failures = [(out + err) for (out, err), code in results if code != 0]
        assert not failures, failures[:3]
    with psycopg.connect(fresh_dsn) as c:
        assert c.execute("SELECT to_regclass('tenkyu_events') IS NOT NULL").fetchone()[0]


@needs_pg
def test_setup_is_idempotent_and_leaves_existing_data_alone(pg):
    prefix = _prefix()
    a = pg(prefix)
    a.add("k", "v", ts=T0)
    b = pg(prefix)  # opens again: no error, no data loss
    assert b.values("k", 10, T0) == {"v"}


@needs_pg
def test_custom_table_name_is_created_and_used(pg):
    name = "tenkyu_t_" + uuid.uuid4().hex[:8]
    try:
        s = pg(table=name)
        s.add("k", "v", ts=T0)
        assert s.values("k", 10, T0) == {"v"}
        with psycopg.connect(DSN, autocommit=True) as c:
            assert c.execute(f"SELECT count(*) FROM {name}").fetchone()[0] == 1
    finally:
        with psycopg.connect(DSN, autocommit=True) as c:
            c.execute(f"DROP TABLE IF EXISTS {name}")


@needs_pg
def test_works_for_a_role_that_cannot_create_tables_once_the_table_exists(pg):
    pg()  # make sure the table exists
    with psycopg.connect(DSN, autocommit=True) as c:
        role = "tk_ro_" + uuid.uuid4().hex[:8]
        c.execute(f"CREATE ROLE {role} LOGIN PASSWORD 'pw-{role}'")
        c.execute(f"GRANT SELECT, INSERT, DELETE, UPDATE ON tenkyu_events TO {role}")
        c.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {role}")
        try:
            from psycopg.conninfo import make_conninfo
            dsn = make_conninfo(DSN, user=role, password=f"pw-{role}")
            try:
                s = PostgresStore(dsn, prefix=_prefix(), clock=lambda: T0, allow_raw_keys=True)
            except psycopg.OperationalError as e:  # e.g. the server requires a password for other roles
                pytest.skip(f"cannot log in as a second role here: {e}")
            try:
                s.add("k", "v", ts=T0)
                assert s.count("k", 10, T0) == 1
            finally:
                s.close()
                _forget(s.prefix)
        finally:
            c.execute(f"DROP OWNED BY {role}")
            c.execute(f"DROP ROLE {role}")


# --- lazy import: these need no database
def test_import_tenkyu_does_not_import_psycopg():
    code = "import sys, tenkyu; print('psycopg' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=_child_env(), timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "False"


def test_missing_driver_gives_an_install_hint(monkeypatch):
    monkeypatch.setitem(sys.modules, "psycopg", None)  # makes `import psycopg` raise ImportError
    with pytest.raises(ImportError, match=r"tenkyu\[postgres\]"):
        PostgresStore("postgresql://nobody@127.0.0.1:1/none", allow_raw_keys=True)


def test_postgres_store_is_listed_in_all_and_exported_lazily():
    import tenkyu
    assert "PostgresStore" in tenkyu.__all__
    from tenkyu.postgres_store import PostgresStore as direct
    assert tenkyu.PostgresStore is direct
