import pytest

from tenkyu import ALLOW, BLOCK, REVIEW, Guard, MemoryStore, Signup, normalize_email

T0 = 1_700_000_000.0
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"


def make(**kw):
    return Guard(clock=lambda: T0, **kw)


def su(email="real.person@acme.io", ip="203.0.113.5", dev="d1", ua=UA, ts=T0):
    return Signup(email=email, ip=ip, device_id=dev, user_agent=ua, ts=ts)


# --- email normalization
def test_gmail_dots_and_plus_collapse():
    assert normalize_email("J.o.h.n+promo@Gmail.com") == "john@gmail.com"


def test_googlemail_maps_to_gmail():
    assert normalize_email("john@googlemail.com") == "john@gmail.com"


def test_plus_tag_stripped_other_domains():
    assert normalize_email("Ann+x@example.org") == "ann@example.org"


@pytest.mark.parametrize("bad", [None, "", "nope", "a@b", "@x.com", "+t@x.com", 42, "a@@b.com", "x" * 300 + "@a.com"])
def test_invalid_emails_normalize_to_none(bad):
    assert normalize_email(bad) is None


# --- decisions
def test_clean_signup_allowed():
    d = make().check(su())
    assert d.action == ALLOW and d.allowed and d.score < 0.35 and d.reasons == []


def test_disposable_domain_blocked():
    d = make().check(su(email="x1@mailinator.com"))
    assert d.action == BLOCK and any("disposable" in r for r in d.reasons)


def test_disposable_subdomain_caught():
    assert make().check(su(email="a@foo.yopmail.com")).action == BLOCK


def test_alias_reuse_blocked():
    g = make()
    assert g.check(su(email="john@gmail.com")).action == ALLOW
    d = g.check(su(email="j.o.h.n+free2@gmail.com", ip="198.51.100.9", dev="d9"))
    assert d.action == BLOCK and any("alias" in r or "already" in r for r in d.reasons)


def test_ip_burst_blocked():
    g = make()
    out = [g.check(su(email=f"user{i}x@acme.io", ip="10.0.0.1", dev=f"d{i}", ts=T0 + i)) for i in range(3)]
    assert out[0].action == ALLOW and out[2].action == BLOCK


def test_ip_velocity_slow_farm_reviewed_then_blocked():
    g = make()
    acts = [g.check(su(email=f"p{chr(97 + i) * 4}@acme.io", ip="10.0.0.2", dev=f"d{i}", ts=T0 + i * 600)).action for i in range(6)]
    assert acts[0] == ALLOW and acts[-1] in (REVIEW, BLOCK) and acts.index(ALLOW) == 0


def test_device_reuse_escalates():
    g = make()
    a = [g.check(su(email=f"{n}@acme.io", ip=f"10.1.0.{i}", dev="same", ts=T0 + i)).action
         for i, n in enumerate(["alice", "bobby", "carol"])]
    # a shared device alone never blocks: a household's third account goes to review
    assert a[0] == ALLOW and a[1] == REVIEW and a[2] == REVIEW


def test_device_reuse_plus_another_signal_blocks():
    g = make()
    for i, n in enumerate(["alice", "bobby"]):
        g.check(su(email=f"{n}@acme.io", ip=f"10.1.0.{i}", dev="same", ts=T0 + i))
    d = g.check(su(email="carol@mailinator.com", ip="10.1.0.9", dev="same", ts=T0 + 3))
    assert d.action == BLOCK
    assert {"device_reuse", "disposable_email"} <= {s.name for s in d.signals}


def test_pattern_emails_same_ip_flagged():
    g = make()
    g.check(su(email="farmer1@acme.io", ip="10.2.0.1", dev="a", ts=T0))
    g.check(su(email="farmer2@acme.io", ip="10.2.0.1", dev="b", ts=T0 + 4000))
    d = g.check(su(email="farmer3@acme.io", ip="10.2.0.1", dev="c", ts=T0 + 8000))
    assert d.action == BLOCK and any("similar" in r for r in d.reasons)


def test_unrelated_emails_same_ip_not_pattern():
    g = make()
    for i, n in enumerate(["alice", "bobby", "carol"]):
        d = g.check(su(email=f"{n}@acme.io", ip="10.2.0.2", dev=f"x{i}", ts=T0 + i * 4000))
    assert d.action == ALLOW


def test_headless_user_agent_reviewed():
    d = make().check(su(ua="python-requests/2.31"))
    assert d.action == REVIEW and any("automation" in r for r in d.reasons)


def test_missing_user_agent_small_penalty_not_block():
    d = make().check(su(ua=None))
    assert d.action == ALLOW and 0 < d.score < 0.35


def test_invalid_email_blocked():
    d = make().check(su(email="garbage"))
    assert d.action == BLOCK and d.credit_fraction == 0 and "email address is not valid" in d.reasons


def test_dry_run_does_not_record():
    g = make()
    g.check(su(email="dry@acme.io"), record=False)
    assert g.check(su(email="dry@acme.io")).action == ALLOW


def test_window_expiry_burst_resets():
    g = make()
    for i in range(2):
        g.check(su(email=f"u{i}abc@acme.io", ip="10.3.0.1", dev=f"d{i}", ts=T0 + i))
    d = g.check(su(email="later@acme.io", ip="10.3.0.1", dev="dz", ts=T0 + 500))
    assert "signups from this IP in 60s" not in " ".join(d.reasons)


def test_deterministic():
    def run():
        g = make()
        return [g.check(su(email=f"n{i}@mailinator.com", ts=T0 + i)).to_dict() for i in range(3)]
    assert run() == run()


# --- robustness
@pytest.mark.parametrize("junk", [
    Signup(), Signup(email=None, ip=None), Signup(email="‮\u0000@x.com", ip="☃", device_id="\ud83d"),
    Signup(email=123, ip=456, device_id=789, user_agent=0), Signup(ts="nan-ish"), Signup(ts=float("nan")),
    Signup(email="a" * 100000 + "@x.com"),
])
def test_junk_never_raises(junk):
    d = make().check(junk)
    assert d.action in (ALLOW, REVIEW, BLOCK) and 0 <= d.score <= 1


def test_broken_custom_signal_ignored():
    def boom(signup, store, now):
        raise RuntimeError("x")
    assert make(extra_signals=[boom]).check(su()).action == ALLOW


def test_bad_thresholds_rejected():
    with pytest.raises(ValueError):
        Guard(review_at=0.9, block_at=0.2)


def test_store_cap_bounded():
    s = MemoryStore(clock=lambda: T0)
    for i in range(5000):
        s.add("k", i, ts=T0)
    assert s.count("k", 10, T0) <= 2000


def test_to_dict_shape():
    d = make().check(su(email="q@mailinator.com")).to_dict()
    assert set(d) == {"action", "score", "reasons", "credit_fraction", "signals"} and d["signals"][0]["name"]


# --- IPv6: one network hands out 2**64 addresses, so counting exact addresses lets a farmer rotate for free
def _burst(ips):
    g = Guard()
    ua = "Mozilla/5.0 Chrome/120.0"
    return [g.check(Signup(email=f"user{i}x{i * 7}@acme{i}.io", ip=ip, device_id=f"dev{i}", user_agent=ua,
                           ts=1_700_000_000 + i * 5)).action for i, ip in enumerate(ips)]


def test_rotating_addresses_inside_one_ipv6_network_count_as_one_ip():
    same_v4 = _burst(["203.0.113.9"] * 10)
    one_net_v6 = _burst([f"2001:db8:abcd:12::{i + 1:x}" for i in range(10)])
    assert same_v4[-1] == BLOCK
    assert one_net_v6 == same_v4  # same volume from one network is judged like the same volume from one IPv4


def test_different_ipv6_networks_are_not_pooled():
    assert set(_burst([f"2001:db8:abcd:{i:x}::1" for i in range(10)])) == {ALLOW}


def test_ipv4_mapped_ipv6_counts_with_plain_ipv4():
    mixed = _burst(["1.2.3.4", "::ffff:1.2.3.4", "1.2.3.4"])
    assert mixed[-1] == BLOCK and _burst(["1.2.3.4"] * 3)[-1] == BLOCK


def test_ip_with_spaces_brackets_or_zone_still_works():
    assert _burst([" [2001:db8::1] ", "2001:db8::2", "2001:db8::3"])[-1] == BLOCK
    assert _burst(["fe80::1%eth0", "fe80::2%eth0", "fe80::3%eth0"])[-1] == BLOCK


def test_opaque_non_ip_strings_are_kept_as_they_are():
    assert _burst(["proxy-a"] * 3)[-1] == BLOCK and set(_burst(["a", "b", "c"])) == {ALLOW}


def test_trailing_dot_domain_is_the_same_mailbox_and_still_disposable():
    # "gmail.com." is a valid fully qualified name; it must not dodge alias or disposable checks
    from tenkyu.signals import normalize_email
    assert normalize_email("j.o.h.n@gmail.com.") == "john@gmail.com"
    assert normalize_email("x@Mailinator.com.") == "x@mailinator.com"
    g = Guard(clock=lambda: 1_000.0)
    UA = "Mozilla/5.0 Chrome/120"
    assert g.check(Signup(email="john@gmail.com", user_agent=UA)).action == "allow"
    again = g.check(Signup(email="j.o.h.n+x@gmail.com.", user_agent=UA))
    assert any(s.name == "alias_reuse" for s in again.signals)
    d = g.check(Signup(email="x@mailinator.com.", user_agent=UA))
    assert any(s.name == "disposable_email" for s in d.signals)


def test_oversized_device_id_and_ip_do_not_bloat_the_store_but_still_match():
    # client-controlled strings were stored whole: 100 signups with 100 KB values cost ~30 MB
    keys = []

    class SpyStore(MemoryStore):
        def add(self, key, value=None, ts=None):
            keys.append(key)
            return super().add(key, value, ts) if ts is not None else super().add(key, value)

    big_dev, big_ip = "d" * 100_000, "9" * 100_000
    g = Guard(store=SpyStore(clock=lambda: 1_000.0), clock=lambda: 1_000.0)
    UA = "Mozilla/5.0 Chrome/120"
    g.check(Signup(email="one111@x.io", device_id=big_dev, ip=big_ip, user_agent=UA))
    d = g.check(Signup(email="two222@x.io", device_id=big_dev, ip=big_ip, user_agent=UA))
    assert max(len(k) for k in keys) < 400
    assert any(s.name == "device_reuse" for s in d.signals)  # identity survives
    other = g.check(Signup(email="three33@x.io", device_id=big_dev[:-1] + "e", ip="203.0.113.9", user_agent=UA))
    assert not any(s.name == "device_reuse" for s in other.signals)  # near-identical long ids stay distinct


def test_custom_signal_can_read_the_same_ip_key_the_guard_writes_for_ipv6():
    # docs/signals.md shows a custom signal reading store.count(f"ip:{...}"); the guard keys IPv6 by /64,
    # so raw f"ip:{signup.ip}" silently never matched. ip_key() is the public way to build the key.
    from tenkyu import Signal, ip_key

    def quick_repeat(signup, store, now):
        if signup.ip and store.count(f"ip:{ip_key(signup.ip)}", 600, now) >= 1:
            return Signal("quick_repeat", 0.6, "second signup from this IP within 10 minutes")

    UA = "Mozilla/5.0 Chrome/120.0"
    for addr in ("198.51.100.4", "2001:db8::1", "::ffff:198.51.100.4", " [2001:db8::1] "):
        g = Guard(extra_signals=[quick_repeat])
        g.check(Signup(email="one@acme.io", ip=addr, user_agent=UA, ts=1_700_000_000))
        d = g.check(Signup(email="two@acme.io", ip=addr, user_agent=UA, ts=1_700_000_120))
        assert any(s.name == "quick_repeat" for s in d.signals), addr
    assert ip_key("2001:db8::1") == ip_key("2001:db8::ffff") == "2001:db8::/64"
    assert ip_key("::ffff:198.51.100.4") == "198.51.100.4"
