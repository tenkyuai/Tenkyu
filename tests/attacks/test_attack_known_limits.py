"""The honest limits, pinned. These tests PASS when Tenkyu fails, on purpose: they stop the README and the code
drifting apart. If one starts failing, the defence got better: update the README, docs/threat-model-and-attacks.md and this file."""
from tenkyu import ALLOW, BLOCK, Guard

from attack_helpers import T0, UA, fired


def _fresh_everything(signup, i):
    """Each account: its own inbox on an unrelated provider, home IP, device and a normal browser, a day apart."""
    return signup(f"person{i}x{i * 977}@outlook.com", f"{60 + i}.{10 + i}.{20 + i}.{30 + i}", f"fresh-dev-{i}", ts=i * 86_400)


def test_a_patient_farmer_with_a_fresh_inbox_ip_and_device_gets_every_grant(make_guard, signup):
    g = make_guard()
    out = [g.check(_fresh_everything(signup, i)) for i in range(10)]
    assert all(d.action == ALLOW and d.credit_fraction == 1.0 for d in out)


def test_the_ai_preset_halves_what_that_farmer_gets_but_does_not_stop_them(signup):
    g = Guard.for_ai_apps(clock=lambda: T0)
    out = [g.check(_fresh_everything(signup, i)) for i in range(10)]
    assert all(d.action == ALLOW and d.credit_fraction == 0.5 for d in out)


def test_only_a_link_the_farmer_must_reuse_closes_the_gap(make_guard, signup):
    g = make_guard()
    first = g.check(signup("person0@outlook.com", "60.10.20.30", "fresh-dev-0", links={"card": "fp_one_card"}))
    second = g.check(signup("person1@proton.me", "61.11.21.31", "fresh-dev-1", ts=86_400, links={"card": "fp_one_card"}))
    assert first.action == ALLOW and second.action == BLOCK and "link_reuse" in fired(second)


def test_a_farmer_who_buys_a_new_card_per_account_is_not_stopped_by_links(make_guard, signup):
    g = make_guard()
    out = [g.check(signup(f"person{i}x{i * 977}@outlook.com", f"{60 + i}.1.1.1", f"d{i}", ts=i * 86_400, links={"card": f"fp_card_{i}"}))
           for i in range(5)]
    assert all(d.action == ALLOW for d in out)


def test_an_unlisted_throwaway_domain_is_not_known_as_disposable(make_guard, signup):
    d = make_guard().check(signup("x@brand-new-throwaway-domain.example", "84.1.1.1", "d1"))
    assert "disposable_email" not in fired(d)
