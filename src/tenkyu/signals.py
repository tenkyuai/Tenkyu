"""Pure signal helpers: each returns a Signal or None. No network, no randomness."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Optional

DISPOSABLE_DOMAINS = frozenset("""
mailinator.com guerrillamail.com guerrillamail.net guerrillamail.org sharklasers.com 10minutemail.com
10minutemail.net tempmail.com temp-mail.org temp-mail.io tempmailo.com throwawaymail.com yopmail.com
yopmail.fr getnada.com nada.email maildrop.cc dispostable.com fakeinbox.com trashmail.com trashmail.net
mintemail.com mohmal.com emailondeck.com burnermail.io spamgourmet.com mailnesia.com moakt.com
tempinbox.com tempr.email discard.email dropmail.me inboxkitten.com mailcatch.com spambox.us
getairmail.com anonbox.net harakirimail.com mytemp.email tmpmail.org tmpmail.net emailfake.com
fakemail.net fakemailgenerator.com minuteinbox.com 1secmail.com 1secmail.net 1secmail.org
byom.de mail.tm mailpoof.com tempail.com luxusmail.org linshiyouxiang.net cs.email
""".split())

# Matched anywhere in the lowercased user agent (long, unambiguous names).
HEADLESS_MARKERS = ("headlesschrome", "python-requests", "curl/", "wget/", "httpclient",
                    "phantomjs", "selenium", "puppeteer", "playwright", "aiohttp", "go-http-client",
                    "okhttp", "scrapy")
# Matched as whole tokens (the user agent split on anything that is not a letter or digit): "curl/8.0", "wget".
HEADLESS_TOKENS = frozenset({"curl", "wget", "bot", "bots"})
# Crawler names end in "bot" (Googlebot, bingbot, AhrefsBot), so a token ending in "bot" counts, except
# honest brands that happen to end that way (CUBOT is an Android phone maker).
_BOT_SUFFIX_EXEMPT = frozenset({"cubot", "abbot", "talbot"})
_UA_TOKEN = re.compile(r"[a-z0-9]+")

GMAIL_DOMAINS = frozenset({"gmail.com", "googlemail.com"})
# Real Yahoo domains only: yahoo.com, yahoo.co.xx, yahoo.com.xx, yahoo.xx (two-letter country code), ymail, rocketmail.
# NOT yahoo.<anything>.<anything>: an attacker can register yahoo.mailfarm.xyz or yahoo.com.evil.net.
_YAHOO_CC = "de|fr|it|es|in|ca|cn|gr|ie|se|dk|no|pl|ro|at|be|ch|nl|pt|fi|cz|hu|bg"
_YAHOO_CO = "uk|jp|in|id|nz|kr|za|th|il"
_YAHOO_COM = "au|br|mx|ar|tw|hk|sg|ph|my|vn|tr|co|pe|ve|cl"
# Only Yahoo's real country domains: a bare yahoo.<cc> wildcard would admit yahoo.co, yahoo.tk and similar.
_YAHOO_RE = re.compile(r"^(yahoo\.(com|(" + _YAHOO_CC + r")|co\.(" + _YAHOO_CO + r")|com\.(" + _YAHOO_COM + r"))"
                       r"|ymail\.com|rocketmail\.com)$")

# Big mailbox providers that make a new address cost real effort (phone or captcha checks). Used to tell a crowd of
# distinct real people behind one IP from a farm, and as the targets look-alike domains imitate.
WELL_KNOWN_PROVIDERS = frozenset({
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "msn.com", "icloud.com", "me.com",
    "mac.com", "yahoo.com", "ymail.com", "aol.com", "proton.me", "protonmail.com", "gmx.com", "gmx.de", "gmx.net",
    "web.de", "mail.ru", "yandex.ru", "yandex.com", "zoho.com", "fastmail.com", "qq.com", "163.com", "naver.com",
    "walla.co.il", "seznam.cz", "wp.pl", "o2.pl", "interia.pl", "onet.pl", "ukr.net", "rediffmail.com",
    "rambler.ru", "bk.ru", "inbox.ru", "list.ru", "hanmail.net", "daum.net", "laposte.net", "sfr.fr", "virgilio.it",
    "tutanota.com", "tuta.io", "btinternet.com", "sky.com", "bigpond.com", "shaw.ca", "rogers.com", "sina.com",
    "126.com", "yeah.net", "nate.com", "abv.bg", "mail.bg", "freenet.de", "bluewin.ch", "telenet.be", "ziggo.nl",
    "orange.fr", "free.fr", "libero.it", "t-online.de", "comcast.net", "att.net", "verizon.net",
})
_EDU_RE = re.compile(r"(^|\.)(edu|ac\.[a-z]{2}|edu\.[a-z]{2}|k12\.[a-z]{2}\.us)$")


def well_known_domain(domain: Optional[str]) -> bool:
    """True for a big mailbox provider (any yahoo.* country domain counts) or a university/school domain."""
    if not domain:
        return False
    return domain in WELL_KNOWN_PROVIDERS or bool(_YAHOO_RE.match(domain)) or bool(_EDU_RE.search(domain))
_FASTMAIL_SUB_RE = re.compile(r"^[^.]+\.(fastmail\.com|fastmail\.fm)$")  # anything@user.fastmail.com -> user@
# Domains that are the same mailbox under another name. Outlook/Hotmail/Live/MSN are deliberately absent.
_DOMAIN_CANON = {
    "googlemail.com": "gmail.com",
    "protonmail.com": "proton.me", "protonmail.ch": "proton.me", "pm.me": "proton.me",
    "me.com": "icloud.com", "mac.com": "icloud.com",
}
_MAX_RAW_EMAIL = 4096  # anything longer cannot become a valid address; do not spend time normalizing it


def _invisible(ch: str) -> bool:
    """Characters that render as nothing and so let one address be written many ways: every Unicode format
    character (category Cf: zero-width space and joiners, bidi marks, soft hyphen U+00AD, word joiner, BOM),
    the combining grapheme joiner U+034F, and variation selectors."""
    cp = ord(ch)
    return (unicodedata.category(ch) == "Cf" or cp == 0x034F or 0xFE00 <= cp <= 0xFE0F or 0x180B <= cp <= 0x180D
            or 0xE0100 <= cp <= 0xE01EF)


def _clean_text(value: str) -> str:
    """NFKC first (fullwidth and other compatibility forms become their plain letters), then drop invisible characters."""
    return "".join(ch for ch in unicodedata.normalize("NFKC", value) if not _invisible(ch))


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass(frozen=True)
class Signal:
    name: str
    score: float  # 0..1 probability-like
    reason: str


def normalize_email(email: object) -> Optional[str]:
    """Canonical form so aliases collapse. None if invalid.

    Every domain: "+tag" stripped. Gmail: dots removed, googlemail.com -> gmail.com. Yahoo: "-keyword" stripped.
    Fastmail: anything@user.fastmail.com (or .fm) -> user@fastmail.com (or .fm), one subdomain label only.
    Proton: protonmail.com / protonmail.ch / pm.me -> proton.me. iCloud: me.com / mac.com -> icloud.com.
    Outlook, Hotmail, Live and MSN are separate mailboxes and stay separate.
    The text is first put through Unicode NFKC and stripped of invisible characters (zero-width, soft hyphen,
    combining grapheme joiner), so fullwidth or soft-hyphened writings of a domain collapse to the plain one.
    Look-alike letters from other scripts (a Cyrillic "а" in gmаil.com) are not mapped: verify email ownership."""
    if not isinstance(email, str):
        return None
    if len(email) > _MAX_RAW_EMAIL:
        return None
    e = _clean_text(email).strip().lower()
    try:
        e.encode("utf-8")  # a lone surrogate cannot be stored or hashed: treat it as malformed
    except UnicodeEncodeError:
        return None
    if len(e) > 254 or not _EMAIL_RE.match(e) or e.count("@") != 1:
        return None
    local, domain = e.split("@")
    domain = domain.rstrip(".")  # "gmail.com." is the same mailbox as "gmail.com"
    if "." not in domain:
        return None
    local = local.split("+", 1)[0]
    if not local:
        return None
    if domain in GMAIL_DOMAINS:
        local = local.replace(".", "")
        domain = "gmail.com"
    elif _YAHOO_RE.match(domain):
        local = local.split("-", 1)[0]  # Yahoo disposable addresses: base-keyword@yahoo.com
    elif _FASTMAIL_SUB_RE.match(domain):
        local, _, domain = domain.partition(".")  # user.fastmail.com delivers to user@fastmail.com
    else:
        domain = _DOMAIN_CANON.get(domain, domain)
    if not local:
        return None
    return f"{local}@{domain}"


def is_plain(email: object, norm: Optional[str]) -> bool:
    """True when the address carries no alias tag: no "+tag", no "-keyword" on Yahoo, and no Fastmail subdomain
    (a@x.fastmail.com). Dots do not count, because people write their own Gmail address with dots
    (maya.cohen@gmail.com). Domain-only canonicalization (pm.me, me.com) does not make an address an alias."""
    if not isinstance(email, str) or norm is None or len(email) > _MAX_RAW_EMAIL:
        return False
    local, _, domain = _clean_text(email).strip().lower().rstrip(".").partition("@")
    return ("+" not in local and not (_YAHOO_RE.match(domain) and "-" in local)
            and not _FASTMAIL_SUB_RE.match(domain))



def host_of(raw: str) -> Optional[str]:
    """The address part of a forwarded entry: drops a port (`1.2.3.4:5678`, `[::1]:443`) and brackets.
    Some proxies (Azure Application Gateway, IIS) append the client port to X-Forwarded-For."""
    raw = raw.strip()
    if raw.startswith("["):
        end = raw.find("]")
        rest = raw[end + 1:] if end > 0 else None
        if rest is None or not (rest == "" or (rest.startswith(":") and rest[1:].isdigit() and len(rest) <= 6)):
            return None
        return raw[1:end]
    if raw.count(":") == 1:  # a bare IPv6 address has two or more colons
        host, _, port = raw.partition(":")
        return host if port.isdigit() and len(port) <= 5 else None
    return raw


_LINK_NAME = re.compile(r"^[a-z][a-z0-9_]{0,23}$")
_LINK_DIGIT_PARTS = ("phone", "mobile", "sms")  # a link name containing one of these is compared by digits only
_LINK_DIGIT_TOKENS = frozenset({"tel", "telephone"})  # ... and so is a name with one of these as an `_` part (not "hotel")
_LINK_SEPARATORS = frozenset("-.()_")
_TRUNK_ZERO = re.compile(r"\(\s*0\s*\)")  # "+44 (0)20 ...": the optional national trunk 0 is not part of the number


def _is_phone_name(name: str) -> bool:
    return any(p in name for p in _LINK_DIGIT_PARTS) or any(t in _LINK_DIGIT_TOKENS for t in name.split("_"))


_TWO_DIGIT_CODES = frozenset("20 27 30 31 32 33 34 36 39 40 41 43 44 45 46 47 48 49 51 52 53 54 55 56 57 58 60 61 62 63 64 65 66 "
                             "81 82 84 86 90 91 92 93 94 95 98".split())


def _split_country(digits: str) -> tuple:
    """(country calling code, national number) of international digits. Calling codes are prefix-free, so the
    first digit (1 and 7 are whole codes) plus a list of the two-digit codes decide; every other code has 3 digits."""
    n = 1 if digits[0] in "17" else 2 if digits[:2] in _TWO_DIGIT_CODES else 3
    return digits[:n], digits[n:]


def normalize_country_code(code: object) -> Optional[str]:
    """"+972" / "972" / 972 -> "972"; None -> None. ValueError for anything that is not a 1 to 3 digit calling code."""
    if code is None:
        return None
    text = str(code).strip().lstrip("+") if isinstance(code, (str, int)) and not isinstance(code, bool) else ""
    if not (text.isascii() and text.isdigit() and 1 <= len(text) <= 3 and text[0] != "0"):
        raise ValueError(f"default_country_code must be a country calling code such as '972' or '+1': {code!r}")
    return text


def _canonical_phone(text: str, default_cc: Optional[str]) -> str:
    """One form for one phone. Digits only (any script) with the "(0)" marker dropped; a leading "+" or "00" means the
    number is international, a single leading 0 means national, and a number with neither is taken as international
    without its "+" (14155550100). An international number is split into country code and national number and the
    trunk 0 some people keep after the code (+9720501234567) is dropped (not for Italy, where it is part of the
    number). A national number loses its trunk 0 and gets `default_cc`. With a default_cc the key is "<cc><national>",
    so only the same country matches; without one it is the national number alone, so national and international
    forms match and the same national number in two countries is one phone (documented on Guard)."""
    text = _TRUNK_ZERO.sub("", text)
    plus = text.lstrip().startswith("+")
    digits = "".join(str(unicodedata.decimal(ch)) for ch in text if unicodedata.category(ch) == "Nd")
    if len(digits) < 7:  # too short to be a phone number with a country code: compared as typed
        return digits
    if not plus and digits.startswith("00") and len(digits) > 2:
        plus, digits = True, digits[2:]
    if plus or not digits.startswith("0"):
        cc, nsn = _split_country(digits)
        if not nsn:  # only a country code: nothing to compare but the digits
            return digits
        if nsn.startswith("0") and cc != "39":
            nsn = nsn.lstrip("0") or nsn
    else:
        cc, nsn = default_cc, digits.lstrip("0") or digits
    return (cc or "") + nsn if default_cc else nsn


def _link_value(name: str, value: str, default_cc: Optional[str] = None) -> str:
    """The form of a link value that is hashed: NFKC, case-folded, without whitespace, invisible characters or
    the separators - . ( ) _. For names containing phone, mobile or sms (or tel as a word) the value is a
    canonical phone (see `_canonical_phone`): "+1 (415) 555-0100" and "14155550100", "+44 (0)20 7946 0958" and
    "0044 20 7946 0958", "+972501234567", "0501234567" and "+9720501234567" each match.
    Falls back to the plain stripped value if nothing is left."""
    text = _clean_text(unicodedata.normalize("NFKC", value).casefold())
    if _is_phone_name(name):
        phone = _canonical_phone(text, default_cc)
        if phone:
            return phone
    out = "".join(ch for ch in text if not ch.isspace() and ch not in _LINK_SEPARATORS)
    return out or value


def link_key(name: object, value: object, secret: Optional[bytes] = None, default_country_code: Optional[str] = None) -> str:
    """Stored form of one custom link: `x:<name>:<32 hex chars>`. The raw value is never stored.
    The value is normalized first (see `_link_value`): case, whitespace, - . ( ) _ and invisible characters do
    not matter, and phone-like names compare by digits only. Keys stored by 0.2.1 for values with spaces,
    capitals or punctuation do not match the 0.2.2 keys.
    Without `secret` the digest is plain SHA-256 of the normalized value.
    With `secret` (bytes, at least 16 bytes) it is HMAC-SHA256(secret, value), so a leaked store cannot be
    checked against guessed values without the secret; keys made with different secrets do not match.
    Raises ValueError for a bad name, a bad value, or a secret that is not bytes or is shorter than 16 bytes."""
    import hashlib
    import hmac
    if secret is not None and (not isinstance(secret, bytes) or len(secret) < 16):
        raise ValueError("link secret must be bytes of at least 16 bytes")
    if not isinstance(name, str) or not _LINK_NAME.match(name):
        raise ValueError(f"link name must match {_LINK_NAME.pattern}: {name!r}")
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError(f"link value must be a string or an integer: {name}={value!r}")
    v = str(value).strip()
    if not 1 <= len(v) <= 256:
        raise ValueError(f"link value must be 1 to 256 characters: {name}")
    data = _link_value(name, v, normalize_country_code(default_country_code)).encode("utf-8", "surrogatepass")
    digest = (hashlib.sha256(data) if secret is None else hmac.new(secret, data, hashlib.sha256)).hexdigest()
    return "x:" + name + ":" + digest[:32]


def email_domain(norm: Optional[str]) -> Optional[str]:
    return norm.split("@", 1)[1] if norm else None


def local_stem(norm: Optional[str]) -> Optional[str]:
    """Local part with digits and separators stripped: john1, john22, j.ohn -> john."""
    if not norm:
        return None
    stem = re.sub(r"[\d._\-]+", "", norm.split("@", 1)[0])
    return stem if len(stem) >= 3 else None


def email_missing(email: object) -> bool:
    """True when no email was supplied at all: None, "" or whitespace only (phone-auth users have none)."""
    return email is None or (isinstance(email, str) and not email.strip())


def invalid_email(norm: Optional[str], email: object = "") -> Optional[Signal]:
    """Only a supplied but malformed email is invalid. A missing one (`email_missing`) gives no signal; pass the
    raw `email` so it can be told apart (without it, a None `norm` counts as malformed)."""
    if norm is None and not email_missing(email):
        return Signal("invalid_email", 1.0, "email address is not valid")
    return None


def _clean_domain(raw: object) -> Optional[str]:
    if not isinstance(raw, str):
        return None
    d = raw.strip().lower().lstrip("@").removeprefix("*.").rstrip(".")
    return d if d and "." in d and " " not in d else None


def load_domains(path) -> frozenset:
    """Read a one-domain-per-line file (blank lines and # comments ignored) into a frozenset."""
    out = set()
    with open(path, encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            line = line.split("#", 1)[0]
            d = _clean_domain(line)
            if d:
                out.add(d)
    return frozenset(out)


def clean_domains(items) -> frozenset:
    return frozenset(d for d in (_clean_domain(x) for x in items) if d)


def _domain_listed(domain: str, domains) -> bool:
    """True when `domain` or any parent domain (a.b.c.com -> b.c.com, c.com, com) is in `domains`."""
    if not isinstance(domains, (set, frozenset)):
        domains = frozenset(domains)
    parts = domain.split(".")
    return any(".".join(parts[i:]) in domains for i in range(len(parts)))


def disposable_email(norm: Optional[str], domains=DISPOSABLE_DOMAINS) -> Optional[Signal]:
    d = email_domain(norm)
    if d and _domain_listed(d, domains):
        return Signal("disposable_email", 0.8, f"disposable email domain ({d})")
    return None


def headless_agent(user_agent: object) -> Optional[Signal]:
    if not isinstance(user_agent, str) or not user_agent.strip():
        return Signal("no_user_agent", 0.25, "no user agent sent")
    ua = user_agent.lower()
    for m in HEADLESS_MARKERS:
        if m in ua:
            return Signal("automation_agent", 0.45, f"automation-style user agent ({m})")
    for tok in _UA_TOKEN.findall(ua):
        if tok in HEADLESS_TOKENS:
            return Signal("automation_agent", 0.45, f"automation-style user agent ({tok})")
        if tok.endswith(("bot", "bots")) and tok not in _BOT_SUFFIX_EXEMPT:
            return Signal("automation_agent", 0.45, "automation-style user agent (bot)")
    return None


def count_signal(name: str, n: int, tiers, what: str) -> Optional[Signal]:
    """tiers: [(min_count, score), ...] checked from highest."""
    for min_n, score in sorted(tiers, reverse=True):
        if n >= min_n:
            return Signal(name, score, f"{n} {what}")
    return None
