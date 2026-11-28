"""Guard: score a signup, return an explainable allow/review/block decision."""
from __future__ import annotations

import hashlib
import ipaddress
import math
import threading
import logging
import time
import warnings
from contextlib import nullcontext
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from . import signals as S
from . import proxy as P
from . import lookalike as L
from . import domains as D
from .store import MemoryStore

ALLOW, REVIEW, BLOCK = "allow", "review", "block"


@dataclass
class Signup:
    email: Optional[str] = None
    ip: Optional[str] = None
    device_id: Optional[str] = None
    user_agent: Optional[str] = None
    ts: Optional[float] = None
    links: Optional[dict] = None  # stable ids your app already has, e.g. {"card": stripe_fingerprint, "phone": verified_phone}


@dataclass
class Decision:
    action: str
    score: float
    reasons: List[str] = field(default_factory=list)
    signals: List[S.Signal] = field(default_factory=list)
    credit_fraction: float = 0.0  # share of the free grant to release now (graduated unlock)
    limit: Optional[str] = None  # why credit_fraction is lower than the action alone gives ("starter budget", "surge guard")
    shadow_action: Optional[str] = None  # in shadow mode: what Tenkyu would have done; action is then always allow
    would_credit_fraction: Optional[float] = None  # in shadow mode: the credit_fraction Tenkyu would have released
    would_limit: Optional[str] = None  # in shadow mode: the limit that would have applied
    earn_back: Optional[dict] = None  # REVIEW only: {"requires": "email" | "phone_or_card", "reason": ...}, the proof that releases the rest of the grant (Guard.earn_back)
    would_earn_back: Optional[dict] = None  # in shadow mode: the earn_back the real decision (review) would have carried
    _earn: Optional[dict] = field(default=None, repr=False, compare=False)  # what Guard.earn_back needs to find this signup again

    @property
    def allowed(self) -> bool:
        return self.action == ALLOW

    def to_dict(self) -> dict:
        out = {"action": self.action, "score": round(self.score, 3), "reasons": list(self.reasons),
               "credit_fraction": self.credit_fraction,
               "signals": [{"name": s.name, "score": s.score, "reason": s.reason} for s in self.signals]}
        if self.limit is not None:
            out["limit"] = self.limit
        if self.shadow_action is not None:
            out["shadow_action"] = self.shadow_action
            out["would_credit_fraction"] = self.would_credit_fraction
        if self.earn_back is not None:
            out["earn_back"] = dict(self.earn_back)
        if self.would_earn_back is not None:
            out["would_earn_back"] = dict(self.would_earn_back)
        return out


def _network_key(ip: str) -> str:
    """Key used to count signups per IP.

    An IPv6 network hands out 2**64 addresses to one customer, so counting exact addresses would let a
    farmer rotate for free: IPv6 is counted per /64. An IPv4-mapped address (::ffff:1.2.3.4) is the same
    client as plain 1.2.3.4. A port (`1.2.3.4:5000`, `[2001:db8::1]:443`) is dropped. Anything that is not an
    IP address is kept as given (opaque ids still work).
    """
    raw = ip.strip()
    addr = None
    for candidate in (S.host_of(raw), raw.strip("[]")):
        if candidate is None:
            continue
        try:
            addr = ipaddress.ip_address(candidate.strip("[]").split("%")[0])
            break
        except ValueError:
            continue
    if addr is None:
        return raw
    if addr.version == 6:
        if addr.ipv4_mapped is not None:
            return str(addr.ipv4_mapped)
        return str(ipaddress.ip_network((addr, 64), strict=False))
    return str(addr)


_MAX_KEY_PART = 128  # real IPs are <= 45 chars and visitor ids are short; anything longer is client-controlled


def _bounded(value: str, limit: int = _MAX_KEY_PART) -> str:
    """Keep storage cost flat: long client-supplied strings are replaced by their SHA-256, so equal
    values still match each other and nearly-equal ones stay distinct."""
    if len(value) <= limit:
        try:
            value.encode("utf-8")
            return value
        except UnicodeEncodeError:  # a lone surrogate cannot be stored (SQLite raises): key it by its hash
            pass
    return "sha256:" + hashlib.sha256(value.encode("utf-8", "surrogatepass")).hexdigest()


def ip_key(ip: str) -> str:
    """The IP address as Tenkyu keys it in the store: IPv6 by its /64 network, `::ffff:a.b.c.d` as
    `a.b.c.d`, whitespace, brackets and a trailing port removed, very long strings hashed. Custom signals that read
    the store should build keys from this, e.g. `store.count(f"ip:{ip_key(signup.ip)}", 600, now)`,
    because a raw `signup.ip` does not match for IPv6."""
    return _bounded(_network_key(ip))


MAX_LINKS = 8
VERIFYING_LINKS = frozenset({"phone", "card"})


def _link_keys(links, secret: Optional[bytes] = None, default_country_code: Optional[str] = None) -> List[str]:
    """Validate Signup.links and return their stored keys (keyed with `secret` when given).
    Raises ValueError on bad input."""
    if links is None:
        return []
    if not isinstance(links, dict) or len(links) > MAX_LINKS:
        raise ValueError(f"links must be a dict of at most {MAX_LINKS} entries")
    return [S.link_key(k, v, secret, default_country_code) for k, v in links.items()]


def _net_group(ip: str) -> Optional[str]:
    """The wider network an IP key belongs to, for the network rules: the /24 of an IPv4 address, the /48 of
    an IPv6 key (which is already a /64, so a farmer rotating /64s inside one /48 still lands in one group).
    None for anything else (opaque ids, hashed strings)."""
    try:
        return str(ipaddress.ip_network((ipaddress.IPv4Address(ip), 24), strict=False))
    except ValueError:
        pass
    try:
        net = ipaddress.IPv6Network(ip, strict=False)
    except ValueError:
        return None
    return str(net.supernet(new_prefix=48)) if net.prefixlen >= 48 else None


PROXY_MODE = "corroborated"  # "off" | "corroborated" | "soft" (see tenkyu.proxy)
DEVICE_CROWD_MAX = 5  # accounts one device can carry in 7 days and still look like a household
DOMAIN_BURST_MIN_SHAPE = 0.5  # domain_burst counts only for a domain that already looks a little odd
CROWD_MAX_DOMAIN_SHAPE = 0.3  # an own domain this normal-looking can still be a school or startup crowd
CROWD_MAX_PER_HOUR = 60  # a big class signing up together; past this one IP is a farm, crowd or not
CROWD_MAX_PER_DAY = 200
DOMAIN_SPREAD_NETWORKS = 6  # a small domain seen from this many /24s within one hour
NETWORK_BURST_ALONE = 15  # signups from one network in 10 minutes that count with no other risk signal
EARN_BACK_WINDOW = 30 * 86400.0  # a release is remembered this long (one per signup); a phone or card is counted over it
EARN_BACK_MAX_OTHER_ACCOUNTS = 2  # a phone or card already used to release credits on this many other accounts releases no more
EARN_EMAIL_PROOFS = frozenset({"email_verified"})
EARN_STRONG_PROOFS = frozenset({"phone_verified", "card_verified"})
EARN_REASONS = {"email": "shared-network signup: a verified email releases the rest of the credits",
                "phone_or_card": "unusual signup: a verified phone or card releases the rest of the credits"}
NETWORK_DAILY_CAP = 30  # full grants per /24 (IPv4) or /48 (IPv6) network in 24 hours; past it, ALLOW becomes REVIEW


def _is_soft(sig: S.Signal, soft_ids) -> bool:
    """Signals an honest crowd produces: a shared device and volume from one network. They never block on their own
    and do not count as the "other" risk signal that switches the network rules on. Only the built-in signals Guard
    itself made count (`soft_ids` holds their identities): a custom signal is never soft, whatever it is called."""
    return id(sig) in soft_ids


def _safe_ts(ts, clock) -> float:
    try:
        t = float(ts)
    except (TypeError, ValueError, OverflowError):
        return clock()
    return t if math.isfinite(t) else clock()


def _clean_signal(sig) -> Optional[S.Signal]:
    """Accept only a well-formed Signal from a custom signal function; anything else is ignored."""
    if not isinstance(sig, S.Signal):
        return None
    score = sig.score
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        return None
    score = 0.0 if math.isnan(score) else max(0.0, min(1.0, float(score)))
    name = str(sig.name)
    return S.Signal(name, score, name if sig.reason is None else str(sig.reason))


class Guard:
    """Score = noisy-or of signal scores. score >= block_at -> block, >= review_at -> review."""

    def __init__(self, store: Optional[MemoryStore] = None, review_at: float = 0.35, block_at: float = 0.7,
                 clock: Callable[[], float] = time.time, extra_signals=(),
                 extra_disposable=(), credit_fractions: Optional[dict] = None,
                 starter_fraction: Optional[float] = None, surge_threshold: Optional[int] = None,
                 shadow: bool = False, log=None, link_secret: Optional[bytes] = None,
                 network_daily_cap: Optional[int] = NETWORK_DAILY_CAP, default_country_code: Optional[str] = None):
        """starter_fraction: cap on credit_fraction for signups without a verified phone or card link
        (for example 0.5). surge_threshold: when this many unverified signups are released credits within an
        hour, the next ones get credit_fraction 0 until verified. shadow: score and record everything, but
        always return action allow with the full grant; the real decision is in shadow_action.
        All three are off by default, so existing integrations behave exactly as before.
        link_secret: at least 16 secret bytes; link values (phone, card, OAuth id) are then stored as
        HMAC-SHA256 instead of plain SHA-256, so a leaked store cannot be reversed by guessing phone numbers.
        Keep it stable: changing it stops old links from matching.
        network_daily_cap: at most this many full grants (decisions that stay allow) per /24 network (/48 for IPv6)
        in 24 hours, 20 by default. Further signups from that network that would be allow become review (partial
        credit, reason `network_daily_cap`) until the day's grants age out. It never blocks and never touches a
        signup that is already review or block. Pass None to turn it off.
        default_country_code: calling code ("972", "+1") of the country whose national phone numbers (`0501234567`)
        your users type. With it, a phone is compared as country code + national number, so `0501234567` and
        `+972501234567` are one phone and the same digits in another country are not. Without it (None) the country is
        unknown: international numbers lose their country code (a longest-match table of ITU codes) and are compared
        by national number, so national and international forms still match, at the price of treating one national
        number in two different countries as one phone. Keys made with and without it do not match."""
        if network_daily_cap is not None and (isinstance(network_daily_cap, bool) or not isinstance(network_daily_cap, int) or network_daily_cap < 1):
            raise ValueError("network_daily_cap must be a positive integer, or None to turn it off")
        self.network_daily_cap = network_daily_cap
        if not 0 <= review_at <= block_at <= 1:
            raise ValueError("need 0 <= review_at <= block_at <= 1")
        self.clock = clock
        self._lock = threading.RLock()
        self.store = store or MemoryStore(clock=clock)
        self.review_at, self.block_at = review_at, block_at
        self.extra_signals = list(extra_signals)
        self.disposable = S.DISPOSABLE_DOMAINS | S.clean_domains(extra_disposable)
        self.credit_fractions = {ALLOW: 1.0, REVIEW: 0.25, BLOCK: 0.0, **(credit_fractions or {})}
        if starter_fraction is not None and not 0 <= starter_fraction <= 1:
            raise ValueError("starter_fraction must be between 0 and 1")
        if surge_threshold is not None and (isinstance(surge_threshold, bool) or not isinstance(surge_threshold, int) or surge_threshold < 1):
            raise ValueError("surge_threshold must be a positive integer")
        self.starter_fraction, self.surge_threshold, self.shadow = starter_fraction, surge_threshold, bool(shadow)
        if link_secret is not None and (not isinstance(link_secret, (bytes, bytearray)) or len(link_secret) < 16):
            raise ValueError("link_secret must be bytes, at least 16 bytes long")
        self.link_secret = None if link_secret is None else bytes(link_secret)
        self.default_country_code = S.normalize_country_code(default_country_code)
        self._warned_link_secret = False
        self.log = log  # e.g. DecisionLog("decisions.jsonl"); gets every recorded decision

    @classmethod
    def for_ai_apps(cls, **overrides) -> "Guard":
        """Recommended settings for AI apps with a free credit grant: unverified new accounts get half the grant
        (starter_fraction=0.5) and, when 25 unverified accounts were released credits within an hour, the next ones
        must verify a phone or card first (surge_threshold=25). Pass phone or card links to give verified users
        the full grant. Any argument can be overridden."""
        return cls(**{"starter_fraction": 0.5, "surge_threshold": 25, **overrides})

    def check(self, signup: Signup, record: bool = True) -> Decision:
        """Evaluate a signup. record=False scores it without remembering it (dry run).

        Reading the history and recording the signup happen as one step: without that, a farmer who
        sends many aliases of one person in parallel gets every one of them past the alias check."""
        _link_keys(signup.links, self.link_secret, self.default_country_code)  # validate before taking any lock or writing anything
        if self.link_secret is None and signup.links and not self._warned_link_secret:
            with self._lock:
                first, self._warned_link_secret = not self._warned_link_secret, True
            if first:
                message = ("Signup.links are stored as plain SHA-256, and SHA-256 of low-entropy values such as phone "
                           "numbers can be reversed by guessing. Pass Guard(link_secret=<16+ secret bytes>) to key "
                           "them with HMAC.")
                try:
                    warnings.warn(message, stacklevel=2)
                except Warning:  # -W error / filterwarnings=error must not turn this notice into a failed signup
                    logging.getLogger("tenkyu").warning(message)
        if not record:
            return self._check(signup, False)
        transaction = getattr(self.store, "transaction", None)
        with self._lock:
            if transaction is None:  # a custom store without transactions: at least one process at a time
                decision = self._check(signup, True)
            else:
                with transaction():
                    decision = self._check(signup, True)
        if self.log is not None:
            try:
                self.log.write(signup, decision, self.clock() if signup.ts is None else _safe_ts(signup.ts, self.clock))
            except Exception:  # a broken log must never break signup
                pass
        return decision

    def _blind(self, v):
        return None if v is None else getattr(self.store, "blind", lambda x: x)(v)

    def _blocked_subject(self, norm, dev) -> tuple:
        """(email token, device token): the store-blinded form of the identity (the identity itself on a store that
        keeps raw values). A caller that keeps its own record of a decision persists this, never the raw email or device."""
        return (self._blind(norm) if norm else None, self._blind(dev) if dev else None)

    def _blocked_count_tokens(self, e_tok, d_tok, now: float) -> int:
        n = 0
        if e_tok:
            n += self.store.count(f"blocked:e:{e_tok}", EARN_BACK_WINDOW, now)
        if d_tok:
            n += self.store.count(f"blocked:d:{d_tok}", EARN_BACK_WINDOW, now)
        return n

    def _blocked_count(self, norm, dev, now: float) -> int:
        """Block decisions seen for this email or device in the last 30 days."""
        return self._blocked_count_tokens(*self._blocked_subject(norm, dev), now)

    def mark_blocked(self, email: Optional[str] = None, device_id: Optional[str] = None) -> None:
        """Tell Guard an email or device was blocked or confirmed as a farmer outside Guard (your own review): an
        earn_back for any earlier signup of it is then refused."""
        now = self.clock()
        norm = S.normalize_email(email)
        dev = _bounded(device_id.strip()) if isinstance(device_id, str) and device_id.strip() else None
        with self._lock:
            e_tok, d_tok = self._blocked_subject(norm, dev)
            if e_tok:
                self.store.add(f"blocked:e:{e_tok}", ts=now)
            if d_tok:
                self.store.add(f"blocked:d:{d_tok}", ts=now)

    def earn_back(self, decision: Decision, proof: str, links: Optional[dict] = None) -> float:
        """Release the rest of a REVIEW decision's grant after proof. Returns the extra credit_fraction to grant
        (1 - the decision's credit_fraction), or 0.0.

        proof "email_verified" satisfies decision.earn_back["requires"] == "email"; "phone_verified" or "card_verified"
        satisfies either requirement and needs the verified value in `links` (e.g. {"phone": "+15551234567"}) unless
        the signup already carried it: it is stored as a link, so a later signup with the same phone or card is link_reuse.
        One release per signup, ever (a second call gives 0.0). 0.0 as well when the email or device was linked to a
        block since the decision (see mark_blocked), or when the phone or card already released credits on 2 other
        accounts in 30 days."""
        if not isinstance(decision, Decision):
            raise TypeError("earn_back needs the Decision returned by check()")
        info = decision._earn
        if decision.earn_back is None or info is None:
            return 0.0
        blocked = self._blocked_count(info["norm"], info["dev"], self.clock()) > info["blocked"]
        return self.redeem_earn_back(info["key"], decision.earn_back["requires"], decision.credit_fraction, proof,
                                     links if links is not None else info["links"], blocked=blocked)[0]

    def redeem_earn_back(self, key: str, requires: str, fraction: float, proof, links=None, blocked: bool = False,
                         record: bool = True):
        """The rules of earn_back for a caller that keeps its own record of the decision (the hosted API): returns
        (extra_credit_fraction, reason). `key` identifies the signup (one release per key); record=False only reports."""
        if requires not in EARN_REASONS:
            return 0.0, "this signup has nothing to earn back"
        if not isinstance(proof, str):  # unhashable or garbage proof: nothing is released
            return 0.0, f"the proof does not satisfy {requires}"
        strong = proof in EARN_STRONG_PROOFS
        if not (strong or (proof in EARN_EMAIL_PROOFS and requires == "email")):
            return 0.0, f"the proof does not satisfy {requires}"
        extra = round(max(0.0, 1.0 - fraction), 6)
        if extra <= 0:
            return 0.0, "the full grant was already released"
        if blocked:
            return 0.0, "this identity was linked to a block since the signup"
        keys = []
        if strong:
            wanted = {k: v for k, v in (links or {}).items() if k in VERIFYING_LINKS} if isinstance(links, dict) else {}
            if not wanted:
                return 0.0, "send the verified phone or card in links"
            keys = _link_keys(wanted, self.link_secret, self.default_country_code)
        st = self.store
        blind = getattr(st, "blind", None) or (lambda v: v)
        transaction = getattr(st, "transaction", None)
        with self._lock:
            with (transaction() if transaction is not None else nullcontext()):
                now = self.clock()
                if st.count(f"earnback:{key}", EARN_BACK_WINDOW, now) >= 1:
                    return 0.0, "the rest of the credits was already released for this signup"
                for k in keys:
                    if len(st.values(f"earnlink:{k}", EARN_BACK_WINDOW, now) - {blind(key)}) >= EARN_BACK_MAX_OTHER_ACCOUNTS:
                        return 0.0, "this phone or card already released credits on other accounts"
                if record:
                    st.add(f"earnback:{key}", ts=now)
                    for k in keys:
                        st.add(f"earnlink:{k}", key, ts=now)
                        st.add(k, ts=now)  # the existing links mechanism: the next signup with it is link_reuse
        return extra, "released"

    @staticmethod
    def _clean_identity(signup: Signup, norm, sigs, dev_single_net: bool, builtin_ids) -> bool:
        """A plain address at a big provider, a school or a normal-looking own domain, a normal browser, and no real
        risk signal besides IP volume (and, on a device that never left one network, device sharing: a school lab)."""
        if not norm or not S.is_plain(signup.email, norm):
            return False
        dom = S.email_domain(norm)
        if not S.well_known_domain(dom) and D.domain_shape_score(dom) >= CROWD_MAX_DOMAIN_SHAPE:
            return False
        if not isinstance(signup.user_agent, str) or not signup.user_agent.strip():
            return False
        quiet = {"ip_burst", "ip_velocity", "odd_domain", "domain_burst"} | ({"device_reuse"} if dev_single_net else set())
        return not any(s.score > 0 and not (s.name in quiet and id(s) in builtin_ids) for s in sigs)

    def _check(self, signup: Signup, record: bool) -> Decision:
        try:
            now = float(signup.ts) if signup.ts is not None else self.clock()
        except (TypeError, ValueError, OverflowError):
            now = self.clock()
        if not math.isfinite(now):  # nan/inf would poison every window comparison
            now = self.clock()
        norm = S.normalize_email(signup.email)
        ip = ip_key(signup.ip) if isinstance(signup.ip, str) and signup.ip.strip() else None
        dev = _bounded(signup.device_id.strip()) if isinstance(signup.device_id, str) and signup.device_id.strip() else None
        stem = S.local_stem(norm)
        st = self.store
        links = _link_keys(signup.links, self.link_secret, self.default_country_code)
        # A store that hides what it keeps (BlindStore, PostgresStore(key_secret=...)) returns blinded values from
        # values(). Every raw value compared with those results goes through blind() first; a store without it
        # (a custom one written before blind() existed) keeps raw values, so the identity applies.
        blind = getattr(st, "blind", None) or (lambda v: v)
        verified = bool(signup.links) and any(k in VERIFYING_LINKS for k in signup.links)
        net = _net_group(ip) if ip else None

        soft_ids = set()  # identities of the built-in soft signals made below (see _is_soft)
        crowd_ids = set()  # the subset an honest crowd explains (shared network, family device, busy school): earn_back "email"
        owner_ids = set()  # identity of the built-in owner_after_aliases signal, so a custom one cannot force review
        found: List[Optional[S.Signal]] = [
            S.invalid_email(norm, signup.email), S.disposable_email(norm, self.disposable), S.headless_agent(signup.user_agent),
            L.lookalike_signal(norm, signup.email), L.obfuscation_signal(signup.email)]

        if norm and st.count(f"email:{norm}", 30 * 86400, now) >= 1:
            forms_seen = st.values(f"emailform:{norm}", 30 * 86400, now)
            if forms_seen and S.is_plain(signup.email, norm) and blind("plain") not in forms_seen:
                # The plain address arrives after only aliases of it were used: the aliases squatted the owner's inbox.
                # (No forms at all means history from before forms were recorded: treat it as a plain reuse.)
                owner_sig = S.Signal("owner_after_aliases", 0.0, "plain address after earlier alias signups of the same inbox: partial credit (2 accounts per inbox)")
                owner_ids.add(id(owner_sig))
                found.append(owner_sig)
            else:
                found.append(S.Signal("alias_reuse", 0.9, "this email (or an alias of it) already signed up"))
        for k in links:
            if st.count(k, 30 * 86400, now) >= 1:
                found.append(S.Signal("link_reuse", 0.9, f"shares a {k.split(':')[1]} with another account"))
        if ip:
            found.append(S.count_signal("ip_burst", st.count(f"ip:{ip}", 60, now) + 1, [(3, 0.9)], "signups from this IP in 60s"))
            found.append(S.count_signal("ip_velocity", st.count(f"ip:{ip}", 3600, now) + 1, [(3, 0.5), (6, 0.9)], "signups from this IP in 1h"))
            if stem:
                same_stem = st.values(f"ipstem:{ip}:{stem}", 86400, now) | {blind(norm)}
                found.append(S.count_signal("pattern_emails", len(same_stem), [(3, 0.75)], "similar-looking emails from this IP"))
        dev_single_net = True
        if dev:
            seen = st.values(f"devemail:{dev}", 7 * 86400, now)
            if norm:
                seen = seen | {blind(norm)}
            dsig = S.count_signal("device_reuse", len(seen), [(2, 0.5), (3, 0.65)], "accounts on this device")
            dev_nets = st.values(f"devnet:{dev}", 7 * 86400, now) | ({blind(net)} if net else set())
            # a lab computer passes from student to student; another account on the same device within 20 seconds is a script
            dev_single_net = len(dev_nets) <= 1 and st.count(f"devemail:{dev}", 20, now) == 0
            if len(seen) >= DEVICE_CROWD_MAX + 1 and len(dev_nets) >= 3:
                # More accounts on one device in a week than a household makes, carried across 3+ networks: a farm
                # hopping proxies, not a family or a shared kiosk (those stay on one network and stay soft).
                found.append(S.Signal("device_farm", 0.75, f"{len(seen)} accounts on this device across {len(dev_nets)} networks in 7 days"))
            else:
                found.append(dsig)
                if dsig is not None:
                    soft_ids.add(id(dsig))
                    if dev_single_net:
                        crowd_ids.add(id(dsig))
        dom = S.email_domain(norm)
        if dom and not any(s is not None and s.name == "disposable_email" and s.score > 0 for s in found):
            for ds in D.new_domain_signals(st, now, dom, blind=blind):
                if (ds.name == "domain_burst" and D.domain_shape_score(dom) < DOMAIN_BURST_MIN_SHAPE
                        and D.domain_network_spread(st, now, dom, blind=blind, window=3600) < DOMAIN_SPREAD_NETWORKS):
                    continue  # a remote team trickles in over days; six networks on one small domain within an hour is a farm
                found.append(ds)
                if ds.name == "odd_domain" and ds.score >= D.ODD_DOMAIN_WORDS_SCORE:
                    # a name made of disposable-mail words: reviewed alone (see ODD_DOMAIN_WORDS_SCORE, below the block
                    # line) and it counts as a real risk signal for the network rules, so it is not marked soft
                    continue
                soft_ids.add(id(ds))  # weak on their own: a real company with an odd name must never block on them
                crowd_ids.add(id(ds))
        builtin_ids = {id(s) for s in found if s is not None}  # custom signals are never treated as crowd noise
        for fn in self.extra_signals:
            try:
                found.append(_clean_signal(fn(signup, st, now)))
            except Exception:  # a broken custom signal must never break signup
                continue

        sigs = [s for s in found if s is not None]
        if (ip and st.count(f"ip:{ip}", 3600, now) < CROWD_MAX_PER_HOUR and st.count(f"ip:{ip}", 86400, now) < CROWD_MAX_PER_DAY
                and self._clean_identity(signup, norm, sigs, dev_single_net, builtin_ids)):
            # A school, office or carrier NAT puts many honest people behind one IP. When this signup is otherwise a
            # clean, distinct identity (a plain address at a big mailbox provider or a school, never seen before, its own device id
            # used by no other account, a normal browser), IP volume alone means a crowd, not a farm: it can cost
            # credit (review) but never a block. Any real signal about this signup switches the IP rules back on.
            for s in sigs:
                if s.name in ("ip_burst", "ip_velocity", "device_reuse") and id(s) in builtin_ids:
                    soft_ids.add(id(s))
                    crowd_ids.add(id(s))
        if ip:
            # Rotating residential proxies: weak, soft signals (see tenkyu.proxy). They count only when this signup
            # already has a real risk signal: an iPhone crowd on one carrier shares a browser string too.
            corroborated_now = any(s.score > 0 for s in sigs if not _is_soft(s, soft_ids))
            if PROXY_MODE == "soft" or (PROXY_MODE == "corroborated" and corroborated_now):
                for ps in P.signals(st, now, ip, signup.user_agent, dev, blind=blind):
                    soft_ids.add(id(ps))
                    sigs.append(ps)
        if net:
            # A university campus or carrier network has many honest signups at once, and a family shares a device,
            # so volume from one network only counts alongside a real risk signal about this same signup. Device
            # reuse is not one: it is what an honest household looks like. A network burst on its own needs a much
            # bigger crowd (15 in 10 minutes) than with another risk signal present (5).
            corroborated = any(s.score > 0 for s in sigs if not _is_soft(s, soft_ids))
            nets = [S.count_signal("network_burst", st.count(f"net:{net}", 600, now) + 1,
                                   [(5 if corroborated else NETWORK_BURST_ALONE, 0.4)], "signups from one network in 10 min")]
            if corroborated:
                nets.append(S.count_signal("network_day", st.count(f"net:{net}", 86400, now) + 1,
                                           [(4, 0.15), (10, 0.4)], "signups from one network today"))
            for ns in nets:
                if ns is not None:
                    soft_ids.add(id(ns))
                    crowd_ids.add(id(ns))
                    sigs.append(ns)
        keep = 1.0
        for s in sigs:
            sc = s.score if isinstance(s.score, (int, float)) and not math.isnan(s.score) else 0.0
            keep *= 1.0 - max(0.0, min(1.0, sc))
        score = 1.0 - keep
        action = BLOCK if score >= self.block_at else REVIEW if score >= self.review_at else ALLOW
        firing = [s for s in sigs if s.score > 0]
        if action == BLOCK and firing and all(_is_soft(s, soft_ids) for s in firing):
            action = REVIEW  # a shared device and a busy network look like a household or a campus: never a block alone
        if action == REVIEW and firing and all(s.name == "domain_burst" and id(s) in builtin_ids for s in firing):
            action = ALLOW  # many colleagues on one company domain is how a company signs up; a burst needs a second signal
        if action == ALLOW and any(s.name == "owner_after_aliases" and id(s) in owner_ids for s in sigs):
            action = REVIEW  # the inbox already has an alias account: the owner gets partial credit, not a second full grant
        if (action == ALLOW and net and self.network_daily_cap is not None
                and st.count(f"netfull:{net}", 86400, now) >= self.network_daily_cap):
            # A slow farm that rotates IPs inside one /24 never trips a burst rule. Honest networks stay far below this
            # in a day; past it the signup gets partial credit (review), never a block.
            cap_sig = S.Signal("network_daily_cap", 0.0, f"network_daily_cap: {self.network_daily_cap} full grants from this network in 24h already; partial credit")
            sigs.append(cap_sig)
            crowd_ids.add(id(cap_sig))
            action = REVIEW
        sigs.sort(key=lambda s: -s.score)
        decision = Decision(action, score, [s.reason for s in sigs], sigs, self.credit_fractions[action])
        if not verified and decision.credit_fraction > 0:
            if self.surge_threshold is not None and st.count("surge:released", 3600, now) >= self.surge_threshold:
                decision.credit_fraction, decision.limit = 0.0, "surge guard: verify a phone or card to unlock"
            elif self.starter_fraction is not None and decision.credit_fraction > self.starter_fraction:
                decision.credit_fraction, decision.limit = self.starter_fraction, "starter budget until verified"
        earn = None
        if action == REVIEW:
            # Proof that costs a farmer more than a real person: an honest crowd (every firing signal is one the crowd rules
            # made, by identity) needs only a verified email; anything else, and any decision held under a starter budget or
            # surge guard, needs a verified phone or card.
            firing = [s for s in sigs if s.score > 0 or id(s) in owner_ids]
            requires = "email" if decision.limit is None and all(id(s) in crowd_ids for s in firing) else "phone_or_card"
            earn = {"requires": requires, "reason": EARN_REASONS[requires]}
            subject = ("e:" + norm) if norm else ("d:" + dev) if dev else ("i:" + ip) if ip else None
            decision._earn = {"key": subject if subject is not None else f"n:{now}:{id(decision)}", "links": signup.links, "norm": norm, "dev": dev,
                              "blocked": self._blocked_count(norm, dev, now), "subject": self._blocked_subject(norm, dev)}
        if self.shadow:
            decision.shadow_action, decision.would_credit_fraction, decision.would_limit = decision.action, decision.credit_fraction, decision.limit
            decision.action, decision.credit_fraction, decision.limit = ALLOW, self.credit_fractions[ALLOW], None
            decision.would_earn_back = earn
        else:
            decision.earn_back = earn

        if record:
            if not verified and decision.credit_fraction > 0 and decision.shadow_action is None:
                st.add("surge:released", ts=now)
            for k in links:
                st.add(k, ts=now)
            if action == BLOCK:  # remembered so an earn_back for an earlier signup of this email or device is refused
                e_tok, d_tok = self._blocked_subject(norm, dev)
                if e_tok:
                    st.add(f"blocked:e:{e_tok}", ts=now)
                if d_tok:
                    st.add(f"blocked:d:{d_tok}", ts=now)
            if net:
                st.add(f"net:{net}", ts=now)
                if action == ALLOW:  # a full grant (the pre-shadow action): what the daily cap counts
                    st.add(f"netfull:{net}", ts=now)
            if norm:  # only whether the address carried an alias tag, never the raw address
                st.add(f"emailform:{norm}", "plain" if S.is_plain(signup.email, norm) else "alias", ts=now)
            if norm:
                st.add(f"email:{norm}", ts=now)
            if ip:
                st.add(f"ip:{ip}", ts=now)
                if PROXY_MODE != "off":
                    P.record(st, now, ip, signup.user_agent, device_id=dev, blind=blind)
                if stem:
                    st.add(f"ipstem:{ip}:{stem}", norm, ts=now)
            if dev and norm:
                st.add(f"devemail:{dev}", norm, ts=now)
            if dev and net:
                st.add(f"devnet:{dev}", net, ts=now)
            if norm and S.email_domain(norm):
                D.record_domain(st, now, S.email_domain(norm), norm, blind=blind, net=net)
        return decision
