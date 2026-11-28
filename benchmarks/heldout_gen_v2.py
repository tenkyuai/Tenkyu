#!/usr/bin/env python3
"""Held-out synthetic sign-up data, v2 (independently authored, stdlib only).

generate(seed, n_real=800) -> list of dict rows sorted by ts. Honest groups are
deliberately look-alike hard (school NAT, CGNAT, VPN, families, work+personal)
and farmers mix proxy rotation, homoglyph domains, disposable mail, reuse and
timing tricks. Everything is labelled; deterministic per seed.
"""
import collections
import datetime
import random
import sys
import unicodedata

T0 = int(datetime.datetime(2026, 3, 1, tzinfo=datetime.timezone.utc).timestamp())  # a Sunday
DAY, WINDOW = 86400, 14 * 86400
HOURW = [1, 1, 1, 1, 1, 2, 3, 5, 6, 7, 7, 7, 7, 7, 7, 7, 8, 9, 10, 10, 10, 9, 6, 3]
WEEKDAYS = [d for d in range(14) if d % 7 in (1, 2, 3, 4, 5)]
PUB_A = [a for a in range(2, 224) if a not in (10, 100, 127, 169, 172, 192, 198, 203)]
ALNUM = "abcdefghijklmnopqrstuvwxyz0123456789"

FIRST = ("james olivia liam emma noah ava lucas mia ethan sophia mason isabella logan amelia jack harper owen ella "
         "daniel chloe david grace samuel lily henry zoe leo nora adam ruth tom anna ben eva max lena paul sara ian "
         "kate dan maya omar yael noam tamar eitan shira ariel dana").split()
LAST = ("smith johnson brown taylor miller wilson moore clark lewis walker hall young king wright scott green baker "
        "adams nelson hill cohen levi mizrahi peretz katz friedman rosen schwartz meyer fischer weber becker rossi "
        "ferrari bianchi dubois martin bernard silva santos costa kowalski novak horvat andersson larsen virtanen").split()
INTL = [("José", "García"), ("François", "Lefèvre"), ("Zoë", "Müller-Schmidt"), ("Aleksandr", "Petrovich-Sokolov"),
        ("Jean-Baptiste", "de la Fontaine-Beaumarchais"), ("Nguyen", "Thi Minh Khai"), ("Mohammed", "Al-Rashidi"),
        ("Dmitriy", "Ivanov"), ("Svetlana", "Kuznetsova"), ("Hiroshi", "Tanaka"), ("Yuki", "Nakamura"),
        ("Min-jun", "Kim"), ("Wei", "Zhang"), ("Priyanka", "Venkataraman-Subramaniam"), ("Ólafur", "Þórsson"),
        ("Łukasz", "Wiśniewski"), ("Çağlar", "Yıldırım"), ("Søren", "Kierkegaard-Østergaard"),
        ("Zeynep", "Öztürk"), ("Mariana", "Fernández de Córdoba y Alcázar"), ("Anastasiia", "Shevchenko"),
        ("Giorgi", "Kvaratskhelia"), ("Fatima", "Zahra Benali"), ("Tzvi", "Hirschhorn"),
        ("Oluwaseun", "Adeyemi-Ogunbanjo"), ("Siddharth", "Chattopadhyay"), ("Björn", "Åkesson"),
        ("Sean", "O'Brien"), ("Dara", "D'Angelo"), ("Ngoc", "Trần"), ("Ana", "Muñoz")]
PROVIDERS = (["gmail.com"] * 55 + ["outlook.com"] * 10 + ["yahoo.com"] * 8 + ["hotmail.com"] * 6 + ["icloud.com"] * 8
             + ["proton.me"] * 4 + ["live.com"] * 2 + ["aol.com", "gmx.com", "gmx.com", "fastmail.com", "yahoo.co.uk"])
INTL_PROV = ["mail.ru", "yandex.ru", "naver.com", "qq.com", "163.com", "web.de", "gmx.de", "orange.fr", "libero.it",
             "walla.co.il", "gmail.com", "gmail.com", "gmail.com", "outlook.com", "yahoo.co.jp", "hotmail.es",
             "rediffmail.com", "seznam.cz", "wp.pl", "uol.com.br", "abv.bg", "icloud.com"]
IDN = ["bäckerei-wagner.de", "académie-lyon.fr", "universität-köln.de", "россия-почта.рф", "münchen-it.de",
       "café-numérique.fr"]
COMPANIES = ["northwind-freight.com", "brightpath.io", "kestrelworks.co", "orbitalfoods.com", "quantaleap.xyz",
             "tessellate.dev", "harborline.ca", "medisync.health", "blueharbor-capital.com", "fernhill.co.uk",
             "studio-lumen.design", "novacrate.shop"]
UNIS = ["uni-lindenberg.de", "student.midlands.ac.uk", "nordhaven.edu", "etu.univ-rivage.fr", "mail.tokaido-u.ac.jp",
        "alumni.pacificridge.edu", "stud.polytech-vistula.pl", "students.unisul-norte.br"]
DISP = ["qx7vm2.xyz", "zk93lp.top", "m0rb-t4.icu", "vv8-kd21.click", "a1b9x.work", "tqj4-9z.site", "rn3k0.online",
        "ztm-77q.xyz", "hh2x9d.top", "l0wk3y-m.icu", "7pz4q.cyou", "bxn-4412.buzz", "kj8w2.rest", "f4tt-n0.fun",
        "u9r1e.monster", "wq-2k8d.shop", "c3dmx.cfd", "yy0-p5.sbs", "e7gq-ls.xyz", "n0v4k.top", "tmpbox-44.icu",
        "quickdrop-9.click", "inboxlane.xyz", "mailnest-7.top", "throwbin.site", "fastpost-x2.online",
        "dropmail-k9.work", "burnermx.cyou", "nomail-4u.buzz", "ephem-ail.icu", "oneuse-mx.xyz", "pocketmx9.top",
        "zerobox-41.site", "tempo-rx.click", "snapinbox-8.online", "vanish-q3.cfd", "kwik-mailr.sbs",
        "instamx-77.fun", "hidemy-in.rest", "lz9qv.work"]
BIG = ["gmail", "outlook", "yahoo", "hotmail", "icloud", "protonmail", "live", "aol"]
CONF = {"o": ["о", "ο", "0"], "a": ["а", "α"], "e": ["е"], "l": ["1", "ӏ"], "i": ["і", "1", "ι"], "m": ["rn"],
        "g": ["ɡ", "q"], "c": ["с"], "p": ["р"], "y": ["у"], "h": ["һ"], "u": ["υ"]}
HOMO = {"a": "а", "e": "е", "o": "о", "p": "р", "c": "с", "x": "х", "i": "і", "y": "у", "s": "ѕ", "j": "ј"}
HOMO_GR = {"o": "ο", "a": "α", "v": "ν", "i": "ι", "k": "κ", "t": "τ"}
TRANSLIT = {"ł": "l", "ø": "o", "þ": "th", "ð": "d", "ı": "i", "ß": "ss", "æ": "ae", "å": "a"}
PFX = ([("+1%d" % d, 9) for d in range(2, 10)] + [("+447", 9), ("+9725", 8), ("+4915", 9), ("+4916", 9), ("+4917", 9),
       ("+919", 9), ("+336", 8), ("+55119", 8), ("+79", 9), ("+8210", 8), ("+8613", 9), ("+346", 8), ("+614", 8),
       ("+316", 8)])
OS = {"win": "Windows NT 10.0; Win64; x64", "mac": "Macintosh; Intel Mac OS X 10_15_7", "lin": "X11; Linux x86_64",
      "cros": "X11; CrOS x86_64 14541.0.0"}

# per-tactic option menus: ip, email, device, timing, phone behaviour
CFG = {
    "proxy_rotate": dict(ip=["resi", "resi6"], em=["plain", "plain", "mixed"], dev=["churn", "churn", "pool"], tm=["bot", "jitter"], ph=["none", "none", "uniq"], n=(8, 30)),
    "lookalike_domain": dict(ip=["resi", "dc", "resi6"], em=["lookalike"], dev=["churn", "empty", "pool"], tm=["jitter", "bot", "burst"], ph=["none"], n=(5, 18)),
    "unicode_local": dict(ip=["resi", "resi", "home"], em=["unicode"], dev=["churn", "pool"], tm=["jitter", "bot"], ph=["none", "uniq"], n=(4, 15)),
    "plus_dot": dict(ip=["home", "resi", "dc"], em=["plusdot"], dev=["reuse", "churn", "pool"], tm=["jitter", "drip", "burst"], ph=["none", "reuse"], n=(4, 14)),
    "disposable": dict(ip=["resi", "dc", "vpn"], em=["disposable"], dev=["churn", "empty", "reuse"], tm=["bot", "jitter", "burst"], ph=["none"], n=(6, 25)),
    "phone_card_reuse": dict(ip=["resi", "home", "vpn"], em=["plain", "mixed", "disposable"], dev=["churn", "pool"], tm=["jitter", "bot"], ph=["reuse", "block", "reuse"], n=(3, 10)),
    "device_reuse": dict(ip=["home", "resi", "resi6"], em=["plain", "mixed", "disposable"], dev=["reuse", "reuse", "pool"], tm=["jitter", "burst"], ph=["none", "uniq"], n=(3, 12)),
    "slow_drip": dict(ip=["resi", "home", "resi6"], em=["plain", "plusdot", "mixed"], dev=["churn", "reuse", "pool"], tm=["drip"], ph=["none", "uniq"], n=(6, 14)),
    "burst": dict(ip=["resi", "dc", "single"], em=["plain", "disposable", "mixed", "lookalike"], dev=["churn", "empty", "pool"], tm=["burst"], ph=["none"], n=(10, 40)),
    "vpn_shared": dict(ip=["vpn"], em=["plain", "mixed", "disposable"], dev=["churn", "pool"], tm=["jitter", "bot"], ph=["none", "uniq"], n=(3, 12)),
}

# ---------------------------------------------------------------- primitives
def hexid(r, n=32):
    return "%0*x" % (n, r.getrandbits(4 * n))

def digits(r, n):
    return "".join(str(r.randrange(10)) for _ in range(n))

def mkphone(r, pfx=None):
    p, n = pfx or r.choice(PFX)
    return p + digits(r, n)

def v4(r):
    return "%d.%d.%d.%d" % (r.choice(PUB_A), r.randrange(256), r.randrange(256), r.randint(1, 254))

def v6p32(r):
    return "%s:%x" % (r.choice(["2001", "2a01", "2a02", "2600", "2601", "2603", "2804", "2a00", "2400", "2405"]),
                      r.randrange(1, 65536))

def v6(r, pre):
    n = pre.count(":") + 1
    return pre + ":" + ":".join("%x" % r.randrange(1, 65536) for _ in range(8 - n))

def anyip(r):
    return v6(r, v6p32(r)) if r.random() < .2 else v4(r)

def chrome_ua(os_, v, edge=False):
    return ("Mozilla/5.0 (%s) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/%d.0.0.0 Safari/537.36%s"
            % (OS[os_], v, " Edg/%d.0.0.0" % v if edge else ""))

def ua(r, kind=None):
    kind = kind or ("d" if r.random() < .62 else "m")
    if kind == "m":
        c = r.randrange(5)
        v = r.randint(126, 146)
        if c <= 1:
            a, b = r.choice([17, 18]), r.randint(0, 6)
            return ("Mozilla/5.0 (iPhone; CPU iPhone OS %d_%d like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
                    "Version/%d.%d Mobile/15E148 Safari/604.1" % (a, b, a, b))
        model = r.choice(["SM-S918B", "Pixel 8", "SM-A546B", "M2101K6G", "Pixel 7a", "CPH2451"])
        sb = " SamsungBrowser/25.0" if c == 4 else ""
        return ("Mozilla/5.0 (Linux; Android %d; %s) AppleWebKit/537.36 (KHTML, like Gecko)%s Chrome/%d.0.0.0 "
                "Mobile Safari/537.36" % (r.randint(12, 15), model, sb, v))
    c = r.choices(["chrome", "edge", "ff", "safari"], [55, 12, 13, 20])[0]
    if c == "chrome":
        return chrome_ua(r.choice(["win", "win", "mac", "lin"]), r.randint(126, 146))
    if c == "edge":
        return chrome_ua("win", r.randint(126, 146), True)
    if c == "ff":
        v = r.randint(125, 142)
        return "Mozilla/5.0 (%s; rv:%d.0) Gecko/20100101 Firefox/%d.0" % (
            r.choice(["Windows NT 10.0; Win64; x64", "Macintosh; Intel Mac OS X 10.15", "X11; Linux x86_64"]), v, v)
    return ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) "
            "Version/%d.%d Safari/605.1.15" % (r.choice([17, 18]), r.randint(0, 6)))

def htime(r, day=None):
    d = r.randrange(14) if day is None else day
    return T0 + d * DAY + r.choices(range(24), HOURW)[0] * 3600 + r.randrange(3600)

def ascii_(s):
    s = "".join(TRANSLIT.get(ch, ch) for ch in s.lower())
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return "".join(ch for ch in s if ch.isalnum() or ch in ".-")

def person(r, intl=False):
    return r.choice(INTL) if intl else (r.choice(FIRST), r.choice(LAST))

def real_local(r, f, l):
    f, l = ascii_(f), ascii_(l)
    return [f + "." + l, f + l, f[0] + l, f + "_" + l, "%s%s%d" % (f, l, r.randrange(100)), l + "." + f,
            "%s%d" % (f, r.randrange(1970, 2012)), "%s.%s%d" % (f, l[0], r.randrange(10, 99)),
            "%s%d" % (f, r.randrange(1000))][r.randrange(9)]

def rand_loc(r):
    return "".join(r.choice(ALNUM) for _ in range(r.randint(7, 12)))

def lookalike(r):
    name = r.choice(BIG)
    idx = [i for i, ch in enumerate(name) if ch in CONF]
    out = list(name)
    for i in r.sample(idx, min(len(idx), r.randint(1, 2))):
        out[i] = r.choice(CONF[name[i]])
    return "".join(out) + r.choice([".com"] * 8 + [".co", ".net"])

def homoglyph(r, s):
    mp = HOMO if r.random() < .7 else HOMO_GR
    out = [mp[ch] if ch in mp and r.random() < .5 else ch for ch in s]
    if r.random() < .25:  # combining acute accent on a random letter
        i = r.randrange(len(out))
        out[i] += "́"
    res = "".join(out)
    return res if res != s else s + mp.get("a", "α")

def dotted(r, b):
    cuts = sorted(r.sample(range(1, len(b)), min(len(b) - 1, r.randint(1, 3))), reverse=True)
    for c in cuts:
        b = b[:c] + "." + b[c:]
    return b

class G:
    def __init__(s, r):
        s.r, s.rows, s.used, s.ctr, s.vpn, s.spike = r, [], set(), collections.Counter(), [], 3

    def aid(s, kind):
        s.ctr[kind] += 1
        return "%s_%04d" % (kind, s.ctr[kind])

    def uemail(s, loc, dom):
        e = loc + "@" + dom
        while e in s.used:
            loc += str(s.r.randrange(10))
            e = loc + "@" + dom
        s.used.add(e)
        return e

    def add(s, ts, email, ip, dev, agent, ph, cd, label, tactic, actor):
        s.rows.append(dict(ts=min(max(int(ts), T0), T0 + WINDOW - 1), email=email, ip=ip, device_id=dev,
                           user_agent=agent, phone=ph, card=cd, label=label, tactic=tactic, actor=actor))

def honest(g, tactic, ts, ip, agent=None, dev=None, fl=None, dom=None, loc=None, actor=None, ph=.35, cd=.12,
           phone=None, card=None):
    r = g.r
    f, l = fl or person(r)
    email = g.uemail(loc or real_local(r, f, l), dom or r.choice(PROVIDERS))
    if dev is None:
        dev = hexid(r) if r.random() < .92 else ""
    if phone is None:
        phone = mkphone(r) if r.random() < ph else ""
    if card is None:
        card = "fp_" + hexid(r, 16) if r.random() < cd else ""
    g.add(ts, email, ip, dev, agent or ua(r), phone, card, "real", tactic, actor or g.aid("real"))

# ---------------------------------------------------------------- honest groups
def school(g, k):
    r = g.r
    ip, st = v4(r), T0 + r.choice(WEEKDAYS) * DAY + r.randrange(8, 13) * 3600 + r.randrange(3600)
    base = chrome_ua(r.choice(["win", "cros"]), r.randint(128, 146))
    for _ in range(k):
        dom = r.choice(["gmail.com"] * 14 + ["outlook.com"] * 3 + ["icloud.com"] * 2 + ["yahoo.com"])
        honest(g, "school", st + r.randrange(20 * 60), ip, base if r.random() < .88 else ua(r, "d"),
               dev=hexid(r), dom=dom, ph=.08, cd=0)

def cgnat(g, k):
    r = g.r
    ip, d, pf = v4(r), r.randrange(14), r.choice(PFX)
    for _ in range(k):
        honest(g, "cgnat", htime(r, d), ip, ua(r, "m"), dev=hexid(r) if r.random() < .85 else "",
               phone=mkphone(r, pf) if r.random() < .6 else "")
    pres = [v6p32(r) + ":%x:%x" % (r.randrange(65536), r.randrange(65536)) for _ in range(3)]
    for i in range(max(2, k // 3)):
        honest(g, "cgnat_v6", htime(r, d), v6(r, pres[i % 3]), ua(r, "m"), dev=hexid(r) if r.random() < .85 else "")

def vpn_users(g, k):
    r = g.r
    for _ in range(k):
        ip = g.vpn[min(int(r.expovariate(.2)), len(g.vpn) - 1)]
        honest(g, "vpn_user", htime(r), ip, ua(r, "d" if r.random() < .8 else "m"))

def churn(g, k):
    r = g.r
    for _ in range(k):
        honest(g, "device_churn", htime(r), anyip(r), dev="" if r.random() < .5 else hexid(r), cd=.2)

def families(g, k):
    r = g.r
    for _ in range(k):
        ip = v4(r) if r.random() < .8 else v6(r, v6p32(r) + ":%x:%x" % (r.randrange(65536), r.randrange(65536)))
        dev, agent, sur = hexid(r), ua(r), r.choice(LAST)
        card = "fp_" + hexid(r, 16) if r.random() < .3 else ""
        famdom = r.choice(PROVIDERS) if r.random() < .5 else None
        t = htime(r, r.randrange(8))
        for j in range(r.randint(2, 4)):
            t += r.choice([r.randrange(60, 600), r.randrange(3600, 3 * DAY)])
            honest(g, "family", t, ip, agent, dev=dev, fl=(r.choice(FIRST), sur), dom=famdom, ph=.4,
                   card=card if j < 2 else "")

def intl(g, k):
    r = g.r
    for _ in range(k):
        f, l = r.choice(INTL)
        loc = dom = None
        if r.random() < .12:  # genuinely internationalised mailbox: accents kept, IDN domain
            loc, dom = (f + "." + l).lower().replace(" ", "-"), r.choice(IDN)
        else:
            dom = r.choice(INTL_PROV)
        honest(g, "intl", htime(r), anyip(r), ua(r), fl=(f, l), loc=loc, dom=dom)

def companies(g, k):
    r = g.r
    for dom in r.sample(COMPANIES, min(k, len(COMPANIES))):
        office, d, shared = v4(r), r.choice(WEEKDAYS), r.random() < .7
        agent = chrome_ua("win", r.randint(130, 146), edge=r.random() < .4)
        for _ in range(r.randint(3, 8)):
            f, l = person(r)
            ts = T0 + d * DAY + r.randrange(9, 18) * 3600 + r.randrange(3600) + r.choice([0, 0, DAY])
            honest(g, "company", ts, office if shared else v4(r), agent if r.random() < .8 else ua(r),
                   dev=hexid(r), loc=ascii_(r.choice([f + "." + l, f[0] + l, f])), dom=dom, ph=.15)

def universities(g, k):
    r = g.r
    for dom in r.sample(UNIS, min(k, len(UNIS))):
        campus, st = v4(r), htime(r, r.choice(WEEKDAYS))
        for _ in range(r.randint(4, 10)):
            honest(g, "university", st + r.randrange(2 * 3600), campus if r.random() < .85 else anyip(r),
                   ua(r, "d" if r.random() < .75 else "m"), dom=dom, ph=.1)

def spike(g, k):
    r = g.r
    t0 = T0 + g.spike * DAY + 15 * 3600
    for _ in range(k):
        ip = r.choice(g.vpn) if r.random() < .05 else anyip(r)
        honest(g, "launch_spike", t0 + min(int(r.expovariate(1 / 2400)), 6 * 3600), ip)

def work_personal(g, k):
    r = g.r
    for _ in range(k):
        fl, act = person(r, r.random() < .3), g.aid("real")
        ip, dev, agent = anyip(r), hexid(r), ua(r, "d")
        phone = mkphone(r) if r.random() < .5 else ""
        card = "fp_" + hexid(r, 16) if r.random() < .2 else ""
        t1 = htime(r)
        t2 = t1 + r.choice([r.randrange(120, 3600), r.randrange(3600, 5 * DAY)])
        for ts, dom in ((t1, r.choice(COMPANIES)), (t2, r.choice(PROVIDERS))):
            honest(g, "work_personal", ts, ip if r.random() < .65 else anyip(r), agent if r.random() < .8 else ua(r),
                   dev=dev if r.random() < .75 else hexid(r), fl=fl, dom=dom, actor=act,
                   phone=phone if r.random() < .6 else "", card=card if r.random() < .5 else "")

def organic(g, k):
    for _ in range(k):
        honest(g, "organic", htime(g.r), anyip(g.r))

# ---------------------------------------------------------------- farmers
def ipgen(g, kind):
    r = g.r
    if kind == "resi":
        pre, seen = ["%d.%d" % (r.choice(PUB_A), r.randrange(256)) for _ in range(r.randint(2, 7))], set()

        def f():
            for _ in range(50):
                p = r.choice(pre) if r.random() < .85 else "%d.%d" % (r.choice(PUB_A), r.randrange(256))
                c = r.randrange(256)
                if (p, c) not in seen:
                    break
            seen.add((p, c))
            return "%s.%d.%d" % (p, c, r.randint(1, 254))
        return f
    if kind == "resi6":
        pre = [v6p32(r) for _ in range(r.randint(1, 3))]
        return lambda: v4(r) if r.random() < .1 else v6(r, r.choice(pre))
    pool = {"dc": lambda: [v4(r) for _ in range(r.randint(2, 6))], "home": lambda: [v4(r) for _ in range(r.randint(1, 2))],
            "single": lambda: [v4(r)], "vpn": lambda: r.sample(g.vpn, r.randint(3, min(8, len(g.vpn))))}[kind]()
    return lambda: r.choice(pool)

def mkemail(g, kind):
    r = g.r
    style, (f, l) = r.choice(["nd", "rnd", "seq", "ini"]), person(r)
    lad = [lookalike(r) for _ in range(r.randint(1, 3))]
    dd = r.sample(DISP, r.randint(3, 8))
    fixdom = r.choice(PROVIDERS) if r.random() < .5 else None
    bases = ["%s%s%d" % (f, l, r.randrange(10, 99)) for _ in range(r.randint(1, 2))]

    def loc(i):
        if style == "nd":
            a, b = person(r)
            return "%s%s%d" % (a, b, r.randrange(10, 9999))
        if style == "rnd":
            return rand_loc(r)
        return "%s%s%02d" % (f, l, i + 1) if style == "seq" else "%s%s%d" % (f[0], l, r.randrange(100))

    def plusdot(i):
        b, m = r.choice(bases), r.randrange(3)
        tok = r.choice([str(i), rand_loc(r)[:5], "promo", "ai%d" % r.randrange(99)])
        if m != 1:
            return (dotted(r, b) if m == 2 else b) + "+" + tok
        return dotted(r, b)

    def one(k, i):
        if k == "plain":
            return g.uemail(loc(i), fixdom or r.choice(PROVIDERS))
        if k == "lookalike":
            return g.uemail(loc(i), r.choice(lad))
        if k == "unicode":
            return g.uemail(homoglyph(r, loc(i)), r.choice(PROVIDERS) if r.random() < .6 else r.choice(lad))
        if k == "plusdot":
            return g.uemail(plusdot(i), r.choice(["gmail.com"] * 4 + ["googlemail.com", "outlook.com"]))
        return g.uemail(rand_loc(r) if r.random() < .6 else loc(i), r.choice(dd))
    return lambda i: one(r.choice(["plain", "plusdot", "disposable"]) if kind == "mixed" else kind, i)

def timing(g, kind, n):
    r = g.r
    s0 = T0 + r.randrange(0, WINDOW - 2 * DAY)
    if kind == "bot":
        iv = r.choice([15, 30, 45, 60, 90, 120, 300])
        return [s0 + int(i * iv * (1 + r.uniform(-.03, .03))) for i in range(n)]
    if kind == "jitter":
        m, t, out = r.choice([600, 1800, 3600, 7200]), s0, []
        for _ in range(n):
            t += r.expovariate(1 / m)
            out.append(t)
        return out
    if kind == "drip":
        d0 = r.randrange(max(1, 14 - n))
        return [T0 + (d0 + i) * DAY + r.randrange(6 * 3600, 22 * 3600) for i in range(n)]
    if r.random() < .3:  # burst timed to hide inside the launch spike
        s0 = T0 + g.spike * DAY + 15 * 3600 + r.randrange(3600)
    span = r.randint(300, 1800)
    return [s0 + r.randrange(span) for _ in range(n)]

def farm_actor(g, t, n):
    r, c, act = g.r, CFG[t], g.aid("farm")
    ipk, emk, devk, tmk, phk = (r.choice(c[k]) for k in ("ip", "em", "dev", "tm", "ph"))
    nextip, nextem, times = ipgen(g, ipk), mkemail(g, emk), timing(g, tmk, n)
    uamode = r.choices(["same", "near", "rand"], [5, 3, 2])[0]
    os_, v = r.choice(["win", "mac", "lin"]), r.randint(126, 146)
    dev0, devpool = hexid(r), [hexid(r) for _ in range(r.randint(2, 3))]
    ph_pool, ph_base, cd_pool = [mkphone(r) for _ in range(r.randint(1, 3))], mkphone(r), ["fp_" + hexid(r, 16) for _ in range(r.randint(1, 2))]
    for i in range(n):
        agent = ua(r) if uamode == "rand" else chrome_ua(os_, v + (r.choice([-2, -1, 0, 0, 1, 2]) if uamode == "near" else 0))
        dev = {"churn": lambda: hexid(r), "empty": lambda: "", "reuse": lambda: dev0, "pool": lambda: r.choice(devpool)}[devk]()
        if phk == "uniq":
            ph = mkphone(r) if r.random() < .5 else ""
        elif phk == "reuse":
            ph = r.choice(ph_pool) if r.random() < .75 else ""
        elif phk == "block":
            ph = "+" + str(int(ph_base[1:]) + i) if r.random() < .85 else ""
        else:
            ph = ""
        if t == "phone_card_reuse":
            cd = r.choice(cd_pool) if r.random() < .75 else ""
        else:
            cd = "fp_" + hexid(r, 16) if r.random() < .1 else ""
        g.add(times[i], nextem(i), nextip(), dev, agent, ph, cd, "farmer", t, act)

def farmers(g, total):
    r = g.r
    w = {t: r.uniform(.4, 2.0) for t in CFG}
    made = 0
    for t in CFG:  # every tactic appears at least once
        n = min(r.randint(*CFG[t]["n"]), max(2, total - made))
        farm_actor(g, t, n)
        made += n
    while made < total:
        t = r.choices(list(w), list(w.values()))[0]
        n = max(2, min(r.randint(*CFG[t]["n"]), total - made))
        farm_actor(g, t, n)
        made += n

def generate(seed, n_real=800):
    r = random.Random(seed)
    g = G(r)
    g.vpn = [v4(r) for _ in range(r.randint(18, 30))]
    g.spike = r.randint(2, 6)
    s = min(1.0, n_real / 800.0)

    def sc(x):
        return max(2, round(x * s * r.uniform(.9, 1.1)))
    school(g, 60 if n_real >= 500 else max(8, round(60 * s)))
    cgnat(g, max(2, round(40 * s)))
    vpn_users(g, sc(50))
    churn(g, sc(35))
    families(g, sc(18))
    intl(g, sc(60))
    companies(g, sc(8))
    universities(g, sc(6))
    spike(g, sc(100))
    work_personal(g, sc(30))
    organic(g, max(0, n_real - len(g.rows)))
    farmers(g, round(n_real / 3 * r.uniform(.92, 1.08)))
    return sorted(g.rows, key=lambda x: (x["ts"], x["email"], x["ip"]))

if __name__ == "__main__":
    seed = int(sys.argv[1]) if len(sys.argv) > 1 else 101
    rows = generate(seed)
    print("seed", seed, "rows", len(rows), "actors", len({x["actor"] for x in rows}))
    print("labels", dict(collections.Counter(x["label"] for x in rows)))
    for lab in ("real", "farmer"):
        print(lab, dict(sorted(collections.Counter(x["tactic"] for x in rows if x["label"] == lab).items())))
