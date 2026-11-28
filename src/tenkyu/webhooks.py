"""Signup webhooks from auth providers: verify the signature, then build a Signup.

Clerk signs webhooks with Svix and Supabase Auth hooks follow the Standard Webhooks spec; both use the same
scheme: HMAC-SHA256 over "<id>.<timestamp>.<body>" with a base64 secret prefixed "whsec_". Stdlib only.

Webhooks fire after the account exists, so use the decision to grant or hold free credits (credit_fraction),
not to refuse the signup. Neither payload carries the visitor's IP; pass it in `links` or score at your own
signup endpoint if you need IP signals."""
from __future__ import annotations

import base64
import hashlib
import hmac
import time
from typing import Mapping, Optional

from .guard import Signup
from .http import header

TOLERANCE_S = 300


class WebhookError(ValueError):
    """The webhook could not be verified or read. Answer 400 and do not grant credits."""


def _secret_bytes(secret: str) -> bytes:
    s = secret.strip()
    for prefix in ("v1,whsec_", "whsec_"):
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    try:
        return base64.b64decode(s, validate=True)
    except Exception:
        raise WebhookError("webhook secret is not valid base64 (copy it from your provider's dashboard)") from None


def verify_webhook(secret: str, headers: Mapping, body: bytes, now: Optional[float] = None, tolerance: int = TOLERANCE_S) -> None:
    """Raise WebhookError unless the request carries a valid, fresh signature. Accepts svix-* and webhook-* headers."""
    if isinstance(body, str):
        body = body.encode("utf-8")
    msg_id = header(headers, "svix-id") or header(headers, "webhook-id")
    ts = header(headers, "svix-timestamp") or header(headers, "webhook-timestamp")
    sigs = header(headers, "svix-signature") or header(headers, "webhook-signature")
    if not (msg_id and ts and sigs):
        raise WebhookError("missing webhook id, timestamp or signature header")
    try:
        ts_i = int(ts)
    except ValueError:
        raise WebhookError("webhook timestamp is not an integer") from None
    now = time.time() if now is None else now
    if abs(now - ts_i) > tolerance:
        raise WebhookError("webhook timestamp is too old or in the future")
    expected = base64.b64encode(hmac.new(_secret_bytes(secret), f"{msg_id}.{ts_i}.".encode() + body, hashlib.sha256).digest()).decode()
    for part in sigs.split():
        version, _, sig = part.partition(",")
        if version == "v1" and hmac.compare_digest(sig, expected):
            return
    raise WebhookError("webhook signature does not match")


def signup_from_clerk(event: Mapping) -> Optional[Signup]:
    """A Signup from Clerk's `user.created` event, or None for any other event type."""
    if not isinstance(event, Mapping) or event.get("type") != "user.created":
        return None
    data = event.get("data") or {}
    emails = data.get("email_addresses") or []
    primary = data.get("primary_email_address_id")
    email = next((e.get("email_address") for e in emails if e.get("id") == primary), None) or (emails[0].get("email_address") if emails else None)
    created = data.get("created_at")
    links = {}
    phones = [p for p in data.get("phone_numbers") or [] if (p.get("verification") or {}).get("status") == "verified"]
    if phones and phones[0].get("phone_number"):
        links["phone"] = phones[0]["phone_number"]
    for acct in data.get("external_accounts") or []:
        if acct.get("provider") and acct.get("provider_user_id"):
            name = "".join(c for c in str(acct["provider"]).lower().replace("oauth_", "") if c.isalnum())[:20] or "oauth"
            links.setdefault("o" + name if name[0].isdigit() else name, str(acct["provider_user_id"]))
    return Signup(email=email, ts=created / 1000.0 if isinstance(created, (int, float)) else None, links=links or None)


def signup_from_supabase(payload: Mapping) -> Optional[Signup]:
    """A Signup from a Supabase database webhook on INSERT into auth.users, or None for anything else."""
    if not isinstance(payload, Mapping) or payload.get("type") != "INSERT" or payload.get("table") != "users":
        return None
    rec = payload.get("record") or {}
    from .audit import parse_ts
    links = {}
    if rec.get("phone") and rec.get("phone_confirmed_at"):
        links["phone"] = rec["phone"]
    return Signup(email=rec.get("email"), ts=parse_ts(rec.get("created_at")), links=links or None)
