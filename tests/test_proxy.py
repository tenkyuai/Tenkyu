"""Weak rotating-residential-proxy signals: wide_spread and regular_timing."""
import random

import pytest

from tenkyu import BlindStore, MemoryStore
from tenkyu import proxy
from tenkyu.signals import Signal

T0 = 1_700_000_000.0
SECRET = b"proxy-test-secret-0123456789abcdef"
CHROME = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
HONEST_UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36 Edg/120.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Version/17.1 Safari/605.1.15",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_1 like Mac OS X) AppleWebKit/605.1.15 Version/17.1 Mobile Safari/604.1",
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 Chrome/119.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 12; SM-G991B) AppleWebKit/537.36 Chrome/118.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/120.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPad; CPU OS 16_6 like Mac OS X) AppleWebKit/605.1.15 Version/16.6 Mobile Safari/604.1",
]
LANGS = ["en-US,en;q=0.9", "he-IL,he;q=0.9", "de-DE,de;q=0.8", "en-GB", None]


@pytest.fixture(params=["memory", "blind"])
def store(request):
    inner = MemoryStore(clock=lambda: T0)
    return inner if request.param == "memory" else BlindStore(inner, SECRET)


def run(store, events):
    """events: (ts, ip_key, ua, lang). Mirrors Guard: signals() before record(). Returns the names fired per signup."""
    blind = getattr(store, "blind", None) or (lambda v: v)
    fired = []
    for ts, ip, ua, lang in events:
        sigs = proxy.signals(store, ts, ip, ua, None, blind, accept_language=lang)
        fired.append({s.name for s in sigs})
        proxy.record(store, ts, ip, ua, accept_language=lang, blind=blind)
    return fired


def farm_events(n=30, v6=False, seed=1):
    rng = random.Random(seed)
    span = 3 * 3600
    times = sorted(rng.uniform(0, span) for _ in range(n))
    out = []
    for i, t in enumerate(times):
        if v6:
            ip = f"2001:db8:{i % 25 + 1:x}:{rng.randrange(1, 9999):x}::/64"
        else:
            ip = f"45.12.{i % 25 + 1}.{rng.randrange(1, 255)}"
        out.append((T0 + t, ip, CHROME, "en-US,en;q=0.9"))
    return out


def honest_events(n=40, v6=False, seed=2):
    rng = random.Random(seed)
    times = sorted(rng.uniform(0, 86400) for _ in range(n))
    out = []
    for t in times:
        if v6:
            ip = f"2001:db8:{rng.randrange(1, 60):x}:{rng.randrange(1, 9999):x}::/64"
        else:
            ip = f"73.44.{rng.randrange(1, 60)}.{rng.randrange(1, 255)}"
        out.append((T0 + t, ip, rng.choice(HONEST_UAS), rng.choice(LANGS)))
    return out


def test_wide_prefix_forms():
    assert proxy.wide_prefix("45.12.7.9") == "45.12.0.0/16"
    assert proxy.wide_prefix("2001:db8:1:2::/64") == "2001:db8::/32"
    assert proxy.wide_prefix("2001:db8:1:2::1") == "2001:db8::/32"
    assert proxy.wide_prefix("::ffff:45.12.7.9") == "45.12.0.0/16"
    assert proxy.wide_prefix(" 45.12.7.9 ") == "45.12.0.0/16"


@pytest.mark.parametrize("bad", [None, 7, b"1.2.3.4", "", "  ", "not-an-ip", "999.1.1.1", "1.2.3", "sha256:" + "a" * 64,
                                 "2001:db8::/16", [], {}])
def test_wide_prefix_malformed(bad):
    assert proxy.wide_prefix(bad) is None


def test_rotating_proxy_farm_fires_wide_spread(store):
    fired = run(store, farm_events())
    assert any("wide_spread" in f for f in fired)
    assert "wide_spread" not in fired[8]  # too few signups so far: needs >= 10 with 8 subnets
    # The first firing is the weak tier, the later ones with >= 15 subnets are stronger.
    scores = []
    store2 = MemoryStore(clock=lambda: T0)
    blind = store2.blind
    for ts, ip, ua, lang in farm_events():
        for s in proxy.signals(store2, ts, ip, ua, None, blind, accept_language=lang):
            if s.name == "wide_spread":
                scores.append(s.score)
        proxy.record(store2, ts, ip, ua, accept_language=lang)
    assert scores[0] == 0.35 and scores[-1] == 0.5
    assert all(isinstance(s, float) for s in scores)


def test_farm_signal_is_a_signal_with_reason(store):
    events = farm_events()
    run(store, events[:-1])
    ts, ip, ua, lang = events[-1]
    sigs = proxy.signals(store, ts, ip, ua, None, getattr(store, "blind", lambda v: v), accept_language=lang)
    spread = [s for s in sigs if s.name == "wide_spread"]
    assert len(spread) == 1 and isinstance(spread[0], Signal)
    assert spread[0].score == 0.5 and "45.12.0.0/16" in spread[0].reason
    assert spread[0].name in proxy.SOFT_SIGNALS


def test_farm_with_random_times_does_not_fire_timing(store):
    fired = run(store, farm_events())
    assert not any("regular_timing" in f for f in fired)


def test_ipv6_farm_fires_wide_spread(store):
    events = farm_events(v6=True)
    assert proxy.wide_prefix(events[0][1]) == "2001:db8::/32"
    fired = run(store, events)
    assert any("wide_spread" in f for f in fired)


def test_regular_timing_fires(store):
    rng = random.Random(3)
    events = [(T0 + i * 300 + rng.uniform(-2, 2), f"45.12.{i % 3 + 1}.{i + 1}", CHROME, None) for i in range(14)]
    fired = run(store, events)
    assert not any("regular_timing" in f for f in fired[:5])  # fewer than 6 signups so far
    assert all("regular_timing" in f for f in fired[6:])
    assert not any("wide_spread" in f for f in fired)  # only 3 subnets
    blind = getattr(store, "blind", lambda v: v)
    ts = T0 + 14 * 300
    sig = proxy.signals(store, ts, "45.12.1.200", CHROME, None, blind)
    assert [s.name for s in sig] == ["regular_timing"] and sig[0].score == 0.4


def test_regular_timing_ipv6(store):
    events = [(T0 + i * 120, f"2001:db8:{i % 2 + 1:x}:{i + 1:x}::/64", CHROME, None) for i in range(10)]
    assert any("regular_timing" in f for f in run(store, events))


def test_irregular_or_bursty_timing_does_not_fire(store):
    rng = random.Random(4)
    t = T0
    events = []
    for i in range(20):
        t += rng.expovariate(1 / 300)
        events.append((t, f"45.12.{i % 3 + 1}.{i + 1}", CHROME, None))
    assert not any("regular_timing" in f for f in run(store, events))
    burst = [(T0 + 2000, f"45.12.{i % 3 + 1}.{i + 1}", CHROME, None) for i in range(12)]  # same instant: not a timer
    assert not any("regular_timing" in f for f in run(store, burst))


def test_regular_timing_needs_same_fingerprint(store):
    events = [(T0 + i * 300, f"45.12.{i % 3 + 1}.{i + 1}", HONEST_UAS[i % len(HONEST_UAS)], None) for i in range(14)]
    assert not any("regular_timing" in f for f in run(store, events))


def test_honest_isp_traffic_fires_nothing(store):
    for seed in range(5):
        s = MemoryStore(clock=lambda: T0)
        s = s if isinstance(store, MemoryStore) else BlindStore(s, SECRET)
        fired = run(s, honest_events(seed=seed))
        assert all(not f for f in fired), (seed, fired)


def test_honest_ipv6_traffic_fires_nothing(store):
    fired = run(store, honest_events(v6=True))
    assert all(not f for f in fired)


def test_many_subnets_but_varied_user_agents_is_not_wide_spread(store):
    # 40 signups, 40 subnets, but only a third share the fingerprint: below the 60% share.
    rng = random.Random(5)
    events = []
    for i in range(40):
        ua = CHROME if i % 3 == 0 else rng.choice(HONEST_UAS)
        events.append((T0 + i * 700 + rng.uniform(0, 400), f"45.12.{i + 1}.9", ua, "en-US"))
    assert not any("wide_spread" in f for f in run(store, events))


def test_signals_outside_24h_window_are_forgotten(store):
    events = farm_events(n=30)
    run(store, events)
    late = T0 + 3 * 3600 + 2 * 86400
    blind = getattr(store, "blind", lambda v: v)
    assert proxy.signals(store, late, "45.12.99.1", CHROME, None, blind, accept_language="en-US,en;q=0.9") == []


def test_language_is_part_of_the_fingerprint(store):
    events = [(T0 + i * 400 + (i * 37) % 200, f"45.12.{i + 1}.9", CHROME, "en-US" if i % 2 else "fr-FR") for i in range(30)]
    # Half the signups per language: each fingerprint is only 50% of the /16.
    assert not any("wide_spread" in f for f in run(store, events))


def test_stores_never_hold_raw_values():
    inner = MemoryStore(clock=lambda: T0)
    blinded = BlindStore(inner, SECRET)
    run(blinded, farm_events(12))
    dump = repr(dict(inner._events))
    assert "45.12" not in dump and CHROME not in dump
    assert any(k.startswith("wideua:") for k in inner._events)


def test_timing_key_stays_bounded():
    inner = MemoryStore(clock=lambda: T0)
    for i in range(2300):
        proxy.record(inner, T0 + i, "45.12.1.1", CHROME)
    assert max(len(q) for q in inner._events.values()) <= 2000


@pytest.mark.parametrize("ip", [None, 5, "", "garbage", "sha256:abc", b"1.2.3.4", "2001:db8::/16"])
@pytest.mark.parametrize("ua", [None, "", 12, "   ", CHROME])
def test_malformed_input_is_safe(store, ip, ua):
    blind = getattr(store, "blind", None) or (lambda v: v)
    proxy.record(store, T0, ip, ua)
    assert proxy.signals(store, T0, ip, ua, None, blind) == []


@pytest.mark.parametrize("now", [None, "x", float("nan"), float("inf"), True, object()])
def test_malformed_time_is_safe(store, now):
    blind = getattr(store, "blind", None) or (lambda v: v)
    assert proxy.signals(store, now, "45.12.1.1", CHROME, None, blind) == []
    proxy.record(store, now, "45.12.1.1", CHROME)
    assert not getattr(store, "inner", store)._events  # nothing was written for an unusable timestamp


def test_missing_user_agent_never_fires(store):
    events = [(T0 + i * 300, f"45.12.{i + 1}.1", None, None) for i in range(30)]
    assert all(not f for f in run(store, events))
    assert not any(k.startswith("wide") for k in getattr(store, "inner", store)._events)


def test_broken_store_never_raises():
    class Broken:
        def count(self, *a, **k):
            raise RuntimeError("boom")

        values = count

    assert proxy.signals(Broken(), T0, "45.12.1.1", CHROME, None) == []


def test_fingerprint_ignores_minor_and_build_versions_but_not_the_major():
    ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.6099.1 Safari/537.36"
    assert proxy._fingerprint(ua) == proxy._fingerprint(ua.replace("120.0.6099.1", "120.0.6100.77").replace("10_15_7", "10_15_6"))
    assert proxy._fingerprint(ua) != proxy._fingerprint(ua.replace("Chrome/120", "Chrome/121"))
