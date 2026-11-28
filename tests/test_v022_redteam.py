"""Regression tests for the 2026-11-26 red-team run against 0.2.2 (items 1 to 9)."""
import random

import pytest

from tenkyu import ALLOW, BLOCK, REVIEW, Guard, Signup
from tenkyu.guard import ip_key
from tenkyu.signals import headless_agent, is_plain, link_key, normalize_email

T0 = 1_700_000_000.0
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"
SECRET = b"0123456789abcdef-secret"


def make(**kw):
    return Guard(clock=lambda: T0, **kw)


def su(email, ip="10.0.0.1", dev=None, ts=T0, links=None, ua=UA):
    return Signup(email=email, ip=ip, device_id=dev, user_agent=ua, ts=ts, links=links)


def names(d, firing=True):
    return {s.name for s in d.signals if s.score > 0 or not firing}


# --- 1. a shared device on a busy network never blocks
def _busy_carrier_day(g, n=12):
    for i in range(n):  # honest users on one /24 over the morning
        g.check(su(f"user{i}x{i * 7}@outlook.com", f"192.0.2.{i + 1}", f"dev{i}", ts=T0 + i * 1800))


def test_family_on_a_busy_network_is_reviewed_not_blocked():
    g = make()
    _busy_carrier_day(g)
    first = g.check(su("dad@outlook.com", "192.0.2.100", "family", ts=T0 + 30000))
    second = g.check(su("kid@outlook.com", "192.0.2.101", "family", ts=T0 + 30600))
    third = g.check(su("mum@outlook.com", "192.0.2.102", "family", ts=T0 + 31200))
    assert first.action == ALLOW
    assert second.action == REVIEW and "network_day" not in names(second)
    assert third.action == REVIEW and third.credit_fraction == 0.25
    assert names(third) == {"device_reuse"}


def test_device_reuse_plus_network_burst_alone_is_at_most_review():
    g = make()
    for i in range(12):  # 12 honest signups in 6 minutes
        g.check(su(f"user{i}x{i * 7}@outlook.com", f"192.0.2.{i + 1}", f"dev{i}", ts=T0 + i * 30))
    out = [g.check(su(f"fam{i}@outlook.com", f"192.0.2.{50 + i}", "family", ts=T0 + 360 + i * 20)) for i in range(3)]
    last = out[-1]
    assert names(last) == {"device_reuse", "network_burst"}
    assert last.score >= 0.7  # the numbers alone would block
    assert last.action == REVIEW and last.credit_fraction == 0.25


def test_device_reuse_with_another_real_signal_can_still_block():
    g = make()
    g.check(su("a@mailinator.com", "192.0.2.1", "farm"))
    d = g.check(su("b@mailinator.com", "192.0.2.2", "farm", ts=T0 + 60))
    assert {"device_reuse", "disposable_email"} <= names(d) and d.action == BLOCK


# --- 2. honest campus bursts
def test_campus_of_200_honest_users_in_8_hours_stays_under_5_percent_review():
    for seed in (1, 2, 3):
        rng = random.Random(seed)
        g = make(network_daily_cap=None)  # the daily cap deliberately partial-credits a 200-person day on one /24
        times = sorted(T0 + rng.uniform(0, 8 * 3600) for _ in range(200))
        ds = [g.check(su(f"stu{i}x{rng.randint(1, 10**6)}@uni.example.edu", f"198.51.100.{i % 250 + 1}", f"dev{i}", ts=t))
              for i, t in enumerate(times)]
        flagged = sum(d.action != ALLOW for d in ds)
        assert flagged < 10, (seed, flagged)  # 5% of 200


def test_five_in_ten_minutes_alone_no_longer_flags_but_fifteen_does():
    g = make()
    ds = [g.check(su(f"p{i}x{i * 13}@outlook.com", f"198.51.100.{i + 1}", f"dev{i}", ts=T0 + i * 30)) for i in range(15)]
    assert all(d.action == ALLOW and "network_burst" not in names(d) for d in ds[:14])
    assert "network_burst" in names(ds[14]) and ds[14].action == REVIEW


def test_farm_on_one_network_with_reused_devices_is_still_caught():
    g = make()
    ds = [g.check(su(f"farmer{i}x{i * 11}@outlook.com", f"198.51.100.{i + 1}", f"dev{i % 3}", ts=T0 + i * 30)) for i in range(20)]
    assert ds[0].action == ALLOW
    assert all(d.action != ALLOW for d in ds[3:])


def test_farm_on_one_network_with_aliases_is_blocked_and_burst_counts_with_alias_signal():
    g = make()
    ds = [g.check(su(f"fa.rm+{i}@gmail.com", f"198.51.100.{i + 1}", f"dev{i}", ts=T0 + i * 30)) for i in range(8)]
    assert ds[0].action == ALLOW
    assert all(d.action == BLOCK for d in ds[1:])
    assert "network_burst" in names(ds[5])  # 5+ tier applies next to alias_reuse


# --- 3. alias first, then the owner
@pytest.mark.parametrize("alias,plain", [
    ("maya.cohen+promo@gmail.com", "mayacohen@gmail.com"),
    ("maya-promo@yahoo.com", "maya@yahoo.com"),
    ("x@maya.fastmail.com", "maya@fastmail.com"),
    ("maya+a@example.org", "maya@example.org"),
])
def test_owner_after_aliases_gets_review_not_full_grant_and_not_block(alias, plain):
    g = make()
    squatter = g.check(su(alias, "45.32.7.1", "dv-s"))
    owner = g.check(su(plain, "79.177.4.2", "dv-o", ts=T0 + 3600))
    assert squatter.action == ALLOW
    assert owner.action == REVIEW and owner.credit_fraction == 0.25
    assert "owner_after_aliases" in names(owner, firing=False)
    assert any("earlier alias signups" in r for r in owner.reasons)


def test_owner_precedence_stays_plain_repeat_and_alias_after_owner_still_block():
    g = make()
    g.check(su("maya+a@gmail.com", "45.32.7.1", "dv-s"))
    assert g.check(su("maya@gmail.com", "79.1.1.1", "dv-o", ts=T0 + 60)).action == REVIEW
    assert g.check(su("maya@gmail.com", "79.2.2.2", "dv-o2", ts=T0 + 120)).action == BLOCK  # a repeat plain address
    assert g.check(su("maya+b@gmail.com", "79.3.3.3", "dv-o3", ts=T0 + 180)).action == BLOCK  # an alias after the owner


# --- 4. Unicode tricks
@pytest.mark.parametrize("raw,expected", [
    ("john@\uff47mail.com", "john@gmail.com"),                  # fullwidth g
    ("john@\uff47\uff4d\uff41\uff49\uff4c.com", "john@gmail.com"),
    ("\uff4aohn@gmail.com", "john@gmail.com"),
    ("x@mailin\u00adator.com", "x@mailinator.com"),            # soft hyphen
    ("a@gm\u034fail.com", "a@gmail.com"),                      # combining grapheme joiner
    ("a@gm\u200bail.com", "a@gmail.com"),                      # zero-width space
    ("a@gm\u200eail.com", "a@gmail.com"),                      # left-to-right mark (category Cf)
    ("a@gm\u202eail.com", "a@gmail.com"),                      # right-to-left override (category Cf)
    ("a@gm\ufe0fail.com", "a@gmail.com"),                      # variation selector
    ("j.o\u00adhn+tag@gmail.com", "john@gmail.com"),
])
def test_unicode_variants_normalize_to_the_plain_address(raw, expected):
    assert normalize_email(raw) == expected


def test_unicode_trick_disposable_domain_is_flagged():
    d = make().check(su("x@mailin\u00adator.com"))
    assert "disposable_email" in names(d)


def test_unicode_variants_count_as_one_inbox():
    g = make()
    g.check(su("john@gmail.com", "79.1.1.1", "d1"))
    for i, v in enumerate(["john@\uff47mail.com", "john@gm\u034fail.com", "jo\u00adhn@gmail.com"]):
        assert g.check(su(v, f"79.{i + 2}.1.1", f"d{i + 2}", ts=T0 + 100 + i)).action == BLOCK


def test_is_plain_applies_the_same_cleaning():
    assert is_plain("john@gm\u034fail.com", normalize_email("john@gm\u034fail.com")) is True
    assert is_plain("john\uff0btag@gmail.com", normalize_email("john\uff0btag@gmail.com")) is False  # fullwidth +
    assert normalize_email("john\uff0btag@gmail.com") == "john@gmail.com"


def test_cyrillic_lookalike_domain_is_left_alone():
    assert normalize_email("john@gm\u0430il.com") == "john@gm\u0430il.com"  # documented limit: verify email ownership


# --- 5. link value variants
@pytest.mark.parametrize("secret", [None, SECRET])
def test_phone_variants_share_one_key(secret):
    keys = {link_key(name, v, secret) .split(":")[2] for name in ("phone",) for v in
            ["+14155550100", " +1 (415) 555-0100 ", "+1-415-555-0100", "14155550100", 14155550100, "\uff0b\uff11\uff14\uff11\uff15\uff15\uff15\uff15\uff10\uff11\uff10\uff10",
             "+1 415 555 0100\u200b", "+\u0661415555\u0660100"]}
    assert len(keys) == 1


@pytest.mark.parametrize("name", ["phone", "tel", "mobile"])
def test_phone_like_names_keep_digits_only(name):
    assert link_key(name, "+1 (415) 555-0100") == link_key(name, "14155550100")


def test_phone_digits_do_not_collapse_different_numbers():
    assert link_key("phone", "+14155550100") != link_key("phone", "+14155550101")
    assert link_key("phone", "abc") != link_key("phone", "abd")  # no digits at all: falls back to the text


def test_card_variants_share_one_key_but_other_ids_stay_apart():
    base = link_key("card", "card_AbCd1234")
    assert link_key("card", " CARD_abcd1234\t") == base == link_key("card", "card-Ab Cd.1234")
    assert link_key("card", "card_AbCd1235") != base
    assert link_key("card", "ab") != link_key("oauth", "ab")  # the name is part of the key


def test_guard_matches_link_variants():
    for secret in (None, SECRET):
        g = make(link_secret=secret) if secret else make()
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            g.check(su("a@outlook.com", "79.1.1.1", "d1", links={"phone": "+14155550100"}))
            d = g.check(su("b@proton.me", "84.2.2.2", "d2", ts=T0 + 60, links={"tel": "x"}))  # different name: no match
            assert "link_reuse" not in names(d)
            d = g.check(su("c@proton.me", "84.3.3.3", "d3", ts=T0 + 120, links={"phone": "+1 (415) 555-0100"}))
        assert "link_reuse" in names(d) and d.action == BLOCK


# --- 6. IP evasion: ports and /48
def test_port_suffix_is_dropped_before_keying():
    assert ip_key("203.0.113.7:50000") == ip_key("203.0.113.7") == "203.0.113.7"
    assert ip_key("[2001:db8:abcd:1::5]:50000") == ip_key("2001:db8:abcd:1::9") == "2001:db8:abcd:1::/64"
    assert ip_key("proxy-a:8080") == "proxy-a:8080"  # opaque ids are kept as given


def test_rotating_ports_do_not_dodge_ip_burst():
    g = make()
    ds = [g.check(su(f"v{i}x{i * 5}@outlook.com", f"203.0.113.7:{50000 + i}", f"d{i}", ts=T0 + i * 5)) for i in range(4)]
    # distinct clean identities on one IP: partial credit (crowd rule), and the burst still counts on the one IP
    assert ds[-1].action != "allow" and ds[-1].credit_fraction <= 0.25 and "ip_burst" in names(ds[-1])


def test_rotating_ipv6_64s_inside_one_48_feed_the_network_rules():
    def run(prefix_fn):
        g = make()
        return [g.check(su(f"x{i}@mailinator.com", prefix_fn(i), f"d{i}", ts=T0 + i * 30)) for i in range(6)]
    same_48 = run(lambda i: f"2001:db8:abcd:{i:x}::1")
    other_48s = run(lambda i: f"2001:db8:{i:x}:1::1")
    assert "network_burst" in names(same_48[4]) and "network_day" in names(same_48[4])
    assert all("network_burst" not in names(d) and "network_day" not in names(d) for d in other_48s)


def test_ipv6_48_alone_needs_the_same_crowd_as_an_ipv4_24():
    g = make()
    ds = [g.check(su(f"p{i}x{i * 13}@outlook.com", f"2001:db8:abcd:{i:x}::1", f"d{i}", ts=T0 + i * 30)) for i in range(15)]
    assert all("network_burst" not in names(d) for d in ds[:14]) and "network_burst" in names(ds[14])


# --- 7. invalid email
@pytest.mark.parametrize("bad", ["garbage", "a@b", "john smith@gmail.com", "john@@gmail.com"])
def test_invalid_email_blocks_with_a_clear_reason(bad):
    d = make().check(su(bad))
    assert d.action == BLOCK and d.credit_fraction == 0.0
    assert "email address is not valid" in d.reasons


# --- 8. device ids
def test_device_id_whitespace_is_stripped():
    g = make()
    g.check(su("a@outlook.com", "79.1.1.1", " dev-1 "))
    d = g.check(su("b@outlook.com", "84.2.2.2", "dev-1\t", ts=T0 + 60))
    assert "device_reuse" in names(d)


def test_empty_device_id_after_strip_is_no_device_id():
    g = make()
    for i, blank in enumerate(["", "   ", "\t\n"]):
        d = g.check(su(f"u{i}@outlook.com", f"79.{i}.1.1", blank, ts=T0 + i))
        assert "device_reuse" not in names(d)
    assert not [k for k in g.store._events if k.startswith("devemail:")]


# --- 9. headless_agent token matching
@pytest.mark.parametrize("ua", [
    "Mozilla/5.0 (Linux; Android 11; CUBOT_X30 Build/RP1A) AppleWebKit/537.36 Chrome/120 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 13; CUBOT KINGKONG 9) AppleWebKit/537.36 Chrome/120 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 12; Cubot P60) Chrome/120 Mobile Safari/537.36",
])
def test_cubot_phones_are_not_automation(ua):
    assert headless_agent(ua) is None


@pytest.mark.parametrize("ua", [
    "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
    "Mozilla/5.0 (compatible; bingbot/2.0)", "AhrefsBot/7.0", "bot/1.0", "Bot", "my-bot",
    "Mozilla/5.0 HeadlessChrome/120.0", "python-requests/2.31", "curl/8.4.0", "curl", "Wget/1.21", "wget",
    "Apache-HttpClient/4.5", "PhantomJS/2.1", "selenium", "puppeteer", "playwright", "aiohttp/3.9",
    "Go-http-client/1.1", "okhttp/4.12", "Scrapy/2.11",
])
def test_automation_agents_still_match(ua):
    s = headless_agent(ua)
    assert s is not None and s.name == "automation_agent"


def test_cubot_does_not_push_a_signup_to_review():
    d = make().check(su("a@outlook.com", ua="Mozilla/5.0 (Linux; Android 11; CUBOT_X30) Chrome/120 Mobile Safari/537.36"))
    assert d.action == ALLOW and not d.signals
