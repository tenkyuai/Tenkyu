"""narrow_cluster: a small residential pool that sits in one /24 (the shape wide_spread cannot see)."""
import random

import pytest

from tenkyu import ALLOW, BLOCK, REVIEW, BlindStore, Guard, MemoryStore, Signup
from tenkyu import proxy

T0 = 1_700_000_000.0
SECRET = b"proxy-test-secret-0123456789abcdef"
CHROME = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
IPHONE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
          "Version/17.1 Mobile/15E148 Safari/604.1")


def chrome(v):
    return CHROME.replace("Chrome/130", f"Chrome/{v}")


@pytest.fixture(params=["memory", "blind"])
def store(request):
    inner = MemoryStore(clock=lambda: T0)
    return inner if request.param == "memory" else BlindStore(inner, SECRET)


def run(store, events):
    blind = getattr(store, "blind", None) or (lambda v: v)
    fired = []
    for ts, ip, ua in events:
        fired.append({s.name for s in proxy.signals(store, ts, ip, ua, None, blind)})
        proxy.record(store, ts, ip, ua, blind=blind)
    return fired


def pool(n=12, base="98.207.14", gap=900, ua=CHROME):
    return [(T0 + i * gap, f"{base}.{20 + i * 7}", ua) for i in range(n)]


def test_one_slash24_pool_with_one_client_fires_from_the_third_ip(store):
    fired = run(store, pool())
    assert not any("narrow_cluster" in f for f in fired[:2])
    assert all("narrow_cluster" in f for f in fired[2:])
    assert "narrow_cluster" in proxy.SOFT_SIGNALS


def test_same_ip_again_and_again_is_not_a_cluster_of_ips(store):
    events = [(T0 + i * 300, "172.56.9.20" if i % 2 else "172.56.9.21", IPHONE) for i in range(20)]
    assert not any("narrow_cluster" in f for f in run(store, events))


def test_varied_browsers_in_one_slash24_do_not_fire(store):
    events = [(T0 + i * 600, f"98.207.14.{20 + i * 7}", chrome(120 + i % 10)) for i in range(15)]
    assert not any("narrow_cluster" in f for f in run(store, events))


def test_one_browser_dominating_a_carrier_spread_over_many_slash24s_does_not_fire(store):
    rng = random.Random(5)
    events = [(T0 + i * 60, f"110.70.{rng.randrange(12)}.{rng.randrange(2, 250)}", IPHONE) for i in range(300)]
    fired = run(store, events)
    assert sum("narrow_cluster" in f for f in fired) <= len(fired) // 20


def test_clusters_older_than_the_window_are_forgotten(store):
    events = pool(6, gap=60) + [(T0 + 7 * 3600 + i * 60, f"98.207.14.{100 + i}", CHROME) for i in range(2)]
    assert not any("narrow_cluster" in f for f in run(store, events)[6:])


def test_ipv6_pool_in_one_slash48_fires(store):
    events = [(T0 + i * 600, f"2001:db8:5:{i + 1:x}::/64", CHROME) for i in range(8)]
    assert any("narrow_cluster" in f for f in run(store, events))


def test_stores_never_hold_the_raw_ip():
    inner = MemoryStore(clock=lambda: T0)
    run(BlindStore(inner, SECRET), pool())
    dump = repr(dict(inner._events))
    assert "98.207" not in dump
    assert any(k.startswith("narrowua:") for k in inner._events)


def _guard():
    now = [T0]
    return Guard(clock=lambda: now[0]), now


def _names(n):
    first = "liam noah emma olivia ava mia lucas ethan maya omar yael noa daniel sofia ivan anna pedro lucia".split()
    last = "cohen levi smith garcia kim lee wang chen novak kowalski rossi mueller schmidt ivanov petrov".split()
    return [f"{first[i % len(first)]}.{last[(i * 7) % len(last)]}{'' if i < len(first) else i}@gmail.com" for i in range(n)]


def test_office_in_one_slash24_with_one_browser_and_fresh_devices_is_never_flagged_alone():
    g, now = _guard()
    out = []
    for i, name in enumerate(_names(12)):
        now[0] = T0 + i * 1800
        out.append(g.check(Signup(email=name, ip=f"212.143.5.{i + 1}", device_id=f"dv-{i}", user_agent=CHROME, ts=now[0])))
    assert {d.action for d in out} == {ALLOW}


def test_honest_vpn_users_with_varied_browsers_on_six_exits_are_allowed():
    g, now = _guard()
    out = []
    for i, name in enumerate(_names(30)):
        now[0] = T0 + i * 600
        out.append(g.check(Signup(email=name, ip=f"185.220.101.{10 + i % 6}", device_id=f"d{i}",
                                  user_agent=chrome(126 + i % 6), ts=now[0])))
    assert {d.action for d in out} == {ALLOW}


def test_cluster_only_counts_next_to_a_real_signal():
    """Same pool twice: with plain emails the soft cluster signal is dropped, with a disposable domain it joins in."""
    for plain in (True, False):
        g, now = _guard()
        out = []
        for i, name in enumerate(_names(10)):
            now[0] = T0 + i * 900
            email = name if plain else name.split("@")[0] + "@mailinator.com"
            out.append(g.check(Signup(email=email, ip=f"98.207.14.{20 + i * 7}", device_id=f"dv-{i}", user_agent=CHROME, ts=now[0])))
        fired = {s.name for s in out[-1].signals if s.score > 0}
        assert ("narrow_cluster" in fired) is (not plain), fired
        assert out[-1].action == (ALLOW if plain else BLOCK)
