#!/usr/bin/env python3
"""Held-out synthetic signup generator for evaluating a signup-abuse detector.

generate(seed, n_real=800) -> list[dict], deterministic per seed, sorted by ts.
Columns: ts, email, ip, device_id, user_agent, phone, card, label, tactic, actor.

Honest rows (~n_real, label "real", tactic "honest_*") are deliberately hard: ordinary
Gmail/Outlook/Yahoo/iCloud/Proton/corporate/university inboxes, a launch spike (25% in the first
6h), an 80-user university /24 with minute-level bursts, mobile CGNAT, shared VPN exits, families
sharing a device (and sometimes a card), work+personal accounts on one device, genuine plus-
addresses (~3%), IPv6, accented addresses, ~30% phone / ~15% card in mixed formats.

Farmers (~25% of all rows, label "farmer", many actors of 3-40 accounts): lazy_alias, disposable,
rotating_proxy, datacenter_bot, device_reuse, card_or_phone_reuse, unicode_trick, and
patient_residential (capped at 7 accounts, one per day; near-indistinguishable from honest).
"""
import argparse
import itertools
import csv
import datetime
import random
import sys

T0 = int(datetime.datetime(2026, 3, 2, tzinfo=datetime.timezone.utc).timestamp())
DAY, WEEK = 86400, 7 * 86400
COLUMNS = ["ts", "email", "ip", "device_id", "user_agent", "phone", "card", "label", "tactic", "actor"]
TACTICS = "lazy_alias disposable rotating_proxy datacenter_bot device_reuse card_or_phone_reuse patient_residential unicode_trick".split()
TACTIC_WEIGHTS = [14, 18, 14, 10, 12, 10, 8, 14]

FIRST = ("james mary wei priya mohammed olga sofia kenji liam emma noah ava lucas mia ethan isabella amir fatima "
         "carlos lucia hiro yuki anna tomas elena daniel sarah david rachel omar aisha jonas freya lars ingrid ravi "
         "anjali chen li jun minji diego camila nikolai katya tariq leila sean aoife kwame zainab").split()
LAST = ("smith johnson garcia nguyen patel kim lee wang chen cohen levi silva santos martin muller schmidt rossi "
        "ivanov petrov kowalski nowak jensen hansen olsen andersson khan ali hassan tanaka sato suzuki park choi "
        "yamamoto fernandez lopez gonzalez brown wilson taylor moore clark walker hall allen young king okafor mensah").split()
NICK = "gamer dragon sunny bluejay pixel coffee ninja panda techie wanderer skater".split()
ACCENTED = [("josé", "garcía"), ("jürgen", "müller"), ("søren", "jørgensen"), ("zoë", "lefèvre"), ("łukasz", "kowalski")]
TAGS = "a b x free promo test cr1 try2 new ai work shop news".split()
PUBLIC = ["gmail.com"] * 46 + ["outlook.com"] * 10 + ["hotmail.com"] * 6 + ["yahoo.com"] * 8 + ["icloud.com"] * 8 + [
    "proton.me"] * 3 + ["protonmail.com"] * 2 + ["aol.com", "gmx.net", "live.com", "fastmail.com", "me.com"]
CORP = ("acmelogistics.com brightpath.io northwind-group.com kestrelbio.com halcyonbank.co pinecrest.org ledgerly.app "
        "orbitworks.dev tidewater.net summitdental.com bluefinmedia.co corvusenergy.com mapleandrye.ca helixlabs.ai "
        "quarry.studio vantagepoint.co.uk cs.berkeley.edu students.tau.ac.il u.nus.edu.sg stu.westbrook.edu").split()
CAMPUS_DOMAINS = ["student.northfield.edu", "cs.northfield.edu", "northfield.edu"]
DISPOSABLE = ("mailinator.com guerrillamail.com 10minutemail.com tempmail.net yopmail.com trashmail.io throwawaymail.com "
              "getnada.com dispostable.com maildrop.cc fakeinbox.com sharklasers.com tempinbox.xyz burnermail.io "
              "mintemail.com spamgourmet.net mailnesia.com moakt.cc tmpmail.org inboxkitten.com emailondeck.com mohmal.in "
              "temp-mail.org 1secmail.net anonbox.xyz zapmail.top quickdrop.email nullbox.site voidpost.click "
              "puffmail.online cinderbox.work fleetingmail.co bouncebin.net oneshotmail.com dumpmail.store").split()
RES_PREFIXES = [tuple(map(int, p.split("."))) for p in "73.162 98.207 24.18 71.56 86.12 92.40 81.100 176.12 5.29 188.24 109.66 82.32 77.127 2.50 31.154 67.180".split()]
DC_PREFIXES = [tuple(map(int, p.split("."))) for p in "3.80 3.236 18.206 34.201 35.196 52.14 13.58 20.185 40.112 104.196 138.68 159.65 167.99 45.33 95.216 65.108".split()]
CARRIER_PREFIXES = [tuple(map(int, p.split("."))) for p in "172.56 174.226 37.228 213.57".split()]
VPN_PREFIXES = [tuple(map(int, p.split("."))) for p in "185.220 193.32 103.231 146.70".split()]
HOMO = {"a": "\u0430", "e": "\u0435", "o": "\u043e", "p": "\u0440", "c": "\u0441", "x": "\u0445", "i": "\u0456"}
ZW = ["\u200b", "\u200c", "\u200d", "\u2060", "\ufeff"]
SUBS = "mail m us secure web inbox post".split()
ANDROID = ["SM-S918B", "SM-A546B", "Pixel 8", "Pixel 7a", "2201123G", "23049PCD8G", "CPH2451", "RMX3511", "V2250",
           "M2102J20SG", "LM-G900", "NE2215", "moto g power (2022)", "TECNO KG5k", "HUAWEI P40"]
HL = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
BOT_UAS = ["python-requests/2.31.0", "curl/8.4.0", "Go-http-client/2.0", "node-fetch/1.0", "aiohttp/3.9.1", "okhttp/4.12.0",
           HL + "HeadlessChrome/131.0.0.0 Safari/537.36", HL + "Chrome/130.0.0.0 Safari/537.36 Playwright"]
HOUR_W = [1, 1, 1, 1, 1, 2, 3, 5, 7, 8, 8, 8, 8, 8, 8, 8, 8, 8, 9, 10, 10, 9, 6, 3]
PHONE_CC = {"1": 10, "44": 10, "972": 9, "91": 10, "49": 10, "55": 11, "61": 9, "33": 9}
WIN = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
MAC = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/"


class Gen:
    def __init__(self, seed):
        self.r = random.Random(seed)
        self.rows, self.used, self.n_actor = [], set(), 0
    def actor(self, kind):
        self.n_actor += 1
        return f"{kind}{self.n_actor:04d}"
    def row(self, ts, email, ip, dev, ua, phone, card, label, tactic, actor):
        ts = max(T0, min(T0 + WEEK - 1, int(ts)))
        self.rows.append(dict(ts=ts, email=email, ip=ip, device_id=dev, user_agent=ua, phone=phone,
                              card=card, label=label, tactic=tactic, actor=actor))
    def uniq(self, email):
        loc, dom = email.rsplit("@", 1)
        while email.lower() in self.used:
            loc += str(self.r.randint(2, 99))
            email = f"{loc}@{dom}"
        self.used.add(email.lower())
        return email
    def hour_secs(self):
        return self.r.choices(range(24), HOUR_W)[0] * 3600 + self.r.randrange(3600)
    def honest_ts(self):
        r = self.r
        if r.random() < 0.28:  # launch spike (campus users are timed separately)
            return T0 + min(int(r.expovariate(1 / 7200)), 6 * 3600 - 1)
        t = T0 + r.randrange(7) * DAY + self.hour_secs()
        return t if t >= T0 + 6 * 3600 else t + 6 * 3600
    def name(self):
        return self.r.choice(FIRST), self.r.choice(LAST)
    def local(self, f, l):
        r = self.r
        yy = r.randint(70, 99) if r.random() < 0.5 else r.randint(0, 12)
        opts = [f"{f}.{l}", f"{f}{l}", f"{f[0]}{l}", f"{f}_{l}", f"{f}.{l}{yy:02d}", f"{f}{r.randint(1, 999)}",
                f"{l}.{f}", f"{f}{l[0]}", f"{f[0]}.{l}", f"{r.choice(NICK)}{r.randint(1, 99)}"]
        return r.choices(opts, [26, 12, 10, 8, 10, 8, 4, 4, 4, 6])[0]
    def email(self, f, l, dom=None, plus=False):
        dom = dom or (self.r.choice(CORP) if self.r.random() < 0.18 else self.r.choice(PUBLIC))
        return self.uniq(f"{self.local(f, l)}{'+' + self.r.choice(TAGS) if plus else ''}@{dom}")
    def dev(self):
        return "%032x" % self.r.getrandbits(128)
    def ua(self, mobile=None):
        r, v = self.r, self.r.randint(128, 146)
        if mobile is None:
            mobile = r.random() < 0.55
        if mobile and r.random() < 0.4:
            m = r.randint(2, 6)
            return (f"Mozilla/5.0 (iPhone; CPU iPhone OS 17_{m} like Mac OS X) AppleWebKit/605.1.15 "
                    f"(KHTML, like Gecko) Version/17.{m} Mobile/15E148 Safari/604.1")
        if mobile:
            return (f"Mozilla/5.0 (Linux; Android {r.randint(12, 15)}; {r.choice(ANDROID)}) AppleWebKit/537.36 (KHTML, "
                    f"like Gecko) Chrome/{v}.0.0.0 Mobile Safari/537.36{' SamsungBrowser/24.0' if r.random() < 0.12 else ''}")
        c = f"Chrome/{v}.0.0.0 Safari/537.36"
        return r.choice([WIN + c, WIN + c + f" Edg/{v}.0.0.0", MAC + "537.36 (KHTML, like Gecko) " + c, HL + c,
                         f"Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:{v}.0) Gecko/20100101 Firefox/{v}.0",
                         MAC + f"605.1.15 (KHTML, like Gecko) Version/17.{r.randint(2, 6)} Safari/605.1.15"])
    def net24(self, prefixes):
        a, b = self.r.choice(prefixes)
        return a, b, self.r.randint(0, 255)
    def ip_in(self, net):
        return f"{net[0]}.{net[1]}.{net[2]}.{self.r.randint(2, 253)}"
    def res_ip(self):
        return self.ip_in(self.net24(RES_PREFIXES))
    def ipv6(self):
        return self.r.choice(["2a02:8109", "2600:1700", "2001:4c4c"]) + "".join(f":{self.r.getrandbits(16):x}" for _ in range(6))
    def canon_phone(self):
        r = self.r
        cc = r.choices(list(PHONE_CC), [30, 12, 10, 14, 10, 8, 4, 4])[0]
        return cc, str(r.randint(2, 9)) + "".join(r.choice("0123456789") for _ in range(PHONE_CC[cc] - 1))
    def fmt_phone(self, c):
        (cc, n), s = c, self.r.randrange(5)
        return [f"+{cc}{n}", f"+{cc} {n[:3]} {n[3:6]} {n[6:]}", f"+{cc}-{n[:3]}-{n[3:6]}-{n[6:]}",
                f"({n[:3]}) {n[3:6]}-{n[6:]}" if cc == "1" else f"0{n[:2]} {n[2:5]} {n[5:]}", f"00{cc} {n[:3]} {n[3:]}"][s]
    def canon_card(self):
        return self.r.choice("45") + "".join(self.r.choice("0123456789") for _ in range(15))
    def fmt_card(self, d):
        g = [d[i:i + 4] for i in range(0, 16, 4)]
        return self.r.choice([d, " ".join(g), "-".join(g)])
    def contact(self, p_phone=0.30, p_card=0.15):
        r = self.r
        return (self.fmt_phone(self.canon_phone()) if r.random() < p_phone else "",
                self.fmt_card(self.canon_card()) if r.random() < p_card else "")
    def person(self, ts, ip, tactic, mobile=None, dom=None, dev=None, ua=None, **kw):
        f, l = kw.pop("name", None) or self.name()
        ph, cd = self.contact()
        self.row(ts, self.email(f, l, dom, kw.get("plus", False)), ip, dev or self.dev(), ua or self.ua(mobile), ph, cd,
                 "real", tactic, self.actor("h"))

    def honest(self, n):
        r = self.r
        n_campus, n_cg, n_vpn = min(80, round(n * 0.10)), round(n * 0.12), round(n * 0.03)
        net, base = self.net24([(143, 215)]), None  # university /24: bursts within minutes over a few days
        for _ in range(n_campus):
            if base is None or r.random() < 0.2:
                base = T0 + r.randint(1, 4) * DAY + r.randint(9, 21) * 3600 + r.randrange(3600)
            ts = base + (r.randrange(420) if r.random() < 0.65 else r.randrange(2 * DAY))
            self.person(ts, self.ip_in(net), "honest_campus", r.random() < 0.4,
                        r.choice(CAMPUS_DOMAINS) if r.random() < 0.75 else "gmail.com")
        for cnt, pfx, tactic, mob, doms in [(n_cg, CARRIER_PREFIXES, "honest_cgnat", True, ["gmail.com"] * 2 + ["yahoo.com", "icloud.com"]),
                                            (n_vpn, VPN_PREFIXES, "honest_vpn", False, [None])]:
            shared = [self.ip_in(self.net24(pfx)) for _ in range(4 if mob else 6)]  # many users, few IPv4s
            for _ in range(cnt):
                self.person(self.honest_ts(), r.choice(shared), tactic, mob, r.choice(doms))
        fam_rows = 0  # families/roommates: 2-3 humans, one device, one home IP, sometimes one card
        while fam_rows < round(n * 0.03):
            size, surname, dev, ua, ip = r.choice([2, 3]), r.choice(LAST), self.dev(), self.ua(), self.res_ip()
            card = self.fmt_card(self.canon_card()) if r.random() < 0.4 else ""
            for _ in range(size):
                ph, cd = self.contact(0.3, 0)
                self.row(self.honest_ts(), self.email(r.choice(FIRST), surname), ip, dev, ua, ph, card or cd, "real",
                         "honest_family", self.actor("h"))
            fam_rows += size
        for _ in range(max(3, round(n * 0.01))):  # work vs personal inboxes on one device
            f, l = self.name()
            dev, ua, a, home, t1 = self.dev(), self.ua(False), self.actor("h"), self.res_ip(), self.honest_ts()
            ph, cd = self.contact(0.5, 0.2)
            for k in range(2):
                ip = home if (k == 1 or r.random() < 0.5) else self.res_ip()
                self.row(t1 + k * r.randint(120, 2 * DAY), self.email(f, l, r.choice(CORP) if k == 0 else "gmail.com"),
                         ip, dev, ua, ph if r.random() < 0.6 else "", cd if k == 0 else "", "real",
                         "honest_work_personal", a)
        for _ in range(max(0, n - len(self.rows))):  # everyone else
            u, ts = r.random(), self.honest_ts()
            tactic = "honest_launch" if ts < T0 + 6 * 3600 else "honest_regular"
            ip, name, dom, plus = self.res_ip(), None, None, False
            if u < 0.035:
                tactic, dom, plus = "honest_plus", ("gmail.com" if r.random() < 0.8 else "icloud.com"), True
            elif u < 0.05:
                tactic, name, dom = "honest_intl", r.choice(ACCENTED), r.choice(CORP + ["gmx.de", "gmail.com"])
            elif u < 0.11:
                tactic, ip = "honest_ipv6", self.ipv6()
            self.person(ts, ip, tactic, dom=dom, plus=plus, name=name)

    def farm_times(self, tactic, n):
        r = self.r
        if tactic == "patient_residential":
            d0 = r.randrange(WEEK // DAY - n + 1)
            return [T0 + (d0 + i) * DAY + self.hour_secs() for i in range(n)]
        lo, hi = {"lazy_alias": (20, 600), "disposable": (5, 180), "rotating_proxy": (30, 1800),
                  "datacenter_bot": (1, 6), "device_reuse": (600, 3 * 3600),
                  "card_or_phone_reuse": (900, 2 * 3600), "unicode_trick": (20, 900)}[tactic]
        gaps = [r.randint(lo, hi) for _ in range(n - 1)]
        span = sum(gaps)
        start = T0 + (r.randrange(min(6 * 3600, WEEK - span)) if r.random() < 0.3 else r.randrange(WEEK - span))
        return list(itertools.accumulate(gaps, initial=start))

    def tricky(self, f, l, dom):
        r = self.r
        mode, loc = r.choice(["homoglyph", "fullwidth", "zerowidth", "subdomain"]), \
            f"{f}.{l}{r.randint(1, 99)}"
        if mode == "homoglyph":
            loc = "".join(HOMO.get(c, c) if r.random() < 0.6 else c for c in loc)
        elif mode == "fullwidth":
            loc = "".join(chr(ord(c) - 97 + 0xFF41) if c.isalpha() and r.random() < 0.7 else c for c in loc)
        elif mode == "zerowidth":
            for _ in range(r.randint(1, 3)):
                p = r.randrange(1, len(loc))
                loc = loc[:p] + r.choice(ZW) + loc[p:]
        else:
            dom = f"{r.choice(SUBS)}.{dom}"
        return self.uniq(f"{loc}@{dom}")

    def farm_actor(self, tactic, n):
        r = self.r
        actor, (f, l) = self.actor("f"), self.name()
        times = self.farm_times(tactic, n)
        ua, dev, home = self.ua(), self.dev(), self.net24(RES_PREFIXES)
        fixed_ip, mode, c_phone, c_card = self.ip_in(home), r.choice(["card", "phone", "both"]), self.canon_phone(), self.canon_card()
        nets, dcs = [self.net24(RES_PREFIXES) for _ in range(r.randint(2, 4))], [self.net24(DC_PREFIXES) for _ in range(r.randint(1, 3))]
        p_reuse, sticky = r.choice([0.3, 0.6, 0.9]), r.sample(DISPOSABLE, 2)
        bot_ua, bot_dev = r.choice(BOT_UAS), r.choice(["same", "random", "none"])
        base, fam = f"{f}{l}{r.randint(1, 999)}", r.choice(["gmail.com", "outlook.com", "yahoo.com", "hotmail.com", "icloud.com"])
        lazy_dom = "gmail.com" if r.random() < 0.75 else r.choice(["outlook.com", "yahoo.com", "proton.me"])
        rand_loc = lambda k: "".join(r.choices("abcdefghijklmnopqrstuvwxyz0123456789", k=k))
        for ts in times:
            ip, d, u, ph, cd = self.res_ip(), self.dev(), ua, "", ""
            if tactic == "lazy_alias":
                loc = "".join(c + ("." if r.random() < 0.35 else "") for c in base).rstrip(".") if lazy_dom == "gmail.com" and r.random() < 0.5 else f"{base}+{r.choice(TAGS)}{r.randint(0, 99)}"
                em, ip, d = self.uniq(f"{loc}@{lazy_dom}"), fixed_ip, dev
            elif tactic == "disposable":
                nm = f"{r.choice(FIRST)}{r.randint(1, 9999)}" if r.random() < 0.5 else rand_loc(r.randint(6, 11))
                em = self.uniq(f"{nm}@{r.choice(sticky) if r.random() < 0.5 else r.choice(DISPOSABLE)}")
                ip, d = (fixed_ip if r.random() < 0.5 else ip), (dev if r.random() < 0.6 else d)
            elif tactic == "rotating_proxy":
                em, ip = self.email(*self.name(), fam), self.ip_in(r.choice(nets))
                d, u = (dev if r.random() < p_reuse else d), (ua if r.random() < 0.7 else self.ua())
            elif tactic == "datacenter_bot":
                em = self.uniq(f"{rand_loc(r.randint(8, 12))}@{fam if r.random() < 0.7 else 'mail-' + base[:5] + '.xyz'}")
                ip, u, d = self.ip_in(r.choice(dcs)), bot_ua, {"same": dev, "random": d, "none": ""}[bot_dev]
            elif tactic == "device_reuse":
                em, d, u = self.email(*self.name()), dev, (ua if r.random() < 0.7 else self.ua())
            elif tactic == "card_or_phone_reuse":
                em = self.email(*self.name())
                ph = self.fmt_phone(c_phone) if mode in ("phone", "both") else ""
                cd = self.fmt_card(c_card) if mode in ("card", "both") else ""
            elif tactic == "patient_residential":
                em, u = self.email(*self.name()), self.ua()
            else:  # unicode_trick
                em, ip, d = self.tricky(f, l, fam), self.ip_in(r.choice(nets)), (dev if r.random() < 0.5 else d)
            if tactic != "card_or_phone_reuse":
                ph, cd = self.contact(0.15, 0.08)
            self.row(ts, em, ip, d, u, ph, cd, "farmer", tactic, actor)
        return len(times)
    def farmers(self, target):
        r, order, made = self.r, TACTICS[:], 0
        r.shuffle(order)
        while made < target:
            t = order.pop() if order else r.choices(TACTICS, TACTIC_WEIGHTS)[0]
            n = max(3, min(40, 3 + int(r.expovariate(1 / 9)), target - made))
            made += self.farm_actor(t, min(n, 7) if t == "patient_residential" else n)


def generate(seed: int, n_real: int = 800) -> list:
    g = Gen(seed)
    g.honest(n_real)
    g.farmers(round(n_real / 3))
    g.rows.sort(key=lambda x: x["ts"])
    return g.rows


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--n-real", type=int, default=800)
    ap.add_argument("--out", default="-")
    a = ap.parse_args()
    rows = generate(a.seed, a.n_real)
    with (sys.stdout if a.out == "-" else open(a.out, "w", newline="", encoding="utf-8")) as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)


if __name__ == "__main__":
    main()
