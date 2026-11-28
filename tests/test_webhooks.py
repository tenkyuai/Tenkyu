"""Clerk (Svix) and Supabase (Standard Webhooks) signup webhooks."""
import base64
import hashlib
import hmac
import json

import pytest

from tenkyu import Guard
from tenkyu.webhooks import WebhookError, signup_from_clerk, signup_from_supabase, verify_webhook

SECRET = "whsec_" + base64.b64encode(b"tenkyu-test-secret-32-bytes-long").decode()
NOW = 1_700_000_000


def _sign(body, msg_id="msg_1", ts=NOW, secret=SECRET, prefix="svix"):
    key = base64.b64decode(secret.split("_", 1)[1])
    sig = base64.b64encode(hmac.new(key, f"{msg_id}.{ts}.".encode() + body, hashlib.sha256).digest()).decode()
    return {f"{prefix}-id": msg_id, f"{prefix}-timestamp": str(ts), f"{prefix}-signature": f"v1,{sig}"}


def test_standard_webhooks_spec_vector():
    # Published example from the Standard Webhooks / Svix docs.
    verify_webhook("whsec_MfKQ9r8GKYqrTwjUPD8ILPZIo2LaLaSw",
                   {"webhook-id": "msg_p5jXN8AQM9LWM0D4loKWxJek", "webhook-timestamp": "1614265330",
                    "webhook-signature": "v1,g0hM9SsE+OTPJTGt/tmIKtSyZlE3uFJELVlNIOLJ1OE="},
                   b'{"test": 2432232314}', now=1614265330)


@pytest.mark.parametrize("prefix", ["svix", "webhook"])
def test_valid_signature_passes(prefix):
    body = b'{"type":"user.created"}'
    verify_webhook(SECRET, _sign(body, prefix=prefix), body, now=NOW)
    verify_webhook("v1," + SECRET, _sign(body, prefix=prefix), body, now=NOW)  # Supabase shows the secret with v1,


def test_multiple_signatures_any_match():
    body = b"{}"
    h = _sign(body)
    h["svix-signature"] = "v1,AAAA " + h["svix-signature"]
    verify_webhook(SECRET, h, body, now=NOW)


@pytest.mark.parametrize("mutate,msg", [
    (lambda h, b: (h, b + b" "), "does not match"),
    (lambda h, b: ({**h, "svix-timestamp": str(NOW - 301)}, b), "too old"),
    (lambda h, b: ({**h, "svix-timestamp": "x"}, b), "not an integer"),
    (lambda h, b: ({k: v for k, v in h.items() if k != "svix-signature"}, b), "missing"),
    (lambda h, b: ({**h, "svix-signature": h["svix-signature"].replace("v1,", "v2,")}, b), "does not match"),
])
def test_bad_requests_rejected(mutate, msg):
    body = b'{"a":1}'
    h, b = mutate(_sign(body), body)
    with pytest.raises(WebhookError, match=msg):
        verify_webhook(SECRET, h, b, now=NOW)


def test_bad_secret_rejected():
    with pytest.raises(WebhookError, match="base64"):
        verify_webhook("whsec_not base64!!", _sign(b"{}"), b"{}", now=NOW)


CLERK = {"type": "user.created", "data": {
    "id": "user_1", "created_at": 1_700_000_000_000, "primary_email_address_id": "e2",
    "email_addresses": [{"id": "e1", "email_address": "old@x.com"}, {"id": "e2", "email_address": "j.o.h.n+2@gmail.com"}],
    "phone_numbers": [{"phone_number": "+972500000001", "verification": {"status": "verified"}}],
    "external_accounts": [{"provider": "oauth_google", "provider_user_id": "g-123"}]}}


def test_clerk_user_created_to_signup():
    su = signup_from_clerk(CLERK)
    assert su.email == "j.o.h.n+2@gmail.com" and su.ts == 1_700_000_000.0
    assert su.links == {"phone": "+972500000001", "google": "g-123"}
    assert signup_from_clerk({"type": "session.created", "data": {}}) is None


def test_clerk_end_to_end_alias_blocked():
    g = Guard(clock=lambda: NOW)
    first = json.loads(json.dumps(CLERK))
    first["data"]["email_addresses"][1]["email_address"] = "john@gmail.com"
    first["data"]["phone_numbers"] = []
    first["data"]["external_accounts"] = []
    g.check(signup_from_clerk(first))
    body = json.dumps(CLERK).encode()
    verify_webhook(SECRET, _sign(body), body, now=NOW)
    d = g.check(signup_from_clerk(json.loads(body)))
    assert d.action == "block"


def test_supabase_insert_to_signup():
    payload = {"type": "INSERT", "table": "users", "schema": "auth",
               "record": {"id": "u1", "email": "ana@outlook.com", "created_at": "2023-11-14T22:13:20.123+00:00", "phone": "+97250", "phone_confirmed_at": "2023-11-14T22:14:00Z"}}
    su = signup_from_supabase(payload)
    assert su.email == "ana@outlook.com" and abs(su.ts - 1_700_000_000.123) < 1e-3 and su.links == {"phone": "+97250"}
    assert signup_from_supabase({**payload, "type": "UPDATE"}) is None
    assert signup_from_supabase({"type": "INSERT", "table": "profiles", "record": {}}) is None


def test_adapters_survive_junk():
    for junk in [None, [], "x", {"type": "user.created"}, {"type": "user.created", "data": {"email_addresses": [{}]}}]:
        su = signup_from_clerk(junk)
        if su is not None:
            Guard(clock=lambda: NOW).check(su)
    assert signup_from_supabase(None) is None
