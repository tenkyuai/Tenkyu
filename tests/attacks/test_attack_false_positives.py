"""Attack on the defender: make Tenkyu punish honest people. These must stay quiet or soft."""
import random

import pytest

from tenkyu import ALLOW, BLOCK, REVIEW
from tenkyu.signals import headless_agent

from attack_helpers import T0, fired


def test_campus_of_200_honest_users_in_8_hours_stays_under_5_percent_flagged(make_guard, signup):
    # The per-network daily cap (default 30 full grants per /24) deliberately partial-credits a 200-person day on one /24:
    # it cannot tell that from a slow rotating-IP farm. This test is about every other rule, so the cap is off here;
    # the next test pins what the default does to the same campus.
    for seed in (1, 2, 3):
        rng = random.Random(seed)
        g = make_guard(network_daily_cap=None)
        times = sorted(rng.uniform(0, 8 * 3600) for _ in range(200))
        out = [g.check(signup(f"stu{i}x{rng.randint(1, 10**6)}@uni.example.edu", f"198.51.100.{i % 250 + 1}", f"dev{i}", ts=t))
               for i, t in enumerate(times)]
        assert sum(d.action != ALLOW for d in out) < 10, seed


def test_campus_of_200_on_one_slash24_gets_partial_credit_past_the_daily_cap_and_is_never_blocked(make_guard, signup):
    from tenkyu.guard import NETWORK_DAILY_CAP
    rng = random.Random(1)
    g = make_guard()
    times = sorted(rng.uniform(0, 8 * 3600) for _ in range(200))
    out = [g.check(signup(f"stu{i}x{rng.randint(1, 10**6)}@uni.example.edu", f"198.51.100.{i % 250 + 1}", f"dev{i}", ts=t))
           for i, t in enumerate(times)]
    assert not any(d.action == BLOCK for d in out)
    assert sum(d.action == ALLOW for d in out) == NETWORK_DAILY_CAP
    assert all(d.credit_fraction == 0.25 and any(x.name == "network_daily_cap" for x in d.signals) for d in out if d.action == REVIEW)
    # a school that knows it is big raises the cap and is untouched again
    g2 = make_guard(network_daily_cap=250)
    assert all(g2.check(signup(f"stu{i}x{i * 31}@uni.example.edu", f"198.51.100.{i % 250 + 1}", f"dev{i}", ts=t)).action == ALLOW
               for i, t in enumerate(times))


def test_five_signups_in_ten_minutes_on_one_network_is_not_a_signal_alone_but_fifteen_is(make_guard, signup):
    g = make_guard()
    out = [g.check(signup(f"p{i}x{i * 13}@outlook.com", f"198.51.100.{i + 1}", f"dev{i}", ts=i * 30)) for i in range(15)]
    assert all(d.action == ALLOW and "network_burst" not in fired(d) for d in out[:14])
    assert out[14].action == REVIEW and "network_burst" in fired(out[14])


def test_a_family_sharing_one_device_on_a_busy_network_is_reviewed_never_blocked(make_guard, signup):
    g = make_guard()
    for i in range(12):
        g.check(signup(f"user{i}x{i * 7}@outlook.com", f"192.0.2.{i + 1}", f"dev{i}", ts=i * 30))
    out = [g.check(signup(f"fam{i}@outlook.com", f"192.0.2.{50 + i}", "family", ts=360 + i * 20)) for i in range(3)]
    assert out[0].action == ALLOW
    assert all(d.action == REVIEW and d.credit_fraction == 0.25 for d in out[1:])
    assert all(d.action != BLOCK for d in out)


def test_a_shared_device_alone_never_blocks_however_many_accounts(make_guard, signup):
    g = make_guard()
    out = [g.check(signup(f"member{i}x{i * 3}@outlook.com", f"203.0.113.{i + 1}", "household", ts=i * 4000)) for i in range(8)]
    assert max(d.score for d in out) < 0.7 and all(d.action != BLOCK for d in out)


def test_nine_colleagues_on_one_office_ip_over_a_day_are_not_flagged(make_guard, signup):
    g = make_guard()
    names = ["ana", "ben", "cara", "dan", "eli", "fay", "gil", "hana", "ido"]
    out = [g.check(signup(f"{n}@acme.io", "203.0.113.50", f"laptop-{n}", ts=i * 3000)) for i, n in enumerate(names)]
    assert all(d.action == ALLOW for d in out)


@pytest.mark.parametrize("ua", [
    "Mozilla/5.0 (Linux; Android 12; CUBOT P50) AppleWebKit/537.36 Chrome/120.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 13; CUBOT KingKong 9) AppleWebKit/537.36 Chrome/120.0 Mobile Safari/537.36",
])
def test_cubot_phones_are_not_automation(ua):
    assert not headless_agent(ua)


@pytest.mark.parametrize("ua", ["python-requests/2.31", "curl/8.4.0", "Googlebot/2.1", "HeadlessChrome/120.0", "bot/1.0"])
def test_real_automation_agents_still_match(ua):
    assert headless_agent(ua)
