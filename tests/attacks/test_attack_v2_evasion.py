"""Attack round v2 (branch night-2): ways around look-alike, device-farm, crowd, domain and proxy detection.

Every test here fails today. Each one asserts the behaviour a fix should give; delete this note when they pass.
Library only, no network, frozen clock.
"""
import random
import string

import pytest

from tenkyu import ALLOW, Guard, Signup
from tenkyu import domains as D
from tenkyu import lookalike as L
from tenkyu import proxy as P
from tenkyu import signals as S
from tenkyu.store import MemoryStore

from attack_helpers import T0, UA, fired


def _names(n, seed=7):
    r = random.Random(seed)
    return ["".join(r.choice(string.ascii_lowercase) for _ in range(r.randint(6, 10))) for _ in range(n)]


# --- 1. crowd rule: one IP, fresh gmail-looking addresses, own random device ids, real UA -------------------------

def test_crowd_rule_caps_credit_for_one_ip_flood_of_clean_looking_gmail():
    """100 distinct plain gmail addresses from ONE IP in an hour. Today ip_burst/ip_velocity go soft, nothing blocks,
    every signup after the second is `review` (25%): ~26 grants/hour, unbounded as the rate grows (998 of 1000 reviewed).
    A crowd rule must stop being soft once one IP carries far more than a classroom (say 20 per hour)."""
    g = Guard(clock=lambda: T0)
    r = random.Random(1)
    total = 0.0
    for i, name in enumerate(_names(100)):
        d = g.check(Signup(email=f"{name}@gmail.com", ip="203.0.113.9", device_id=f"v{r.getrandbits(64):x}",
                           user_agent=UA, ts=T0 + i * 36))
        total += d.credit_fraction
    # Design (Phase 9): up to 60 clean identities an hour per IP get partial credit so a school is never blocked;
    # past that the IP rules are hard again. Worst case one IP: 2 full + 60 x 0.25 = about 17 grants an hour, 50 a day.
    assert total <= 17, f"one IP got {total:.1f} grants in an hour"


# --- 2. yahoo.* wildcard in the well-known list ------------------------------------------------------------------

@pytest.mark.parametrize("domain", ["yahoo.mailfarm.xyz", "yahoo.com.evil.net", "yahoo.qzkvbnwt.xyz"])
def test_attacker_subdomain_named_yahoo_is_not_a_well_known_provider(domain):
    """signals._YAHOO_RE is `yahoo\\.[a-z.]+`: ANY domain whose first label is `yahoo` counts as well-known, which
    switches off odd_domain, domain_burst, look-alike checks and enables the crowd rule."""
    assert not S.well_known_domain(domain)


def test_catch_all_farm_on_yahoo_dot_attacker_domain_is_seen():
    g = Guard(clock=lambda: T0)
    last = None
    for i, name in enumerate(_names(15)):
        last = g.check(Signup(email=f"{name}@yahoo.qzkvbnwt.xyz", ip=f"45.{i}.9.9", device_id=f"d{i}",
                              user_agent=UA, ts=T0 + i * 60))
    assert last.action != ALLOW or "domain_burst" in fired(last)


# --- 3. domain_burst keyed on the full domain: one wildcard-MX domain, one subdomain per account -----------------

def test_domain_burst_counts_the_registrable_domain_not_each_subdomain():
    """u<i>@s<i>.qzkvbnwt.xyz: every address sits on its own subdomain, so the per-domain distinct-address count
    never leaves 0. Twelve accounts, all full grants."""
    g = Guard(clock=lambda: T0)
    out = []
    for i, name in enumerate(_names(12)):
        out.append(g.check(Signup(email=f"{name}@s{i}.qzkvbnwt.xyz", ip=f"45.{i}.9.9", device_id=f"d{i}",
                                  user_agent=UA, ts=T0 + i * 60)))
    assert "domain_burst" in fired(out[-1]) or out[-1].action != ALLOW


def test_documented_limit_word_like_catch_all_domain_spread_alone_is_allowed():
    """Documented limit (Phase 9): 15 mailboxes on one normal-looking domain from 15 networks in 15 minutes looks
    exactly like a big company's staff signing up (the synthetic benchmark's corp.example). domain_burst alone never
    costs credit; it counts only next to another signal. Pinned so a change is deliberate."""
    g = Guard(clock=lambda: T0)
    out = []
    for i, name in enumerate(_names(15)):
        out.append(g.check(Signup(email=f"{name}@harborline.com", ip=f"45.{i}.9.9", device_id=f"d{i}",
                                  user_agent=UA, ts=T0 + i * 60)))
    assert out[-1].action == ALLOW and "domain_burst" in [s.name for s in out[-1].signals if s.score > 0]


# --- 4. look-alike: TLD swaps and homoglyphs the table does not know ---------------------------------------------

@pytest.mark.parametrize("domain", ["gmail.cm", "gmail.con", "gmail.om", "outlook.co", "hotmail.co",
                                    "icloud.co", "gmailc.om"])
def test_one_edit_tld_swap_of_a_provider_is_a_lookalike(domain):
    """lookalike_target only tries one typo in the LABEL with the SAME suffix. (gmail.co is left out on purpose:
    tests/test_lookalike.py pins it as NOT a look-alike.)"""
    assert L.lookalike_target(domain) is not None


@pytest.mark.parametrize("domain", [
    "outl๐๐k.com",          # Thai digit zero x2 (one is caught by the one-typo rule, two are not)
    "outl০০k.com",          # Bengali zero x2
    "gᴍᴀil.com",            # Latin small capitals M, A
    "ᴀol.com",                   # aol.com has no one-typo rule, small capital A is not in the table
    "proᴛon.me",                 # proton is excluded from the one-typo rule
    "h\u2c9ftmai\u1d0c.com",          # Coptic o + small capital L with stroke
    "gmail.cⲟm",                 # homoglyph in the TLD
    "outlook.c๐m",
])
def test_homoglyphs_missing_from_confusables_table_still_flag(domain):
    assert L.lookalike_target(domain) is not None


# --- 5. odd_domain: leetspeak and non-ASCII spellings of throwaway-service names --------------------------------

@pytest.mark.parametrize("domain", ["trashmai1.com", "yopmai1.com", "guerrillamai1.com", "tempmail.xyz".replace("a", "а"),
                                    "burnerma1l.com", "tempma1l.com"])
def test_disposable_word_in_leetspeak_or_cyrillic_is_still_odd(domain):
    assert D.domain_shape_score(domain) >= D.ODD_DOMAIN_THRESHOLD


# --- 6. proxy: rotate the minor version in the user agent --------------------------------------------------------

def test_wide_spread_survives_a_user_agent_minor_version_rotation():
    """wide_spread needs 60% of the /16 to share one fingerprint. Four Chrome 120.0.x build numbers split it 4 ways."""
    st = MemoryStore(clock=lambda: T0 + 99999)
    seen = set()
    for i in range(40):
        ua = UA.replace("120.0 ", f"120.0.{6000 + i % 4}.1 ")
        ip = f"45.12.{i}.7"
        seen |= {s.name for s in P.signals(st, T0 + i * 60, ip, ua)}
        P.record(st, T0 + i * 60, ip, ua)
    assert "wide_spread" in seen
