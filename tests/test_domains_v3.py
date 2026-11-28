"""Tests for the compound disposable-word model in tenkyu.domains (names like dumpmail, voidpost, oneshotmail)."""
from __future__ import annotations

import pytest

from tenkyu import ALLOW, BLOCK, REVIEW, Guard, Signup
from tenkyu.domains import (BURST_WORDS_MIN, ODD_DOMAIN_SCORE, ODD_DOMAIN_THRESHOLD, ODD_DOMAIN_WORDS_SCORE,
                            ODD_DOMAIN_WORDS_THRESHOLD, compound_score, disposable_word_score, domain_shape_score,
                            new_domain_signals, record_domain)
from tenkyu.store import MemoryStore

NOW = 1_800_000_000.0
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

COMPOUNDS = """dumpmail.store oneshotmail.com fleetingmail.co puffmail.online voidpost.click nullbox.site cinderbox.work
anonbox.xyz bouncebin.net tempinbox.net burnermail.dev spam-mail.org trash-inbox.co junkmail.cc discardbox.com
nul-mail.io vanishmail.net ephemeralmail.org poofinbox.com expiringmail.com""".split()

# Real brands and services that contain one of the words but are not made only of throwaway + mailbox words.
HONEST = """dropbox.com scrapbox.io fastmail.com mailbox.org postbox.com mailchimp.com mailgun.com postmark.com hushmail.com
runbox.com startmail.com zoho.com templates.office.com tempo.io spamhaus.org guerrillagames.com nullpointer.io
voidlinux.org dumpster.com hubspot.com sendgrid.com mailjet.com mailfence.com onedrive.com oneplus.com shotgun.com
quickbooks.com fasttrack.com bounce.io burnside.com junkyard.com trashy.com ghostery.com phantom.app""".split()


@pytest.mark.parametrize("domain", COMPOUNDS)
def test_compound_names_are_strong_word_evidence(domain):
    assert disposable_word_score(domain) >= ODD_DOMAIN_WORDS_THRESHOLD, domain
    assert domain_shape_score(domain) >= ODD_DOMAIN_THRESHOLD


@pytest.mark.parametrize("domain", HONEST)
def test_honest_names_are_not_word_evidence(domain):
    assert disposable_word_score(domain) < ODD_DOMAIN_WORDS_THRESHOLD, domain
    assert domain_shape_score(domain) < ODD_DOMAIN_THRESHOLD, domain


def test_strong_word_tiers():
    assert compound_score("dumpmail") == 0.9
    assert compound_score("voidpost") == 0.8
    assert compound_score("oneshotmail") == 0.9  # the longest cover wins: "oneshot" is one strong word
    assert compound_score("quickmail") == 0.5  # weak word + mail word: only the weak odd_domain signal
    assert compound_score("quickdrop") == 0.45 and compound_score("quickdrop", "email") == 0.5
    assert compound_score("tempmail24") == 0.9 and compound_score("24tempmail") == 0.9


def test_needs_both_a_throwaway_word_and_a_mailbox_word():
    for label in ("dump", "mail", "mailbox", "dropbox", "dumpster", "temple", "attempt", "inboxinbox", "postdrop"):
        assert compound_score(label) == 0.0, label


def test_digits_and_odd_input_are_safe():
    for bad in ("", "12", "a", "dump-12-mail-34-x", "tempmail" * 10, "dümpmail", None, 5):
        try:
            compound_score(bad) if isinstance(bad, str) else disposable_word_score(bad)
        except Exception as exc:  # pragma: no cover
            pytest.fail(f"{bad!r} raised {exc!r}")
    assert disposable_word_score(None) == 0.0 and disposable_word_score("no-dot") == 0.0
    assert disposable_word_score("gmail.com") == 0.0 and disposable_word_score("cs.stanford.edu") == 0.0


def test_reserved_tlds_are_test_names_not_judged():
    for d in ("dumpmail.example", "tempmail.test", "voidpost.invalid", "nomail-tmp.example"):
        assert disposable_word_score(d) == 0.0, d


def test_subdomain_and_country_second_level():
    assert disposable_word_score("www.dumpmail.store") >= 0.8
    assert disposable_word_score("dumpmail.co.uk") >= 0.8
    assert disposable_word_score("mail.dumpmail.co.uk") >= 0.8


def test_odd_domain_score_has_two_tiers():
    st = MemoryStore(clock=lambda: NOW)
    words = [s for s in new_domain_signals(st, NOW, "dumpmail.store") if s.name == "odd_domain"]
    shape = [s for s in new_domain_signals(st, NOW, "x7k2q9.com") if s.name == "odd_domain"]
    assert [s.score for s in words] == [ODD_DOMAIN_WORDS_SCORE] == [0.45]
    assert [s.score for s in shape] == [ODD_DOMAIN_SCORE] == [0.3]
    weak = [s for s in new_domain_signals(st, NOW, "quickmail.com") if s.name == "odd_domain"]
    assert [s.score for s in weak] == [0.3]
    assert ODD_DOMAIN_WORDS_SCORE < 0.5  # still below anything the tests call a verdict


def test_burst_starts_earlier_for_word_like_names_only():
    def names(domain, n):
        st = MemoryStore(clock=lambda: NOW)
        for i in range(n):
            record_domain(st, NOW, domain, f"u{i}@{domain}", st.blind)
        return {s.name: s.score for s in new_domain_signals(st, NOW, domain, st.blind)}

    assert "domain_burst" not in names("quickmail.com", 2)
    assert names("quickmail.com", 3)["domain_burst"] == 0.15
    assert disposable_word_score("quickmail.com") >= BURST_WORDS_MIN
    assert "domain_burst" not in names("acme.com", 4)
    assert "domain_burst" not in names("xkqzvtrp.com", 4)  # odd shape but no disposable words: still needs 5
    assert names("xkqzvtrp.com", 5)["domain_burst"] == 0.3


def _signup(email, ip="203.0.113.7", dev="dev-1", ts=NOW):
    return Signup(email=email, ip=ip, device_id=dev, user_agent=UA, ts=ts)


def test_word_domain_alone_is_reviewed_never_blocked():
    g = Guard(store=MemoryStore(clock=lambda: NOW))
    d = g.check(_signup("jane.doe@dumpmail.store"))
    assert d.action == REVIEW and [s.name for s in d.signals if s.score > 0] == ["odd_domain"]
    assert BLOCK != d.action


def test_weak_word_or_random_shape_alone_is_still_allowed():
    g = Guard(store=MemoryStore(clock=lambda: NOW))
    assert g.check(_signup("a@quickmail.com", dev="d2", ip="198.51.100.1")).action == ALLOW
    assert g.check(_signup("b@xkqzvtrp.com", dev="d3", ip="198.51.100.2")).action == ALLOW


def test_honest_domains_are_untouched():
    g = Guard(store=MemoryStore(clock=lambda: NOW))
    for i, d in enumerate(HONEST[:12]):
        r = g.check(_signup(f"person{i}@{d}", ip=f"198.51.100.{i + 10}", dev=f"dev-h{i}"))
        assert r.action == ALLOW, d


def test_word_domain_with_a_second_signal_can_block():
    g = Guard(store=MemoryStore(clock=lambda: NOW))
    r = None
    for i in range(3):  # three accounts on one device from one IP: device_reuse + ip signals on top of the domain
        r = g.check(_signup(f"user{i}x@voidpost.click", ts=NOW + i))
    assert r.action in (REVIEW, BLOCK) and "odd_domain" in [s.name for s in r.signals]


# Real products, SaaS and dev tools with short, consonant-heavy or word-built labels (inboxsdk, npmjs, yarnpkg, cdnjs,
# phpbb) and mail/trash/drop/temp/spam/inbox/box/post words (trashbin, mailbox, postdrop). None may reach the odd_domain
# threshold: letter randomness weighs less for a label made of common English/dev words, and "trash" + "bin" is a bin,
# not a throwaway mailbox.
REAL_BRANDS_V3 = """
akamai.com amd.com bbva.com bitbucket.org bmw.com bnpparibas.com bnymellon.com boxbilling.com boxcryptor.com
btcpay.org bunny.net cdn.jsdelivr.net cdn77.com cdnjs.cloudflare.com cdnjs.com cdnsun.com cloudflare.com cnbc.com
cpanel.net curl.se dbeaver.io dmarcian.com dnsimple.com dnsmadeeasy.com drizzle.team dropbox.com dropboxapi.com
dropcontact.com dropmark.com dropzone.dev emailable.com emailengine.app emailjs.com emailoctopus.com emailrep.io
esbuild.github.io fastly.com fastmail.com ffmpeg.org ge.com gitbook.com gitea.io github.com gitlab.com gitpod.io
grpc.io hp.com hsbc.com htmlcsstoimage.com htmx.org hushmail.com ikea.com inbox.com inbox.lv inboxdollars.com
inboxsdk.com inboxzero.com ing.com jotai.org jsbin.com jsdelivr.com jsdelivr.net jsfiddle.net jsonbin.io jsonblob.com
jsonbox.io kdb.com knex.org kpmg.com kx.com lg.com libcurl.com lxml.de mailbird.com mailbox.com mailbox.org
mailboxlayer.com mailboxvalidator.com mailbutler.io mailcatcher.me mailchimp.com mailerlite.com mailfence.com
mailgun.com mailhog.com mailjet.com mailmodo.com mailmunch.com mailosaur.com mailpit.axllent.org mailpoet.com
mailshake.com mailslurp.com mailtrap.io mariadb.org mbed.com mhtml.org mjml.io mobx.js.org ms.com mtgjson.com
mxguard.com mxlogin.com mxroute.com mxtoolbox.com mysql.com nbcuniversal.com nghttp2.org nginx.org nmap.org npmjs.com
npmjs.org npr.org nuxt.com nvidia.com nytimes.com openssl.org pgadmin.org pgbouncer.org pgexercises.com pgloader.io
pgvector.dev php.net phpbb.com phpmyadmin.net plaid.com pnpm.io postbox.com postboxapp.com postdrop.io posteo.de
postfix.org postgres.org posthog.com postman.com postmarkapp.com postmates.com prisma.io protobuf.dev protonmail.com
psql.io psycopg.org pwc.com qualcomm.com qwant.com qwik.dev qwiklabs.com recoil.dev redux.js.org rspack.dev rsync.net
runbox.com rxjs.dev scryfall.com sendbird.com sendbox.io sendgrid.com sendinblue.com smtp.com smtp2go.com solidjs.com
spamassassin.apache.org spambrella.com spamcop.net spamhaus.org spampd.org spamtitan.com sphinx-doc.org sqlalchemy.org
sqlite.org sqreen.com squarespace.com startmail.com stripe.com svelte.dev svgator.com svgomg.dev swr.vercel.app
tcl.com tcpdump.org tempcover.com tempo.io temporal.io tempworks.com thrift.apache.org tmdb.org tmpfs.dev trashbag.com
trashbin.com trashbin.io trashcan.com trpc.io twilio.com ubs.com unpkg.com vite.dev vrbo.com vrchat.com vuejs.org
webpack.js.org wireshark.org wpengine.com wpvip.com wsj.com xilinx.com xkcd.com xmpp.org yarnpkg.com ycombinator.com
zod.dev zsh.org zustand.dev zxcvbn.js.org zxing.org
""".split()


def test_real_short_and_word_built_brands_stay_below_the_odd_threshold():
    store = MemoryStore(clock=lambda: NOW)
    assert len(REAL_BRANDS_V3) >= 150 and len(set(REAL_BRANDS_V3)) == len(REAL_BRANDS_V3)
    flagged = {d: domain_shape_score(d) for d in REAL_BRANDS_V3 if domain_shape_score(d) >= ODD_DOMAIN_THRESHOLD}
    assert not flagged, flagged
    for d in REAL_BRANDS_V3:
        assert [s for s in new_domain_signals(store, NOW, d) if s.name == "odd_domain"] == [], d


@pytest.mark.parametrize("domain", ["trashbin.com", "trashcan.com", "trash-bin.com", "thetrashbin.com", "junkbin.com", "garbagecan.com"])
def test_waste_word_plus_container_is_a_bin_not_a_disposable_mailbox(domain):
    assert compound_score(domain.split(".")[0]) == 0.0
    assert domain_shape_score(domain) < ODD_DOMAIN_THRESHOLD


@pytest.mark.parametrize("domain", ["trashmail.com", "trashbox.net", "trash-inbox.co", "junkmail.cc", "discardbox.com", "bouncebin.net"])
def test_trash_with_a_mail_word_or_a_non_waste_container_stays_throwaway(domain):
    assert domain_shape_score(domain) >= ODD_DOMAIN_THRESHOLD


@pytest.mark.parametrize("domain", ["qzxkvbsdk.com", "xkqzvbtnjs.com", "sdkqxzkvbwrt.io", "inboxqzxkvbwr.com", "kzqxjvwbpgsdk.net", "cdnx7k2q9m.com"])
def test_a_dev_word_does_not_hide_a_random_remainder(domain):
    assert domain_shape_score(domain) >= ODD_DOMAIN_THRESHOLD, domain
