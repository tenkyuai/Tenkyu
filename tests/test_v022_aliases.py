import hashlib
import hmac

import pytest

from tenkyu import ALLOW, Guard, Signup
from tenkyu.signals import is_plain, link_key, normalize_email

T0 = 1_700_000_000.0
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"
SECRET = b"0123456789abcdef"


# --- Fastmail subdomain aliases
@pytest.mark.parametrize("addr,expected", [
    ("a@x.fastmail.com", "x@fastmail.com"),
    ("b@x.fastmail.com", "x@fastmail.com"),
    ("x@fastmail.com", "x@fastmail.com"),
    ("anything@user.fastmail.fm", "user@fastmail.fm"),
    ("x@fastmail.fm", "x@fastmail.fm"),
    ("a+tag@x.fastmail.com", "x@fastmail.com"),
])
def test_fastmail_subdomain_collapses(addr, expected):
    assert normalize_email(addr) == expected


def test_fastmail_aliases_share_one_identity():
    ids = {normalize_email(a) for a in ("a@x.fastmail.com", "b@x.fastmail.com", "x@fastmail.com", "X@FASTMAIL.COM")}
    assert ids == {"x@fastmail.com"}


def test_fastmail_fm_stays_separate_from_com():
    assert normalize_email("a@u.fastmail.fm") != normalize_email("a@u.fastmail.com")


@pytest.mark.parametrize("addr", ["a@b.x.fastmail.com", "a@fastmail.com.evil.io", "a@x.fastmail.net", "a@xfastmail.com"])
def test_fastmail_only_exact_one_label_pattern(addr):
    assert normalize_email(addr) == addr


def test_fastmail_mixed_case_zero_width_trailing_dot():
    assert normalize_email("Foo@X.FastMail.COM") == "x@fastmail.com"
    assert normalize_email("a@x​.fast‍mail.com") == "x@fastmail.com"
    assert normalize_email("a@x.fastmail.com.") == "x@fastmail.com"


# --- Proton
@pytest.mark.parametrize("domain", ["protonmail.com", "protonmail.ch", "proton.me", "pm.me"])
def test_proton_domains_canonicalize(domain):
    assert normalize_email(f"x@{domain}") == "x@proton.me"


def test_proton_mixed_case_zero_width_trailing_dot_tag():
    assert normalize_email("Ann+t@PM.me") == "ann@proton.me"
    assert normalize_email("ann@pm​.me") == "ann@proton.me"
    assert normalize_email("ann@protonmail.ch.") == "ann@proton.me"


def test_proton_keeps_dots_in_local():
    assert normalize_email("a.b@proton.me") == "a.b@proton.me"
    assert normalize_email("a.b@proton.me") != normalize_email("ab@pm.me")


# --- iCloud
@pytest.mark.parametrize("domain", ["icloud.com", "me.com", "mac.com"])
def test_icloud_domains_canonicalize(domain):
    assert normalize_email(f"x@{domain}") == "x@icloud.com"


def test_icloud_mixed_case_zero_width_trailing_dot_tag():
    assert normalize_email("Bob+a@ME.com") == "bob@icloud.com"
    assert normalize_email("bob@mac​.com") == "bob@icloud.com"
    assert normalize_email("bob@me.com.") == "bob@icloud.com"


# --- negatives
@pytest.mark.parametrize("domain", ["outlook.com", "hotmail.com", "live.com", "msn.com"])
def test_microsoft_domains_stay_separate(domain):
    assert normalize_email(f"x@{domain}") == f"x@{domain}"


def test_microsoft_domains_not_merged_pairwise():
    out = {normalize_email(f"x@{d}") for d in ("outlook.com", "hotmail.com", "live.com", "msn.com")}
    assert len(out) == 4


@pytest.mark.parametrize("domain", ["outlook.com", "hotmail.com", "live.com", "msn.com", "fastmail.com",
                                    "proton.me", "pm.me", "icloud.com", "me.com", "yahoo.com", "example.org"])
def test_dots_not_stripped_outside_gmail(domain):
    assert normalize_email(f"a.b.c@{domain}").split("@")[0] == "a.b.c"


def test_gmail_still_strips_dots():
    assert normalize_email("a.b@Gmail.com") == "ab@gmail.com"
    assert normalize_email("a.b@googlemail.com") == "ab@gmail.com"


def test_invalid_still_none():
    assert normalize_email("+t@x.fastmail.com") is None
    assert normalize_email("@pm.me") is None


# --- is_plain
@pytest.mark.parametrize("addr,expected", [
    ("a@x.fastmail.com", False),
    ("a@x.fastmail.fm", False),
    ("A@X.FASTMAIL.COM.", False),
    ("x@fastmail.com", True),
    ("x@fastmail.fm", True),
    ("x@pm.me", True),
    ("x@protonmail.com", True),
    ("x@me.com", True),
    ("x@mac.com", True),
    ("x+t@pm.me", False),
    ("a.b@gmail.com", True),
    ("a-b@yahoo.com", False),
    ("a@b.x.fastmail.com", True),
])
def test_is_plain(addr, expected):
    assert is_plain(addr, normalize_email(addr)) is expected


def test_is_plain_false_for_invalid():
    assert is_plain("nope", None) is False
    assert is_plain(None, None) is False


# --- link_key
def test_link_key_without_secret_is_sha256_of_normalized_value():
    # 0.2.2 normalizes before hashing: "fp_123" is stored as "fp123" (underscore dropped)
    expected = "x:card:" + hashlib.sha256(b"fp123").hexdigest()[:32]
    assert link_key("card", "fp_123") == expected
    assert link_key("card", "fp_123", None) == expected
    assert link_key("card", "fp_123", secret=None) == expected
    assert link_key("phone", 12345) == "x:phone:" + hashlib.sha256(b"12345").hexdigest()[:32]


def test_link_key_with_secret_is_hmac():
    k = link_key("card", "fp_123", SECRET)
    assert k == "x:card:" + hmac.new(SECRET, b"fp123", hashlib.sha256).hexdigest()[:32]
    assert k != link_key("card", "fp_123")
    assert len(k.split(":")[2]) == 32 and k.startswith("x:card:")


def test_link_key_secret_stable_and_secret_specific():
    assert link_key("card", "v", SECRET) == link_key("card", "v", SECRET)
    assert link_key("card", "v", SECRET) != link_key("card", "v", b"fedcba9876543210")
    assert link_key("card", "v", SECRET) != link_key("card", "w", SECRET)


def test_link_key_value_is_stripped_with_secret():
    assert link_key("card", " v ", SECRET) == link_key("card", "v", SECRET)


def test_link_key_exactly_16_bytes_ok():
    assert link_key("card", "v", b"x" * 16).startswith("x:card:")


@pytest.mark.parametrize("bad", ["a-string-secret-long-enough", bytearray(b"x" * 32), 12345678901234567, b"", b"x" * 15, [1, 2]])
def test_link_key_bad_secret(bad):
    with pytest.raises(ValueError):
        link_key("card", "v", bad)


def test_link_key_still_validates_name_and_value_with_secret():
    with pytest.raises(ValueError):
        link_key("Bad Name", "v", SECRET)
    with pytest.raises(ValueError):
        link_key("card", "", SECRET)


# --- end to end
def _guard():
    return Guard(clock=lambda: T0)


def _su(email, ip, dev):
    return Signup(email=email, ip=ip, device_id=dev, user_agent=UA, ts=T0)


def test_e2e_proton_alias_swap_not_full_credit():
    g = _guard()
    first = g.check(_su("x@proton.me", "203.0.113.5", "d1"))
    assert first.action == ALLOW and first.credit_fraction == 1.0
    second = g.check(_su("x@pm.me", "198.51.100.77", "d2"))
    assert second.credit_fraction < 1.0
    assert any(s.name == "alias_reuse" for s in second.signals)


def test_e2e_fastmail_and_icloud_alias_not_full_credit():
    g = _guard()
    assert g.check(_su("x@fastmail.com", "203.0.113.5", "d1")).credit_fraction == 1.0
    assert g.check(_su("anything@x.fastmail.com", "198.51.100.77", "d2")).credit_fraction < 1.0
    assert g.check(_su("y@me.com", "192.0.2.10", "d3")).credit_fraction == 1.0
    assert g.check(_su("y@icloud.com", "192.0.2.99", "d4")).credit_fraction < 1.0


def test_e2e_microsoft_domains_not_merged():
    g = _guard()
    assert g.check(_su("x@outlook.com", "203.0.113.5", "d1")).credit_fraction == 1.0
    d = g.check(_su("x@hotmail.com", "198.51.100.77", "d2"))
    assert not any(s.name == "alias_reuse" for s in d.signals)
