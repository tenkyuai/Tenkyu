"""Weak signals for rotating residential proxies. Stdlib only, no network, no paid IP data.

A residential-proxy farm hands the attacker a fresh IP for every request, so per-IP and per-/24 counters never see a
repeat. What does not rotate is the proxy provider's pool: the exits sit inside a few big ISP allocations (one /16 or
/32), they land in many different /24s (/48s), and the client behind them is one script, so the user-agent (and
language) never changes and the signups tend to arrive on a timer.

Three signals, all WEAK:

* ``wide_spread``   many signups in 24h from one /16 (IPv6: /32), spread over many distinct /24s (/48s), where most of
                    them share this signup's user-agent fingerprint. Honest ISP traffic has varied user agents.
* ``regular_timing`` the last signups in this /16 with this fingerprint arrive at near-constant intervals.
* ``narrow_cluster`` the opposite shape: a pool that is not spread but sits in one /24 (/48). Several signups in a few
                    hours from three or more different IPs of the same /24 (different households, so one script
                    behind a small residential proxy pool), all with this signup's fingerprint, and that /24 holds
                    most of what this /16 sent with this fingerprint. Honest traffic in one /24 is rare, mixed in
                    browsers, or (a big carrier) spread over many /24s of its /16.

Contract for the guard that uses them (see ``SOFT_SIGNALS``):

* They are SOFT. They must never block on their own and never move a decision past "review" on their own: a big ISP,
  a campus, or a phone carrier with one dominant browser (Chrome's user agent is frozen, so many honest users send the
  same string) can look like this.
* They must not switch on other rules (they are not "another risk signal" that makes network rules stricter) and
  should only count towards the score together with at least one other, non-soft, firing signal. When nothing else
  fires, drop them (or log them in shadow mode).

Call ``signals()`` BEFORE ``record()`` for the same signup; ``signals()`` counts this signup itself on top of the history.

Store layout (all keys are blinded by BlindStore, so nothing raw is kept; the values are subnets and hashes):

    wide:{prefix}              value = the /24 (/48) of the signup                        24h count of the whole /16
    wideua:{prefix}:{fp}       value = the /24 (/48)                                      same-fingerprint signups + their subnets
    widetime:{prefix}:{fp}     no value; event timestamps are read back with count() windows, because a blinded store
                               cannot return them as values. At most 2000 events are kept per key by the store.
"""
from __future__ import annotations

import hashlib
import ipaddress
import math
import re
import statistics
import unicodedata
from typing import Callable, List, Optional, Tuple

from .signals import Signal

SOFT_SIGNALS = frozenset({"wide_spread", "regular_timing", "narrow_cluster"})

WINDOW = 86400.0              # wide_spread looks at the last 24 hours
MIN_SIGNUPS = 10              # signups with this fingerprint in the /16 (this one included)
SPREAD_LOW, SPREAD_HIGH = 8, 15   # distinct /24s (/48s) among those signups
SPREAD_SCORES = (0.35, 0.5)
MIN_SAME_UA_SHARE = 0.6       # share of the /16's signups that carry this fingerprint

TIMING_MIN_SIGNUPS = 6        # this signup plus the 5 before it -> 5 intervals
TIMING_WINDOW = 12 * 3600.0   # the last N signups must all be inside this window
TIMING_MAX_CV = 0.15          # coefficient of variation of the intervals
TIMING_MIN_MEAN = 5.0         # seconds; a simultaneous burst is the burst rules' job, not this one
TIMING_SCORE = 0.4
NARROW_WINDOW = 6 * 3600.0    # narrow_cluster looks at the last 6 hours
NARROW_MIN_IPS = 3            # distinct IPs of the /24 among the same-fingerprint signups (this one included)
NARROW_MIN_SHARE = 0.7        # same-fingerprint share of the /24's signups in the window
NARROW_MIN_CONCENTRATION = 0.6  # share of the /16's same-fingerprint signups that sit in this one /24
NARROW_SCORE = 0.5            # corroborated only (see the guard): alone it would review offices and campuses
_RESOLUTION = 1.0             # seconds, precision of the timestamps recovered from count() windows

_MAX_UA = 1024


def _address(ip_key) -> Optional[Tuple[int, ipaddress._BaseAddress, int]]:
    """(version, address, prefix length known) for an IP as Tenkyu keys it: a plain IPv4 address, an IPv4-mapped IPv6
    address, an IPv6 address or an IPv6 network such as the /64 `ip_key()` produces. None for anything else."""
    if not isinstance(ip_key, str):
        return None
    raw = ip_key.strip()
    if not raw or len(raw) > 64:
        return None
    try:
        a4 = ipaddress.IPv4Address(raw)
        return 4, a4, 32
    except ValueError:
        pass
    try:
        net = ipaddress.IPv6Network(raw, strict=False)
    except ValueError:
        return None
    mapped = net.network_address.ipv4_mapped
    if mapped is not None and net.prefixlen == 128:
        return 4, mapped, 32
    return 6, net.network_address, net.prefixlen


def _supernet(ip_key, v4_bits: int, v6_bits: int) -> Optional[str]:
    parsed = _address(ip_key)
    if parsed is None:
        return None
    version, addr, known = parsed
    bits = v4_bits if version == 4 else v6_bits
    if known < bits:  # a key that is itself wider than the group (a /16 passed in) says nothing about the group
        return None
    return str(ipaddress.ip_network((addr, bits), strict=False))


def wide_prefix(ip_key: str) -> Optional[str]:
    """The /16 of an IPv4 key or the /32 of an IPv6 key ("45.12.0.0/16", "2001:db8::/32"). None for input that is not
    an IP address (opaque ids, hashed strings, None, numbers)."""
    return _supernet(ip_key, 16, 32)


def _subnet(ip_key) -> Optional[str]:
    """The /24 of an IPv4 key or the /48 of an IPv6 key: the unit whose spread inside the wide prefix is measured."""
    return _supernet(ip_key, 24, 48)


_VERSION_DOTS = re.compile(r"(\d+)(?:\.\d+)+")
_VERSION_UNDERSCORES = re.compile(r"(\d+)(?:_\d+)+")


def _major_versions(text: str) -> str:
    """Chrome/120.0.6099.1 -> Chrome/120, Mac OS X 10_15_7 -> 10: rotating minor or build numbers must not split a fingerprint."""
    return _VERSION_UNDERSCORES.sub(r"\1", _VERSION_DOTS.sub(r"\1", text))


def _fingerprint(ua, accept_language=None) -> Optional[str]:
    """Hash of the normalized user agent (case, whitespace) plus the language when given. None when there is no usable
    user agent: a missing one is not evidence of anything and must not make all UA-less signups look identical."""
    if not isinstance(ua, str):
        return None
    text = _major_versions(" ".join(unicodedata.normalize("NFKC", ua[:_MAX_UA]).lower().split()))
    if not text:
        return None
    lang = ""
    if isinstance(accept_language, str):
        lang = " ".join(accept_language[:_MAX_UA].lower().split())
    data = (text + "\0" + lang).encode("utf-8", "surrogatepass")
    return hashlib.sha256(data).hexdigest()[:16]


def _usable_time(ts) -> Optional[float]:
    if isinstance(ts, bool):
        return None
    try:
        t = float(ts)
    except (TypeError, ValueError, OverflowError):
        return None
    return t if math.isfinite(t) else None


def _keys(prefix: str, fp: str) -> Tuple[str, str, str]:
    return f"wide:{prefix}", f"wideua:{prefix}:{fp}", f"widetime:{prefix}:{fp}"


def _narrow_keys(subnet: str, fp: str) -> Tuple[str, str]:
    return f"narrow:{subnet}", f"narrowua:{subnet}:{fp}"


def record(store, ts, ip_key: str, ua, accept_language=None, device_id=None,
           blind: Callable = lambda v: v) -> None:
    """Remember this signup for the next calls to `signals()`. Safe to call with anything: input it cannot use is
    ignored. `blind` and `device_id` are accepted for call symmetry with `signals()`; values are written raw because
    the store itself blinds what it keeps (BlindStore, PostgresStore(key_secret=...))."""
    t = _usable_time(ts)
    prefix, subnet, fp = wide_prefix(ip_key), _subnet(ip_key), _fingerprint(ua, accept_language)
    if t is None or prefix is None or subnet is None or fp is None:
        return
    wide, wideua, wtime = _keys(prefix, fp)
    store.add(wide, subnet, ts=t)
    store.add(wideua, subnet, ts=t)
    store.add(wtime, None, ts=t)
    narrow, narrowua = _narrow_keys(subnet, fp)
    store.add(narrow, str(ip_key).strip(), ts=t)
    store.add(narrowua, str(ip_key).strip(), ts=t)


def _recent_times(store, key: str, now: float, k: int) -> Optional[List[float]]:
    """Timestamps (newest first, to within _RESOLUTION) of the `k` most recent events under `key`, found with count()
    windows only, so it works on a blinded store that cannot hand timestamps back. None if fewer than k exist."""
    if store.count(key, TIMING_WINDOW, now) < k:
        return None
    out: List[float] = []
    lo = 0.0
    for need in range(1, k + 1):
        hi = TIMING_WINDOW
        while hi - lo > _RESOLUTION:
            mid = (lo + hi) / 2
            if store.count(key, mid, now) >= need:
                hi = mid
            else:
                lo = mid
        out.append(now - hi)
        lo = hi - _RESOLUTION if hi - _RESOLUTION > 0 else 0.0
    return out


def _wide_spread(store, now, prefix, subnet, fp, blind) -> Optional[Signal]:
    wide, wideua, _ = _keys(prefix, fp)
    total = store.count(wide, WINDOW, now) + 1
    same = store.count(wideua, WINDOW, now) + 1
    if same < MIN_SIGNUPS or same / total < MIN_SAME_UA_SHARE:
        return None
    subnets = len(store.values(wideua, WINDOW, now) | {blind(subnet)})
    if subnets < SPREAD_LOW:
        return None
    score = SPREAD_SCORES[1] if subnets >= SPREAD_HIGH else SPREAD_SCORES[0]
    return Signal("wide_spread", score,
                  f"{same} of {total} signups in 24h from {prefix} share this client and come from {subnets} different "
                  f"subnets (rotating proxy pool?); weak signal")


def _regular_timing(store, now, prefix, fp) -> Optional[Signal]:
    _, _, wtime = _keys(prefix, fp)
    prior = _recent_times(store, wtime, now, TIMING_MIN_SIGNUPS - 1)
    if prior is None:
        return None
    times = [now] + prior  # newest first
    gaps = [a - b for a, b in zip(times, times[1:])]
    mean = sum(gaps) / len(gaps)
    if mean < TIMING_MIN_MEAN:
        return None
    cv = statistics.pstdev(gaps) / mean
    if cv >= TIMING_MAX_CV:
        return None
    return Signal("regular_timing", TIMING_SCORE,
                  f"last {len(times)} signups from {prefix} with this client arrive every {mean:.0f}s "
                  f"(variation {cv:.2f}): a timer, not people; weak signal")


def _narrow_cluster(store, now, prefix, subnet, fp, ip_key, blind) -> Optional[Signal]:
    narrow, narrowua = _narrow_keys(subnet, fp)
    same = store.count(narrowua, NARROW_WINDOW, now) + 1
    if same < NARROW_MIN_IPS:
        return None
    ips = store.values(narrowua, NARROW_WINDOW, now) | {blind(str(ip_key).strip())}
    if len(ips) < NARROW_MIN_IPS:
        return None
    total = store.count(narrow, NARROW_WINDOW, now) + 1
    if same / total < NARROW_MIN_SHARE:
        return None
    wide_same = store.count(_keys(prefix, fp)[1], NARROW_WINDOW, now) + 1
    if same / max(wide_same, same) < NARROW_MIN_CONCENTRATION:
        return None
    return Signal("narrow_cluster", NARROW_SCORE,
                  f"{same} signups in {NARROW_WINDOW / 3600:.0f}h from {len(ips)} different IPs of {subnet} share this client "
                  f"and sit mostly in this one subnet (small proxy pool?); weak signal")


def signals(store, now, ip_key: str, ua, device_id=None, blind: Callable = lambda v: v,
            accept_language=None) -> List[Signal]:
    """Weak rotating-proxy signals for the signup about to be recorded. Call BEFORE `record()` for it. Returns a list
    (possibly empty); never raises on malformed input. Treat every result as soft (see the module docstring).

    `blind` is `store.blind` (identity for a plain store): the signup's own /24 is compared with the blinded
    values the store returns. `device_id` is accepted for symmetry and not used. `accept_language` must be passed
    the same way to `record()` and here."""
    t = _usable_time(now)
    prefix, subnet, fp = wide_prefix(ip_key), _subnet(ip_key), _fingerprint(ua, accept_language)
    if t is None or prefix is None or subnet is None or fp is None:
        return []
    out: List[Signal] = []
    for make in (lambda: _wide_spread(store, t, prefix, subnet, fp, blind),
                 lambda: _regular_timing(store, t, prefix, fp),
                 lambda: _narrow_cluster(store, t, prefix, subnet, fp, ip_key, blind)):
        try:
            sig = make()
        except Exception:  # a weak signal must never break signup
            sig = None
        if sig is not None:
            out.append(sig)
    return out
