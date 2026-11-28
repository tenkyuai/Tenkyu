"""Honest users against the new detection (lookalike, device_farm, crowd rule, odd_domain/domain_burst, proxy).

Every group is simulated with Guard() and Guard.for_ai_apps(), a controllable clock and Signup(ts=...), in process,
with fixed seeds. Tests that fail today are findings (honest people blocked, or most of a group reviewed); the rest
pin behaviour that is already right. Library only, no network.
"""
import random

import pytest

from tenkyu import ALLOW, BLOCK, REVIEW, Guard, Signup

T0 = 1_700_000_000.0
CHROME = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
IPHONE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
          "Version/17.1 Mobile/15E148 Safari/604.1")
IPAD = ("Mozilla/5.0 (iPad; CPU OS 17_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.1 Mobile/15E148 Safari/604.1")

FIRST = ("liam noah emma olivia ava mia lucas ethan maya omar yael noa daniel sofia ivan anna pedro lucia hana kenji aiko "
         "wei li jin sara tom eli ron dina amir lior tamar yossi mika nina paul luis jose carla marco giulia felix lena "
         "jonas anton petr jana").split()
LAST = ("cohen levi smith garcia kim lee wang chen novak kowalski rossi mueller schmidt ivanov petrov silva santos ito "
        "sato tanaka nguyen patel khan ali haddad cruz lopez martin dubois bianchi jansen berg olsen").split()


def _names(count):
    """`count` distinct, realistic local parts (distinct after Gmail dot removal)."""
    out, seen, i = [], set(), 0
    while len(out) < count:
        r = random.Random(i * 7919)
        local = FIRST[i % len(FIRST)] + r.choice([".", "", "_", ""]) + r.choice(LAST) + r.choice(["", "", str(r.randint(1, 99)), str(r.randint(1970, 2008))])
        i += 1
        if local.replace(".", "") not in seen:
            seen.add(local.replace(".", ""))
            out.append(local)
    return out


NAMES = _names(400)

GUARDS = {"Guard()": lambda clock: Guard(clock=clock), "for_ai_apps": lambda clock: Guard.for_ai_apps(clock=clock)}


@pytest.fixture(params=sorted(GUARDS))
def guard(request):
    return GUARDS[request.param](lambda: T0)


def arrivals(n, span, seed):
    r = random.Random(seed)
    return sorted(r.uniform(0, span) for _ in range(n))


def run(guard, signups):
    return [guard.check(s) for s in signups]


def counts(decisions):
    return tuple(sum(d.action == a for d in decisions) for a in (ALLOW, REVIEW, BLOCK))


def school(domain, n=60, device_ids=True, span=1200, ip="203.0.113.5"):
    return [Signup(email=f"{NAMES[i]}@{domain}", ip=ip, device_id=f"chromebook-{i}" if device_ids else None,
                   user_agent=CHROME, ts=T0 + t) for i, t in enumerate(arrivals(n, span, 7))]


# ---------------------------------------------------------------------------------------------------------------
# Already right: pinned as protection
# ---------------------------------------------------------------------------------------------------------------

INTL_DOMAINS = ("mail.ru yandex.ru naver.com qq.com 163.com gmx.de web.de seznam.cz wp.pl o2.pl libero.it ukr.net "
                "rediffmail.com rambler.ru bk.ru inbox.ru list.ru yahoo.co.jp hanmail.net daum.net").split()
COMPANY_DOMAINS = ("x.ai a16z.com h2o.ai 3m.com kpmg.de mit.edu tau.ac.il technion.ac.il uni-heidelberg.de ethz.ch "
                   "bücher.de münchen.de xn--bcher-kva.de").split()


@pytest.mark.parametrize("domain", INTL_DOMAINS + COMPANY_DOMAINS)
def test_real_international_company_and_university_domains_are_allowed_alone_and_in_a_dozen(guard, domain):
    first = guard.check(Signup(email=f"maria.garcia@{domain}", ip="198.51.100.9", device_id="d-first", user_agent=CHROME, ts=T0))
    assert first.action == ALLOW and first.credit_fraction in (1.0, 0.5)
    out = [guard.check(Signup(email=f"{NAMES[i]}@{domain}", ip=f"198.51.{i}.9", device_id=f"d{i}", user_agent=CHROME,
                              ts=T0 + 3000 * i)) for i in range(12)]
    assert counts(out) == (12, 0, 0)


@pytest.mark.parametrize("email", [
    "иван.петров@mail.ru", "иван_петров1990@yandex.ru", "γιάννης.παπαδόπουλος@gmail.com", "محمد.علي@gmail.com",
    "אורי.כהן@gmail.com", "אורי@walla.co.il", "山田太郎@yahoo.co.jp", "王小明@qq.com", "김민수@naver.com",
    "ivan.иванов@gmail.com", "maya.cohen@gmail.com", "M.A.Y.A.C.O.H.E.N@gmail.com", "MAYA.COHEN@GMAIL.COM",
    "ŞÜKRÜ.İNCE@GMAIL.COM", "jürgen.müller@bücher.de", "anna@münchen.de", "anna@xn--mnchen-3ya.de",
])
def test_one_script_names_gmail_dots_caps_and_idn_are_allowed(guard, email):
    d = guard.check(Signup(email=email, ip="198.51.100.9", device_id="d1", user_agent=CHROME, ts=T0))
    assert d.action == ALLOW, [(s.name, s.score) for s in d.signals if s.score]


def test_honest_vpn_users_sharing_exits_are_allowed(guard):
    doms = ["gmail.com", "outlook.com", "proton.me", "icloud.com", "yahoo.com", "hotmail.com", "gmx.de", "acmecorp.com"]
    out = run(guard, [Signup(email=f"{NAMES[i]}@{doms[i % 8]}", ip=f"185.65.{134 + i % 6}.{10 + i % 8}", device_id=f"d{i}",
                             user_agent=CHROME, ts=T0 + t) for i, t in enumerate(arrivals(40, 86400, 4))])
    assert counts(out) == (40, 0, 0)


def test_iphone_crowd_on_one_carrier_across_many_slash24s_is_allowed(guard):
    r = random.Random(5)
    out = run(guard, [Signup(email=f"{NAMES[i]}@gmail.com", ip=f"172.20.{i % 80}.{r.randint(2, 250)}", device_id=f"d{i}",
                             user_agent=IPHONE, ts=T0 + t) for i, t in enumerate(arrivals(120, 86400, 11))])
    assert counts(out) == (120, 0, 0)


def test_family_of_4_on_one_tablet_over_four_networks_is_reviewed_never_blocked(guard):
    nets = ["192.0.2.10", "198.51.100.20", "203.0.113.30", "100.100.5.40"]
    out = run(guard, [Signup(email=f"{NAMES[i]}@gmail.com", ip=nets[i], device_id="tablet-1", user_agent=IPAD,
                             ts=T0 + i * 86400 * 1.3) for i in range(4)])
    assert out[0].action == ALLOW and all(d.action != BLOCK for d in out)


def test_library_terminal_used_by_20_patrons_over_a_week_is_never_blocked(guard):
    out = run(guard, [Signup(email=f"{NAMES[i]}@gmail.com", ip="203.0.113.77", device_id="lib-term-3", user_agent=CHROME,
                             ts=T0 + i * 86400 * 0.3) for i in range(20)])
    assert BLOCK not in {d.action for d in out}


def test_a_remote_team_on_a_normal_company_domain_is_allowed(guard):
    r = random.Random(3)
    out = run(guard, [Signup(email=f"{FIRST[i]}.{LAST[i]}@acme.com", ip=f"{20 + i * 9}.{i + 3}.{i}.{i + 4}", device_id=f"l{i}",
                             user_agent=CHROME, ts=T0 + t) for i, t in enumerate(sorted(r.uniform(0, 86400) for _ in range(14)))])
    assert counts(out) == (14, 0, 0)


# ---------------------------------------------------------------------------------------------------------------
# Findings: these fail today
# ---------------------------------------------------------------------------------------------------------------

def test_school_of_60_on_gmail_without_device_ids_is_not_blocked(guard):
    """HIGH: no device_id means _clean_identity is False, so ip_burst/ip_velocity (0.9) hit unsoftened. 58/60 blocked."""
    out = run(guard, school("gmail.com", device_ids=False))
    assert BLOCK not in {d.action for d in out}, counts(out)


def test_school_of_60_on_its_own_non_edu_domain_is_not_blocked(guard):
    """HIGH: lincoln-hs.org is not an _EDU_RE match, so no student is a clean identity: 58/60 blocked."""
    out = run(guard, school("lincoln-hs.org"))
    assert BLOCK not in {d.action for d in out}, counts(out)


@pytest.mark.parametrize("domain", "seznam.cz wp.pl o2.pl ukr.net rediffmail.com rambler.ru bk.ru inbox.ru list.ru hanmail.net daum.net".split())
def test_school_of_60_on_a_national_mailbox_provider_is_not_blocked(guard, domain):
    """HIGH: these real providers are missing from WELL_KNOWN_PROVIDERS, so the crowd rule never applies to them."""
    out = run(guard, school(domain))
    assert BLOCK not in {d.action for d in out}, counts(out)


def test_school_of_60_with_clean_identities_gets_partial_credit_never_a_block(guard):
    """Design choice (Phase 9): a crowd of clean identities on one IP gets partial credit (review), never a block.
    Full credit for all of them would let a farm on one IP collect full grants; review keeps both sides bounded."""
    out = run(guard, school("gmail.com"))
    allow, review, block = counts(out)
    assert block == 0 and all(d.credit_fraction > 0 or d.limit for d in out), (allow, review, block)


def test_startup_team_of_8_signing_up_from_the_office_is_not_blocked(guard):
    """HIGH: 8 people, one office IP, 10 minutes, own company domain: ip_velocity 0.9 at the 6th signup, 4 blocked."""
    r = random.Random(1)
    out = run(guard, [Signup(email=f"{FIRST[i * 3 % len(FIRST)]}.{LAST[i]}@acme.com", ip="203.0.113.50", device_id=f"laptop{i}",
                             user_agent=CHROME, ts=T0 + t) for i, t in enumerate(sorted(r.uniform(0, 600) for _ in range(8)))])
    assert BLOCK not in {d.action for d in out}, counts(out)


def test_shared_lab_computer_used_by_20_students_on_one_network_is_not_blocked(guard):
    """HIGH: device_reuse makes every later student "not clean", so ip_velocity is hard: 11/20 blocked."""
    # one machine, used by one student after another: at least a minute between signups
    times = [t + i * 60 for i, t in enumerate(arrivals(20, 12000 - 20 * 60, 3))]
    out = run(guard, [Signup(email=f"{NAMES[i]}@gmail.com", ip="203.0.113.77", device_id="lab-pc-12", user_agent=CHROME,
                             ts=T0 + t) for i, t in enumerate(times)])
    assert BLOCK not in {d.action for d in out}, counts(out)


def test_family_of_5_on_one_tablet_that_travels_is_not_blocked(guard):
    """LOW: device_farm (6+ accounts, 3+ networks, 7 days) is a hard 0.75 and cannot tell a big family from a farm."""
    nets = ["192.0.2.10", "198.51.100.20", "203.0.113.30"]
    out = run(guard, [Signup(email=f"{NAMES[i]}@gmail.com", ip=nets[i % 3], device_id="tablet-1", user_agent=IPAD,
                             ts=T0 + i * 86400) for i in range(5)])
    assert BLOCK not in {d.action for d in out}, counts(out)


@pytest.mark.parametrize("email", [
    "maya.cohen@gmail.com‎",        # LRM tacked on by copy-paste from an RTL document
    "‎מאיה.כהן@gmail.com",          # Hebrew pasted with a leading LRM
    "maya.cohen@gmail.com‏",        # RLM
    "maya.cohen@gmail.com​",        # zero-width space from Notion/Slack
    "maya.cohen@gmail.com﻿",        # BOM
    "maya­cohen@gmail.com",         # soft hyphen from a wrapped Word line
    "می‌خواهم@gmail.com",           # Persian ZWNJ is part of the word
])
def test_pasted_or_script_required_invisible_character_is_not_a_block(guard, email):
    """HIGH: obfuscated_email 0.75 blocks a first, only signup. Normalization already collapses these to one inbox."""
    d = guard.check(Signup(email=email, ip="198.51.100.9", device_id="d1", user_agent=CHROME, ts=T0))
    assert d.action != BLOCK, [(s.name, s.score) for s in d.signals if s.score]


@pytest.mark.parametrize("email", ["ｔａｒｏ１２３@yahoo.co.jp", "山田１２３@gmail.com", "taro@ｙａｈｏｏ.co.jp"])
def test_fullwidth_digits_typed_by_a_cjk_keyboard_are_not_a_block(guard, email):
    """MEDIUM: fullwidth forms are what a CJK IME produces; obfuscated_email 0.75 blocks the first signup."""
    d = guard.check(Signup(email=email, ip="198.51.100.9", device_id="d1", user_agent=CHROME, ts=T0))
    assert d.action != BLOCK, [(s.name, s.score) for s in d.signals if s.score]


def test_korean_carrier_iphone_crowd_with_daum_and_hanmail_is_not_pushed_to_block_by_wide_spread(guard):
    """MEDIUM: daum/hanmail are not "well known", so ip_velocity is hard; wide_spread (0.5) then tips review into block."""
    rr = random.Random(2)
    doms = ["naver.com", "naver.com", "gmail.com", "daum.net", "hanmail.net", "gmail.com", "naver.com", "kakao.com"]
    ips = [f"110.70.{k % 12}.{k + 10}" for k in range(30)]
    out = run(guard, [Signup(email=f"{NAMES[i]}@{doms[i % 8]}", ip=rr.choice(ips), device_id=f"d{i}", user_agent=IPHONE,
                             ts=T0 + t) for i, t in enumerate(arrivals(300, 21600, 2))])
    assert BLOCK not in {d.action for d in out}, counts(out)


def test_company_with_an_odd_looking_real_domain_is_not_blocked_inside_an_iphone_carrier_crowd(guard):
    """MEDIUM: cdprojekt.com scores 0.505 (odd_domain); with domain_burst and wide_spread three weak signals add up to block."""
    rr = random.Random(4)
    oddish = ["cdprojekt.com", "strongdm.com", "sprinklr.com", "fxstreet.com", "pkobp.pl"]
    out = run(guard, [Signup(email=f"{NAMES[i]}@{oddish[i % 5] if i % 5 == 0 or i % 7 == 0 else 'gmail.com'}",
                             ip=f"172.20.{i % 90}.{rr.randint(2, 250)}", device_id=f"d{i}", user_agent=IPHONE, ts=T0 + t)
                       for i, t in enumerate(arrivals(150, 86400, 4))])
    assert BLOCK not in {d.action for d in out}, counts(out)


def test_team_on_a_real_company_domain_that_looks_slightly_odd_is_not_reviewed_by_domain_burst_alone(guard):
    """LOW: strongdm.com (shape 0.40) passes the 0.25 gate, so from the 5th employee in 24h domain_burst 0.35 == review_at."""
    r = random.Random(3)
    out = run(guard, [Signup(email=f"{FIRST[i]}.{LAST[i]}@strongdm.com", ip=f"{20 + i * 9}.{i + 3}.{i}.{i + 4}", device_id=f"l{i}",
                             user_agent=CHROME, ts=T0 + t) for i, t in enumerate(sorted(r.uniform(0, 86400) for _ in range(14)))])
    assert REVIEW not in {d.action for d in out}, counts(out)
