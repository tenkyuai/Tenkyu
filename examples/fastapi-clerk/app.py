"""Clerk signup webhook, scored by Tenkyu. Run: uvicorn app:app   (from this folder)

Needs `pip install tenkyu fastapi uvicorn` (FastAPI is not a Tenkyu dependency).
Set CLERK_WEBHOOK_SECRET to the signing secret (starts with whsec_) of your Clerk webhook endpoint.
In the Clerk dashboard, subscribe the endpoint to the user.created event.

What this does with each delivery:
  1. Reads the raw body and checks the svix-* signature.  Bad signature: 400.
  2. Ignores every event that is not user.created.        Answer 200 so Clerk stops sending it.
  3. Scores the new user with Tenkyu.
  4. Grants int(GRANT * decision.credit_fraction) credits in YOUR database.
  5. Answers 200 only once the grant is saved. Any crash before that is a 500, and Clerk retries.

Webhooks carry no IP address and no device id. Only the email signals (and phone/OAuth links) work here.
For IP and device signals, call Guard from your own signup endpoint instead (examples/fastapi_app.py).
"""
import json
import os
import sqlite3

from fastapi import FastAPI, Request, Response

from tenkyu import Guard, SQLiteStore
from tenkyu.webhooks import WebhookError, signup_from_clerk, verify_webhook

GRANT = 500  # your free grant, in credits
SECRET = os.environ["CLERK_WEBHOOK_SECRET"]  # whsec_...

app = FastAPI()
# On disk: the history survives restarts and is shared by workers on one machine.
# Several machines: use PostgresStore instead (pip install "tenkyu[postgres]").
guard = Guard(store=SQLiteStore(os.environ.get("TENKYU_DB", "tenkyu.db")))

# Stand-in for YOUR database. Replace the three functions below with your own tables.
_db = sqlite3.connect(os.environ.get("APP_DB", "app.db"), check_same_thread=False)
_db.execute("CREATE TABLE IF NOT EXISTS grants (svix_id TEXT PRIMARY KEY, user_id TEXT, credits INTEGER)")


def already_handled(svix_id: str) -> bool:
    return _db.execute("SELECT 1 FROM grants WHERE svix_id = ?", (svix_id,)).fetchone() is not None


def grant_credits(svix_id: str, user_id: str, credits: int) -> None:
    """The granting step. Do your real write here, e.g. UPDATE users SET credits = credits + ? WHERE clerk_id = ?.
    Keep it safe to run twice for one svix_id: Clerk can deliver an event more than once.
    A decision of 0 credits (block) is still stored, so a retry does not score the user again."""
    with _db:
        _db.execute("INSERT OR IGNORE INTO grants VALUES (?, ?, ?)", (svix_id, user_id, credits))


@app.post("/webhooks/clerk")
async def clerk_webhook(request: Request):
    body = await request.body()  # the raw bytes: the signature covers them exactly, so do not re-serialize
    try:
        verify_webhook(SECRET, request.headers, body)  # reads svix-id, svix-timestamp, svix-signature
        event = json.loads(body)
    except (WebhookError, ValueError):
        return Response(status_code=400)  # not 2xx: rejected. A forged request is dropped; a real one is retried
    signup = signup_from_clerk(event)  # None unless this is a user.created event
    if signup is None:
        return Response(status_code=200)  # not ours to handle; 2xx so Clerk does not retry it
    svix_id = request.headers["svix-id"]
    if already_handled(svix_id):
        return Response(status_code=200)  # a repeat delivery; the grant is already saved
    decision = guard.check(signup)  # an exception here (e.g. database locked) is a 500: Clerk retries
    grant_credits(svix_id, event["data"]["id"], int(GRANT * decision.credit_fraction))  # a failure here is a 500 too
    return Response(status_code=200)  # 2xx only now: tells Clerk "done, do not retry"
