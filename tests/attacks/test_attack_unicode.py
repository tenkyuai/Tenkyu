"""Attack: look-alike characters in the address. NFKC plus invisible-character removal must undo the cheap ones."""
import pytest

from tenkyu import ALLOW, BLOCK
from tenkyu.signals import is_plain, normalize_email

from attack_helpers import fired

CHEAP_TRICKS = [
    ("fullwidth g in domain", "john@\uff47mail.com", "john@gmail.com"),
    ("fullwidth local part", "\uff4aohn@gmail.com", "john@gmail.com"),
    ("soft hyphen in domain", "a@gm\u00adail.com", "a@gmail.com"),
    ("combining grapheme joiner", "a@gm\u034fail.com", "a@gmail.com"),
    ("zero-width space", "a@gm\u200bail.com", "a@gmail.com"),
    ("left-to-right mark", "a@gm\u200eail.com", "a@gmail.com"),
    ("right-to-left override", "a@gm\u202eail.com", "a@gmail.com"),
    ("variation selector", "a@gm\ufe0fail.com", "a@gmail.com"),
]


@pytest.mark.parametrize("label,raw,plain", CHEAP_TRICKS, ids=[t[0] for t in CHEAP_TRICKS])
def test_cheap_unicode_tricks_normalize_to_the_plain_address(label, raw, plain):
    assert normalize_email(raw) == plain


def test_a_trick_address_counts_as_the_same_inbox(make_guard, signup):
    g = make_guard()
    g.check(signup("john@gmail.com", "45.32.7.1", "dv-1"))
    again = g.check(signup("john@\uff47mail.com", "79.177.4.2", "dv-2", ts=3600))
    assert again.action == BLOCK and "alias_reuse" in fired(again)


def test_invisible_character_does_not_hide_a_disposable_domain(make_guard, signup):
    d = make_guard().check(signup("x@mailin\u00adator.com"))
    assert "disposable_email" in fired(d)


def test_invisible_characters_do_not_make_an_alias_look_plain():
    for raw, plain in (("john\u200b@gmail.com", True), ("john\u200b+tag@gmail.com", False)):
        assert is_plain(raw, normalize_email(raw)) is plain


def test_cyrillic_lookalike_domain_is_flagged_as_a_lookalike(make_guard, signup):
    """Was a documented limit (not merged with gmail.com). It is still not merged, but now flagged as a look-alike."""
    g = make_guard()
    g.check(signup("john@gmail.com", "45.32.7.1", "dv-1"))
    d = g.check(signup("john@gm\u0430il.com", "79.177.4.2", "dv-2", ts=3600))
    assert d.action != ALLOW and "lookalike_domain" in [s.name for s in d.signals if s.score > 0]
