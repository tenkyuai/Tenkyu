"""Earn-back credits: a REVIEW decision carries the proof that releases the rest of the grant (Decision.earn_back) and
Guard.earn_back checks it."""
import pytest

from tenkyu import ALLOW, REVIEW, Guard, Signal, Signup

T0 = 1_700_000_000.0
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"
NAMES = ["maya.cohen", "dan.levi", "noa.barak", "yossi.mizrahi", "tamar.azulay", "ron.peretz"]


def make(**kw):
    return Guard(clock=lambda: T0, **kw)


def crowd(g, n=4, ip="203.0.113.5"):
    """Honest people behind one IP: the third and later get the crowd review."""
    return [g.check(Signup(email=f"{NAMES[i]}@gmail.com", ip=ip, device_id=f"dev{i}", user_agent=UA, ts=T0 - 10 + i)) for i in range(n)]


def headless(g, i, **kw):
    return g.check(Signup(email=f"{NAMES[i]}@gmail.com", ip=f"198.51.100.{i + 1}", device_id=f"farm{i}",
                          user_agent="python-requests/2.31", ts=T0, **kw))


def test_allow_and_block_carry_no_earn_back():
    g = make()
    d = g.check(Signup(email="real.person@acme.io", ip="203.0.113.5", device_id="d1", user_agent=UA, ts=T0))
    assert d.action == ALLOW and d.earn_back is None and "earn_back" not in d.to_dict()
    b = g.check(Signup(email="x@mailinator.com", ip="203.0.113.6", user_agent=UA, ts=T0))
    assert b.earn_back is None and g.earn_back(b, "email_verified") == 0.0


def test_crowd_review_needs_only_an_email():
    g = make()
    first, second, third, fourth = crowd(g)
    assert first.earn_back is None and second.earn_back is None
    assert third.action == REVIEW and third.earn_back["requires"] == "email"
    assert third.to_dict()["earn_back"]["requires"] == "email"
    assert g.earn_back(third, "email_verified") == pytest.approx(0.75)
    assert g.earn_back(fourth, "phone_verified", {"phone": "+972501234567"}) == pytest.approx(0.75)  # phone satisfies email too


def test_unusual_review_needs_phone_or_card_and_an_email_is_not_enough():
    g = make()
    d = headless(g, 0)
    assert d.action == REVIEW and d.earn_back["requires"] == "phone_or_card"
    assert g.earn_back(d, "email_verified") == 0.0
    assert g.earn_back(d, "phone_verified") == 0.0  # the verified phone itself must be sent
    assert g.earn_back(d, "card_verified", {"card": "fp_abc123"}) == pytest.approx(0.75)


def test_alias_owner_review_needs_phone_or_card():
    g = make()
    g.check(Signup(email="john+free@gmail.com", ip="198.51.100.1", device_id="a", user_agent=UA, ts=T0))
    owner = g.check(Signup(email="john@gmail.com", ip="198.51.100.2", device_id="b", user_agent=UA, ts=T0))
    assert owner.action == REVIEW and owner.earn_back["requires"] == "phone_or_card"
    assert g.earn_back(owner, "email_verified") == 0.0


def test_custom_signal_named_like_a_crowd_signal_is_not_crowd():
    def fake(signup, store, now):
        return Signal("ip_burst", 0.5, "looks like a crowd")
    g = make(extra_signals=[fake])
    d = g.check(Signup(email="maya.cohen@gmail.com", ip="203.0.113.5", device_id="d1", user_agent=UA, ts=T0))
    assert d.action == REVIEW and d.earn_back["requires"] == "phone_or_card"
    assert g.earn_back(d, "email_verified") == 0.0


def test_one_release_per_signup():
    g = make()
    third = crowd(g)[2]
    assert g.earn_back(third, "email_verified") == pytest.approx(0.75)
    assert g.earn_back(third, "email_verified") == 0.0
    assert g.earn_back(third, "phone_verified", {"phone": "+972501234567"}) == 0.0


def test_unknown_proof_is_refused_and_does_not_use_up_the_release():
    g = make()
    third = crowd(g)[2]
    assert g.earn_back(third, "trust_me") == 0.0
    assert g.earn_back(third, None) == 0.0
    assert g.earn_back(third, "email_verified") == pytest.approx(0.75)


def test_refused_when_the_email_or_device_was_blocked_since():
    g = make()
    third, fourth = crowd(g)[2:]
    g.check(Signup(email="noa.barak+again@gmail.com", ip="203.0.113.9", device_id="dev9", user_agent=UA, ts=T0))  # alias_reuse: block
    assert g.earn_back(third, "email_verified") == 0.0  # same email as the blocked alias
    g.mark_blocked(device_id="dev3")
    assert g.earn_back(fourth, "email_verified") == 0.0


def test_a_block_before_the_signup_does_not_refuse():
    g = make()
    g.mark_blocked(device_id="old-device")
    d = g.check(Signup(email="maya.cohen@gmail.com", ip="198.51.100.1", device_id="old-device", user_agent="python-requests/2.31", ts=T0))
    assert d.action == REVIEW
    assert g.earn_back(d, "card_verified", {"card": "fp_1"}) == pytest.approx(0.75)


def test_one_phone_across_three_accounts_releases_only_two():
    g = make()
    results = [g.earn_back(headless(g, i), "phone_verified", {"phone": "+972 50-123-4567"}) for i in range(3)]
    assert results == [pytest.approx(0.75), pytest.approx(0.75), 0.0]


def test_the_phone_is_linked_so_a_later_signup_with_it_is_link_reuse():
    g = make()
    d = headless(g, 0)
    assert g.earn_back(d, "phone_verified", {"phone": "+972501234567"}) > 0
    later = g.check(Signup(email="dan.levi@gmail.com", ip="198.51.100.50", device_id="z", user_agent=UA, ts=T0,
                           links={"phone": "+972501234567"}))
    assert any(s.name == "link_reuse" for s in later.signals)


def test_earn_back_needs_a_decision():
    with pytest.raises(TypeError):
        make().earn_back(Signup(email="a@gmail.com"), "email_verified")


def test_shadow_decisions_report_the_would_be_requirement_only():
    g = make(shadow=True)
    d = headless(g, 0)
    assert d.action == ALLOW and d.earn_back is None and d.would_earn_back["requires"] == "phone_or_card"
    assert g.earn_back(d, "card_verified", {"card": "fp_1"}) == 0.0  # the full grant was already given
