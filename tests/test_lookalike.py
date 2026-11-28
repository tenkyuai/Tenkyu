"""Look-alike email domains: homoglyphs, punycode, digit swaps, one-typo domains, and zero false positives on real ones."""
from __future__ import annotations

import random
import time

import pytest

from tenkyu import lookalike, signals
from tenkyu.lookalike import lookalike_signal, lookalike_target, skeleton
from tenkyu.signals import WELL_KNOWN_PROVIDERS, normalize_email

CYR_A, CYR_I, CYR_O, CYR_E, CYR_C = "а", "і", "о", "е", "с"
GREEK_ALPHA, GREEK_OMICRON = "α", "ο"
FULLWIDTH_G = "ｇ"


def _signal(address):
    return lookalike_signal(normalize_email(address), address)


# --- data file ---------------------------------------------------------------------------------------------------

def test_confusables_data_is_loaded_from_file():
    assert len(lookalike._CONFUSABLES) >= 100
    assert lookalike._CONFUSABLES[CYR_A] == "a"
    assert lookalike._CONFUSABLES[CYR_I] == "i"
    assert lookalike._CONFUSABLES[GREEK_OMICRON] == "o"
    assert lookalike._CONFUSABLES[FULLWIDTH_G] == "g"


def test_confusables_file_lines_are_well_formed():
    from importlib import resources
    text = resources.files("tenkyu").joinpath("data", "confusables.txt").read_text(encoding="utf-8")
    assert "https://www.unicode.org/Public/security/latest/confusables.txt" in text.split("\n\n", 1)[0]
    seen = set()
    for line in text.splitlines():
        body = line.split("#", 1)[0].split()
        if not body:
            continue
        assert len(body) == 2, line
        cp = int(body[0], 16)
        assert cp >= 0x80 and cp not in seen
        seen.add(cp)
        assert body[1].isascii() and body[1] == body[1].lower() and body[1].isalnum(), line


# --- skeleton ----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("domain, expected", [
    ("gmail.com", "gmail.com"),
    ("GMAIL.COM", "gmail.com"),
    ("gma" + CYR_I + "l.com", "gmail.com"),
    ("gm" + CYR_A + "il.com", "gmail.com"),
    (FULLWIDTH_G + "mail.com", "gmail.com"),
    ("ｇｍａｉｌ．ｃｏｍ", "gmail.com"),
    ("outl" + GREEK_OMICRON * 2 + "k.com", "outlook.com"),
    ("outl00k.com", "outlook.com"),
    ("g00glemail.com", "googlemail.com"),
    ("hotmai1.com", "hotmail.com"),
    ("rnicrosoft.com", "microsoft.com"),
    ("vvalla.co.il", "walla.co.il"),
    ("cloud.com", "doud.com"),
    ("gmäil.com", "gmail.com"),           # accent stripped
    ("gmail​.com", "gmail.com"),          # zero-width space dropped
    ("gmail.com.", "gmail.com"),
    ("gmail。com", "gmail.com"),           # ideographic full stop
])
def test_skeleton_examples(domain, expected):
    assert skeleton(domain) == expected


def test_skeleton_decodes_punycode_labels():
    cyrillic = "gm" + CYR_A + "il.com"
    ascii_form = cyrillic.encode("idna").decode("ascii")
    assert ascii_form.startswith("xn--")
    assert skeleton(ascii_form) == "gmail.com"
    assert skeleton(ascii_form.upper()) == "gmail.com"
    # raw punycode body through the plain punycode codec (an `xn--` label the idna codec may reject)
    label = "gm" + CYR_A + "il"
    assert skeleton("xn--" + label.encode("punycode").decode("ascii") + ".com") == "gmail.com"


@pytest.mark.parametrize("bad", ["xn--", "xn--a", "xn---", "xn--zzzzzzzzzzzzzzzzzzzzzzzzzz.com",
                                 "xn--" + "a" * 80 + ".com", "xn--а.com", "xn--xn--gmail.com"])
def test_skeleton_survives_bad_punycode(bad):
    assert isinstance(skeleton(bad), str)
    assert lookalike_target(bad) is None


@pytest.mark.parametrize("value", [None, 5, 5.5, b"gmail.com", ["gmail.com"], {"a": 1}, object(), True])
def test_skeleton_non_string_is_empty(value):
    assert skeleton(value) == ""


# --- lookalike_target: homoglyphs and punycode -------------------------------------------------------------------

@pytest.mark.parametrize("domain", [
    "gma" + CYR_I + "l.com",                         # gmaіl.com, Cyrillic і
    "gm" + CYR_A + "il.com",                         # gmаil.com, Cyrillic а
    "GM" + CYR_A.upper() + "IL.COM",                 # upper-case Cyrillic А folds like the lower-case one
    "gm" + GREEK_ALPHA + "il.com",                   # Greek alpha
    FULLWIDTH_G + "m" + CYR_A + "il.com",            # fullwidth g plus Cyrillic а
    "ɡmail.com",                                # script g
    "gmailı.com".replace("lı", "l"),       # control: plain gmail (see the dedicated None test below)
])
def test_homoglyph_gmail_variants(domain):
    if domain == "gmail.com":
        assert lookalike_target(domain) is None
    else:
        assert lookalike_target(domain) == "gmail.com"


def test_fullwidth_spelling_of_the_real_domain_is_the_real_domain():
    assert skeleton(FULLWIDTH_G + "mail.com") == "gmail.com"
    assert lookalike_target(FULLWIDTH_G + "mail.com") is None
    assert lookalike_target("ｇｍａｉｌ．ｃｏｍ") is None


def test_punycode_of_a_confusable_gmail():
    cyrillic = "gm" + CYR_A + "il.com"
    ascii_form = cyrillic.encode("idna").decode("ascii")
    assert lookalike_target(ascii_form) == "gmail.com"
    assert lookalike_target(ascii_form.upper()) == "gmail.com"
    assert lookalike_target(ascii_form + ".") == "gmail.com"
    other = ("outl" + CYR_O + CYR_O + "k.com").encode("idna").decode("ascii")
    assert lookalike_target(other) == "outlook.com"


def test_other_confusable_providers():
    assert lookalike_target("outl" + CYR_O + CYR_O + "k.com") == "outlook.com"
    assert lookalike_target("hotma" + CYR_I + "l.com") == "hotmail.com"
    assert lookalike_target("yah" + CYR_O * 2 + ".com") == "yahoo.com"
    assert lookalike_target("ıcloud.com") == "icloud.com"
    assert lookalike_target("pr" + CYR_O + "tonmail.com") == "protonmail.com"
    assert lookalike_target("օutlook.com") == "outlook.com"          # Armenian oh
    # a short provider label is fine to flag when another script stands in for its letters
    assert lookalike_target("m" + CYR_E + ".com") == "me.com"
    assert lookalike_target(CYR_A + "ol.com") == "aol.com"


def test_yahoo_country_domains_are_targets_too():
    assert lookalike_target("y" + CYR_A + "hoo.co.uk") == "yahoo.co.uk"
    assert lookalike_target("yahoo.co.uk") is None
    assert lookalike_target("yahoo.co.jp") is None
    assert lookalike_target("yahoo.fr") is None


# --- lookalike_target: digit and letter swaps --------------------------------------------------------------------

@pytest.mark.parametrize("domain, target", [
    ("g00glemail.com", "googlemail.com"),
    ("gma1l.com", "gmail.com"),
    ("gmai1.com", "gmail.com"),
    ("outl00k.com", "outlook.com"),
    ("0utlook.com", "outlook.com"),
    ("hotmai1.com", "hotmail.com"),
    ("h0tmail.com", "hotmail.com"),
    ("yah00.com", "yahoo.com"),
    ("ic1oud.com", "icloud.com"),
    ("hotrnail.com", "hotmail.com"),              # rn looks like m
    ("grnail.com", "gmail.com"),
    ("fastrnail.com", "fastmail.com"),
    ("vvalla.co.il", "walla.co.il"),              # vv looks like w
    ("1ive.com", "live.com"),
])
def test_digit_and_letter_swaps(domain, target):
    assert lookalike_target(domain) == target


def test_g00gle_style_without_a_mail_target_is_not_flagged():
    # google.com is not a mailbox provider in WELL_KNOWN_PROVIDERS, so there is nothing to imitate.
    assert lookalike_target("g00gle.com") is None
    assert lookalike_target("google.com") is None


def test_swaps_on_very_short_ascii_provider_names_are_not_flagged():
    # rnsn.com / 1ive-like collisions on 3-letter labels are too ambiguous to call from ASCII alone.
    assert lookalike_target("rnsn.com") is None
    assert lookalike_target("a0l.com") is None


def test_cl_to_d_rule_does_not_flag_real_d_domains():
    assert lookalike_target("idoud.com") is None
    assert skeleton("icloud.com") == skeleton("ic1oud.com") == "idoud.com"


# --- lookalike_target: one typo ----------------------------------------------------------------------------------

@pytest.mark.parametrize("domain, target", [
    ("gmial.com", "gmail.com"),            # adjacent swap
    ("gmaill.com", "gmail.com"),           # insertion
    ("gmal.com", "gmail.com"),             # deletion
    ("gmaik.com", "gmail.com"),            # substitution
    ("gnail.com", "gmail.com"),
    ("outlok.com", "outlook.com"),
    ("outlookk.com", "outlook.com"),
    ("outloook.com", "outlook.com"),
    ("otulook.com", "outlook.com"),
    ("hotmial.com", "hotmail.com"),
    ("hotmal.com", "hotmail.com"),
    ("hotmaill.com", "hotmail.com"),
    ("yahooo.com", "yahoo.com"),
    ("yaho.com", "yahoo.com"),
    ("yahho.com", "yahoo.com"),
    ("iclod.com", "icloud.com"),
    ("icoud.com", "icloud.com"),
    ("googlemial.com", "googlemail.com"),
    ("protonmial.com", "protonmail.com"),
    ("comcsat.net", "comcast.net"),
    ("verison.net", "verizon.net"),
    ("t-onlin.de", "t-online.de"),
    ("yandx.ru", "yandex.ru"),
])
def test_one_typo_domains(domain, target):
    assert lookalike_target(domain) == target


@pytest.mark.parametrize("domain", [
    "gmial.org", "gmial.co.uk",                    # different suffix and a typo: too far to call

    "gmaiil.net", "outlok.de",
    "gmailer.com", "gmail-login.com", "xgmailx.com",    # two or more edits away
    "mygmail.com", "gmail.com.evil.org",
])
def test_typo_requires_same_suffix_and_one_edit(domain):
    assert lookalike_target(domain) is None


@pytest.mark.parametrize("domain", [
    # short or ordinary-word provider labels are not matched by typo distance
    "mo.com", "ma.com", "msm.com", "aoi.com", "never.com", "saver.com", "range.fr", "oranges.fr",
    "photon.me", "protos.me", "wall.co.il", "walls.co.il", "qa.com", "free.com", "zohol.com", "libera.it",
    "mail.ru", "mall.ru"
])
def test_short_or_wordlike_provider_labels_have_no_typo_matches(domain):
    assert lookalike_target(domain) is None


# --- real domains and real neighbours ----------------------------------------------------------------------------

@pytest.mark.parametrize("domain", [
    "gmx.com", "gmx.de", "gmx.net", "gmx.at", "gmx.ch", "gmx.us", "gmx.co.uk",
    "mail.com", "email.com", "cloud.com", "me.com", "mac.com", "live.com", "line.me", "live.fr", "msn.com",
    "hotmail.fr", "hotmail.co.uk", "outlook.fr", "outlook.de", "icloud.com", "yahoo.com", "ymail.com",
    "rocketmail.com", "googlemail.com", "gmail.com", "proton.me", "pm.me", "protonmail.ch", "aol.com",
    "google.com", "microsoft.com", "apple.com", "mailbox.org", "hey.com", "tutanota.com", "zoho.eu",
    "stanford.edu", "ox.ac.uk", "mit.edu", "tau.ac.il", "school.k12.ca.us",
])
def test_real_domains_and_neighbours_are_not_flagged(domain):
    assert lookalike_target(domain) is None
    assert lookalike_target(domain.upper()) is None
    assert lookalike_target(domain + ".") is None


def test_every_well_known_provider_is_not_flagged():
    assert WELL_KNOWN_PROVIDERS
    for provider in WELL_KNOWN_PROVIDERS:
        assert lookalike_target(provider) is None, provider
        assert lookalike_target(provider.upper()) is None, provider
        assert lookalike_target(provider + ".") is None, provider
        assert lookalike_target("  " + provider + " ") is None, provider
        assert _signal("someone@" + provider) is None, provider


def test_every_provider_has_a_flagged_homoglyph_twin():
    # Replace one Latin letter with a Cyrillic twin in each provider and the twin must point at that provider
    # (or another provider with the same skeleton). Skips providers with no mappable letter.
    reverse = {"a": CYR_A, "e": CYR_E, "o": CYR_O, "c": CYR_C, "i": CYR_I}
    checked = 0
    for provider in sorted(WELL_KNOWN_PROVIDERS):
        for i, ch in enumerate(provider):
            if ch in reverse:
                twin = provider[:i] + reverse[ch] + provider[i + 1:]
                assert lookalike_target(twin) == provider, (provider, twin)
                checked += 1
                break
    assert checked >= 20


def test_lookalike_target_always_returns_a_real_provider():
    for domain in ("gmial.com", "gm" + CYR_A + "il.com", "y" + CYR_A + "hoo.co.uk", "outl00k.com"):
        assert signals.well_known_domain(lookalike_target(domain))


_WORDS = ["acme", "globex", "initech", "umbrella", "hooli", "vandelay", "stark", "wayne", "wonka", "cyberdyne",
          "tyrell", "soylent", "pied", "piper", "aperture", "blackmesa", "oscorp", "dunder", "mifflin", "bluth",
          "nakatomi", "gringotts", "monarch", "massive", "dynamic", "prestige", "worldwide", "northwind", "contoso",
          "fabrikam", "adatum", "litware", "tailspin", "wingtip", "lucerne", "proseware", "alpine", "ski", "house",
          "trey", "research", "relecloud", "margie", "travel", "coho", "winery", "vineyard", "graphic", "design",
          "institute", "blue", "yonder", "airlines", "fourth", "coffee", "consulting", "partners", "labs", "studio",
          "robotics", "logistics", "freight", "capital", "ventures", "analytics", "cloudworks", "dataflow", "pixel",
          "forge", "harbor", "summit", "lighthouse", "meridian", "cobalt", "juniper", "quartz", "falcon", "maple"]
_SUFFIXES = ["com", "io", "co", "net", "org", "ai", "dev", "co.uk", "de", "fr", "com.au", "co.il", "app", "tech"]


def _company_domains(n=200, seed=20261127):
    rng = random.Random(seed)
    out = set()
    while len(out) < n:
        parts = rng.sample(_WORDS, rng.choice([1, 1, 2]))
        out.add(rng.choice(["", "get", "try", "the"]) + rng.choice(["", "-"]).join(parts) + "." + rng.choice(_SUFFIXES))
    return sorted(out)


def test_two_hundred_company_domains_are_not_flagged():
    domains = _company_domains()
    assert len(domains) == 200
    for domain in domains:
        assert lookalike_target(domain) is None, domain
        assert _signal("jane.doe@" + domain) is None, domain


@pytest.mark.parametrize("domain", [
    "почта.рф",                      # почта.рф
    "яндекс.рф",                # яндекс.рф
    "münchen.de", "café.fr", "日本語.jp", "bücher.de", "ελλάδα.gr",
])
def test_genuine_internationalised_domains_are_not_flagged(domain):
    assert lookalike_target(domain) is None
    assert lookalike_target(domain.encode("idna").decode("ascii")) is None


# --- garbage and huge input --------------------------------------------------------------------------------------

_GARBAGE = [None, 0, 1.5, True, b"gmail.com", bytearray(b"x"), [], ["gmail.com"], {}, {"a": "b"}, object(), "", " ",
            ".", "..", "@", "@@", "a@b@c", "com", "gmail", ".com", "gmail..com", "\x00", "\x00.com", "\ud800.com",
            "\udfff", "gmail.com\n", "‮gmail.com", "\U0001f600.com", "\U0001f600" * 500, "́" * 1000,
            "xn--", "xn--" * 5000, "xn--a.xn--b.xn--c", "." * 5000, "a" * 100000, "a." * 50000, "а" * 100000,
            "gmail.com" * 5000, "ｇ" * 3000, "-", "_", "*.gmail.com", "http://gmail.com", "gmail.com/path"]


@pytest.mark.parametrize("value", _GARBAGE)
def test_garbage_never_raises(value):
    skeleton(value)
    lookalike_target(value)
    lookalike_signal(None, value)
    lookalike_signal(value, value)
    lookalike_signal(value, None)


def test_garbage_is_not_flagged():
    for value in _GARBAGE:
        assert lookalike_target(value) is None, repr(value)[:40]


def test_huge_input_is_fast():
    start = time.perf_counter()
    for huge in ("a" * 10**6, "а" * 10**6, "xn--" + "a" * 10**6, "gmail.com" * 10**5, "@" * 10**6,
                 "x" * 10**6 + "@gmail.com"):
        assert lookalike_target(huge) is None
        lookalike_signal(huge, huge)
        skeleton(huge)
    assert time.perf_counter() - start < 3


def test_skeleton_output_is_bounded():
    assert len(skeleton("a" * 10**6)) <= 1024


# --- lookalike_signal --------------------------------------------------------------------------------------------

def test_signal_for_cyrillic_domain():
    address = "bob@gm" + CYR_A + "il.com"
    sig = _signal(address)
    assert sig is not None
    assert (sig.name, sig.score, sig.reason) == ("lookalike_domain", 0.75, "email domain imitates gmail.com")


@pytest.mark.parametrize("address, target", [
    ("bob@gma" + CYR_I + "l.com", "gmail.com"),
    ("bob@gmial.com", "gmail.com"),
    ("bob@outl00k.com", "outlook.com"),
    ("bob@hotrnail.com", "hotmail.com"),
    ("bob@" + ("gm" + CYR_A + "il.com").encode("idna").decode("ascii"), "gmail.com"),
    ("bob+tag@gmaill.com", "gmail.com"),
    ("  BOB@GMIAL.COM ", "gmail.com"),
])
def test_signal_domain_cases(address, target):
    sig = _signal(address)
    assert sig is not None and sig.name == "lookalike_domain" and sig.score == 0.75
    assert sig.reason == f"email domain imitates {target}"


def test_signal_none_for_real_addresses():
    for address in ("maya.cohen@gmail.com", "bob@outlook.com", "x@gmx.de", "dev@acme.io", "a@mail.com",
                    "a@yahoo.co.uk", "иван@mail.ru", "иван@yandex.ru",
                    "a@" + FULLWIDTH_G + "mail.com"):
        assert _signal(address) is None, address


def test_signal_uses_the_raw_address_when_the_normalized_one_is_missing_or_clean():
    raw = "bob@gm" + CYR_A + "il.com"
    assert lookalike_signal(None, raw).name == "lookalike_domain"           # normalization failed upstream
    assert lookalike_signal("bob@gmail.com", raw).name == "lookalike_domain"  # norm looks clean, raw does not
    assert lookalike_signal("bob@gm" + CYR_A + "il.com", "bob@gmail.com").name == "lookalike_domain"  # norm only
    assert lookalike_signal("bob@gmail.com", "bob@gmail.com") is None
    assert lookalike_signal(None, None) is None


def test_signal_defensive_inputs():
    for norm, raw in [(None, 123), (123, None), (b"a@gmial.com", b"a@gmial.com"), ([], {}), ("noat", "noat"),
                      ("a@" + "b" * 10**6, "a@" + "b" * 10**6), ("", ""), (None, "x" * 10**6)]:
        assert lookalike_signal(norm, raw) is None


# --- mixed-script local parts ------------------------------------------------------------------------------------

def test_mixed_script_local_part_is_flagged():
    address = "p" + CYR_A + "ypal@example.com"                  # Cyrillic а inside a Latin word
    sig = _signal(address)
    assert sig is not None
    assert sig.name == "mixed_script_email" and sig.score == 0.5


@pytest.mark.parametrize("local", [
    "jo" + CYR_O + "hn", "adm" + CYR_I + "n", "supp" + CYR_O + "rt", "support." + "t" + CYR_E + "am",
    "g" + GREEK_OMICRON + "ogle", "μsic", "john" + CYR_A, FULLWIDTH_G + "о" + "od",
])
def test_mixed_script_variants_flagged(local):
    sig = _signal(local + "@example.com")
    assert sig is not None and sig.name == "mixed_script_email" and sig.score == 0.5


@pytest.mark.parametrize("local", [
    "иван", "иван.петров",           # иван, иван.петров
    "мария_иванова",
    "иван1985", "анна-2000",
    "γιάννης", "μαρία.123",
    "ivan", "ivan.petrov", "ivan-1985", "j.smith", "obrien", "ivan.иван",
    "ivan_иванов", "a1b2c3", "émile", "jürgen", "清水",
])
def test_single_script_local_parts_are_not_flagged(local):
    assert _signal(local + "@example.com") is None
    assert _signal(local + "@gmail.com") is None
    assert _signal(local + "@mail.ru") is None


def test_pure_cyrillic_name_on_a_real_russian_provider_is_clean():
    assert _signal("иван.петров@yandex.ru") is None
    assert _signal("анна@mail.ru") is None


def test_lookalike_domain_wins_over_mixed_script():
    sig = _signal("p" + CYR_A + "ypal@gm" + CYR_A + "il.com")
    assert sig.name == "lookalike_domain"


def test_mixed_script_checks_the_raw_local_part():
    raw = "p" + CYR_A + "ypal@example.com"
    assert lookalike_signal(None, raw).name == "mixed_script_email"
    assert lookalike_signal("x@example.com", "x@example.com") is None


# --- small capitals, Coptic, other-script digits, non-ASCII wildcard, homoglyph in the TLD -----------------------

@pytest.mark.parametrize("domain", [
    "gᴍᴀil.com", "ᴀol.com", "proᴛon.me", "hⲟtmaiᴌ.com", "gmail.cⲟm",
    "outl๐๐k.com", "outl০০k.com", "outlook.c๐m", "gmail.c०m",
    "gmɐil.com",       # a letter the table has never heard of: only the non-ASCII wildcard catches it
    "yahǝo.com",
])
def test_unlisted_homoglyphs_and_tld_homoglyphs_flag(domain):
    assert lookalike.lookalike_target(domain) is not None


@pytest.mark.parametrize("domain", ["bücher.de", "münchen.de", "почта.рф",
                                    "яндекс.рф", "café.fr", "gmx.de"])
def test_real_idn_and_neighbours_are_not_flagged_by_the_wildcard(domain):
    assert lookalike.lookalike_target(domain) is None


@pytest.mark.parametrize("domain,real", [
    ("yahoo.com", True), ("yahoo.co.uk", True), ("yahoo.com.br", True), ("yahoo.fr", True), ("yahoo.co.jp", True),
    ("ymail.com", True), ("rocketmail.com", True),
    ("yahoo.mailfarm.xyz", False), ("yahoo.com.evil.net", False), ("yahoo.qzkvbnwt.xyz", False), ("yahoo.xyz", False),
])
def test_only_real_yahoo_domains_are_well_known(domain, real):
    assert signals.well_known_domain(domain) is real
