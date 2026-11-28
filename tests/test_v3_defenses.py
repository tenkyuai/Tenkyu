"""v0.2 defenses ported from the 2026-11-26 attack run. One test per attack or honest-user case."""
import pytest

from tenkyu import ALLOW, BLOCK, REVIEW, Guard, MemoryStore, Signup, SQLiteStore, normalize_email

T0 = 1_700_000_000.0
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/120.0 Safari/537.36"


@pytest.fixture(params=["memory", "sqlite"])
def make(request, tmp_path):
    made = []

    def _make(**kw):
        store = MemoryStore(clock=lambda: T0) if request.param == "memory" else SQLiteStore(tmp_path / f"t{len(made)}.db", clock=lambda: T0)
        made.append(store)
        return Guard(store=store, clock=lambda: T0, **kw)

    yield _make
    for s in made:
        if hasattr(s, "close"):
            s.close()


def su(email, ip, dev=None, ts=T0, links=None):
    return Signup(email=email, ip=ip, device_id=dev, user_agent=UA, ts=ts, links=links)


# --- normalization
def test_yahoo_hyphen_aliases_collapse():
    assert normalize_email("farmer-1@yahoo.com") == normalize_email("farmer-77@yahoo.com") == "farmer@yahoo.com"
    assert normalize_email("ana-maria@outlook.com") == "ana-maria@outlook.com"  # hyphens elsewhere are real


def test_zero_width_characters_are_stripped():
    assert normalize_email("jo​hn@gmail.com") == "john@gmail.com"


def test_yahoo_hyphen_farm_is_blocked(make):
    g = make()
    first = g.check(su("farmer-0@yahoo.com", "88.1.1.1"))
    later = [g.check(su(f"farmer-{i}@yahoo.com", f"88.{i}.2.2", ts=T0 + i * 900)) for i in range(1, 6)]
    assert first.action == ALLOW
    assert all(d.action == BLOCK for d in later)


# --- squatting
def test_owner_is_not_punished_for_an_earlier_alias(make):
    g = make()
    squatter = g.check(su("maya.cohen+promo@gmail.com", "45.32.7.1", "dv-atk"))
    owner = g.check(su("mayacohen@gmail.com", "79.177.4.2", "dv-maya", ts=T0 + 3600))
    assert squatter.action == ALLOW
    assert owner.action == REVIEW and owner.credit_fraction == 0.25  # 0.2.2: partial credit, never a block
    assert any(r.startswith("plain address after earlier alias signups") for r in owner.reasons)


def test_owner_with_dots_in_gmail_is_not_punished(make):
    # Regression (2026-11-26): an owner who writes their address with a dot was treated as an alias.
    g = make()
    g.check(su("maya.cohen+promo@gmail.com", "45.32.7.1", "dv-atk"))
    owner = g.check(su("maya.cohen@gmail.com", "79.177.4.2", "dv-maya", ts=T0 + 3600))
    assert owner.action == REVIEW  # owner precedence kept (not a block); 0.2.2 gives partial credit


def test_dot_trick_without_tags_is_still_blocked(make):
    g = make()
    g.check(su("mayacohen@gmail.com", "79.1.1.1"))
    assert g.check(su("maya.cohen@gmail.com", "79.2.2.2", ts=T0 + 60)).action == BLOCK


def test_plain_address_reused_is_still_blocked(make):
    g = make()
    g.check(su("mayacohen@gmail.com", "79.1.1.1"))
    again = g.check(su("mayacohen@gmail.com", "79.2.2.2", ts=T0 + 60))
    assert again.action == BLOCK


def test_alias_after_owner_is_blocked(make):
    g = make()
    g.check(su("mayacohen@gmail.com", "79.1.1.1"))
    assert g.check(su("maya.cohen+2@gmail.com", "79.2.2.2", ts=T0 + 60)).action == BLOCK


# --- custom links
def test_shared_card_links_fresh_accounts(make):
    g = make()
    a = g.check(su("ann@outlook.com", "79.1.1.1", links={"card": "fp_9xK2"}))
    b = g.check(su("bob@proton.me", "84.2.2.2", ts=T0 + 3600, links={"card": "fp_9xK2"}))
    assert a.action == ALLOW
    assert b.action == BLOCK and "shares a card with another account" in b.reasons


def test_link_values_are_stored_hashed():
    store = MemoryStore(clock=lambda: T0)
    Guard(store=store, clock=lambda: T0).check(su("a@outlook.com", "1.2.3.4", links={"phone": "+972501234567"}))
    assert not any("972501234567" in k for k in store._events)
    assert any(k.startswith("x:phone:") for k in store._events)


@pytest.mark.parametrize("bad", [{"Card!": "x"}, {"card": ""}, {"card": None}, {"card": {"a": 1}}, {"card": 1.5},
                                 {"card": True}, ["card"], "card", {f"k{i}": "v" for i in range(9)}])
def test_bad_links_rejected_and_nothing_written(bad):
    store = MemoryStore(clock=lambda: T0)
    with pytest.raises(ValueError):
        Guard(store=store, clock=lambda: T0).check(su("u@outlook.com", "1.2.3.4", links=bad))
    assert not store._events


# --- networks
def test_fast_burst_from_one_network_gets_review(make):
    # 0.2.2: a burst with no other risk signal needs 15 signups in 10 minutes (it was 5)
    g = make()
    ds = [g.check(su(f"user{i}x{i * 7}@outlook.com", f"203.0.113.{i + 1}", f"dv-{i}", ts=T0 + i * 40)) for i in range(16)]
    assert [d.action for d in ds[:14]] == [ALLOW] * 14
    assert all(d.action in (REVIEW, BLOCK) for d in ds[14:])


def test_honest_office_of_nine_in_a_day_is_allowed(make):
    g = make()
    ds = [g.check(su(f"person{i}q{i * 3}@acme-corp.io", f"212.143.5.{i + 1}", f"dv-{i}", ts=T0 + i * 1800)) for i in range(9)]
    assert all(d.action == ALLOW for d in ds)


# --- starter budget and surge guard
def test_starter_budget_is_off_by_default(make):
    assert make().check(su("ana@outlook.com", "1.2.3.4")).credit_fraction == 1.0


def test_starter_budget_caps_unverified_signups(make):
    g = make(starter_fraction=0.5)
    plain = g.check(su("ana@outlook.com", "1.2.3.4"))
    verified = g.check(su("ben@outlook.com", "5.6.7.8", ts=T0 + 60, links={"phone": "+972500000001"}))
    assert (plain.action, plain.credit_fraction, plain.limit) == (ALLOW, 0.5, "starter budget until verified")
    assert (verified.credit_fraction, verified.limit) == (1.0, None)


def test_surge_guard_stops_a_fresh_account_farm(make):
    g = make(starter_fraction=0.5, surge_threshold=8)
    ds = [g.check(su(f"q{i}z{i * 11}@domain{i}.com", f"{60 + i}.{i}.9.1", f"dv-{i}", ts=T0 + i * 40)) for i in range(20)]
    assert all(d.action == ALLOW for d in ds)
    assert sum(d.credit_fraction for d in ds) == 8 * 0.5  # 20 accounts get 4 budgets' worth instead of 20
    assert ds[-1].limit == "surge guard: verify a phone or card to unlock"


def test_surge_guard_lets_verified_users_through_and_resets(make):
    g = make(surge_threshold=3)
    for i in range(3):
        g.check(su(f"r{i}w@site{i}.com", f"{70 + i}.1.1.1", f"dv-{i}", ts=T0 + i))
    held = g.check(su("held@site9.com", "99.1.1.1", "dv-9", ts=T0 + 10))
    verified = g.check(su("v@site8.com", "98.1.1.1", "dv-8", ts=T0 + 20, links={"card": "fp_v"}))
    later = g.check(su("later@site7.com", "97.1.1.1", "dv-7", ts=T0 + 3700))
    assert held.credit_fraction == 0.0
    assert verified.credit_fraction == 1.0
    assert later.credit_fraction == 1.0


@pytest.mark.parametrize("kw", [{"starter_fraction": 1.5}, {"starter_fraction": -0.1}, {"surge_threshold": 0}, {"surge_threshold": True}, {"surge_threshold": 2.5}])
def test_bad_settings_rejected(kw):
    with pytest.raises(ValueError):
        Guard(**kw)


# --- shadow mode
def test_shadow_mode_never_refuses_but_reports(make):
    g = make(shadow=True, surge_threshold=1)
    g.check(su("john@gmail.com", "79.1.1.1"))
    d = g.check(su("j.o.h.n+2@gmail.com", "79.2.2.2", ts=T0 + 60))
    assert (d.action, d.credit_fraction, d.shadow_action) == (ALLOW, 1.0, BLOCK)
    assert d.to_dict()["shadow_action"] == BLOCK


def test_shadow_mode_still_remembers_signups(make):
    g = make(shadow=True)
    g.check(su("john@gmail.com", "79.1.1.1"))
    g.shadow = False
    assert g.check(su("j.o.h.n+3@gmail.com", "79.3.3.3", ts=T0 + 60)).action == BLOCK


def test_to_dict_shape_unchanged_by_default(make):
    assert set(make().check(su("ana@outlook.com", "1.2.3.4")).to_dict()) == {"action", "score", "reasons", "credit_fraction", "signals"}


def test_for_ai_apps_preset_and_overrides():
    g = Guard.for_ai_apps()
    assert (g.starter_fraction, g.surge_threshold, g.shadow) == (0.5, 25, False)
    assert Guard.for_ai_apps(surge_threshold=100, shadow=True).surge_threshold == 100
