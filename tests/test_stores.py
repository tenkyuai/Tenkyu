import sqlite3
import threading

import pytest

from tenkyu import BLOCK, BlindStore, Guard, MemoryStore, Signup, SQLiteStore

T0 = 1_700_000_000.0
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/120.0 Safari/537.36"


SECRET = b"unit-test-secret-0123456789abcdef"


@pytest.fixture(params=["memory", "sqlite", "blind-memory", "blind-sqlite"])
def store(request, tmp_path):
    if request.param == "memory":
        yield MemoryStore(clock=lambda: T0)
    elif request.param == "blind-memory":
        yield BlindStore(MemoryStore(clock=lambda: T0), SECRET)
    elif request.param == "blind-sqlite":
        s = BlindStore(SQLiteStore(tmp_path / "b.db", clock=lambda: T0), SECRET)
        yield s
        s.close()
    else:
        s = SQLiteStore(tmp_path / "t.db", clock=lambda: T0)
        yield s
        s.close()


# --- contract: both stores must behave identically
def test_count_within_window(store):
    for i in range(3):
        store.add("k", "v", ts=T0 - i)
    assert store.count("k", 10, T0) == 3
    assert store.count("k", 1, T0) == 2  # T0 and T0-1 inclusive
    assert store.count("other", 10, T0) == 0


def test_distinct_and_values(store):
    for v in ["a", "b", "a", None]:
        store.add("k", v, ts=T0)
    assert store.distinct("k", 10, T0) == 3
    b = store.blind  # the identity for plain stores, the keyed hash for blinded ones
    assert store.values("k", 10, T0) == {b("a"), b("b"), None}


def test_window_excludes_old_and_future(store):
    store.add("k", 1, ts=T0 - 100)
    store.add("k", 2, ts=T0 + 100)
    store.add("k", 3, ts=T0)
    assert store.values("k", 10, T0) == {store.blind(3)}


def test_default_now_uses_clock(store):
    store.add("k", "x")
    assert store.count("k", 5) == 1


def test_retention_purges(tmp_path):
    for s in (MemoryStore(clock=lambda: T0, retention=100), SQLiteStore(tmp_path / "r.db", clock=lambda: T0, retention=100)):
        s.add("k", 1, ts=T0)
        s.add("k", 2, ts=T0 + 500)
        assert s.count("k", 10_000, T0 + 500) == 1


def test_per_key_cap(store):
    for i in range(2100):
        store.add("k", i, ts=T0)
    assert store.count("k", 10, T0) == 2000


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf"), "x"])
def test_nonfinite_ts_uses_clock(store, bad):
    store.add("k", 1, ts=T0)
    store.add("k", 2, ts=bad)
    assert store.count("k", 10, T0) == 2


def test_nonfinite_now_uses_clock(store):
    store.add("k", 1, ts=T0)
    assert store.count("k", 10, float("nan")) == 1


# --- sqlite specifics
def test_persists_across_reopen(tmp_path):
    p = tmp_path / "p.db"
    with SQLiteStore(p, clock=lambda: T0) as s:
        s.add("email:a@b.co", ts=T0)
    with SQLiteStore(p, clock=lambda: T0) as s2:
        assert s2.count("email:a@b.co", 60, T0) == 1


def test_two_handles_on_one_file_share_state(tmp_path):
    p = tmp_path / "shared.db"
    a, b = SQLiteStore(p, clock=lambda: T0), SQLiteStore(p, clock=lambda: T0)
    a.add("k", "from-a", ts=T0)
    assert b.values("k", 10, T0) == {"from-a"}
    a.close(); b.close()


def test_guard_remembers_alias_across_restart(tmp_path):
    p = tmp_path / "g.db"
    g1 = Guard(store=SQLiteStore(p, clock=lambda: T0), clock=lambda: T0)
    assert g1.check(Signup(email="john@gmail.com", ip="1.1.1.1", user_agent=UA, ts=T0)).allowed
    g1.store.close()
    g2 = Guard(store=SQLiteStore(p, clock=lambda: T0), clock=lambda: T0)
    assert g2.check(Signup(email="j.o.h.n+x@gmail.com", ip="2.2.2.2", user_agent=UA, ts=T0)).action == BLOCK


def test_thread_smoke_sqlite(tmp_path):
    s = SQLiteStore(tmp_path / "th.db", clock=lambda: T0)
    g = Guard(store=s, clock=lambda: T0)
    errors = []

    def work(n):
        try:
            for i in range(60):
                g.check(Signup(email=f"t{n}u{i}@acme.io", ip=f"10.{n}.0.{i % 4}", device_id=f"d{n}", user_agent=UA, ts=T0 + i))
        except Exception as e:  # pragma: no cover
            errors.append(e)

    ts = [threading.Thread(target=work, args=(n,)) for n in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errors
    s.close()


def test_bad_path_raises_cleanly(tmp_path):
    with pytest.raises(sqlite3.OperationalError):
        SQLiteStore(tmp_path / "no" / "such" / "dir" / "x.db")


def test_memory_path_works():
    with SQLiteStore(clock=lambda: T0) as s:
        s.add("k", "v", ts=T0)
        assert s.count("k", 5, T0) == 1


def test_unhashable_value_does_not_crash_add(tmp_path):
    s = SQLiteStore(tmp_path / "u.db", clock=lambda: T0)
    s.add("k", object(), ts=T0)  # default=str fallback
    assert s.count("k", 5, T0) == 1
    s.close()


def test_guard_decisions_identical_on_both_stores(tmp_path):
    def run(store):
        g = Guard(store=store, clock=lambda: T0)
        out = []
        for i in range(8):
            out.append(g.check(Signup(email=f"user{i}@acme.io", ip="3.3.3.3", device_id=f"d{i % 3}", user_agent=UA, ts=T0 + i * 20)).to_dict())
        return out
    assert run(MemoryStore(clock=lambda: T0)) == run(SQLiteStore(tmp_path / "eq.db", clock=lambda: T0))


def test_second_process_writes_are_seen_by_parent(tmp_path):
    """A different OS process (no shared handle) records a signup; this process must see it."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    import tenkyu

    src = str(Path(tenkyu.__file__).resolve().parent.parent)
    db = str(tmp_path / "shared.db")
    child = (
        "import sys\n"
        "from tenkyu import Guard, Signup, SQLiteStore\n"
        "s = SQLiteStore(sys.argv[1])\n"
        "d = Guard(store=s).check(Signup(email='john@gmail.com', ip='203.0.113.7', device_id='dev-1', user_agent=sys.argv[2]))\n"
        "s.close()\n"
        "print(d.action)\n"
    )
    env = {**os.environ, "PYTHONPATH": src + os.pathsep + os.environ.get("PYTHONPATH", "")}
    done = subprocess.run([sys.executable, "-c", child, db, UA], capture_output=True, text=True, env=env, timeout=60)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "allow"

    store = SQLiteStore(db)
    try:
        again = Guard(store=store).check(
            Signup(email="j.o.h.n+free2@gmail.com", ip="198.51.100.9", device_id="dev-2", user_agent=UA))
    finally:
        store.close()
    assert again.action == BLOCK
    assert any("already signed up" in r for r in again.reasons)


@pytest.mark.parametrize("kind", ["memory", "sqlite"])
def test_default_retention_covers_device_window(kind, tmp_path):
    """device_reuse looks back 7 days; the default store must keep events that long."""
    day = 86400.0
    clock = lambda: T0 + 6 * day  # noqa: E731
    store = MemoryStore(clock=clock) if kind == "memory" else SQLiteStore(tmp_path / "r.db", clock=clock)
    try:
        g = Guard(store=store)
        for i, email in enumerate(("a@acme.io", "b@acme.io")):
            g.check(Signup(email=email, ip=f"203.0.113.{i}", device_id="dev-1", user_agent=UA, ts=T0))
        g.check(Signup(email="c@acme.io", ip="198.51.100.9", device_id="dev-1", user_agent=UA, ts=T0 + 6 * day))
        # c's write used to purge a@ and b@ (1-day retention), so the next account saw only 2 accounts
        third = g.check(Signup(email="d@acme.io", ip="198.51.100.8", device_id="dev-1", user_agent=UA, ts=T0 + 6.1 * day))
    finally:
        if kind == "sqlite":
            store.close()
    dev = [s for s in third.signals if s.name == "device_reuse"]
    assert dev and dev[0].score == 0.65


def test_sqlite_store_rejects_none_path_instead_of_creating_a_file_named_none(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(TypeError):
        SQLiteStore(None)
    assert list(tmp_path.iterdir()) == []


def test_failed_sqlite_setup_closes_its_connection(tmp_path, monkeypatch):
    opened = []
    real_connect = sqlite3.connect

    def spy(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        opened.append(conn)
        return conn

    monkeypatch.setattr("tenkyu.sqlite_store.sqlite3.connect", spy)
    junk = tmp_path / "junk.db"
    junk.write_text("this is not a sqlite file")
    with pytest.raises(sqlite3.DatabaseError):
        SQLiteStore(str(junk))
    assert len(opened) == 1
    with pytest.raises(sqlite3.ProgrammingError):  # "Cannot operate on a closed database"
        opened[0].execute("SELECT 1")


def test_memory_store_forgets_keys_whose_events_all_expired():
    # retention only trimmed a key when that same key was written again, so one-off emails/IPs
    # (the common case) stayed in memory forever
    now = [0.0]
    st = MemoryStore(clock=lambda: now[0], retention=86400.0)
    for i in range(1500):
        st.add(f"k{i}")
    now[0] += 2 * 86400.0
    for i in range(1600):
        st.add(f"fresh{i}")
    assert len(st._events) <= 1700  # the 1500 expired keys are gone (was 3100)
    assert st.count("fresh5", 60) == 1


def test_sqlite_store_deletes_expired_rows_for_keys_that_never_return(tmp_path):
    now = [0.0]
    path = tmp_path / "e.db"
    st = SQLiteStore(path, clock=lambda: now[0], retention=86400.0)
    for i in range(1100):
        st.add(f"k{i}")
    now[0] += 2 * 86400.0
    for i in range(1100):
        st.add(f"fresh{i}")
    rows = st._db.execute("select count(*) from events").fetchone()[0]
    assert rows < 1500  # the 1100 expired rows are gone (was 2200)
    st.close()


def test_sqlite_store_cleans_expired_rows_when_reopened(tmp_path):
    # a process that restarts before 1000 adds must still not grow the file forever
    now = [0.0]
    path = tmp_path / "e.db"
    st = SQLiteStore(path, clock=lambda: now[0], retention=86400.0)
    for i in range(50):
        st.add(f"k{i}")
    st.close()
    now[0] += 2 * 86400.0
    again = SQLiteStore(path, clock=lambda: now[0], retention=86400.0)
    assert again._db.execute("select count(*) from events").fetchone()[0] == 0
    again.close()


# --- check-then-record must be atomic: parallel requests for one person cannot all be allowed

def _parallel_aliases(guard, n):
    bar = threading.Barrier(n)
    out = [None] * n

    def work(i):
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


def test_parallel_signups_for_one_person_allow_only_the_first_memory_store():
    class SlowCount(MemoryStore):  # widens the read-then-write window so the race is not luck
        def count(self, key, window, now=None):
            import time as _t
            _t.sleep(0.002)
            return super().count(key, window, now)

    for _ in range(3):
        out = _parallel_aliases(Guard(store=SlowCount()), 12)
        assert out.count("allow") == 1, out


def test_parallel_signups_for_one_person_allow_only_the_first_sqlite_store(tmp_path):
    for r in range(5):
        guard = Guard(store=SQLiteStore(tmp_path / f"p{r}.db", timeout=60))
        out = _parallel_aliases(guard, 16)
        assert out.count("allow") == 1, out


def _proc_worker(path, i, barrier, q):
    g = Guard(store=SQLiteStore(path))
    barrier.wait()
    q.put(g.check(Signup(email=f"j.o.h.n+{i}@gmail.com", ip=f"198.51.100.{i}", user_agent=UA)).action)


@pytest.mark.skipif(not hasattr(__import__("os"), "fork"), reason="needs fork")
def test_parallel_processes_sharing_one_sqlite_file_allow_only_the_first(tmp_path):
    import multiprocessing as mp
    ctx = mp.get_context("fork")
    for r in range(3):
        path = tmp_path / f"m{r}.db"
        SQLiteStore(path).close()
        barrier, q = ctx.Barrier(6), ctx.Queue()
        ps = [ctx.Process(target=_proc_worker, args=(path, i, barrier, q)) for i in range(6)]
        for p in ps:
            p.start()
        for p in ps:
            p.join(30)
        out = [q.get(timeout=5) for _ in ps]
        assert out.count("allow") == 1, out


# --- lock contention: wait a bounded, configurable time, then raise; housekeeping never blocks startup

def _hold_write_lock(path):
    other = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    other.execute("BEGIN IMMEDIATE")
    return other


def test_sqlite_lock_wait_is_configurable_and_raises_instead_of_allowing(tmp_path):
    import time as _t
    path = tmp_path / "busy.db"
    guard = Guard(store=SQLiteStore(path, timeout=0.2))
    other = _hold_write_lock(path)
    try:
        start = _t.time()
        with pytest.raises(sqlite3.OperationalError):
            guard.check(Signup(email="a@x.io", ip="203.0.113.1", user_agent=UA))
        assert _t.time() - start < 2.0  # not the old fixed 5 s
    finally:
        other.execute("ROLLBACK")
        other.close()
    # nothing was half-recorded, and the store works again once the lock is gone
    assert guard.check(Signup(email="a@x.io", ip="203.0.113.1", user_agent=UA)).action != BLOCK


def test_sqlite_open_does_not_fail_when_another_process_holds_the_write_lock(tmp_path):
    path = tmp_path / "busy2.db"
    SQLiteStore(path).close()
    other = _hold_write_lock(path)
    try:
        import time as _t
        start = _t.time()
        st = SQLiteStore(path)  # default 5 s timeout: the open-time sweep is housekeeping, skip it at once
        assert _t.time() - start < 1.0
        st.close()
    finally:
        other.execute("ROLLBACK")
        other.close()


@pytest.mark.parametrize("bad", [0, -1, "5", None, float("nan"), True])
def test_sqlite_timeout_must_be_a_positive_number(tmp_path, bad):
    with pytest.raises((TypeError, ValueError)):
        SQLiteStore(tmp_path / "t.db", timeout=bad)


@pytest.mark.parametrize("empty", ["", "   "])
def test_sqlite_store_rejects_an_empty_path_instead_of_a_throwaway_database(empty):
    # SQLite treats "" as a private temporary database: history silently never persists, which is what
    # happens when SQLiteStore(os.environ.get("TENKYU_DB", "")) runs with the variable unset
    with pytest.raises(ValueError):
        SQLiteStore(empty)



def test_many_processes_opening_a_new_file_at_once(tmp_path):
    # Regression (2026-11-26): changing the journal mode skips SQLite's busy wait, so simultaneous first opens of
    # a new file failed with "database is locked" about 1 time in 27 (16 at once). Setup now retries.
    errors = []
    for t in range(40):
        path, bar = tmp_path / f"fresh{t}.db", threading.Barrier(16)

        def opener():
            bar.wait()
            try:
                SQLiteStore(path, timeout=30).close()
            except Exception as e:
                errors.append(repr(e))

        threads = [threading.Thread(target=opener) for _ in range(16)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
    assert not errors, errors[:3]
