"""Attack: one inbox, many addresses. Each family below must collapse to one identity."""
import pytest

from tenkyu import ALLOW, BLOCK

from attack_helpers import fired

FAMILIES = [
    ("gmail dots and plus", "john@gmail.com", "j.o.h.n+free2@gmail.com"),
    ("googlemail domain", "john@gmail.com", "john@googlemail.com"),
    ("trailing dot in domain", "john@gmail.com", "john@gmail.com."),
    ("yahoo hyphen keyword", "maya@yahoo.com", "maya-promo@yahoo.com"),
    ("ymail hyphen keyword", "maya@ymail.com", "maya-x@ymail.com"),
    ("fastmail subdomain", "maya@fastmail.com", "anything@maya.fastmail.com"),
    ("icloud me.com", "maya@icloud.com", "maya@me.com"),
    ("icloud mac.com", "maya@icloud.com", "maya@mac.com"),
    ("proton protonmail.com", "maya@proton.me", "maya@protonmail.com"),
    ("proton pm.me", "maya@proton.me", "maya@pm.me"),
]


@pytest.mark.parametrize("label,first,second", FAMILIES, ids=[f[0] for f in FAMILIES])
def test_alias_of_a_signed_up_inbox_is_not_a_fresh_account(make_guard, signup, label, first, second):
    g = make_guard()
    assert g.check(signup(first, "45.32.7.1", "dv-1")).action == ALLOW
    again = g.check(signup(second, "79.177.4.2", "dv-2", ts=3600))  # new IP and device: only the inbox links them
    assert again.action == BLOCK and again.credit_fraction == 0.0
    assert "alias_reuse" in fired(again)


def test_a_burst_of_aliases_all_get_blocked_after_the_first(make_guard, signup):
    g = make_guard()
    out = [g.check(signup(f"fa.rm+{i}@gmail.com", f"198.51.100.{i + 1}", f"dev{i}", ts=i * 30)) for i in range(8)]
    assert out[0].action == ALLOW
    assert all(d.action == BLOCK for d in out[1:])


@pytest.mark.parametrize("a,b", [
    ("maya@outlook.com", "maya@hotmail.com"),
    ("maya@outlook.com", "maya@live.com"),
    ("maya@hotmail.com", "maya@live.com"),
])
def test_microsoft_domains_are_not_merged_so_honest_users_are_not_punished(make_guard, signup, a, b):
    g = make_guard()
    g.check(signup(a, "45.32.7.1", "dv-1"))
    assert g.check(signup(b, "79.177.4.2", "dv-2", ts=3600)).action == ALLOW


def test_dots_outside_gmail_are_different_people(make_guard, signup):
    g = make_guard()
    g.check(signup("jo.hn@example.org", "45.32.7.1", "dv-1"))
    assert g.check(signup("john@example.org", "79.177.4.2", "dv-2", ts=3600)).action == ALLOW
