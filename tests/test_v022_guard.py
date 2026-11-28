import warnings

import pytest

from tenkyu import ALLOW, Guard, Signup

T0 = 1_700_000_000.0
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"
SECRET = b"0123456789abcdef-secret"


def make(**kw):
    return Guard(clock=lambda: T0, **kw)


def su(i, ip=None, dev=None, email=None, ts=None, links=None):
    return Signup(email=email or f"student{i}@campus.example.edu", ip=ip or f"10.20.30.{i % 250 + 1}",
                  device_id=dev or f"dev{i}", user_agent=UA, ts=T0 + i * 400 if ts is None else ts, links=links)


def names(decision):
    return {s.name for s in decision.signals}


def test_honest_users_on_one_busy_network_are_never_flagged_for_network_day():
    g = make(network_daily_cap=None)  # the daily cap on full grants per /24 is tested in test_night2_review.py
    decisions = [g.check(su(i)) for i in range(200)]  # 200 users, one /24, one day (about 400 s apart)
    assert all(d.action == ALLOW for d in decisions)
    assert all("network_day" not in names(d) for d in decisions)


def test_network_day_still_added_when_another_signal_fires():
    g = make()
    for i in range(10):
        g.check(su(i, ts=T0 + i * 3000))
    d = g.check(su(100, email="x@mailinator.com", ip="10.20.30.200", ts=T0 + 40000))
    assert {"disposable_email", "network_day"} <= names(d)
    day = next(s for s in d.signals if s.name == "network_day")
    assert day.score == 0.4  # 10+ earlier signups on the network


def test_network_day_added_for_disposable_email_on_busy_network():
    g = make()
    for i in range(5):
        g.check(su(i, ts=T0 + i * 3000))
    d = g.check(su(50, email="x@mailinator.com", ts=T0 + 20000))
    assert {"disposable_email", "network_day"} <= names(d)


def test_network_burst_still_fires_on_its_own_with_a_crowd():
    g = make()
    out = [g.check(su(i, ts=T0 + i * 30)) for i in range(15)]  # 15 in 7.5 minutes
    assert "network_burst" not in names(out[13])
    assert "network_burst" in names(out[14])
    assert all("network_day" not in names(d) for d in out)
    assert out[14].action != ALLOW


def test_link_secret_validation():
    for bad in ("secret-secret-secret", b"short", b"", 12345, ["x"]):
        with pytest.raises(ValueError):
            Guard(link_secret=bad)
    assert Guard(link_secret=SECRET).link_secret == SECRET
    assert Guard.for_ai_apps(link_secret=SECRET).link_secret == SECRET
    assert Guard(link_secret=bytearray(SECRET)).link_secret == SECRET


def test_same_link_with_same_secret_matches_across_signups():
    g = make(link_secret=SECRET)
    first = g.check(su(1, links={"phone": "+972501234567"}))
    second = g.check(su(2, ip="198.51.100.9", links={"phone": "+972501234567"}))
    assert "link_reuse" not in names(first)
    assert "link_reuse" in names(second)


def test_different_secret_does_not_match_stored_links():
    store_g = make(link_secret=SECRET)
    store_g.check(su(1, links={"phone": "+972501234567"}))
    other = Guard(store=store_g.store, clock=lambda: T0, link_secret=b"another-secret-of-16+bytes")
    assert "link_reuse" not in names(other.check(su(2, ip="198.51.100.9", links={"phone": "+972501234567"})))


def test_warns_once_when_links_used_without_secret():
    g = make()
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        g.check(su(0))  # no links: nothing to warn about
        assert not w
        g.check(su(1, links={"phone": "+972501234567"}))
        g.check(su(2, ip="198.51.100.9", links={"phone": "+972509999999"}))
    assert len(w) == 1
    assert "link_secret" in str(w[0].message) and w[0].filename == __file__


def test_warning_is_per_guard_instance():
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        make().check(su(1, links={"card": "fp_1"}))
        make().check(su(1, links={"card": "fp_1"}))
    assert len(w) == 2


def test_no_warning_with_secret():
    g = make(link_secret=SECRET)
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        g.check(su(1, links={"phone": "+972501234567"}))
    assert not w
