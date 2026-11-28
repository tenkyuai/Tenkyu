"""Defense-mode pass on the v0.2 features: fuzzing, a surge race across threads, regex timing, key injection."""
import random
import threading
import time

import pytest

from tenkyu import ALLOW, Guard, MemoryStore, Signup, SQLiteStore, normalize_email
from tenkyu.signals import link_key

T0 = 1_700_000_000.0
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/120.0 Safari/537.36"

_JUNK = ["", " ", "@", "a@", "@b.c", "a@b@c.d", "a" * 300 + "@x.com", "x@y.com.", "​@gmail.com", "ü@ü.de",
         "a+@gmail.com", "-@yahoo.com", "a-b-c@yahoo.co.uk", "A.B+C@GoogleMail.com", None, 5, 3.2, b"x@y.com", [], {}]
_IPS = ["", " ", "1.2.3.4", "::1", "::ffff:1.2.3.4", "[2001:db8::1]", "999.1.1.1", "x" * 500, None, 7, "10.0.0.1%eth0"]


def _rand_links(r):
    pick = r.random()
    if pick < 0.6:
        return None
    if pick < 0.85:
        return {r.choice(["card", "phone", "oauth", "uid"]): r.choice(["fp_1", "fp_2", "+97250", 42, "v" * 300, "", None, True, 1.5, {"a": 1}])}
    return r.choice([[], "card", {"Bad Name": "x"}, {f"k{i}": "v" for i in range(r.randint(0, 12))}])


@pytest.mark.parametrize("store_kind", ["memory", "sqlite"])
def test_fuzz_signups_never_crash(store_kind, tmp_path):
    r = random.Random(1126)
    store = MemoryStore(clock=lambda: T0) if store_kind == "memory" else SQLiteStore(tmp_path / "f.db", clock=lambda: T0)
    g = Guard(store=store, clock=lambda: T0, starter_fraction=0.5, surge_threshold=5)
    bad_links = 0
    for i in range(2000 if store_kind == "memory" else 400):
        email = r.choice(_JUNK + [f"user{r.randint(0, 50)}{r.choice(['', '+t', '-x'])}@{r.choice(['gmail.com', 'yahoo.com', 'outlook.com'])}"])
        ip = r.choice(_IPS + [f"203.0.{r.randint(0, 3)}.{r.randint(0, 255)}"])
        su = Signup(email=email, ip=ip, device_id=r.choice([None, "", "d1", "d" * 400, 9]), user_agent=r.choice([None, UA, "curl/8"]),
                    ts=r.choice([None, T0 + i, float("nan"), "soon", 1e308]), links=_rand_links(r))
        try:
            d = g.check(su)
        except ValueError:
            bad_links += 1  # the only allowed error: invalid links
            continue
        assert d.action in ("allow", "review", "block")
        assert 0.0 <= d.score <= 1.0 and 0.0 <= d.credit_fraction <= 1.0
        d.to_dict()
    assert bad_links > 0
    if hasattr(store, "close"):
        store.close()


def test_bad_links_are_the_only_value_error():
    with pytest.raises(ValueError):
        Guard().check(Signup(email="a@b.com", links={"card": None}))
    Guard().check(Signup(email=None, ip=None, device_id=None, user_agent=None, ts="x"))  # no error


def test_surge_guard_holds_under_parallel_signups(tmp_path):
    path = tmp_path / "race.db"
    released, errors, lock = [], [], threading.Lock()

    def worker(n):
        try:
            g = Guard(store=SQLiteStore(path, clock=lambda: T0, timeout=60), clock=lambda: T0, surge_threshold=5)
            for k in range(4):
                d = g.check(Signup(email=f"w{n}k{k}q@site{n}x{k}.com", ip=f"{20 + n}.{k}.1.1", device_id=f"d{n}{k}", user_agent=UA, ts=T0))
                with lock:
                    released.append(d.credit_fraction)
        except Exception as e:  # surface thread errors instead of a confusing count mismatch
            with lock:
                errors.append(repr(e))

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    assert len(released) == 64
    assert sum(1 for f in released if f > 0) == 5  # exactly the threshold, never more


def test_email_regexes_are_linear_time():
    for evil in ["a" * 50_000 + "@" + "b" * 50_000, "a@" + "." * 50_000, "@" * 50_000, ("a." * 25_000) + "@yahoo.com", "a-" * 25_000 + "@yahoo.com"]:
        t = time.perf_counter()
        normalize_email(evil)
        Guard().check(Signup(email=evil, ip="1.2.3.4", user_agent=UA))
        assert time.perf_counter() - t < 0.5


@pytest.mark.parametrize("name", ["card:x", "card\nemail", "email", "ip", "x:card", "../a", "card ", "c" * 25, "1card", "Card"])
def test_link_names_cannot_forge_store_keys(name):
    if name in ("email", "ip"):  # valid names, but they land under the x: prefix, never as email:/ip: keys
        assert link_key(name, "v").startswith(f"x:{name}:")
        return
    with pytest.raises(ValueError):
        link_key(name, "v")


def test_link_key_never_contains_the_value():
    assert "secret-phone" not in link_key("phone", "secret-phone")
    assert len(link_key("phone", "+972501234567").split(":")[2]) == 32


def test_shadow_mode_does_not_consume_the_surge_budget():
    g = Guard(clock=lambda: T0, surge_threshold=2, shadow=True)
    for i in range(5):
        g.check(Signup(email=f"s{i}z@site{i}.com", ip=f"{30 + i}.1.1.1", user_agent=UA, ts=T0))
    g.shadow = False
    assert g.check(Signup(email="real@site9.com", ip="99.9.9.9", user_agent=UA, ts=T0)).credit_fraction == 1.0


def test_import_and_cli_work_without_sqlite3():
    # Regression (2026-11-26): `import tenkyu` failed on Pythons without sqlite3 (found running it in Pyodide).
    import subprocess, sys
    code = ("import sys; sys.modules['sqlite3'] = None; sys.path.insert(0, 'src'); import tenkyu; "
            "from tenkyu import Guard, Signup; Guard().check(Signup(email='a@b.com')); "
            "from tenkyu.cli import main; assert main(['audit', '--sample', '--json']) == 0; "
            "import pytest\ntry:\n    tenkyu.SQLiteStore\nexcept ImportError: print('lazy ok')")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=str(__import__('pathlib').Path(__file__).resolve().parent.parent))
    assert r.returncode == 0, r.stderr
    assert "lazy ok" in r.stdout
