"""Tests for tenkyu.domains: disposable-looking domain shape and new-domain burst signals."""
from __future__ import annotations

import pytest

from tenkyu.domains import (BURST_WINDOW, ODD_DOMAIN_SCORE, ODD_DOMAIN_THRESHOLD, domain_shape_score,
                            new_domain_signals, record_domain)
from tenkyu.signals import WELL_KNOWN_PROVIDERS
from tenkyu.store import BlindStore, MemoryStore

NOW = 1_800_000_000.0
DAY = 86400.0

# Real company, brand, government, university and international domains (some deliberately awkward: acronyms,
# digits, hyphens, consonant-heavy names, words that contain "temp", "spam", "mail" or "drop").
REAL_DOMAINS = """
acme.com stripe.com shopify.com github.com gitlab.com vercel.com cloudflare.com netlify.com heroku.com digitalocean.com
atlassian.com slack.com notion.so figma.com canva.com airbnb.com uber.com lyft.com doordash.com instacart.com
dropbox.com box.com zoom.us twilio.com sendgrid.com mailchimp.com mailgun.com mailtrap.io inboxdollars.com
1password.com w3schools.com 37signals.com 500px.com 9gag.com 20minutes.fr 99designs.com 1and1.com 123reg.co.uk
guerrillagames.com spamhaus.org templatemonster.com temple.edu.au tempo.io templates.office.com
microsoft.com apple.com amazon.com google.com meta.com netflix.com spotify.com tesla.com nvidia.com intel.com amd.com
ibm.com oracle.com salesforce.com adobe.com sap.com cisco.com dell.com hp.com lenovo.com samsung.com sony.com
nintendo.com toyota.com honda.com bmw.com mercedes-benz.com volkswagen.de audi.de porsche.com ferrari.com
siemens.com bosch.com basf.com bayer.com allianz.com lufthansa.com thyssenkrupp.com schwarzkopf.com
hdfcbank.com icicibank.com bnpparibas.com rbcroyalbank.com jpmorganchase.com goldmansachs.com americanexpress.com
bankofamerica.com wellsfargo.com citigroup.com hsbc.com barclays.co.uk lloydsbank.co.uk natwest.com santander.es
kpmg.com pwc.com deloitte.com ey.com mckinsey.com bcg.com accenture.com
ycombinator.com techcrunch.com wired.com nytimes.com wsj.com bbc.co.uk theguardian.com economist.com
reuters.com bloomberg.com cnn.com foxnews.com washingtonpost.com lemonde.fr spiegel.de elpais.com corriere.it
ynet.co.il haaretz.co.il mako.co.il bezeq.co.il leumi.co.il hapoalim.co.il isracard.co.il teva.co.il elbit.co.il
wix.com monday.com checkpoint.com fiverr.com taboola.com outbrain.com waze.com mobileye.com
alibaba.com tencent.com baidu.com bytedance.com huawei.com xiaomi.com meituan.com jd.com 51job.com 360.cn 58.com 1688.com
sberbank.ru gazprom.ru vk.com ozon.ru wildberries.ru kaspersky.com
tata.com infosys.com wipro.com flipkart.com zomato.com swiggy.com paytm.com
petrobras.com.br itau.com.br nubank.com.br magazineluiza.com.br mercadolibre.com.ar
telstra.com.au commbank.com.au atlassian.net canva.com.au
rakuten.co.jp toyota.co.jp softbank.jp mercari.com naver.com.kr coupang.com
whitehouse.gov nasa.gov irs.gov nih.gov cdc.gov usa.gov fbi.gov gov.uk europa.eu bundesregierung.de gouvernement.fr canada.ca
stanford.edu mit.edu harvard.edu ox.ac.uk cam.ac.uk ethz.ch epfl.ch tum.de kth.se uni-heidelberg.de tu-berlin.de
utoronto.ca mcgill.ca sorbonne-universite.fr kaist.ac.kr technion.ac.il huji.ac.il
coca-cola.com hewlett-packard.com t-mobile.com e-on.com la-z-boy.com mary-kay.com bang-olufsen.com
nestle.com unilever.com danone.com loreal.com nike.com adidas.com puma.com zara.com ikea.com hm.com
walmart.com target.com costco.com homedepot.com lowes.com bestbuy.com ebay.com etsy.com
mcdonalds.com starbucks.com kfc.com subway.com dominos.com pizzahut.com
ford.com gm.com chevrolet.com boeing.com airbus.com lockheedmartin.com
pfizer.com novartis.com roche.com astrazeneca.com moderna.com
szczecin.pl rzeszow.pl orange.pl allegro.pl onet.pl
kuehne-nagel.com maersk.com dhl.com fedex.com ups.com
patagonia.com rei.com thenorthface.com
anthropic.com openai.com huggingface.co mistral.ai cohere.com
strengthsfinder.com rhythmstudio.com crystalclear.org
bet365.com ps4pro.com club4x4.com ticket4u.com
tripadvisor.com booking.com expedia.com kayak.com skyscanner.net
local-bakery.com smithandsons.co.uk joeshardware.com blueskyplumbing.com greenleaflandscaping.net
""".split()
# Invented disposable-looking domains: random letters, digit soup, digit-only, word patterns, mail + noise, hyphen
# piles, very long labels and cheap TLDs. None of these is a real service on purpose.
INVENTED_DISPOSABLE = """
xkqzvtrp.com qwrtplkjh.net zxcvbnmasd.org bkdfgnhs.xyz vhxqzjwk.top
x7k2q9.com a1b2c3d4.net m4k9p2x7.org q8w3e6r1.xyz z9x8c7v6b5.top k3j9d02m.icu
83920174.com 5550192837.net 9182736.xyz 674839.click
tempmail-pro.com trashmail24.net throwawaymail.org burnermail.io fakeinbox.net 10minutemail.co 10minutesmail.xyz
guerrillamailblock.com yopmail-clone.fr mailinator2.com spamgourmet.net dropmail24.me tempinbox.top disposableemail.cc
getnada-x.com nospam-box.org
mailx7k2q9.com inboxqzxvk.net zxqmail.top mail-b8d3f1.com xk39mail.org
my-free-temp-mail-box.com free-cheap-new-mail-now.com a-b-c-d-e.com
thisisaveryrandomgeneratedlongdomainnamethatnobodyuses.com qwertyuiopasdfghjklzxcvbnmqwerty.net
kjhgfdsa123.cfd xqzjkv.sbs wvtrnqlp.rest hjklzxc4567.shop
tmp-9382.com temp7731.net junk-mail-x.org fake-inbox-77.com p0q9w8e7r6.buzz
rtkvbnxz.monster bgfrdsxz.icu t3mpm4il.com
""".split()

SECRET = b"0123456789abcdef0123456789abcdef"


@pytest.fixture(params=["memory", "blind"])
def store(request):
    return MemoryStore(clock=lambda: NOW) if request.param == "memory" else BlindStore(MemoryStore(clock=lambda: NOW), SECRET)


def test_list_sizes():
    assert len(REAL_DOMAINS) >= 150
    assert len(INVENTED_DISPOSABLE) >= 40
    assert len(set(REAL_DOMAINS)) == len(REAL_DOMAINS)
    assert len(set(INVENTED_DISPOSABLE)) == len(INVENTED_DISPOSABLE)


def test_invented_disposable_domains_score_high():
    scores = {d: domain_shape_score(d) for d in INVENTED_DISPOSABLE}
    missed = {d: s for d, s in scores.items() if s < ODD_DOMAIN_THRESHOLD}
    rate = 1 - len(missed) / len(scores)
    assert rate >= 0.95, f"detection rate {rate:.2%}, missed: {missed}"
    assert all(0.0 <= s <= 1.0 for s in scores.values())


@pytest.mark.parametrize("domain", ["xkqzvtrp.com", "x7k2q9.com", "tempmail-pro.com", "83920174.com", "mailx7k2q9.com"])
def test_clear_cases_are_well_above_threshold(domain):
    assert domain_shape_score(domain) >= ODD_DOMAIN_THRESHOLD + 0.1


def test_real_domains_score_low_and_never_odd():
    scores = {d: domain_shape_score(d) for d in REAL_DOMAINS}
    hits = {d: s for d, s in scores.items() if s >= ODD_DOMAIN_THRESHOLD}
    assert not hits, f"false positives: {hits}"
    # a margin, so a small tuning change does not turn a normal company into an odd one
    assert max(scores.values()) < ODD_DOMAIN_THRESHOLD - 0.04, max(scores.items(), key=lambda kv: kv[1])
    assert sum(scores.values()) / len(scores) < 0.06


def test_real_domains_do_not_raise_odd_domain_signal(store):
    for d in REAL_DOMAINS:
        assert [s.name for s in new_domain_signals(store, NOW, d, store.blind) if s.name == "odd_domain"] == [], d


def test_invented_domains_raise_odd_domain_signal():
    st = MemoryStore(clock=lambda: NOW)
    hit = [d for d in INVENTED_DISPOSABLE if any(s.name == "odd_domain" for s in new_domain_signals(st, NOW, d))]
    assert len(hit) / len(INVENTED_DISPOSABLE) >= 0.95
    sig = next(s for s in new_domain_signals(st, NOW, "x7k2q9.com") if s.name == "odd_domain")
    assert sig.score == ODD_DOMAIN_SCORE == 0.3
    assert "x7k2q9.com" in sig.reason


@pytest.mark.parametrize("domain", sorted(WELL_KNOWN_PROVIDERS) + ["yahoo.co.uk", "yahoo.fr", "stanford.edu", "ox.ac.uk",
                                                                  "technion.ac.il", "u-tokyo.ac.jp", "foo.edu.au",
                                                                  "springfield.k12.il.us", "cs.mit.edu"])
def test_well_known_and_edu_score_zero(domain):
    assert domain_shape_score(domain) == 0.0


def test_well_known_never_gets_signals_or_records(store):
    for i in range(12):
        record_domain(store, NOW, "gmail.com", f"u{i}@gmail.com", store.blind)
        record_domain(store, NOW, "stanford.edu", f"u{i}@stanford.edu", store.blind)
    assert new_domain_signals(store, NOW, "gmail.com", store.blind) == []
    assert new_domain_signals(store, NOW, "stanford.edu", store.blind) == []
    assert store.values("dom:gmail.com", DAY, NOW) == set()


def test_tld_alone_is_a_small_nudge():
    plain = domain_shape_score("bakery.com")
    for tld in ("xyz", "top", "icu", "click", "buzz", "monster", "cfd", "sbs", "rest", "shop"):
        assert domain_shape_score(f"bakery.{tld}") < ODD_DOMAIN_THRESHOLD
        assert domain_shape_score(f"bakery.{tld}") <= plain + 0.13


def test_each_shape_feature_scores_in_the_right_direction():
    assert domain_shape_score("zxcvbnmqwrt.com") > domain_shape_score("bakery.com")
    assert domain_shape_score("a1b2c3d4e5.com") > domain_shape_score("bet365.com")
    assert domain_shape_score("123456789.com") >= ODD_DOMAIN_THRESHOLD
    assert domain_shape_score("1-2-3-4-5.com") >= ODD_DOMAIN_THRESHOLD
    assert domain_shape_score("a" * 40 + ".com") > domain_shape_score("a" * 10 + ".com")
    assert domain_shape_score("one-two-three-four-five.com") > domain_shape_score("one-two.com")
    assert domain_shape_score("temp123.com") >= ODD_DOMAIN_THRESHOLD
    assert domain_shape_score("guerrillamail.xyz") >= ODD_DOMAIN_THRESHOLD
    assert domain_shape_score("guerrillagames.com") < ODD_DOMAIN_THRESHOLD  # real studio, not the mail service


def test_registrable_label_is_judged_not_the_subdomain():
    assert domain_shape_score("mail.acme.com") == domain_shape_score("acme.com")
    assert domain_shape_score("zxkqvj.example.co.uk") == domain_shape_score("example.co.uk")
    assert domain_shape_score("shop.xkqzvtrp.com") >= ODD_DOMAIN_THRESHOLD
    assert domain_shape_score("xkqzvtrp.co.uk") >= ODD_DOMAIN_THRESHOLD


def test_case_and_trailing_dot_do_not_matter():
    assert domain_shape_score("XKQZVTRP.COM.") == domain_shape_score("xkqzvtrp.com")


@pytest.mark.parametrize("garbage", [None, 5, 3.14, True, b"x7k2q9.com", [], {}, ("a.com",), "", " ", ".", "..", "a..b",
                                     "no-dot", "has space.com", "a@b.com", "\x00.com", "\ud800.com"])
def test_garbage_is_safe(garbage, store):
    assert 0.0 <= domain_shape_score(garbage) <= 1.0
    assert isinstance(new_domain_signals(store, NOW, garbage, store.blind), list)
    record_domain(store, NOW, garbage, "a@b.com", store.blind)  # must not raise


def test_huge_and_unicode_inputs_are_safe(store):
    for d in ("a" * 100_000 + ".com", ("ab." * 5000) + "com", "x" * 64 + ".com", "x" * 300, "-" * 63 + ".com"):
        assert 0.0 <= domain_shape_score(d) <= 1.0
        assert isinstance(new_domain_signals(store, NOW, d, store.blind), list)
    assert domain_shape_score("a" * 100_000 + ".com") == 0.0  # not a domain at all; invalid_email handles it
    for d in ("münchen.de", "пример.рф", "例え.jp", "bücher.ch", "xn--mnchen-3ya.de", "ｘｋｑｚ.com", "العربية.مصر",
              "‮evil.com", "😀.com", "école.fr", "ΕΛΛΗΝΙΚΑ.gr"):
        s = domain_shape_score(d)
        assert 0.0 <= s <= 1.0
        assert s < ODD_DOMAIN_THRESHOLD, d
        assert isinstance(new_domain_signals(store, NOW, d, store.blind), list)
    record_domain(store, NOW, "münchen.de", "x@münchen.de", store.blind)
    record_domain(store, NOW, "acme.com", 12345, store.blind)  # non-string address ignored
    record_domain(store, NOW, "acme.com", None, store.blind)
    assert store.values("dom:acme.com", DAY, NOW) == set()


def test_invalid_now_does_not_break():
    st = MemoryStore(clock=lambda: NOW)
    record_domain(st, "bad", "acme.com", "a@acme.com")
    assert isinstance(new_domain_signals(st, float("nan"), "acme.com"), list)
    assert isinstance(new_domain_signals(st, None, "acme.com"), list)


def _burst(store, domain, n, now=NOW):
    for i in range(n):
        record_domain(store, now, domain, f"user{i}@{domain}", store.blind)


def _names(sigs):
    return {s.name: s.score for s in sigs}


def test_burst_thresholds(store):
    d = "acme.com"
    assert "domain_burst" not in _names(new_domain_signals(store, NOW, d, store.blind))
    _burst(store, d, 4)
    assert "domain_burst" not in _names(new_domain_signals(store, NOW, d, store.blind))
    _burst(store, d, 5)
    assert _names(new_domain_signals(store, NOW, d, store.blind))["domain_burst"] == 0.3
    _burst(store, d, 9)
    assert _names(new_domain_signals(store, NOW, d, store.blind))["domain_burst"] == 0.3
    _burst(store, d, 10)
    sigs = new_domain_signals(store, NOW, d, store.blind)
    assert _names(sigs)["domain_burst"] == 0.45
    assert [s.name for s in sigs].count("domain_burst") == 1
    assert "odd_domain" not in _names(sigs)  # a burst from a normal-looking domain is not "odd"


def test_burst_counts_distinct_addresses_only(store):
    for _ in range(30):
        record_domain(store, NOW, "acme.com", "same@acme.com", store.blind)
    assert "domain_burst" not in _names(new_domain_signals(store, NOW, "acme.com", store.blind))
    _burst(store, "acme.com", 3)  # 4 distinct with same@
    assert "domain_burst" not in _names(new_domain_signals(store, NOW, "acme.com", store.blind))
    record_domain(store, NOW, "acme.com", "another@acme.com", store.blind)  # 5 distinct
    assert "domain_burst" in _names(new_domain_signals(store, NOW, "acme.com", store.blind))


def test_replay_cannot_push_out_other_addresses():
    st = MemoryStore(clock=lambda: NOW)
    _burst(st, "acme.com", 6)
    for _ in range(3000):
        record_domain(st, NOW, "acme.com", "user0@acme.com", st.blind)
    assert "domain_burst" in _names(new_domain_signals(st, NOW, "acme.com", st.blind))


def test_burst_is_24h_sliding_window(store):
    _burst(store, "acme.com", 6, now=NOW)
    assert "domain_burst" in _names(new_domain_signals(store, NOW + DAY - 60, "acme.com", store.blind))
    assert "domain_burst" not in _names(new_domain_signals(store, NOW + DAY + 60, "acme.com", store.blind))
    assert BURST_WINDOW == DAY


def test_burst_is_per_domain(store):
    _burst(store, "acme.com", 12)
    assert new_domain_signals(store, NOW, "other.com", store.blind) == []
    assert "domain_burst" in _names(new_domain_signals(store, NOW, "ACME.com.", store.blind))


def test_burst_and_odd_together(store):
    _burst(store, "xkqzvtrp.com", 5)
    names = _names(new_domain_signals(store, NOW, "xkqzvtrp.com", store.blind))
    assert names == {"odd_domain": 0.3, "domain_burst": 0.3}


def test_signals_are_weak_by_design(store):
    _burst(store, "xkqzvtrp.top", 20)
    for s in new_domain_signals(store, NOW, "xkqzvtrp.top", store.blind):
        assert s.score <= 0.5
    assert ODD_DOMAIN_SCORE < 0.5


def test_blind_store_holds_no_raw_address_or_domain():
    inner = MemoryStore(clock=lambda: NOW)
    bs = BlindStore(inner, SECRET)
    _burst(bs, "acme.com", 6)
    dump = repr(dict(inner._events))
    assert "acme.com" not in dump and "user0" not in dump
    assert "domain_burst" in _names(new_domain_signals(bs, NOW, "acme.com", bs.blind))


def test_broken_store_does_not_break_signup():
    class Broken:
        def values(self, *a, **k):
            raise RuntimeError("down")

        def add(self, *a, **k):
            raise RuntimeError("down")

    names = _names(new_domain_signals(Broken(), NOW, "xkqzvtrp.com"))
    assert names == {"odd_domain": 0.3}


def test_false_positive_and_detection_rates_are_reported(capsys):
    fp = sum(domain_shape_score(d) >= ODD_DOMAIN_THRESHOLD for d in REAL_DOMAINS) / len(REAL_DOMAINS)
    tp = sum(domain_shape_score(d) >= ODD_DOMAIN_THRESHOLD for d in INVENTED_DISPOSABLE) / len(INVENTED_DISPOSABLE)
    with capsys.disabled():
        print(f"\ndomains: threshold={ODD_DOMAIN_THRESHOLD} real={len(REAL_DOMAINS)} fp={fp:.2%} "
              f"invented={len(INVENTED_DISPOSABLE)} detection={tp:.2%}")
    assert fp == 0.0 and tp >= 0.95


# --- registrable-domain keying, leetspeak / non-ASCII word patterns, network spread --------------------------------

from tenkyu.domains import domain_network_spread, registrable_domain  # noqa: E402


@pytest.mark.parametrize("domain,expected", [
    ("mail.acme.com", "acme.com"), ("s7.acme.com", "acme.com"), ("acme.com", "acme.com"),
    ("a.b.acme.co.uk", "acme.co.uk"), ("x.acme.com.au", "acme.com.au"), ("x.acme.co.il", "acme.co.il"),
    ("x.acme.com.br", "acme.com.br"), ("x.acme.co.jp", "acme.co.jp"), ("x.uni.ac.il", "uni.ac.il"),
    ("x.uni.edu.au", "uni.edu.au"), ("a.b.c.example.xyz", "example.xyz"),
])
def test_registrable_domain(domain, expected):
    assert registrable_domain(domain) == expected


def test_burst_counts_one_subdomain_per_account_as_one_domain():
    st = MemoryStore(clock=lambda: NOW)
    for i in range(6):
        record_domain(st, NOW, f"s{i}.qzkvbnwt.xyz", f"u{i}@s{i}.qzkvbnwt.xyz")
    assert any(s.name == "domain_burst" for s in new_domain_signals(st, NOW, "s9.qzkvbnwt.xyz"))


def test_burst_counts_a_homoglyph_spelling_of_the_domain_with_the_plain_one():
    st = MemoryStore(clock=lambda: NOW)
    for i in range(3):
        record_domain(st, NOW, "qzxvbnwt.xyz", f"a{i}@qzxvbnwt.xyz")
        record_domain(st, NOW, "qzxvbnwt.xyz".replace("x", "\u0445"), f"b{i}@x")  # Cyrillic ha
    assert any(s.name == "domain_burst" for s in new_domain_signals(st, NOW, "qzxvbnwt.xyz"))


@pytest.mark.parametrize("domain", ["trashmai1.com", "yopmai1.com", "guerrillamai1.com", "tempma1l.com",
                                    "burnerma1l.com", "tempmаil.xyz", "mаilinаtor.com"])
def test_disposable_words_in_leetspeak_or_cyrillic_are_odd(domain):
    assert domain_shape_score(domain) >= ODD_DOMAIN_THRESHOLD


@pytest.mark.parametrize("domain", ["bücher.de", "münchen.de", "почта.рф", "w3schools.com",
                                    "1password.com", "t3mple.com"])
def test_real_idn_and_digit_brands_stay_below_the_odd_threshold(domain):
    assert domain_shape_score(domain) < ODD_DOMAIN_THRESHOLD


# Real brands whose names contain a disposable-looking word (mail, temp, drop, inbox, spam, 10minute, nospam). The strong
# word patterns must be bounded so none of them gets the odd_domain words score (0.45, a review).
REAL_BRANDS_WITH_DISPOSABLE_WORDS = """
10minuteschool.com emailtemplates.com emailtemplates.net nospamproxy.com nospamproxy.de
dropbox.com dropmark.com dropcontact.com postdrop.io dropcam.com
mailchimp.com mailgun.com mailjet.com mailbird.com mailpoet.com mailerlite.com mailtrap.io mailshake.com mailbox.org
mailfence.com mailbutler.io mailcatcher.me mailmunch.com mailmodo.com emailoctopus.com emailjs.com emailable.com
inboxdollars.com inbox.lv inbox.com
spamhaus.org spamcop.net spambrella.com spamassassin.apache.org
tempurpedic.com tempur-sealy.com tempo.io tempcover.com tempworks.com templatemonster.com templafy.com
""".split()


def test_real_brands_with_disposable_looking_words_stay_below_the_odd_threshold(store):
    assert len(REAL_BRANDS_WITH_DISPOSABLE_WORDS) >= 40
    scores = {d: domain_shape_score(d) for d in REAL_BRANDS_WITH_DISPOSABLE_WORDS}
    assert not {d: s for d, s in scores.items() if s >= ODD_DOMAIN_THRESHOLD}, scores
    for d in REAL_BRANDS_WITH_DISPOSABLE_WORDS:
        assert [s for s in new_domain_signals(store, NOW, d, store.blind) if s.name == "odd_domain"] == [], d


@pytest.mark.parametrize("domain", ["10minutemail.com", "10minutesmail.com", "tenminutemail.net", "20minutemail.com", "30minutemail.com",
                                    "10minute-mail.org", "mailtemp.net", "emailtemp.org", "mail-temp.com", "email-temp.com",
                                    "inboxtemp.com", "mailtmp.com", "emailtrash.com", "inboxfake.net", "mailspam.org", "nospam.com", "nospam24.net",
                                    "no-spam.com", "mailtemp24.com", "tempmail.com", "mailinator.com"])
def test_bounding_the_strong_words_keeps_real_disposable_patterns_odd(domain):
    assert domain_shape_score(domain) >= ODD_DOMAIN_THRESHOLD


@pytest.mark.parametrize("blinded", [False, True])
def test_network_spread_counts_distinct_networks_per_registrable_domain(blinded):
    inner = MemoryStore(clock=lambda: NOW)
    st = BlindStore(inner, b"s" * 32) if blinded else inner
    blind = getattr(st, "blind", None) or (lambda v: v)
    for i in range(8):
        record_domain(st, NOW, f"s{i}.harborline.com", f"u{i}@harborline.com", blind=blind, net=f"45.{i % 7}.9.0/24")
    assert domain_network_spread(st, NOW, "harborline.com", blind=blind) == 7
    assert domain_network_spread(st, NOW + 2 * DAY, "harborline.com", blind=blind) == 0
    assert domain_network_spread(st, NOW, "gmail.com", blind=blind) == 0
    assert domain_network_spread(st, NOW, "nope", blind=blind) == 0


def test_record_domain_without_net_records_no_network():
    st = MemoryStore(clock=lambda: NOW)
    record_domain(st, NOW, "harborline.com", "a@harborline.com")
    assert domain_network_spread(st, NOW, "harborline.com") == 0
