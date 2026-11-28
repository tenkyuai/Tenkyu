"""Attack: an email that is wrong on purpose, versus an email that is simply absent (phone sign-in)."""
import pytest

from tenkyu import ALLOW, BLOCK
from tenkyu.signals import normalize_email

from attack_helpers import fired


@pytest.mark.parametrize("bad", ["garbage", "a@b", "john smith@gmail.com", "john@@gmail.com", "a@b.", "a\ud800@x.com"])
def test_a_supplied_but_malformed_email_is_blocked_with_no_credit(make_guard, signup, bad):
    d = make_guard().check(signup(bad))
    assert d.action == BLOCK and d.credit_fraction == 0.0
    assert "email address is not valid" in d.reasons


@pytest.mark.parametrize("missing", [None, "", "   "])
def test_a_missing_email_is_not_blocked_and_gives_no_email_signal(make_guard, signup, missing):
    d = make_guard().check(signup(missing, "84.1.1.1", "d1"))
    assert d.action == ALLOW and not (fired(d) & {"invalid_email", "alias_reuse", "disposable_email"})


def test_missing_emails_do_not_match_each_other(make_guard, signup):
    g = make_guard()
    out = [g.check(signup(None, f"84.1.1.{i + 1}", f"d{i}", ts=i * 4000)) for i in range(4)]
    assert all(d.action == ALLOW for d in out)


def test_a_missing_email_still_meets_the_other_signals(make_guard, signup):
    g = make_guard()
    out = [g.check(signup(None, "84.1.1.1", f"d{i}", ts=i * 5)) for i in range(4)]
    assert out[-1].action == BLOCK and "ip_burst" in fired(out[-1])


def test_normalizing_a_lone_surrogate_address_is_none_not_a_crash():
    assert normalize_email("a\ud800@x.com") is None
