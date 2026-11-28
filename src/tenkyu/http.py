"""Framework-agnostic helpers to build a Signup from an HTTP request.

The hard part is the client IP. X-Forwarded-For is attacker-controlled unless you
know how many proxies you run, so you must say so: trusted_proxies=0 (default)
ignores the header entirely and uses the socket address.
"""
from __future__ import annotations

import ipaddress
from typing import Mapping, Optional

from .guard import Signup
from .signals import host_of as _host_of


def header(headers, name: str) -> Optional[str]:
    """Case-insensitive header lookup that works with dicts, Flask/Starlette headers, or None."""
    if not headers:
        return None
    try:
        v = headers.get(name)
        if v is None:
            lname = name.lower()
            for k, val in headers.items():
                if isinstance(k, str) and k.lower() == lname:
                    v = val
                    break
    except Exception:
        return None
    return v.strip() if isinstance(v, str) and v.strip() else None


def _norm_ip(raw) -> Optional[str]:
    if not isinstance(raw, str):
        return None
    host = _host_of(raw)
    if host is None:
        return None
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        return None


def _split_quoted(text: str, sep: str):
    """Split on `sep` outside double quotes, in one pass (RFC 7239 values may be quoted strings)."""
    out, start, quoted, escaped = [], 0, False, False
    for i, ch in enumerate(text):
        if escaped:
            escaped = False
        elif quoted and ch == "\\":
            escaped = True
        elif ch == '"':
            quoted = not quoted
        elif ch == sep and not quoted:
            out.append(text[start:i])
            start = i + 1
    out.append(text[start:])
    return out


def _forwarded_for(element: str) -> Optional[str]:
    """The `for=` value of one Forwarded element, unquoted, or None when it has none or is malformed."""
    for pair in _split_quoted(element, ";"):
        name, eq, value = pair.partition("=")
        if eq and name.strip().lower() == "for":
            value = value.strip()
            if value.startswith('"'):
                if len(value) < 2 or not value.endswith('"'):
                    return None
                value = value[1:-1]
            return value
    return None


def _forwarded_hops(value: str):
    """One entry per proxy hop from a Forwarded header: the claimed client address or None."""
    return [_norm_ip(_forwarded_for(e)) for e in _split_quoted(value, ",") if e.strip()]


def client_ip(headers: Optional[Mapping], remote_addr: Optional[str], trusted_proxies: int = 0) -> Optional[str]:
    """Best-effort real client IP.

    trusted_proxies = number of reverse proxies YOU control in front of the app. Each appends
    the address it saw to X-Forwarded-For, so the real client is the Nth entry from the right.
    If there is no X-Forwarded-For, the standard Forwarded header (`for=`) is read the same way.
    0 means: never trust either header.
    """
    if not isinstance(trusted_proxies, int) or isinstance(trusted_proxies, bool) or trusted_proxies < 0:
        raise ValueError("trusted_proxies must be an int >= 0")
    fallback = _norm_ip(remote_addr) or (remote_addr.strip() if isinstance(remote_addr, str) and remote_addr.strip() else None)
    if trusted_proxies == 0:
        return fallback
    xff = header(headers, "X-Forwarded-For")
    if xff:
        parts = [p.strip() for p in xff.split(",") if p.strip()]
        if len(parts) < trusted_proxies:
            return fallback
        return _norm_ip(parts[-trusted_proxies]) or fallback
    forwarded = header(headers, "Forwarded")  # RFC 7239; X-Forwarded-For wins when both are present
    if not forwarded:
        return fallback
    hops = _forwarded_hops(forwarded)
    if len(hops) < trusted_proxies:
        return fallback
    return hops[-trusted_proxies] or fallback


def signup_from_request(email, headers: Optional[Mapping] = None, remote_addr: Optional[str] = None,
                        trusted_proxies: int = 0, device_id: Optional[str] = None, ts: Optional[float] = None) -> Signup:
    """Build a Signup from request pieces. Pass a Fingerprint/Stytch/Castle visitor id as device_id."""
    return Signup(email=email, ip=client_ip(headers, remote_addr, trusted_proxies), device_id=device_id,
                  user_agent=header(headers, "User-Agent"), ts=ts)
