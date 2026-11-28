"""Attack: the same card or phone written differently. Links must match across formats, and only across formats."""
import pytest

from tenkyu import BLOCK
from tenkyu.signals import link_key

from attack_helpers import fired

SECRET = b"0123456789abcdef-secret"


@pytest.mark.parametrize("secret", [None, SECRET], ids=["sha256", "hmac"])
@pytest.mark.parametrize("variant", [
    "+14155550100", "+1 (415) 555-0100", "+1-415-555-0100", "  +1 415 555 0100  ", "001 415 555 0100",
])
def test_phone_formats_share_one_key(secret, variant):
    assert link_key("phone", variant, secret) == link_key("phone", "+14155550100", secret)


def test_phone_trunk_marker_is_dropped():
    assert link_key("phone", "+44 (0)20 7946 0958") == link_key("phone", "0044 20 7946 0958")


@pytest.mark.parametrize("variant", ["CARD_9xK2", "card-9xk2", " card.9xk2 ", "Card 9xK2"])
def test_card_fingerprint_formatting_does_not_make_a_new_card(variant):
    assert link_key("card", variant) == link_key("card", "card9xk2")


def test_different_numbers_and_cards_stay_apart():
    assert link_key("phone", "+14155550100") != link_key("phone", "+14155550101")
    assert link_key("card", "fp_9xK2") != link_key("card", "fp_9xK3")


def test_the_secret_changes_every_key():
    assert link_key("phone", "+14155550100", SECRET) != link_key("phone", "+14155550100")


def test_reusing_a_card_across_unrelated_accounts_blocks(make_guard, signup):
    g = make_guard()
    g.check(signup("a@outlook.com", "84.1.1.1", "d1", links={"card": "fp_9xK2"}))
    d = g.check(signup("b@proton.me", "84.2.2.2", "d2", ts=60, links={"card": "FP-9xk2"}))
    assert d.action == BLOCK and "link_reuse" in fired(d)


def test_the_same_phone_in_another_format_blocks(make_guard, signup):
    g = make_guard()
    g.check(signup("a@outlook.com", "84.1.1.1", "d1", links={"phone": "+14155550100"}))
    d = g.check(signup("b@proton.me", "84.2.2.2", "d2", ts=60, links={"phone": "+1 (415) 555-0100"}))
    assert d.action == BLOCK and "link_reuse" in fired(d)


def test_a_national_number_matches_its_international_forms():
    """Red-team: one phone in national and international form must be one phone (the 2-account cap depends on it)."""
    k = link_key("phone", "+972501234567")
    for form in ("0501234567", "+9720501234567", "00972 50-123-4567", "+972 (0) 50 123 4567"):
        assert link_key("phone", form) == k, form
    assert link_key("phone", "0501234567", None, "972") == link_key("phone", "+972501234567", None, "+972")
    assert link_key("phone", "0501234567", None, "1") != link_key("phone", "+972501234567", None, "1")  # exact with a country
