"""Look-alike email domains: gmаil.com (Cyrillic a), gmial.com, outl00k.com, xn-- punycode forms of those.

Pure functions, stdlib only, no network. Never raises on any input type or size: bad input means "no look-alike".

A domain is reported only when it is NOT itself a well-known/real domain and either
  1. its skeleton equals the skeleton of a well-known provider (confusable letters from other scripts, plus the
     phishing swaps 0->o, 1->l, rn->m, vv->w, cl->d), or
  2. its first label is one typo (insert, delete, substitute, adjacent swap) away from the first label of a
     frequently squatted provider, with the same suffix (gmial.com, gmaill.com, outlok.com).
Short or dictionary-word provider labels (me, msn, aol, naver, orange...) are only matched by rule 1, and a list of
real near-neighbours (mail.com, email.com, cloud.com, line.me, gmx.at...) is never flagged.
"""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from importlib import resources
from typing import Dict, Optional, Tuple

from . import signals
from .signals import Signal, _clean_text, _MAX_RAW_EMAIL

_DATA_FILE = "confusables.txt"
_MAX_INPUT = 1024  # characters of a domain we are willing to look at; a real domain is at most 253
_MAX_DOMAIN = 253
_MAX_LABEL = 63

# Used only if data/confusables.txt cannot be read (a broken install): the Cyrillic and Greek letters that matter most.
_FALLBACK_CONFUSABLES = {
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x",
    "ѕ": "s", "і": "i", "ј": "j", "ӏ": "l", "ԁ": "d", "ԛ": "q", "ԝ": "w",
    "α": "a", "ι": "i", "ν": "v", "ο": "o", "ρ": "p", "υ": "u",
    "ı": "i", "ɡ": "g", "ɑ": "a", "օ": "o", "ո": "n",
}
_SKELETON_RE = re.compile(r"^[a-z0-9]{1,3}$")


def _load_confusables() -> Dict[str, str]:
    """Read data/confusables.txt (`<hex code point> <ascii skeleton>` lines, `#` comments). Bad lines are skipped."""
    table: Dict[str, str] = {}
    try:
        text = resources.files("tenkyu").joinpath("data", _DATA_FILE).read_text(encoding="utf-8")
    except Exception:
        return dict(_FALLBACK_CONFUSABLES)
    for line in text.splitlines():
        parts = line.split("#", 1)[0].split()
        if len(parts) < 2:
            continue
        try:
            cp = int(parts[0], 16)
            ch = chr(cp)
        except (ValueError, OverflowError):
            continue
        target = parts[1].lower()
        if cp >= 0x80 and _SKELETON_RE.match(target):
            table[ch] = target
    return table or dict(_FALLBACK_CONFUSABLES)


_CONFUSABLES = _load_confusables()

# Real domains that sit one edit or one swap away from a provider. They are never look-alikes.
_REAL_NEIGHBOURS = frozenset({
    "mail.com", "email.com", "cloud.com", "line.me", "mailbox.org", "mail.org",
    "gmx.at", "gmx.ch", "gmx.us", "gmx.fr", "gmx.es", "gmx.co.uk", "gmx.org", "gmx.eu", "gmx.info", "gmx.biz",
    "gmx.ru", "gmx.li", "gmx.it", "gmx.pl", "gmx.cn", "gmx.com.br", "gmx.com.tr", "gmx.net.au", "gmx.de",
    "live.fr", "live.nl", "live.it", "live.co.uk", "live.com.au", "live.ca", "live.be", "live.se", "live.no",
    "outlook.fr", "outlook.de", "outlook.es", "outlook.it", "outlook.jp", "outlook.co.il",
    "hotmail.fr", "hotmail.de", "hotmail.it", "hotmail.es", "hotmail.co.uk", "hotmail.co.il", "hotmail.nl",
    "icloud.com.cn", "yahoo.com.br", "ymail.com", "rocketmail.com", "proton.me", "pm.me", "protonmail.ch",
})

# Provider labels short enough, or ordinary enough as words, that one-edit neighbours are mostly real companies
# (me -> mo, naver -> never, orange -> range). They are matched by skeleton only.
_EDIT_FORCE = frozenset({"gmail", "yahoo"})
_EDIT_EXCLUDE = frozenset({"orange", "libero", "proton", "walla", "naver", "free", "zoho", "ymail"})
_EDIT_MIN_LABEL = 6

# Non-ASCII wildcard rule: a domain whose ONLY differences from a well-known provider of the same length are non-ASCII
# characters imitates it, whatever those characters are (the confusables table can never be complete). Needs a
# provider label of some length so that two-letter names (qq.com) do not collide with real IDN domains.
_WILDCARD_MIN_LABEL = 4
_WILDCARD_MAX_SHARE_DEN = 3  # at most one third of the letters may be wildcards: an all-Cyrillic name is a real IDN

_SWAP_LETTERS = str.maketrans({"0": "o", "1": "l"})
_SWAP_PAIRS = re.compile(r"vv|rn|cl")
_SWAP_PAIR_MAP = {"vv": "w", "rn": "m", "cl": "d"}
_DOTS = str.maketrans({"。": ".", "｡": ".", "．": ".", "․": "."})


def _decode_label(label: str) -> str:
    """Decode one `xn--` label to Unicode, or return it unchanged when it is not valid punycode."""
    if not label.startswith("xn--") or len(label) > _MAX_LABEL or not label.isascii():
        return label
    raw = label.encode("ascii")
    for codec, data in (("idna", raw), ("punycode", raw[4:])):
        try:
            out = data.decode(codec)
            out.encode("utf-8")  # a lone surrogate would break later hashing or encoding
        except (UnicodeError, ValueError):
            continue
        if out and not out.startswith("xn--"):
            return out
    return label


def _prepare(domain: object) -> Optional[str]:
    """Lowercase, NFKC, invisible characters dropped, IDNA dots unified, `xn--` labels decoded. None if unusable."""
    if not isinstance(domain, str) or len(domain) > _MAX_INPUT:
        return None
    try:
        text = _clean_text(domain).strip().translate(_DOTS).casefold().rstrip(".")
        labels = [_decode_label(lb) for lb in text.split(".")]
        out = unicodedata.normalize("NFKC", ".".join(labels)).casefold()
        out.encode("utf-8")
    except (UnicodeError, ValueError):
        return None
    return out


def _fold(text: str, swaps: bool) -> str:
    """NFD, accents dropped, confusables mapped to ASCII; with `swaps` also 0->o, 1->l, vv->w, rn->m, cl->d."""
    decomposed = unicodedata.normalize("NFD", text)
    mapped = "".join(_CONFUSABLES.get(ch, ch) for ch in decomposed if unicodedata.category(ch) != "Mn")
    if swaps:
        mapped = _SWAP_PAIRS.sub(lambda m: _SWAP_PAIR_MAP[m.group()], mapped.translate(_SWAP_LETTERS))
    return mapped


def skeleton(domain: str) -> str:
    """Canonical form in which look-alike domains of the same name collapse: NFKC, casefold, `xn--` labels decoded,
    confusable letters from Cyrillic/Greek/Armenian/fullwidth and friends mapped to ASCII, then the phishing swaps
    0->o, 1->l, vv->w, rn->m, cl->d. Returns "" for anything that is not a string; input is cut at 1024 characters."""
    if not isinstance(domain, str):
        return ""
    prepared = _prepare(domain[:_MAX_INPUT])
    return _fold(prepared, swaps=True) if prepared else ""


def _edit_one(a: str, b: str) -> bool:
    """True when a and b are exactly one insertion, deletion, substitution or adjacent swap apart."""
    la, lb = len(a), len(b)
    if a == b or abs(la - lb) > 1:
        return False
    if la == lb:
        diff = [i for i in range(la) if a[i] != b[i]]
        if len(diff) == 1:
            return True
        return len(diff) == 2 and diff[1] == diff[0] + 1 and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]]
    short, long_ = (a, b) if la < lb else (b, a)
    i = 0
    while i < len(short) and short[i] == long_[i]:
        i += 1
    return short[i:] == long_[i + 1:]


_INDEX_CACHE: dict = {}


def _target_index(providers) -> Tuple[Dict[str, str], tuple, Dict[int, tuple]]:
    """(skeleton -> provider, ((label, suffix, provider), ...) eligible for the one-typo rule, providers by length for
    the non-ASCII wildcard rule). Cached per set."""
    cached = _INDEX_CACHE.get("v")
    if cached is not None and cached[0] is providers:
        return cached[1]
    by_skeleton: Dict[str, str] = {}
    typo = []
    for target in sorted(providers):
        by_skeleton.setdefault(_fold(target, swaps=True), target)
        label, _, suffix = target.partition(".")
        if suffix and label not in _EDIT_EXCLUDE and (len(label) >= _EDIT_MIN_LABEL or label in _EDIT_FORCE):
            typo.append((label, suffix, target))
    by_len: Dict[int, list] = {}
    for target in sorted(providers):
        if len(target.partition(".")[0]) >= _WILDCARD_MIN_LABEL:
            by_len.setdefault(len(target), []).append(target)
    index = (by_skeleton, tuple(typo), {n: tuple(v) for n, v in by_len.items()})
    _INDEX_CACHE["v"] = (providers, index)
    return index


def lookalike_target(domain: str) -> Optional[str]:
    """The well-known provider `domain` imitates (e.g. "gmail.com" for gmаil.com, gmial.com, gmai1.com), else None.

    Real domains are never flagged: every well-known provider and school domain, any yahoo.* country domain,
    the list of real near-neighbours, and a compatibility spelling of a real domain (fullwidth ｇｍａｉｌ.com
    NFKC-folds to gmail.com itself, which `normalize_email` already collapses)."""
    try:
        return _lookalike_target(domain)
    except Exception:  # defensive: untrusted input must never break sign-up
        return None


def _lookalike_target(domain: object) -> Optional[str]:
    prepared = _prepare(domain)
    if not prepared or len(prepared) > _MAX_DOMAIN or "." not in prepared:
        return None
    if signals.well_known_domain(prepared) or prepared in _REAL_NEIGHBOURS:
        return None
    by_skeleton, typo, by_len = _target_index(signals.WELL_KNOWN_PROVIDERS)
    skel = _fold(prepared, swaps=True)
    if skel != prepared:
        hit = by_skeleton.get(skel)
        if hit is not None:
            # Digit and letter swaps on plain ASCII are only convincing for a provider name with some length
            # (rnsn.com is not msn.com); other scripts standing in for Latin letters are convincing for any.
            if not prepared.isascii() or len(hit.partition(".")[0]) >= 4:
                return hit
        if signals._YAHOO_RE.match(skel):
            return skel
    if not prepared.isascii():
        wild = sum(1 for ch in prepared if not ch.isascii())
        if wild * _WILDCARD_MAX_SHARE_DEN <= (len(prepared) - prepared.count(".")):  # mostly ASCII, as an imitation is
            for target in by_len.get(len(prepared), ()):
                if all(a == b or not a.isascii() for a, b in zip(prepared, target)):
                    return target
    light = _fold(prepared, swaps=False)
    label, _, suffix = light.partition(".")
    for target_label, target_suffix, target in typo:
        if suffix == target_suffix and _edit_one(label, target_label):
            return target
    # The provider's own name under a different or mistyped suffix (gmail.co, gmail.con, hotmail.co) or with the dot
    # moved (gmailc.om). None of these is the provider's mailbox; a real user would never receive mail there.
    swap_targets = ("gmail.com", "outlook.com", "hotmail.com", "icloud.com", "yahoo.com", "protonmail.com")
    flat = light.replace(".", "")
    for target in swap_targets:
        t_label = target.partition(".")[0]
        # only a mistyped ".com": hotmail.fr, outlook.de and live.co.uk are real country domains of these providers
        if label == t_label and suffix and "." not in suffix and suffix != "com" and _edit_one(suffix, "com"):
            return target
        if flat == target.replace(".", "") and light != target and ".." not in light and not light.startswith("."):
            return target
    return None


def _scripts(label: str) -> set:
    return {s for s in (_letter_script(ch) for ch in label if ch.isalpha()) if s}


@lru_cache(maxsize=4096)
def _letter_script(ch: str) -> str:
    name = unicodedata.name(ch, "")
    for script in ("LATIN", "CYRILLIC", "GREEK"):
        if name.startswith(script):
            return script
    return ""


_LOCAL_SPLIT = re.compile(r"[._\-+]+")


def _mixed_script_local(local: str) -> bool:
    """True when one dot/dash/underscore-separated part of the local part mixes Latin with Cyrillic or Greek.
    A name written entirely in one script (иван, γιάννης, ivan) is not mixed."""
    for part in _LOCAL_SPLIT.split(local):
        scripts = _scripts(part)
        if "LATIN" in scripts and len(scripts) > 1:
            return True
    return False


def _raw_parts(raw_email: object) -> Optional[Tuple[str, str]]:
    if not isinstance(raw_email, str) or len(raw_email) > _MAX_RAW_EMAIL:
        return None
    local, at, domain = _clean_text(raw_email).strip().rpartition("@")
    return (local, domain) if at else None


def _norm_parts(norm_email: object) -> Optional[Tuple[str, str]]:
    if not isinstance(norm_email, str) or len(norm_email) > _MAX_RAW_EMAIL:
        return None
    local, at, domain = norm_email.rpartition("@")
    return (local, domain) if at else None


def lookalike_signal(norm_email: Optional[str], raw_email: object) -> Optional[Signal]:
    """`lookalike_domain` (0.75) when the domain of the raw or the normalized address imitates a well-known
    provider; else `mixed_script_email` (0.5) when a name in the local part mixes Latin with Cyrillic or Greek.
    One signal is returned; when both apply the domain one wins. The raw address is checked as well because
    normalization can fold a disguised spelling into something that no longer looks disguised."""
    try:
        parts = [p for p in (_raw_parts(raw_email), _norm_parts(norm_email)) if p]  # raw first
        for _, domain in parts:
            target = lookalike_target(domain)
            if target:
                return Signal("lookalike_domain", 0.75, f"email domain imitates {target}")
        # Gmail normalization drops dots, which would glue two single-script names together: judge the raw local part.
        if _mixed_script_local((parts[0] if parts else ("", ""))[0]):
            return Signal("mixed_script_email", 0.5, "email name mixes Latin and Cyrillic/Greek letters")
    except Exception:  # defensive, as above
        return None
    return None


_PROVIDER_PARENTS = ("gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "icloud.com",
                     "yahoo.com", "aol.com", "proton.me", "protonmail.com")


def obfuscation_signal(raw_email: object) -> Optional[Signal]:
    """An address written in a disguise no person types by hand: invisible characters or fullwidth letters in the
    raw address, or a made-up subdomain of a big provider (inbox.icloud.com). Each lets one inbox look like many."""
    if not isinstance(raw_email, str) or "@" not in raw_email or len(raw_email) > _MAX_RAW_EMAIL:
        return None
    local = raw_email.rsplit("@", 1)[0]
    # A zero-width character wedged between two Latin letters is a disguise; a mark pasted at either end, a soft
    # hyphen from a wrapped line or a Persian ZWNJ between Arabic letters is how honest text arrives.
    for i in range(1, len(local) - 1):
        if local[i] in "\u200b\u200c\u200d\u2060" and local[i - 1].isascii() and local[i - 1].isalpha() \
                and local[i + 1].isascii() and local[i + 1].isalpha():
            return Signal("obfuscated_email", 0.75, "invisible characters hidden inside the address")
    if any(0xFF21 <= ord(ch) <= 0xFF3A or 0xFF41 <= ord(ch) <= 0xFF5A for ch in local):
        return Signal("obfuscated_email", 0.6, "email uses fullwidth letters")
    if any(signals._invisible(ch) or 0xFF01 <= ord(ch) <= 0xFF5E for ch in raw_email):
        return Signal("obfuscated_email", 0.3, "email contains invisible or fullwidth characters")
    domain = raw_email.rsplit("@", 1)[1].strip().lower().rstrip(".")
    for parent in _PROVIDER_PARENTS:
        if domain.endswith("." + parent):
            return Signal("obfuscated_email", 0.6, f"made-up subdomain of {parent}")
    return None
