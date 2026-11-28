import math
import random
import threading

import pytest

from tenkyu import ALLOW, BLOCK, REVIEW, Guard, MemoryStore, Signup

T0 = 1_700_000_000.0
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/120.0 Safari/537.36"
VALID = {ALLOW, REVIEW, BLOCK}

JUNK = [None, "", " ", "a", "x@y", "\x00", "‮", "\ud83d", "\U0001f600" * 50, "a" * 5000, "@", "@@", "+", 0, -1,
        3.5, float("nan"), float("inf"), True, [], {}, b"bytes", ("t",), object()]


def junk_signup(rng):
    pick = lambda: rng.choice(JUNK + [f"user{rng.randint(0, 40)}@{rng.choice(['gmail.com', 'acme.io', 'mailinator.com'])}",
                                      f"10.0.{rng.randint(0, 3)}.{rng.randint(0, 9)}", f"dev{rng.randint(0, 8)}", UA])
    return Signup(email=pick(), ip=pick(), device_id=pick(), user_agent=pick(),
                  ts=rng.choice([None, T0 + rng.random() * 5000, float("nan"), float("inf"), -float("inf"), "x", -1e300, 1e300]))


def test_seeded_fuzz_never_raises_and_stays_in_range():
    rng = random.Random(1234)
    g = Guard(clock=lambda: T0)
    for _ in range(2000):
        d = g.check(junk_signup(rng))
        assert d.action in VALID
        assert 0.0 <= d.score <= 1.0 and not math.isnan(d.score)
        assert 0.0 <= d.credit_fraction <= 1.0
        d.to_dict()


def test_fuzz_is_deterministic():
    def run():
        rng = random.Random(99)
        g = Guard(clock=lambda: T0)
        return [(d.action, round(d.score, 6)) for d in (g.check(junk_signup(rng)) for _ in range(500))]
    assert run() == run()


# regression: non-finite timestamps used to poison window comparisons / purge the store
@pytest.mark.parametrize("bad_ts", [float("nan"), float("inf"), -float("inf"), "x", None])
def test_bad_timestamp_falls_back_to_clock_and_velocity_still_works(bad_ts):
    g = Guard(clock=lambda: T0)
    out = [g.check(Signup(email=f"u{i}abc@acme.io", ip="10.7.7.7", device_id=f"d{i}", user_agent=UA, ts=bad_ts)) for i in range(3)]
    assert out[2].action == BLOCK  # burst still detected using the clock


def test_infinite_ts_does_not_wipe_earlier_history():
    g = Guard(clock=lambda: T0)
    g.check(Signup(email="keep@acme.io", ip="10.8.8.8", device_id="k", user_agent=UA, ts=T0))
    g.check(Signup(email="boom@acme.io", ip="10.8.8.9", device_id="k2", user_agent=UA, ts=float("inf")))
    assert g.check(Signup(email="keep@acme.io", ip="10.8.8.10", device_id="k3", user_agent=UA, ts=T0)).action == BLOCK


def test_store_ignores_nonfinite_ts():
    s = MemoryStore(clock=lambda: T0)
    s.add("k", 1, ts=T0)
    s.add("k", 2, ts=float("nan"))
    s.add("k", 3, ts=float("inf"))
    assert s.count("k", 10, T0) == 3  # non-finite ts replaced by clock, nothing purged


def test_clock_going_backwards_does_not_crash():
    g = Guard(clock=lambda: T0)
    g.check(Signup(email="a@acme.io", ip="1.1.1.1", user_agent=UA, ts=T0 + 1000))
    d = g.check(Signup(email="b@acme.io", ip="1.1.1.1", user_agent=UA, ts=T0))
    assert d.action in VALID


# boundaries
def test_score_equal_to_threshold_is_that_action():
    from tenkyu.signals import Signal
    g = Guard(clock=lambda: T0, review_at=0.5, block_at=0.9, extra_signals=[lambda s, st, n: Signal("x", 0.5, "r")])
    assert g.check(Signup(email="a@acme.io", user_agent=UA)).action == REVIEW
    g2 = Guard(clock=lambda: T0, review_at=0.5, block_at=0.9, extra_signals=[lambda s, st, n: Signal("x", 0.9, "r")])
    assert g2.check(Signup(email="a@acme.io", user_agent=UA)).action == BLOCK


def test_thresholds_zero_and_one_allowed():
    Guard(review_at=0.0, block_at=0.0)
    Guard(review_at=1.0, block_at=1.0)


def test_signal_score_out_of_range_is_clamped():
    from tenkyu.signals import Signal
    g = Guard(clock=lambda: T0, extra_signals=[lambda s, st, n: Signal("x", 5.0, "huge"), lambda s, st, n: Signal("y", -3.0, "neg")])
    d = g.check(Signup(email="a@acme.io", user_agent=UA))
    assert d.score == 1.0 and d.action == BLOCK


def test_signal_returning_nan_does_not_poison_score():
    from tenkyu.signals import Signal
    g = Guard(clock=lambda: T0, extra_signals=[lambda s, st, n: Signal("x", float("nan"), "nan")])
    d = g.check(Signup(email="a@acme.io", user_agent=UA))
    assert not math.isnan(d.score) and d.action in VALID


def test_identical_signup_twice_is_alias_reuse_not_error():
    g = Guard(clock=lambda: T0)
    s = Signup(email="same@acme.io", ip="2.2.2.2", device_id="x", user_agent=UA, ts=T0)
    g.check(s)
    assert g.check(s).action == BLOCK


# memory / concurrency
def test_many_distinct_keys_stay_bounded_per_key():
    g = Guard(clock=lambda: T0)
    for i in range(3000):
        g.check(Signup(email=f"user{i}@acme.io", ip="9.9.9.9", device_id="same", user_agent=UA, ts=T0 + i * 0.001))
    assert all(len(q) <= 2000 for q in g.store._events.values())


def test_retention_purges_old_events():
    s = MemoryStore(clock=lambda: T0, retention=100)
    s.add("k", 1, ts=T0)
    s.add("k", 2, ts=T0 + 500)
    assert len(s._events["k"]) == 1


def test_thread_safety_smoke():
    g = Guard(clock=lambda: T0)
    errors = []

    def work(n):
        try:
            for i in range(200):
                g.check(Signup(email=f"t{n}u{i}@acme.io", ip=f"10.{n}.0.{i % 5}", device_id=f"d{n}", user_agent=UA, ts=T0 + i))
        except Exception as e:  # pragma: no cover
            errors.append(e)

    ts = [threading.Thread(target=work, args=(n,)) for n in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errors
