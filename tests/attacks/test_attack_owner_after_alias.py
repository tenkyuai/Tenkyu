"""Attack: squat on the alias of someone else's inbox so the real owner is locked out, or grab two grants per inbox."""
import pytest

from tenkyu import ALLOW, BLOCK, REVIEW

from attack_helpers import fired

PAIRS = [
    ("maya.cohen+promo@gmail.com", "mayacohen@gmail.com"),
    ("maya-promo@yahoo.com", "maya@yahoo.com"),
    ("x@maya.fastmail.com", "maya@fastmail.com"),
    ("maya+a@example.org", "maya@example.org"),
]


@pytest.mark.parametrize("alias,plain", PAIRS)
def test_the_owner_after_a_squatter_is_reviewed_with_partial_credit_not_blocked(make_guard, signup, alias, plain):
    g = make_guard()
    assert g.check(signup(alias, "45.32.7.1", "dv-s")).action == ALLOW
    owner = g.check(signup(plain, "79.177.4.2", "dv-o", ts=3600))
    assert owner.action == REVIEW and owner.credit_fraction == 0.25
    assert "owner_after_aliases" in {s.name for s in owner.signals}
    assert "alias_reuse" not in fired(owner)


def test_the_owner_can_only_come_once_and_aliases_after_the_owner_are_blocked(make_guard, signup):
    g = make_guard()
    g.check(signup("maya+a@gmail.com", "45.32.7.1", "dv-s"))
    assert g.check(signup("maya@gmail.com", "79.1.1.1", "dv-o", ts=60)).action == REVIEW
    assert g.check(signup("maya@gmail.com", "79.2.2.2", "dv-o2", ts=120)).action == BLOCK
    assert g.check(signup("maya+b@gmail.com", "79.3.3.3", "dv-o3", ts=180)).action == BLOCK


def test_owner_first_then_alias_is_plain_alias_reuse(make_guard, signup):
    g = make_guard()
    g.check(signup("maya@gmail.com", "79.1.1.1", "dv-o"))
    d = g.check(signup("maya+promo@gmail.com", "79.2.2.2", "dv-o2", ts=60))
    assert d.action == BLOCK and "alias_reuse" in fired(d)
