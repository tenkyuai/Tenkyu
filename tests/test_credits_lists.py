import pytest

from tenkyu import ALLOW, BLOCK, REVIEW, Guard, Signup, load_domains

T0 = 1_700_000_000.0
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/120.0 Safari/537.36"


def make(**kw):
    return Guard(clock=lambda: T0, **kw)


def su(email="real.person@acme.io", ip="203.0.113.5", dev="d1", ua=UA):
    return Signup(email=email, ip=ip, device_id=dev, user_agent=ua, ts=T0)


# --- credit_fraction
def test_allow_gets_full_credits():
    assert make().check(su()).credit_fraction == 1.0


def test_review_gets_partial_credits():
    d = make().check(su(ua="python-requests/2.31"))
    assert d.action == REVIEW and d.credit_fraction == 0.25


def test_block_gets_no_credits():
    d = make().check(su(email="x@mailinator.com"))
    assert d.action == BLOCK and d.credit_fraction == 0.0


def test_credit_fractions_configurable():
    d = make(credit_fractions={REVIEW: 0.5}).check(su(ua="curl/8.0"))
    assert d.credit_fraction == 0.5


def test_credit_fraction_in_to_dict():
    assert make().check(su()).to_dict()["credit_fraction"] == 1.0


def test_unmentioned_actions_keep_defaults():
    g = make(credit_fractions={ALLOW: 0.9})
    assert g.check(su()).credit_fraction == 0.9
    assert g.check(su(email="y@mailinator.com")).credit_fraction == 0.0


# --- domain lists
def test_load_domains_parses_comments_blanks_and_case(tmp_path):
    f = tmp_path / "d.txt"
    f.write_text("# header\nFoo.Example.COM\n\n  bar.test  # trailing\n@baz.test\n*.wild.test\nnodot\nhas space.com\n.\n")
    assert load_domains(f) == frozenset({"foo.example.com", "bar.test", "baz.test", "wild.test"})


def test_load_domains_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_domains(tmp_path / "nope.txt")


def test_load_domains_survives_bad_bytes(tmp_path):
    f = tmp_path / "bin.txt"
    f.write_bytes(b"ok.test\n\xff\xfe\x00junk\nfine.test\n")
    assert {"ok.test", "fine.test"} <= load_domains(f)


def test_extra_disposable_blocks_custom_domain():
    g = make(extra_disposable=["tempbox.example"])
    assert g.check(su(email="a@tempbox.example")).action == BLOCK
    assert make().check(su(email="a@tempbox.example")).action == ALLOW


def test_extra_disposable_covers_subdomains_and_normalizes():
    g = make(extra_disposable=["  *.Burner.Test. "])
    assert g.check(su(email="a@mail.burner.test")).action == BLOCK


def test_extra_disposable_from_file(tmp_path):
    f = tmp_path / "d.txt"
    f.write_text("fresh-temp.example\n")
    g = make(extra_disposable=load_domains(f))
    assert g.check(su(email="z@fresh-temp.example")).action == BLOCK


@pytest.mark.parametrize("junk", [None, 5, "", "nodot", [], b"x"])
def test_extra_disposable_junk_ignored(junk):
    g = make(extra_disposable=[junk, "ok.example"])
    assert g.check(su(email="a@ok.example")).action == BLOCK
    assert g.check(su(email="b@acme.io", ip="10.9.9.9", dev="q")).action == ALLOW


def test_bundled_list_not_mutated_by_extras():
    from tenkyu.signals import DISPOSABLE_DOMAINS
    before = len(DISPOSABLE_DOMAINS)
    make(extra_disposable=["only-mine.example"])
    assert len(DISPOSABLE_DOMAINS) == before and "only-mine.example" not in DISPOSABLE_DOMAINS
