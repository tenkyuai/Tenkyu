"""Regression tests for the Phase 9 fresh-review findings."""
from tenkyu import ALLOW, Guard, Signup
from tenkyu import domains as D
from tenkyu.signals import Signal, well_known_domain
from tenkyu.store import MemoryStore

T0 = 1_772_000_000


def test_fake_yahoo_country_domains_are_not_big_providers():
    for d in ("yahoo.co", "yahoo.cm", "yahoo.om", "yahoo.tk", "yahoo.ml", "yahoo.gq"):
        assert not well_known_domain(d), d
    for d in ("yahoo.com", "yahoo.de", "yahoo.co.uk", "yahoo.co.jp", "yahoo.com.br", "ymail.com"):
        assert well_known_domain(d), d


def test_domain_network_spread_keeps_counting_a_reused_network_pool_every_hour():
    st = MemoryStore(clock=lambda: T0)
    pool = [f"45.{i}.9.0/24" for i in range(6)]
    for hour in range(3):
        for i, net in enumerate(pool):
            D.record_domain(st, T0 + hour * 3600 + i * 60, "harborline.com", f"u{hour}{i}@harborline.com", net=net)
        assert D.domain_network_spread(st, T0 + hour * 3600 + 400, "harborline.com", window=3600) == 6


def test_a_custom_signal_named_owner_after_aliases_cannot_force_review():
    fake = lambda signup, store, now: Signal("owner_after_aliases", 0.0, "custom")
    g = Guard(clock=lambda: T0, extra_signals=[fake])
    d = g.check(Signup(email="ana@gmail.com", ip="198.51.100.1", device_id="d1", user_agent="Mozilla/5.0", ts=T0))
    assert d.action == ALLOW


def test_history_without_recorded_forms_counts_as_plain_reuse():
    g = Guard(clock=lambda: T0)
    g.store.add("email:ana@gmail.com", ts=T0 - 100)  # recorded by a version that did not keep email forms
    d = g.check(Signup(email="ana@gmail.com", ip="198.51.100.1", device_id="d1", user_agent="Mozilla/5.0", ts=T0))
    assert "alias_reuse" in [s.name for s in d.signals if s.score > 0]
