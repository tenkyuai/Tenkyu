"""Regression tests for the 0.2.2 code review findings."""
import json
import logging
import time
import warnings

import pytest

from tenkyu import ALLOW, BLOCK, REVIEW, Guard, Signup
from tenkyu import signals as S
from tenkyu.decision_log import DecisionLog, read_log
from tenkyu.signals import Signal, link_key, normalize_email
from tenkyu.sqlite_store import SQLiteStore
from tenkyu.webhooks import signup_from_clerk, signup_from_supabase

T0 = 1_700_000_000.0
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"


def make(**kw):
    return Guard(clock=lambda: T0, **kw)


def names(d):
    return {s.name for s in d.signals}


# --- 1. missing email is not an invalid email
@pytest.mark.parametrize("missing", [None, "", "   ", "\t\n"])
def test_missing_email_is_not_blocked(missing):
    d = make().check(Signup(email=missing, ip="79.1.1.1", device_id="d1", user_agent=UA))
    assert d.action == ALLOW and "invalid_email" not in names(d)
    assert "email address is not valid" not in d.reasons


def test_missing_email_still_gets_other_signals():
    g = make()
    for i in range(3):
        d = g.check(Signup(email=None, ip="79.1.1.1", user_agent=UA, ts=T0 + i))
    assert "ip_burst" in names(d) and d.action == BLOCK
    d = make().check(Signup(email=None, ip="79.1.1.1", user_agent="curl/8.0"))
    assert "automation_agent" in names(d)


def test_missing_email_signups_do_not_match_each_other():
    g = make()
    ds = [g.check(Signup(email="", ip=f"79.1.1.{i}", user_agent=UA, ts=T0 + i * 3600)) for i in range(1, 5)]
    assert all(d.action == ALLOW for d in ds)


@pytest.mark.parametrize("bad", ["garbage", "a@b", 12345, ["a@b.com"], "a\ud800@x.com", "​"])
def test_supplied_but_malformed_email_still_blocks(bad):
    d = make().check(Signup(email=bad, user_agent=UA))
    assert d.action == BLOCK and "email address is not valid" in d.reasons


def test_clerk_phone_user_and_supabase_phone_user_are_not_blocked():
    clerk = signup_from_clerk({"type": "user.created", "data": {"id": "u1", "email_addresses": [], "phone_numbers": [
        {"id": "p1", "phone_number": "+14155550100", "verification": {"status": "verified"}}], "primary_phone_number_id": "p1", "created_at": 1_700_000_000_000}})
    sup = signup_from_supabase({"type": "INSERT", "table": "users", "record": {"id": "u2", "email": "", "phone": "14155550101", "phone_confirmed_at": "2023-11-14T22:13:20Z", "created_at": "2023-11-14T22:13:20Z"}})
    assert clerk is not None and clerk.email is None and sup.email == ""
    g = Guard(shadow=False)
    assert g.check(clerk).action != BLOCK and g.check(sup).action != BLOCK


def test_invalid_email_helper_distinguishes_missing_from_malformed():
    assert S.invalid_email(None, None) is None and S.invalid_email(None, "  ") is None
    assert S.invalid_email(None, "nope").score == 1.0


# --- 2. softness comes only from built-in signals
@pytest.mark.parametrize("name", ["network_proxy", "device_reuse"])
def test_custom_signal_with_a_soft_name_is_not_soft(name):
    g = make(extra_signals=[lambda su, st, now: Signal(name, 0.95, "custom")])
    d = g.check(Signup(email="x@outlook.com", ip="79.1.1.1", device_id="d", user_agent=UA))
    assert d.action == BLOCK  # not downgraded to review


def test_custom_soft_named_signal_counts_as_other_risk_signal():
    g = make(extra_signals=[lambda su, st, now: Signal("network_proxy", 0.2, "custom")])
    ds = [g.check(Signup(email=f"u{i}x{i * 7}@outlook.com", ip=f"10.9.8.{i + 1}", device_id=f"d{i}", user_agent=UA, ts=T0 + i)) for i in range(5)]
    assert "network_burst" in names(ds[4])  # 5th signup on the /24 counts: the custom signal corroborates


def test_builtin_soft_signals_are_still_soft():
    g = make()
    for i in range(3):
        d = g.check(Signup(email=f"fam{i}@outlook.com", ip=f"10.1.1.{i + 1}", device_id="shared", user_agent=UA, ts=T0 + i))
    assert "device_reuse" in names(d) and d.action != BLOCK


# --- 3. lone surrogates
SURROGATE_CASES = [
    Signup(email="a\ud800@x.com", user_agent=UA),
    Signup(email="ok@outlook.com", device_id="\ud800", user_agent=UA),
    Signup(email="ok2@outlook.com", ip="\ud800", user_agent=UA),
    Signup(email="ok3@outlook.com", ip="1.2.3.\udfff", device_id="d\ud83d" * 3, user_agent=UA),
    Signup(email="ok4@outlook.com", ip="\ud800" * 500, device_id="\ud800" * 500, user_agent="\ud800", links={"phone": "\ud800", "card": "x\udc00"}),
]


def test_normalize_email_rejects_lone_surrogate():
    assert normalize_email("a\ud800@x.com") is None and normalize_email("a@x\udc00.com") is None


def test_surrogate_inputs_never_raise_with_sqlite(tmp_path):
    log = DecisionLog(tmp_path / "log.jsonl", secret=b"s" * 16)
    with SQLiteStore(str(tmp_path / "t.db")) as st:
        g = Guard(store=st, log=log)
        for su in SURROGATE_CASES:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                d = g.check(su)
            assert d.action in (ALLOW, REVIEW, BLOCK)
        assert g.check(SURROGATE_CASES[0]).action == BLOCK
    records, bad = read_log(tmp_path / "log.jsonl")
    assert bad == 0 and len(records) == len(SURROGATE_CASES) + 1  # no line silently dropped
    assert "email_hash" not in records[0]


def test_same_surrogate_device_id_still_matches_itself():
    g = make()
    g.check(Signup(email="a@outlook.com", device_id="\ud800", user_agent=UA, ts=T0))
    d = g.check(Signup(email="b@outlook.com", device_id="\ud800", user_agent=UA, ts=T0 + 1))
    assert "device_reuse" in names(d)


def test_decision_log_email_hash_survives_surrogates(tmp_path):
    from tenkyu.decision_log import _email_hash
    assert _email_hash("a\ud800@x.com", b"s" * 16) is None
    log = DecisionLog(tmp_path / "l.jsonl")
    g = make()
    d = g.check(Signup(email="a\ud800@x.com", user_agent=UA))
    log.write(Signup(email="a\ud800@x.com"), d, T0)
    assert json.loads((tmp_path / "l.jsonl").read_text())["action"] == "block"


# --- 5. link_secret warning under -W error
def test_link_secret_warning_does_not_raise_when_warnings_are_errors(caplog):
    g = make()
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        with caplog.at_level(logging.WARNING, logger="tenkyu"):
            d = g.check(Signup(email="a@outlook.com", user_agent=UA, links={"phone": "+14155550100"}))
    assert d.action == ALLOW
    assert any("link_secret" in r.getMessage() for r in caplog.records)


def test_link_secret_warning_still_warns_normally():
    with pytest.warns(UserWarning, match="link_secret"):
        make().check(Signup(email="a@outlook.com", user_agent=UA, links={"phone": "+14155550100"}))


# --- 6. disposable lookup
@pytest.mark.parametrize("domain,expected", [
    ("mailinator.com", True), ("a.b.mailinator.com", True), ("xmailinator.com", False), ("mailinator.com.evil.io", False),
    ("com", False), ("gmail.com", False), ("sub.yopmail.fr", True)])
def test_disposable_lookup_same_results_as_before(domain, expected):
    assert (S.disposable_email(f"a@{domain}") is not None) is expected


def test_disposable_matches_old_linear_scan_on_a_big_list():
    domains = frozenset(f"d{i}.example{i % 97}.com" for i in range(100_000)) | {"evil.io"}
    probes = [f"x{i}.d{i * 7}.example{(i * 7) % 97}.com" for i in range(200)] + ["d5.example5.com", "foo.evil.io", "evil.io", "nope.org", "d5.example6.com"]
    for d in probes:
        old = d in domains or any(d.endswith("." + x) for x in domains)  # the 0.2.2 linear scan
        assert (S.disposable_email(f"u@{d}", domains) is not None) == old
    start = time.perf_counter()
    for i in range(1000):
        S.disposable_email(f"u@a{i}.b.c.nothere.org", domains)
    assert time.perf_counter() - start < 1.0


def test_disposable_accepts_a_plain_list_of_domains():
    assert S.disposable_email("a@x.foo.io", ["foo.io"]) is not None and S.disposable_email("a@bar.io", ["foo.io"]) is None


def test_guard_extra_disposable_still_works():
    d = make(extra_disposable=["burn.example"]).check(Signup(email="a@x.burn.example", user_agent=UA))
    assert "disposable_email" in names(d)


# --- 4. alias families (the merges the changelog lists)
@pytest.mark.parametrize("a,b", [
    ("x@me.com", "x@icloud.com"), ("x@mac.com", "x@icloud.com"), ("x@protonmail.com", "x@proton.me"),
    ("x@protonmail.ch", "x@proton.me"), ("x@pm.me", "x@proton.me"), ("anything@user.fastmail.com", "user@fastmail.com"),
    ("x@googlemail.com", "x@gmail.com")])
def test_alias_families_collapse(a, b):
    assert normalize_email(a) == normalize_email(b)


def test_changelog_and_readme_list_the_merges():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    log = (root / "CHANGELOG.md").read_text().split("## 0.2.1")[0]
    row = next(l for l in (root / "README.md").read_text().splitlines() if l.startswith("| `alias_reuse`"))
    for word in ("fastmail", "me.com", "mac.com", "icloud.com", "protonmail.com", "protonmail.ch", "pm.me", "proton.me"):
        assert word in log
    for word in ("Fastmail", "iCloud", "Proton"):
        assert word in row


# --- 7. phone links
PHONE_FORMS = ["+44 (0)20 7946 0958", "+44 20 7946 0958", "0044 20 7946 0958", "+442079460958", "00442079460958", "+44-20-7946-0958"]


@pytest.mark.parametrize("name", ["phone", "tel", "mobile", "sms", "phone_number", "mobile_phone", "sms_number", "cellphone", "tel_no"])
def test_phone_like_link_names_match_across_formats(name):
    keys = {link_key(name, v) for v in PHONE_FORMS}
    assert len(keys) == 1


def test_phone_forms_match_through_guard():
    g = make()
    g.check(Signup(email="a@outlook.com", ip="79.1.1.1", user_agent=UA, links={"phone": PHONE_FORMS[0]}, ts=T0))
    d = g.check(Signup(email="b@outlook.com", ip="84.1.1.1", user_agent=UA, links={"sms_number": PHONE_FORMS[2]}, ts=T0 + 3600))
    assert d.action == ALLOW  # different link names are different keys
    d = g.check(Signup(email="c@outlook.com", ip="85.1.1.1", user_agent=UA, links={"phone": PHONE_FORMS[2]}, ts=T0 + 7200))
    assert "link_reuse" in names(d)


def test_non_phone_names_are_not_digit_normalized():
    assert link_key("hotel_ref", "AB12") != link_key("hotel_ref", "CD12")
    assert link_key("card", "00123") != link_key("card", "123")


def test_national_and_international_formats_match():
    assert link_key("phone", "050-123-4567") == link_key("phone", "+972501234567")


def test_zero_only_phone_value_is_kept():
    assert link_key("phone", "00") == link_key("phone", "0 0")
    assert link_key("phone", "00") != link_key("phone", "000")
