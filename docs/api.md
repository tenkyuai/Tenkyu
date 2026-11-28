# Tenkyu hosted API, v1 contract

Base URL: `https://api.tenkyu.dev` (planned). JSON in and out. UTF-8. Max body 16 KB.

Auth: `Authorization: Bearer <key>`. Keys look like `tk_live_<43 chars>` or `tk_test_<43 chars>`
(32 random bytes, base64url). Only the SHA-256 of a key is stored. A key is shown once, at creation.

## POST /v1/check
Request:
```json
{"email": "maya@gmail.com", "ip": "203.0.113.7", "device_id": "fp_abc", "user_agent": "Mozilla/5.0 ...",
 "ts": 1764190000, "links": {"phone": "+14155550100", "card": "card_fingerprint"}, "request_id": "optional-idempotency-key"}
```
All fields optional except that at least one of email, ip, device_id must be present. `ts` defaults to server time;
a `ts` more than 300 s from server time is ignored (server time is used and the reasons say `client timestamp
ignored`); a `ts` that is not a finite number is a 400. Strings must not contain NUL or lone surrogates (400).
`request_id` makes the call idempotent for 24 h (same answer, charged once). Reusing a `request_id` with a different
body (everything except `request_id` and `ts`) inside that window is a 400 `invalid_request`
("request_id reused with a different body").

Response 200:
```json
{"id": "dec_...", "action": "allow|review|block", "score": 0.12, "credit_fraction": 1.0,
 "reasons": ["..."], "signals": [{"name": "alias_reuse", "score": 0.9}],
 "network_match": false, "credits_exhausted": false, "mode": "live|test"}
```
- `reasons`: fixed phrases, one per signal. They never repeat anything from the request (no domain, no link name).
- `network_match`: true only when the shared-network signal actually fired for this signup (see "Shared network" below).
  It is true only when two or more other customers reported an identity (three for an email or device), with the one
  generic signal `network_match` and the one reason "seen as abusive by other apps". A single report is never visible in
  any field (it only nudges the decision internally), and only a customer that meets the reporting thresholds (below) gets
  network lookups at all, so the response cannot be used to probe whether an email, phone or card was reported.
- `earn_back` (additive, present only on a `review`): `{"requires": "email|phone_or_card", "reason": "..."}`, the proof that
  releases the rest of the grant through `POST /v1/earn-back` (below). In shadow mode the same object is `would_earn_back`.
- `shadow`, `would_action`, `would_credit_fraction` (additive, present only for a LIVE key while the customer is in shadow
  mode, see "Shadow mode" below): `action` is then `allow` and `credit_fraction` is 1.0 whatever the engine decided, and the
  decision Tenkyu would have made is in `would_action` (`allow|review|block`) and `would_credit_fraction`. `reasons` then also
  ends with one fixed line, `shadow mode: allowed; would have been <action>`, so logs show it. Test keys are never shadowed.
- `degraded` (additive, present only when true): the service could not REACH its database (connection refused or lost,
  pool cannot connect, migration failing), so the decision came from
  the instance's own in-memory engine for this customer. It is not stored, not charged, has no network match, and its
  `id` cannot take an outcome. `mode` is unchanged.
- `credits_exhausted`: true when the account has no credits left; the decision then comes from the local engine
  for this customer only (no shared network), and the call is not charged. The endpoint never returns an error
  for lack of credits, so a customer's signup page never breaks.

## Shadow mode
Every new customer starts in shadow mode for 7 days (`customers.shadow_until` = creation time + 7 days). Customers that existed
before shadow mode was introduced are not shadowed. While `shadow_until` is in the future, `/v1/check` runs the full engine,
stores the decision, and charges, exactly as it does otherwise, but a live key is answered:
```json
{"id": "dec_...", "action": "allow", "credit_fraction": 1.0, "shadow": true, "would_action": "block", "would_credit_fraction": 0.0, "...": "..."}
```
Test keys are not shadowed: a test-key check always returns the real action and fraction, so you can see enforcement before
you go live. `score` and `signals` are the engine's own, and `reasons` are too plus the one shadow line described above
(it is not stored). Grant by `credit_fraction` as usual (it is 1.0), and nothing else changes in
the integration; when shadow mode ends the same code starts receiving the real action and fraction, with no `shadow` fields.
- The stored decision (Decisions, Review queue) holds the would-be action and fraction, flagged `shadow`; a replayed `request_id`
  returns the same shadow answer. The dashboard Overview shows the free credit Tenkyu would have withheld while shadowed, and counts
  only real decisions once live.
- The shared network is unchanged: shadowed decisions are real observations and are matched like any other, and outcomes are shared
  only under the usual reporting rules (older than 7 days, 50 live checks), which a customer meets as shadow mode ends.
- The customer ends shadow mode early or extends it in Settings (Shadow mode, a CSRF-protected form; an extension is 1 to 30 days and
  shadow mode never runs more than 30 days past today; a live customer cannot turn it back on). Operators:
  `python -m hosted.api.admin set-shadow --customer ID off|DAYS` (1 to 30 days from now), and
  `create-customer ... --no-shadow` to start live.
- While the database is unreachable, a customer last seen in shadow mode by that instance still gets the shadow answer.

## POST /v1/earn-back
Release the rest of a review decision's grant after your app verified something about the user.
`{"request_id": "...", "proof": "email_verified|phone_verified|card_verified", "links": {"phone": "+14155550100"}}`
-> `{"extra_credit_fraction": 0.75, "granted": true, "reason": "released"}`. Same auth as `/v1/check` (live and test keys), same
per-customer rate limit. `request_id` is the one you sent to `/v1/check`; it finds the decision, scoped to your customer and key
mode (anyone else's, or an unknown one, is a 404). `extra_credit_fraction` is what to ADD to the `credit_fraction` you already granted
(1 minus it; 0.75 for a plain review).
- `earn_back.requires` on the check answer says what is needed. `"email"`: every signal that fired is one an honest crowd produces (a
  school, carrier or family sharing one network or device), so `email_verified` is enough. `"phone_or_card"` (aliases, look-alike
  addresses, device farms, reused phone or card, disposable or headless signups, and anything else): only `phone_verified` or
  `card_verified` count, and `links` must carry the verified value (`{"phone": ...}` or `{"card": ...}`, from your own server-side
  state, never from the browser). A phone or card proof also records that value as a link, so a later signup with it is `link_reuse`. A
  phone or card that already released credits on 2 other accounts in 30 days releases nothing more.
- Idempotent: one release per decision. Asking again returns the same answer. `granted: false` (with `extra_credit_fraction: 0`
  and a fixed `reason`) when the proof does not satisfy the requirement, the decision had nothing to earn back (allow or block), or
  the decision was marked `farmer` since. Verify the proof yourself before you send it: Tenkyu trusts what your server says.
- Shadow mode (decision made while the customer was shadowed): nothing is released, because the full grant was already given. The
  answer is `{"extra_credit_fraction": 0, "granted": false, "shadow": true, "would_extra_credit_fraction": 0.75, "would_grant": true,
  "reason": "..."}`.
- Errors: 400 for a bad body, 401, 404, 429 as elsewhere.

## POST /v1/outcome
`{"id": "dec_...", "outcome": "farmer|real"}` -> `{"ok": true}`. One outcome per decision; a later call replaces it.
The id must look like `dec_` plus up to 60 of `A-Z a-z 0-9 _ -` (anything else is a 400; a well-formed id that is not
yours is a 404). Customers can opt out of sharing in settings.

## Shared network
`farmer` outcomes feed a network of HMAC'd identities (email, device id, `phone` and `card` links). Rules:
- A reporter must be older than 7 days, have at least 50 live checks, not have opted out, and add at most 200
  identity hashes per 24 h (env `NETWORK_MAX_REPORTS_PER_DAY`). Beyond that the outcome is accepted and not shared.
- Scoring counts distinct reporter customers, never the asking customer's own reports, and only reports from the last
  90 days. Email and device are weak (shared inboxes and devices): 1 reporter adds nothing, 2 score 0.4 (review),
  3 or more score 0.9 (block). Phone and card are strong: 2 or more score 0.9 (block); 1 reporter adds a small hidden
  amount to the score that can tip a borderline decision but is never shown. The strongest identity of a signup decides.
  Reports of a customer that opted out do not count, and opting out deletes its earlier reports and disputes.
- A decision the network itself flagged (`network_match: true`) cannot be reported as `farmer` (a `farmer` outcome on
  it is accepted and ignored), so one report cannot launder itself into two. Check a signup before it is reported by
  others if you want your report to count.
- `real` on a decision the network flagged records a dispute for the identities that carried other customers' reports
  (only from a customer that meets the reporting thresholds). A dispute only cancels reports made before it, and
  only once at least max(2, half the reporters) distinct customers disputed; disputes expire after 90 days. Changing that outcome back to `farmer` withdraws the dispute. `real` also takes back the customer's own
  earlier report of that decision.
- Rows are filtered at read after 90 days and deleted by housekeeping or `python -m hosted.api.admin prune-network`.

## GET /v1/usage
`{"plan": "free|prepaid", "period_start": "2026-11-01", "checks_this_period": 120, "free_remaining": 880, "balance": 0}`
`free_remaining` is this UTC month's free checks left (1,000 a month, granted on the first check of the month, not carried
over); `balance` is purchased checks left (they never expire). A live check spends a free check first, then a purchased one;
a replayed `request_id` costs nothing; test keys never spend. Both numbers come from the credit ledger (`hosted/BILLING.md`).

## POST /webhooks/lemonsqueezy
Called by the payment provider, not by customers: no API key; the HMAC-SHA256 signature of the raw body in `X-Signature` is the
credential (400 when it is missing or wrong). `order_created` with status `paid` credits the pack once per order id; any other
event answers 200 and is ignored. Details and the go-live caveat: `hosted/BILLING.md`.

## GET /healthz
`{"ok": true}` with no auth.

## Errors
`{"error": {"code": "unauthorized|invalid_request|rate_limited|not_found|server_error", "message": "..."}}`
Status 401, 400, 429 (with `Retry-After`), 404, 500. A body over 16 KB or a Content-Type other than
`application/json` is a 400 `invalid_request`; a wrong method is a 405 `invalid_request`. When the database is
unreachable, `/v1/outcome` and `/v1/usage` (and a `/v1/check` with a key this instance has not verified in the last
60 s) answer 503 `server_error` with `Retry-After`. Rate limits, per instance: before authentication 100 requests/second per client IP, burst 200 (env
`RATE_LIMIT_IP_RPS`, `RATE_LIMIT_IP_BURST`; the IP is the socket peer, or the first `X-Forwarded-For` hop only when
`TRUST_PROXY=1`; an IPv6 client counts as its /64); after authentication 20 requests/second per customer (all of its
keys share it), burst 40 (`RATE_LIMIT_RPS`, `RATE_LIMIT_BURST`); and at most 16 `/v1/check` calls of one customer at
once (`MAX_INFLIGHT_PER_CUSTOMER`, default 16; extra checks queue without holding a database connection). All three answer 429 `rate_limited` with `Retry-After`.
A made-up key never gets a bucket of its own, and is refused for 10 s without a database call after the first miss.

**Contention and slow databases.** One customer's checks are serialized (an in-process lock per instance, then a Postgres
advisory lock across instances), and waiting costs no pooled connection. A check that cannot get its turn within
`LOCK_TIMEOUT_SECONDS` (2) answers 429 `rate_limited` with `Retry-After: 1`: it is never answered from the empty fail-open
engine. The official JS client retries a 429 once after `Retry-After` and then fails open on its side, so a customer's own
signup flow is never blocked by this; any other client should treat 429 like any other error and let the signup through.
A reachable but busy database (no free connection, a cancelled or deadlocked statement, a check running past
`CHECK_DEADLINE_SECONDS`, 3) answers 503 `server_error` with `Retry-After: 1` and does not open the breaker. A statement
that fails for any other reason (a bug or bad data) answers 500 and is logged with its SQLSTATE. Only connectivity
failures answer `degraded`.

## Guard preset
Each customer has a preset that sets the engine's credit rules. **The default is `ai`, and it halves honest grants unless your app verifies a phone or a card.** Concretely, with `ai` every signup that arrives without a verified `phone` or `card` link gets at most half the credit grant (`credit_fraction` 0.5), even a perfectly honest one, and when 25 unverified signups were released credits within an hour the next ones get 0 until they verify. If your app does not check a phone or a card (a plain email signup), you have nothing to send in `links`, so every honest user gets 50%: **use preset `default` instead.** `default` is a plain `Guard()` with neither rule: a clean signup gets `credit_fraction` 1, and review and block decisions still apply. Use `ai` only when you verify a phone (an OTP you ran) or a card fingerprint and send it as `links.phone` or `links.card`; that is what lifts an honest user to a full grant. Set it with `python -m hosted.api.admin create-customer --name X --preset default|ai` or
`set-preset --customer ID default|ai`.

## Privacy
Raw emails, IPs, device ids, phones and cards are never stored. Per-customer state uses keys
HMAC'd with that customer's secret. The shared network table stores HMACs under a separate network secret,
plus the reporting customer id and time, nothing else. Logs carry decision ids, never personal values.
