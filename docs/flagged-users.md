# What to do about honest users who get partial credit

Some honest users will be flagged. On the blind v2 test (synthetic data, seeds 101-120, `Guard()` defaults, run once after the rules were frozen) about 1 in 5 honest users, 20.4% (19.0 to 22.7), was flagged, and 1.6% (1.0 to 2.1) was blocked. Almost all of the flagged ones are `review`, which is partial credit, not a refusal. The raw record is [benchmarks/FROZEN-v2.md](https://github.com/tenkyuai/tenkyu/blob/main/benchmarks/FROZEN-v2.md).

The flags are not spread evenly. In the same test a school behind one IP was 96.7% reviewed, a family sharing one device 66.8%, university students 49.9%, a second work or personal account on one device 33.8%, and carrier NAT 59.9%. Ordinary organic signups were flagged 0.1% of the time. These are one AI agent's guesses at honest behaviour, not your traffic. Measure your own rate in shadow mode first ([docs/first-week.md](https://github.com/tenkyuai/tenkyu/blob/main/docs/first-week.md)).

## What review means

`review` is a decision, and the decision carries a `credit_fraction`. By default it is 0.25: release a quarter of the free grant now. `allow` is 1.0 and `block` is 0.0. (In shadow mode the response says `allow` and 1.0, and the would-be values are in `would_action` and `would_credit_fraction`.)

```python
grant = int(500 * decision.credit_fraction)   # 500 free credits, review gives 125 now
```

Change the share with `Guard(credit_fractions={"review": 0.5})`. A higher share gives farmers who land in `review` more, so raise it only after you have seen your own numbers. The hosted API returns its own fractions; there is no per-account setting for them.

The user did nothing wrong, so show them the way out: tell them the rest of their credits unlock after one quick step, instead of leaving them with a quarter and no explanation.

## How to release the rest

Tenkyu does not release credits for you. It tells you how much to give at signup; topping up later is your code, in your database. Re-checking the same user with a `phone` or `card` link does not raise a review to a full grant (tested: a review stays 0.25). Pick one or more of these, from cheapest to most sure:

- **Email verification.** Free and low friction, and it stops typos and some bots. It does not stop a farmer who controls a real inbox, so use it as a first unlock (for example, up to 50%), not the last one.
- **Time or first use.** Release the next share after a few days of real activity, or after a first meaningful action (a project created, a document uploaded). Farmers want the credits at once; honest users come back.
- **Phone verification** (an OTP you ran yourself). A farmer must find a new number each time. Pass the verified number as `links={"phone": ...}` on later checks so Tenkyu can see when one number is reused. Use `Guard(link_secret=...)`, or the library warns that phone numbers hashed without a secret can be reversed.
- **Card on file or first paid action.** The strongest unlock, because a farmer must reuse or buy a card. Pass the card fingerprint (for example Stripe's) as `links={"card": ...}`. Two accounts that share a phone or card are flagged as the same person (`link_reuse`).

A simple rule that works for most apps: 25% at signup (review), 100% when the user verifies a phone or adds a card, or after the first paid action. Keep a `granted` number per user and top up the difference, never a second full grant.

## Recommended flows

### Next.js with Supabase

Start from the recipes: [`examples/vercel-nextjs`](https://github.com/tenkyuai/tenkyu/tree/main/examples/vercel-nextjs) or [`examples/supabase-edge-function`](https://github.com/tenkyuai/tenkyu/tree/main/examples/supabase-edge-function). They are not run against a live service yet, because the hosted API is not deployed and the JS client is not on npm.

1. The route creates the account, calls Tenkyu once for a new account and writes a `credits` row of `round(GRANT * credit_fraction)` along with the `decision_id` (review gives a quarter). The browser only sees `{ok: true}`, or a neutral `notice` when the account got 0 credits.
2. When Supabase marks the email as confirmed, a server function raises `credits.granted` to your email-verified share.
3. When the user verifies a phone, or Stripe reports a successful payment, raise it to the full grant. Do this on your server with the service-role key, and never let the browser write to `credits`.
4. If a user writes in about missing credits, top them up by hand and report the decision as `real` (below).

### Python app (FastAPI, Flask)

```python
from tenkyu import Guard, signup_from_request

guard = Guard(link_secret=b"...16+ secret bytes...")   # keep it in your config

def on_signup(request, user):
    d = guard.check(signup_from_request(user.email, request.headers,
                                        remote_addr=request.client.host, trusted_proxies=1))
    user.credits = int(GRANT * d.credit_fraction)       # review: a quarter
    user.tenkyu_decision = d.action                     # keep it so you can top up later

def on_verified(user):                                  # phone OTP passed, card added or first payment
    user.credits = max(user.credits, GRANT)             # top up to the full grant, once
```

Set `trusted_proxies` to the number of reverse proxies you run. With the wrong value every user can look like one IP, and honest users get flagged for it (see the README, "Using it behind a web framework"). Check that before you blame the rules.

## Which preset

- **A plain email signup, no phone or card step: the default `Guard()`.** A clean signup gets 1.0, and review and block work as described above.
- **You verify a phone or card and send it in `links`: `Guard.for_ai_apps()`.** It gives half the grant to any unverified signup, which cuts what residential farmers get roughly in half, and lifts verified users to the full grant. A `review` still gets 0.25. Without a verified link, every honest user starts at 50%, which is by design (on the benchmark, honest users' average grant was 49% against 97% for the default; with a card on every signup it was back to 97%).
- **Hosted:** a new account has preset `ai` today, and moving to `default` is an operator command (`set-preset`, see [hosted/API.md](https://github.com/tenkyuai/tenkyu/blob/main/docs/api.md), "Guard preset"). If your signup has no phone or card step, ask for `default` before you go live.

## Report false positives

Every honest user you find in the flagged set is worth a label. Labels feed your accuracy numbers, and on the hosted service they are the way to tell Tenkyu that a person was real.

- **Hosted, one call:** `POST /v1/outcome` with `{"id": "dec_...", "outcome": "real"}`. The value is `real` (the opposite is `farmer`). One outcome per decision; a later call replaces it. Store the `decision_id` from the check response and never show it to the user. The JS client has `tenkyu.outcome(id, "real")`. A `real` on a decision the shared network flagged also records a dispute against the reports behind it (see [hosted/API.md](https://github.com/tenkyuai/tenkyu/blob/main/docs/api.md), "Shared network").
- **Hosted, no code:** label the decision `real` with one click on the Decisions page or the Review queue, or upload a CSV of `decision_id,label` on "Your accuracy".
- **Library:** make a labels file with `tenkyu shadow-report decisions.jsonl --template labels.csv`, write `real` in the label column for the people you know are honest, and run `tenkyu shadow-report decisions.jsonl labels.csv`. This changes no rules; it shows you how many honest users each setting would have held back.

Then read the line "honest users wrongly flagged" and decide. If it is high on a group you care about (a school, a family plan), keep the unlock easy for that group, or take the preset above that fits. If you are not sure whether to enforce, stay in shadow mode.
