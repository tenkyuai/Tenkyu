"""Disposable-domain heuristics that go beyond the bundled list. Stdlib only, no network, no DNS/MX lookups.

Two weak signals, both for a domain that is NOT a well-known provider or a school:

* `odd_domain`: the domain *looks* throwaway from its shape alone (random-looking label, digit/letter soup,
  disposable word patterns such as "tempmail" or "10minute...", hyphen piles, very long labels, a small push
  from cheap TLDs). See `domain_shape_score`.
* `domain_burst`: many distinct addresses signed up from one unknown domain in 24 hours.

IMPORTANT: both are WEAK evidence. Neither may block a sign-up alone. A new real company, a small team, a
student club on a custom domain or a foreign brand can trip either one (a team of ten signing up from
`acme.com` is a legitimate burst). The guard must treat them as soft signals and act only when something else
corroborates them (a bundled-list hit, a velocity signal, an automation user agent, ...). Their scores are
deliberately low (0.3 for `odd_domain`, 0.35 / 0.5 for `domain_burst`) so that, alone, they stay under any
sensible block threshold.

Usage, in this order for each sign-up (normalized address `norm`, `domain = email_domain(norm)`):

    sigs = new_domain_signals(store, now, domain, blind=store.blind)   # BEFORE recording
    record_domain(store, now, domain, norm, blind=store.blind)         # then record
"""
from __future__ import annotations

import math
import re
import unicodedata
from typing import Optional

from . import lookalike
from .signals import Signal, well_known_domain

# --- tunables ---------------------------------------------------------------------------------------------

# Shape score at or above which `odd_domain` fires. Chosen from tests/test_domains.py: every real company,
# brand, government and university domain in the test list scores below 0.35, while most invented disposable
# looking domains score above 0.6. 0.5 leaves a wide margin on the real side.
ODD_DOMAIN_THRESHOLD = 0.5
ODD_DOMAIN_SCORE = 0.3
# A label built from disposable-mail words ("dumpmail", "tempinbox", "voidpost") is stronger evidence than an odd
# shape: a real company almost never names itself that way. Still below the 0.5 that the tests promise stays weak,
# but above the review line of the default Guard (0.35), so such a domain is reviewed even with nothing else wrong.
ODD_DOMAIN_WORDS_THRESHOLD = 0.8
ODD_DOMAIN_WORDS_SCORE = 0.45

BURST_WINDOW = 86400.0
BURST_TIERS = ((10, 0.45), (5, 0.3))  # (distinct addresses already recorded in the window, score)
# A burst starts counting earlier on a domain whose name already looks like a disposable-mail service (a weak
# "quick" + "drop" compound that is too ordinary to review on its own): 3 distinct addresses in 24 hours.
BURST_WORDS_MIN = 0.45
BURST_WORDS_TIER = (3, 0.15)

_MAX_DOMAIN = 253
_MAX_LABEL = 63

# Small weights only: these TLDs are abused a lot but are also used by plenty of honest sites.
_ABUSED_TLDS = frozenset("""xyz top icu click buzz monster cfd sbs rest tk ml ga cf gq pw bid loan win cam men gdn
cyou quest beauty hair skin makeup boats yachts autos homes mom lol uno""".split())
_SOFT_TLDS = frozenset("shop site online store live fun vip work club website space tech link life world today email mail".split())
_MAIL_TLDS = frozenset("email mail inbox".split())
_ABUSED_TLD_WEIGHT = 0.12
_SOFT_TLD_WEIGHT = 0.05

# Second-level labels that make the registrable label the one before them (co.uk, com.au, ac.il ...).
_SLD_LABELS = frozenset("co com org net gov edu ac or ne go gob nom ltd plc sch mil gouv".split())

# --- vocabulary --------------------------------------------------------------------------------------------

# Matched against the label with hyphens removed. Specific enough that real brands do not collide
# (guerrilla-games, 20minutes, dropbox, templates, spamhaus, burnernotes, 10minuteschool, emailtemplates and
# nospamproxy stay clear: "10minute" needs a mail word after it, the "mail + temp" family has to be the whole
# label, and "nospam" has to end the label).
_STRONG_WORDS = re.compile(
    r"mailinator|guerrilla(?:mail|inbox)|yopmail|"
    r"(?:10|ten|20|30)(?:min|minute|minutes)(?:e?mail|inbox)|"
    r"throw(?:a)?way(?:e?mail|inbox|address)?|"
    r"trash(?:e?mail|inbox)|"
    r"temp(?:orary)?(?:e?mail|inbox|address|post)|"
    r"disposable(?:e?mail|inbox|address)|"
    r"fake(?:e?mail|inbox|mailer|address)|"
    r"burner(?:e?mail|inbox|mail)|"
    r"spam(?:box|mail|gourmet|free|me|catch|egg)|nospam(?=\d*$)|"
    r"dropmail|maildrop|mail(?:nesia|catch(?!er)|poof|tm|drop)|"
    r"getnada|sharklasers|emailondeck|1secmail|"
    r"anonym(?:ous)?(?:e?mail|inbox)")
# "mail + throwaway word" (mailtemp, emailtrash, inboxfake24) only counts when it is the whole label, optionally with
# a few digits: an unanchored match would flag emailtemplates, mailtrapper, inboxspamfilter ...
_STRONG_WHOLE_LABEL = re.compile(
    r"(?:e?mail|inbox)(?:temp|tmp|trash|fake|spam|burner|junk|disposable|throwaway)\d{0,4}$")


def _has_strong_word(label: str) -> bool:
    return any(_STRONG_WORDS.search(r) or _STRONG_WHOLE_LABEL.match(r) for r in _word_readings(label))

_STRONG_WORD_SCORE = 0.9
_MAIL_WORDS = ("mailbox", "email", "inbox", "mail")  # longest first so "mailbox" is removed whole
_MEDIUM_TOKENS = frozenset("temp tmp trash throwaway burner fake spam junk disposable discard dummy anon "
                           "guerrilla yop".split())
_TOKEN_PREFIX_DIGITS = re.compile(r"^(?:temp|tmp|trash|fake|burner|throwaway|spam|junk|disposable)\d+")

# Common words attached to an acronym or a short stem in honest brand names (hdfcbank, bnpparibas,
# rbcroyalbank, nbcuniversal). Stripped once from the end and once from the start before judging a letter run.
_AFFIX_SUFFIX = tuple(sorted("""bank group corp tech labs lab soft software systems system online global media digital
health care store shop team works studio news web app apps cloud data net hq inc ltd llc university school college
financial capital energy motors telecom airlines air foods pharma bio med medical partners holdings solutions services
consulting network networks security insurance paris paribas royal national international universal industries
logistics mobile games gaming""".split(), key=len, reverse=True))
_AFFIX_PREFIX = tuple(sorted("the get try go my e i web net mc mac".split(), key=len, reverse=True))

# Common English and developer words/abbreviations. A label that contains them is a name built from words (inboxsdk,
# npmjs, htmlcsstoimage, tcpdump, yarnpkg), not noise: they are cut out of a letter run before it is judged, and
# what is left over is judged on its own. Only 3+ letter words are cut anywhere; the 2-letter ones (js, db, ui ...)
# only at the very end or start of a label. Random strings rarely contain several of these, and cutting them leaves
# the random part (qzxkvb in qzxkvbsdk) to be judged as before.
_WORDS3 = frozenset("""sdk api cli npm pkg cdn css html xml json yaml toml php sql git svg png jpg pdf csv url uri dns ssl tls
ssh ftp sftp http https smtp imap pop tcp udp ip dump fs tmp img src lib bin dev ops app apps web net org com cloud data
mail mails email inbox box post drop trash temp spam junk bin can send mx dkim mjml mime proxy host hosting server vpn cache
text code core base kit hub lab labs tool tools soft ware wire work flow stack grid bit byte hash key pass lock safe
image images doc docs page pages site sites form forms book note notes link links share mark map maps chat talk call
bnb gram pad pod pay shop store mart bank card coin cash fund loan trade tech sys system systems soft game games play
file files sync backup track watch view show list task todo plan time date clock run build test check scan search find
get set put pull push merge fork clone commit patch pack packs yarn node deno bun rust ruby java perl lua go swift scala
kotlin dart react vue svelte solid next nuxt rails django flask spring laravel redis mongo mysql postgres sqlite oracle
curl wget nginx apache linux ubuntu debian fedora arch alpine docker kube helm terraform ansible jenkins grafana
prometheus kafka rabbit celery lambda azure aws gcp google github gitlab bitbucket stripe twilio slack discord
notion figma canva trello jira asana linear sentry datadog splunk elastic kibana""".split())
_WORDS2 = frozenset("js ts py rb go rs db ui ux io ai ml vr ar gl tv pg my ms mx tc dn".split())
_WORDS3 = frozenset(w for w in _WORDS3 if len(w) >= 3)
_MAX_WORD = max(len(w) for w in _WORDS3)


def _cut_words(seg: str) -> tuple:
    """`seg` with known words removed (left to right, longest first at each position), as (leftover pieces, words
    cut). A leftover piece is a maximal run of letters that no known word covers."""
    pieces, cut, cur, i, n = [], 0, "", 0, len(seg)
    while i < n:
        hit = 0
        for j in range(min(n, i + _MAX_WORD), i + 2, -1):
            if seg[i:j] in _WORDS3:
                hit = j
                break
        if not hit and i == 0 and seg[:2] in _WORDS2 and n > 2:
            hit = 2
        if not hit and seg[i:] in _WORDS2:
            hit = n
        if hit:
            if cur:
                pieces.append(cur)
                cur = ""
            cut += hit - i
            i = hit
        else:
            cur += seg[i]
            i += 1
    if cur:
        pieces.append(cur)
    return pieces, cut

# --- letter-run statistics -------------------------------------------------------------------------------

_VOWELS = frozenset("aeiouy")
# Consonant pairs and vowel pairs that occur all the time in real words and brand names (English, German,
# Romance, Slavic and Hebrew transliteration). Random letters hit pairs outside these sets far more often.
_COMMON_CC = frozenset("""th ch sh st tr nd ng nt ck ll ss pr br cr dr fr gr pl bl cl fl gl sl sp sc sk sm sn sw tw wr
wh ph rt rs rd rm rn rk rl rb rc rp rg rf rv rt ls lt ld lk lm lp lf lv mp mb nk nc ns nn mm ff tt pp rr dd bb gg zz cc
kh zh sz cz rz dz ts tz dg gh tl ks bs ps pt ft ct xt xp mn gn kn dv zd zn zv sv vr vl hr hl hm hn kr kl kv kw tv sf
nz nf nv nh nr ws wn wl wd wm ds dl dn dm gs gm gd gl bt bd bj bn bl bs br dj dw fs ft fn fm lc lg lb lj lw lz ms mt mb
md mr mw ng nj nl nw pn ps pw rh rj rw rz sb sd sg sj sl sr sz tn tm tc td sh bh""".split())
_COMMON_VV = frozenset("""ai ao au ay ea ee ei eo eu ey ia ie io iu oa oe oi oo ou oy ua ue ui uo ya ye yo yu""".split())


def _shannon_ratio(s: str) -> float:
    """Character entropy divided by its maximum for that length: 1.0 = every character different."""
    n = len(s)
    if n < 2:
        return 0.0
    counts = {}
    for ch in s:
        counts[ch] = counts.get(ch, 0) + 1
    h = -sum(c / n * math.log2(c / n) for c in counts.values())
    return h / math.log2(n)


def _or(*ps: float) -> float:
    out = 1.0
    for p in ps:
        out *= 1.0 - max(0.0, min(1.0, p))
    return 1.0 - out


def _strip_affixes(seg: str) -> str:
    for suf in _AFFIX_SUFFIX:
        if seg.endswith(suf) and len(seg) - len(suf) >= 2:
            seg = seg[: -len(suf)]
            break
    for pre in _AFFIX_PREFIX:
        if seg.startswith(pre) and len(seg) - len(pre) >= 2:
            seg = seg[len(pre):]
            break
    return seg


def _run_randomness(seg: str) -> float:
    """How unpronounceable a run of letters is, 0..1. Short runs (acronyms like hsbc, kpmg) are not judged."""
    n = len(seg)
    if n < 5:
        return 0.0
    vowels = sum(1 for ch in seg if ch in _VOWELS)
    if vowels == 0:
        return 0.85
    run = longest = 0
    odd_pairs = 0
    for i, ch in enumerate(seg):
        run = 0 if ch in _VOWELS else run + 1
        longest = max(longest, run)
        if i:
            a, b = seg[i - 1], ch
            av, bv = a in _VOWELS, b in _VOWELS
            if av != bv:
                continue
            pair = a + b
            if (bv and pair not in _COMMON_VV and a != b) or (not bv and pair not in _COMMON_CC):
                odd_pairs += 1
    u = odd_pairs / (n - 1)
    vr = vowels / n
    a = 0.85 if u >= 0.55 else 0.7 if u >= 0.45 else 0.45 if u >= 0.35 else 0.2 if u >= 0.28 else 0.0
    b = 0.6 if longest >= 7 else 0.25 if longest == 6 else 0.15 if longest == 5 and n >= 7 else 0.0
    c = 0.4 if vr < 0.17 and n >= 7 else 0.1 if vr < 0.25 and n >= 6 else 0.0
    p = _or(a, b, c)
    return p * (0.7 if n == 5 else 0.85 if n == 6 else 1.0)


def _label_randomness(label: str) -> float:
    """Randomness of the registrable label: digit/letter mixing plus unpronounceable letter runs."""
    flat = label.replace("-", "")
    digits = sum(ch.isdigit() for ch in flat)
    letters = len(flat) - digits
    mix = 0.0
    if digits and letters:
        transitions = sum(1 for i in range(1, len(flat)) if flat[i - 1].isdigit() != flat[i].isdigit())
        if transitions >= 4:
            mix = 0.85
        elif transitions == 3:
            runs = [len(m.group()) for m in re.finditer(r"\d+|[a-z]+", flat)]
            mix = 0.7 if max(runs) <= 2 else 0.3  # club4x4 is a name, x7k2 is noise
        elif transitions == 2 and len(flat) >= 6 and not (flat[0].isdigit() or flat[-1].isdigit()):
            mix = 0.2  # w3schools, mail2web: one digit inside real words is common
        elif transitions == 1 and digits >= 5 and len(flat) >= 8:
            mix = 0.35  # abc1234567
    best = 0.0
    for seg in re.findall(r"[a-z]+", label):
        pieces, cut = _cut_words(seg)
        if cut >= 3 and cut >= len(seg) // 3:
            # Made partly of real words: judge only what the words do not explain, and trust the rest less.
            left = "".join(pieces)
            r = max([_run_randomness(_strip_affixes(piece)) for piece in pieces] + [_run_randomness(_strip_affixes(left))])
            best = max(best, r * (0.6 if left else 0.0))
        else:
            best = max(best, _run_randomness(_strip_affixes(seg)))
    rand = _or(mix, best)
    if rand > 0.2 and len(flat) >= 10 and _shannon_ratio(flat) >= 0.96:
        rand = min(1.0, rand * 1.15)  # nearly every character different: typical of generated strings
    return rand


def _word_score(label: str, tld: str = "") -> float:
    flat = label.replace("-", "")
    if _has_strong_word(label):
        return _STRONG_WORD_SCORE
    best = compound_score(label, tld)
    if _TOKEN_PREFIX_DIGITS.match(flat):
        best = max(best, 0.55)
    tokens = [t for t in re.split(r"[-\d]+", label) if t]
    if len(tokens) >= 2 and any(t in _MEDIUM_TOKENS for t in tokens):
        best = max(best, 0.65 if any(w in flat for w in _MAIL_WORDS) else 0.4)
    # "mail" plus a random-looking remainder: mailxk7q2, qzxmail, inbox9f3k ...
    for w in _MAIL_WORDS:
        if w in flat:
            rest = flat.replace(w, "", 1)
            if rest.isalpha() and not _cut_words(rest)[0]:
                break  # mail + known words (inboxsdk, mailapi): a product name, not "mail plus noise"
            if len(rest) >= 3 and (any(ch.isdigit() for ch in rest) and any(ch.isalpha() for ch in rest)):
                best = max(best, 0.6)
            elif len(rest) >= 3 and rest.isalpha() and _run_randomness(rest) * (1.0 if len(rest) >= 5 else 0.0) >= 0.5:
                best = max(best, 0.6)
            elif len(rest) >= 3 and rest.isalpha() and not any(ch in _VOWELS for ch in rest):
                best = max(best, 0.6)
            break
    return best


# --- compound model: "<throwaway word> + <mailbox word>" ---------------------------------------------------
# Disposable services are named like their job: a word for "short-lived or nothing" glued to a word for "mailbox"
# (dumpmail, oneshotmail, voidpost, cinderbox, fleetingmail). The label has to be covered completely by words from
# these lists, so an honest name that only contains one of them (dropbox, scrapbox, templates, fastmail) stays clear.
_STRONG_MODS = frozenset("""temp tmp temporary trash throwaway throw dump burner burn fake spam junk null void nul disposable
discard ephemeral fleeting oneshot onetime anon anonymous guerrilla nada shred vanish expire expiring poof puff bounce
cinder rubbish garbage noreply blackhole nospam""".split())
_WEAK_MODS = frozenset("""quick fast instant one shot once zap flash short brief min minute minutes hour hr ten twenty thirty ghost phantom nobody""".split())
_MAIL_NOUNS = frozenset("mail mails email emails inbox mailbox address addr msg letter letters".split())
_BOX_NOUNS = frozenset("box bin post drop hole pit can bucket basket".split())
_FILLERS = frozenset("my the get a an to of for and pro online now".split())
# Real mailbox providers and brands that fit the pattern; never judged by it.
_COMPOUND_SAFE = frozenset("fastmail runbox startmail hushmail mailbox".split())
_COMPOUND_VOCAB = {}
for _w in _STRONG_MODS:
    _COMPOUND_VOCAB[_w] = "S"
for _w in _WEAK_MODS:
    _COMPOUND_VOCAB.setdefault(_w, "W")
for _w in _MAIL_NOUNS:
    _COMPOUND_VOCAB[_w] = "M"
for _w in _BOX_NOUNS:
    _COMPOUND_VOCAB[_w] = "B"
for _w in _FILLERS:
    _COMPOUND_VOCAB.setdefault(_w, "F")
# Ordinary names for a physical waste container: "trash" + "bin" is a bin retailer, not a disposable-mail service,
# so a waste word followed only by one of these (and no mail word) is not evidence. trashbox/discardbox still are.
_WASTE_WORDS = frozenset("trash junk garbage rubbish dump throw throwaway".split())
_CONTAINER_WORDS = frozenset("bin can bucket basket pit hole".split())
_RESERVED_TLDS = frozenset("example test invalid localhost local internal".split())
_MAX_TOKEN = max(len(w) for w in _COMPOUND_VOCAB)


def _compound_covers(seg: str) -> frozenset:
    """Every set of word classes (as a string of S/W/M/B/F letters) that can cover `seg` completely."""
    memo = {len(seg): frozenset({""})}

    def rest(i):
        if i in memo:
            return memo[i]
        out = set()
        for j in range(i + 1, min(len(seg), i + _MAX_TOKEN) + 1):
            kind = _COMPOUND_VOCAB.get(seg[i:j])
            if kind:
                for tail in rest(j):
                    out.add("".join(sorted(set(kind + tail))))
        memo[i] = frozenset(out)
        return memo[i]

    return rest(0)


def compound_score(label: str, tld: str = "") -> float:
    """0..1 for a label made only of a throwaway word and a mailbox word: 0.9 strong word + mail word (dumpmail),
    0.8 strong word + box word (voidpost, nullbox), 0.5 weak word + mail word (oneminutemail), 0.45 weak word + box
    word (quickdrop; 0.5 when the TLD is mail-like). Digits at either end are ignored (tempmail24). 0.0 under the
    reserved TLDs (.example, .test ...): those names are documentation and test fixtures, never real sign-ups."""
    if tld in _RESERVED_TLDS:
        return 0.0
    mail_tld = tld in _MAIL_TLDS
    flat = label.replace("-", "")
    core = flat.strip("0123456789")
    if (flat and flat != core and len(flat) - len(core) > 4) or len(core) < 6 or len(core) > 40 or not core.isalpha() or not core.isascii():
        return 0.0
    if core in _COMPOUND_SAFE:
        return 0.0
    plain = core
    for f in ("the", "my", "get", "a", "an"):
        if plain.startswith(f) and len(plain) > len(f) + 4:
            plain = plain[len(f):]
            break
    if any(plain.startswith(w) and plain[len(w):].rstrip("s") in _CONTAINER_WORDS for w in _WASTE_WORDS):
        return 0.0
    best = 0.0
    for kinds in _compound_covers(core):
        has_mod_s, has_mod_w = "S" in kinds, "W" in kinds
        has_m, has_b = "M" in kinds, "B" in kinds
        if has_mod_s and has_m:
            best = max(best, 0.9)
        elif has_mod_s and has_b:
            best = max(best, 0.8)
        elif has_mod_w and has_m:
            best = max(best, 0.5)
        elif has_mod_w and has_b:
            best = max(best, 0.5 if mail_tld else 0.45)
    return best


def _clean(raw: object) -> Optional[str]:
    """Lowercased domain, or None when it cannot be a domain name (not a string, empty, too long, spaces, '@')."""
    if not isinstance(raw, str) or not raw or len(raw) > _MAX_DOMAIN + 8:
        return None
    d = raw.strip().lower().rstrip(".")
    if not d or len(d) > _MAX_DOMAIN or "." not in d or any(ch.isspace() or ch in "@/\\:" or ord(ch) < 32 for ch in d):
        return None
    return d


def _registrable_label(labels: list) -> str:
    if len(labels) >= 3 and labels[-2] in _SLD_LABELS and len(labels[-1]) == 2:
        return labels[-3]
    return labels[-2] if len(labels) >= 2 else labels[0]


def _canonical(domain: str) -> str:
    """`domain` with non-ASCII spellings folded to the ASCII letters they imitate (Cyrillic a -> a, punycode decoded),
    so one registrable domain written two ways counts once. Plain ASCII is returned as is."""
    if domain.isascii() and "xn--" not in domain:
        return domain
    try:
        prepared = lookalike._prepare(domain)
        return lookalike._fold(prepared, swaps=False) if prepared else domain
    except Exception:
        return domain


def registrable_domain(domain: str) -> str:
    """The domain a person registers: the last two labels, or the last three under a country second level such as
    co.uk, com.au, co.il, com.br, co.jp, ac.xx, edu.xx. mail.acme.com and s7.acme.com are both acme.com."""
    labels = _canonical(domain).split(".")
    if len(labels) >= 3 and labels[-2] in _SLD_LABELS and len(labels[-1]) == 2:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


# Leetspeak read back into letters. "1" is both i and l (trashmai1, yopmai1), so two readings are tried.
_LEET_I = str.maketrans({"1": "i", "0": "o", "3": "e", "5": "s", "@": "a", "$": "s"})
_LEET_L = str.maketrans({"1": "l", "0": "o", "3": "e", "5": "s", "@": "a", "$": "s"})


def _word_readings(label: str) -> list:
    """The label as written, and with digits read as the letters they imitate, for the disposable word patterns."""
    flat = label.replace("-", "")
    out = [flat]
    for table in (_LEET_I, _LEET_L):
        r = flat.translate(table)
        if r not in out:
            out.append(r)
    return out


def domain_shape_score(domain) -> float:
    """How disposable-looking `domain` is from its shape alone, 0.0 to 1.0. Never raises.

    Looks only at the registrable label (acme in mail.acme.com, acme in acme.co.uk): random-looking letters
    (consonant runs, few vowels, pairs that real words rarely contain, nearly all characters different),
    digit/letter soup like x7k2q9, labels of only digits and hyphens, very long labels, piles of hyphens, known
    disposable word patterns (tempmail, trashmail, 10minute..., burner, "mail" plus noise) and, with a small
    weight, TLDs that are abused a lot (.xyz .top .icu .click ...). Independent parts are combined so that
    several mild hints add up while one mild hint stays low.

    Returns 0.0 for well-known providers and school domains (`well_known_domain`), for anything that is not a
    string or cannot be a domain, and ignores letter statistics for non-ASCII (IDN) labels.
    This is a hint, not a verdict: see the module docstring. Fires `odd_domain` at ODD_DOMAIN_THRESHOLD."""
    d = _clean(domain)
    if d is None or well_known_domain(d):
        return 0.0
    labels = [lb for lb in d.split(".")]
    if any(not lb for lb in labels):
        return 0.0
    tld = labels[-1]
    label = _registrable_label(labels)
    parts = []
    if tld in _ABUSED_TLDS:
        parts.append(_ABUSED_TLD_WEIGHT)
    elif tld in _SOFT_TLDS:
        parts.append(_SOFT_TLD_WEIGHT)
    if len(label) > _MAX_LABEL:
        return 1.0
    unicode_label = (not label.isascii()) or label.startswith("xn--")
    if unicode_label:
        folded = _canonical(label)
        if folded.isascii():  # Cyrillic/Greek/... letters standing in for Latin ones: only the word patterns apply
            parts.append(_STRONG_WORD_SCORE if _has_strong_word(folded) else 0.0)
        label = unicodedata.normalize("NFKC", label)
        n = len(label)
        parts.append(0.25 if n > 35 else 0.0)
        return round(_or(*parts), 4)
    hyphens = label.count("-")
    flat = label.replace("-", "")
    n = len(flat)
    if flat and all(ch.isdigit() for ch in flat):
        parts.append(0.7 if n >= 7 else 0.55 if n == 6 else 0.3 if n >= 4 else 0.0)
    elif not flat:
        parts.append(0.7)  # only hyphens
    else:
        parts.append(_label_randomness(label))
        parts.append(_word_score(label, tld))
    if n > 50:
        parts.append(0.8)
    elif n > 35:
        parts.append(0.5)
    elif n > 24:
        parts.append(0.25)
    if hyphens >= 4:
        parts.append(0.55)
    elif hyphens == 3:
        parts.append(0.35)
    elif hyphens == 2:
        parts.append(0.1)
    return round(_or(*parts), 4)


def disposable_word_score(domain) -> float:
    """0..1: how strongly the registrable label of `domain` is made of disposable-mail words (tempinbox, dumpmail,
    voidpost, oneshotmail), independent of random-looking letters or TLD. 0.0 for well-known providers and schools,
    for the reserved TLDs (.example, .test ...: documentation and test names) and for anything that cannot be a
    domain. Never raises."""
    d = _clean(domain)
    if d is None or well_known_domain(d):
        return 0.0
    labels = d.split(".")
    if any(not lb for lb in labels) or labels[-1] in _RESERVED_TLDS:
        return 0.0
    label = _registrable_label(labels)
    if not label.isascii() or label.startswith("xn--") or len(label) > _MAX_LABEL:
        return 0.0
    flat = label.replace("-", "")
    if not flat or flat.isdigit():
        return 0.0
    return round(_word_score(label, labels[-1]), 4)


# --- store-backed signals --------------------------------------------------------------------------------

def _key(domain: str) -> str:
    """Store key of a domain: its registrable domain, so one subdomain per account (s1.x.xyz, s2.x.xyz) still counts."""
    return "dom:" + registrable_domain(domain)


def _net_key(domain: str) -> str:
    return "domnet:" + registrable_domain(domain)


def new_domain_signals(store, now, domain, blind=lambda v: v) -> list:
    """Weak signals for the domain of a sign-up that is about to happen. Call BEFORE `record_domain`.

    * `odd_domain` (0.3) when `domain_shape_score(domain) >= ODD_DOMAIN_THRESHOLD`.
    * `domain_burst` when the domain already has >= 5 distinct addresses recorded in the last 24 hours
      (0.35) or >= 10 (0.5); the address signing up now is not counted yet.

    Nothing is returned for well-known providers, school domains or input that is not a usable domain.
    BOTH ARE WEAK: honest teams, companies and classes also produce bursts, and unusual honest brands also
    look odd. They must not block on their own; the guard treats them as soft unless corroborated.

    `blind` maps a raw address to the form `store.values()` returns (pass `store.blind` for BlindStore or a
    keyed PostgresStore); the count itself is a distinct-count, which blinding preserves."""
    d = _clean(domain)
    if d is None or well_known_domain(d):
        return []
    out = []
    shape = domain_shape_score(d)
    if shape >= ODD_DOMAIN_THRESHOLD:
        words = disposable_word_score(d) >= ODD_DOMAIN_WORDS_THRESHOLD
        out.append(Signal("odd_domain", ODD_DOMAIN_WORDS_SCORE if words else ODD_DOMAIN_SCORE,
                          f"domain name is made of disposable-mail words ({d})" if words
                          else f"domain looks auto-generated or disposable ({d})"))
    try:
        seen = len(store.values(_key(d), BURST_WINDOW, now))
    except Exception:  # a broken store must not break sign-up; the shape signal still counts
        seen = 0
    tiers = BURST_TIERS
    if shape >= ODD_DOMAIN_THRESHOLD and disposable_word_score(d) >= BURST_WORDS_MIN:
        tiers = BURST_TIERS + (BURST_WORDS_TIER,)
    for min_n, score in tiers:
        if seen >= min_n:
            out.append(Signal("domain_burst", score, f"{seen} distinct addresses from {d} in 24h"))
            break
    return out


def record_domain(store, now, domain, norm_email, blind=lambda v: v, net=None) -> None:
    """Remember that `norm_email` signed up from `domain`, for `new_domain_signals`. Call AFTER it.

    Does nothing for well-known providers, school domains, unusable domains or a non-string address. An address
    already recorded in the last 24 hours is not stored again (compared through `blind`), so replaying one
    address cannot push the other distinct addresses out of the store's per-key cap.

    `net` (optional, the /24 or /48 string of the sign-up's IP) is remembered per registrable domain for
    `domain_network_spread`, also once per network per 24 hours."""
    d = _clean(domain)
    if d is None or well_known_domain(d) or not isinstance(norm_email, str) or not norm_email:
        return
    _remember(store, now, _key(d), norm_email, blind)
    if isinstance(net, str) and net:
        _remember(store, now, _net_key(d), net, blind, window=NET_DEDUP_WINDOW)


NET_DEDUP_WINDOW = 600  # re-record a network every 10 minutes so the hourly spread check never loses it


def _remember(store, now, key, value, blind, window=None) -> None:
    try:
        if blind(value) in store.values(key, BURST_WINDOW if window is None else window, now):
            return
    except Exception:
        pass
    store.add(key, value, now)


def domain_network_spread(store, now, domain, blind=lambda v: v, window: float = None) -> int:
    """How many distinct networks (the /24 strings given to `record_domain` as `net`) signed up from the registrable
    domain of `domain` in the last 24 hours. 0 for well-known providers, unusable domains or a broken store.
    One office is one or two networks; fifteen mailboxes on one unknown domain from fifteen networks is not an office.
    `blind` is accepted for symmetry with the other functions: a distinct count survives blinding."""
    d = _clean(domain)
    if d is None or well_known_domain(d):
        return 0
    try:
        return len(store.values(_net_key(d), BURST_WINDOW if window is None else window, now))
    except Exception:
        return 0
