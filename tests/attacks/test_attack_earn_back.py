"""Red-team of Guard.earn_back / redeem_earn_back / mark_blocked (attacks that work assert the SAFE behaviour)."""
import threading

import pytest

from tenkyu import Guard, Signup
from tenkyu.signals import Signal
from attack_helpers import T0, UA

BOT = "python-requests/2.31"


def sg(email, ip="198.51.100.1", dev="d1", ua=BOT, ts=0, links=None):
    return Signup(email=email, ip=ip, device_id=dev, user_agent=ua, ts=T0 + ts, links=links)


def review(g, email="maya.cohen@gmail.com", **kw):
    d = g.check(sg(email, **kw))
    assert d.action == "review" and d.earn_back["requires"] == "phone_or_card", d
    return d


def test_attack_one_phone_in_national_and_international_form():
    """+972501234567, 0501234567 and +9720501234567 are one phone; the cap of 2 accounts per phone must hold."""
    g = Guard(clock=lambda: T0)
    forms = ["+972501234567", "0501234567", "+972 50 123 4567", "050-123-4567", "+9720501234567"]
    names = ["maya.cohen", "dan.levi", "noa.barak", "yossi.mizrahi", "tamar.azulay"]
    got = []
    for i, f in enumerate(forms):
        d = review(g, f"{names[i]}@gmail.com", ip=f"198.51.{i}.1", dev=f"x{i}")
        got.append(g.earn_back(d, "phone_verified", {"phone": f}) > 0)
    assert sum(got) <= 2, got


def test_honest_family_two_accounts_one_phone_both_released():
    g = Guard(clock=lambda: T0)
    a, b = review(g, "maya.cohen@gmail.com"), review(g, "dan.levi@gmail.com", ip="192.0.2.1", dev="d2")
    assert g.earn_back(a, "phone_verified", {"phone": "+972 50-123-4567"}) == 0.75
    assert g.earn_back(b, "phone_verified", {"phone": "+972501234567"}) == 0.75


def test_replay_and_threads_release_once():
    g = Guard(clock=lambda: T0)
    d = review(g)
    out = []
    ts = [threading.Thread(target=lambda: out.append(g.earn_back(d, "phone_verified", {"phone": "+972501234567"}))) for _ in range(10)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert sorted(out) == [0.0] * 9 + [0.75]


def test_block_and_allow_decisions_earn_nothing():
    g = Guard(clock=lambda: T0)
    ok = g.check(Signup(email="real.person@acme.io", ip="203.0.113.5", device_id="z", user_agent=UA, ts=T0))
    assert ok.action == "allow" and g.earn_back(ok, "phone_verified", {"phone": "+15551234567"}) == 0.0
    assert g.earn_back(ok, "email_verified") == 0.0


@pytest.mark.parametrize("variant", ["Maya.Cohen@GMAIL.com", "maya.cohen+farm@gmail.com", "m.a.y.a.cohen@gmail.com"])
def test_mark_blocked_covers_email_variants(variant):
    g = Guard(clock=lambda: T0)
    d = review(g)
    g.mark_blocked(email=variant)
    assert g.earn_back(d, "phone_verified", {"phone": "+972501234567"}) == 0.0


def test_mark_blocked_new_device_and_later_block_decision():
    g = Guard(clock=lambda: T0)
    d = review(g)
    g.mark_blocked(device_id="d1")
    assert g.earn_back(d, "phone_verified", {"phone": "+972501234567"}) == 0.0
    now = [T0]
    g2 = Guard(clock=lambda: now[0])
    d2 = review(g2)
    for i in range(6):
        now[0] = T0 + 10 + i
        b = g2.check(sg("maya.cohen@gmail.com", ip=f"192.0.2.{i + 1}", dev=f"n{i}", ts=10 + i))
        if b.action == "block":
            break
    assert b.action == "block"
    assert g2.earn_back(d2, "phone_verified", {"phone": "+972501234567"}) == 0.0


def test_shadow_mode_never_releases():
    g = Guard(clock=lambda: T0, shadow=True)
    d = g.check(sg("maya.cohen@gmail.com"))
    assert d.action == "allow" and d.earn_back is None and d.would_earn_back
    assert g.earn_back(d, "phone_verified", {"phone": "+972501234567"}) == 0.0


def test_custom_signal_named_like_a_crowd_signal_does_not_downgrade_requires():
    g = Guard(clock=lambda: T0, extra_signals=[lambda s: Signal("ip_burst", 0.5, "ip_burst: custom")])
    d = g.check(Signup(email="maya.cohen@gmail.com", ip="203.0.113.5", device_id="q", user_agent=UA, ts=T0))
    if d.action == "review":
        assert d.earn_back["requires"] == "phone_or_card"


def test_garbage_input_is_refused_cleanly():
    g = Guard(clock=lambda: T0)
    d = review(g)
    for links in ({"phone": "9" * 1000}, {"phone": [1]}, {"phone": True}, {"x" * 99: "1"}, "phone", {"phone": ""}):
        try:
            assert g.earn_back(d, "phone_verified", links) == 0.0
        except ValueError:
            pass
    assert g.earn_back(d, ["phone_verified"], {"phone": "+1"}) == 0.0  # unhashable proof: TypeError today (low; hosted validates first)
    assert g.earn_back(d, "phone_verified", {"card": "4111 1111 1111 1111"}) == 0.75
