# Tenkyu

Stops fake accounts from grabbing your free AI credits.

Tenkyu scores each signup `allow`, `review` or `block` and says how much of the free credit to give. It is a small Python library that runs inside your app and tells you the reason for every decision.

> Status: Early Access. `pip install tenkyu` installs 0.3.0 (Python 3.11 or newer, no dependencies; see the [CHANGELOG](https://github.com/tenkyuai/tenkyu/blob/main/CHANGELOG.md)). It uses fixed rules, not machine learning, and makes no network calls. Read [Honest limits](#honest-limits) before you rely on it.

## Try it in 2 minutes, no file needed

```text
pip install tenkyu
tenkyu audit --sample
```

This replays a bundled synthetic export (939 signups with known farms, not real users) offline, and prints what Tenkyu would have flagged. Real output, trimmed:

```text
Using the bundled sample (synthetic signups with known farms, not real users).
Audited 939 signups (replayed in time order, nothing left this machine)
  allow        689
  review        11
  block        239
Flagged for review or block: 26.6% (review is a softer check; some honest users land there)
Free credits these decisions would have withheld: $741.75 at $3 per grant
```

The free credit is your own number: every dollar figure in this README uses an example $3 grant, and you give your own with `--grant` (or, on the hosted dashboard, in Settings, where new accounts start at $5.00 until you change it).

If `tenkyu` is not on your PATH, use `python -m tenkyu audit --sample`. The full output and how to run it on your own signup export are in [First, find out what you already lost](#first-find-out-what-you-already-lost). Honest users flagged by `review` get partial credit, and [docs/flagged-users.md](https://github.com/tenkyuai/tenkyu/blob/main/docs/flagged-users.md) says what to do about them. Early users: please share your shadow-week numbers with the [shadow-week results form](https://github.com/tenkyuai/tenkyu/issues/new?template=shadow-week-results.yml) (counts and rates only).

New here? Three ways in:

- **Library only.** Run the audit below on your own signup export. Nothing leaves your machine.
- **Supabase or Vercel.** Copy a recipe: [`examples/supabase-edge-function`](https://github.com/tenkyuai/tenkyu/tree/main/examples/supabase-edge-function) or [`examples/vercel-nextjs`](https://github.com/tenkyuai/tenkyu/tree/main/examples/vercel-nextjs). Each creates the account first, then checks it with the hosted API and grants credits by verdict. The hosted API is built but not deployed yet, and the JS client is not on npm yet, so you cannot run a recipe against a live service today. The hosted service (API, dashboard, shared farmer network, paid plans) is closed source; its public API is documented in [docs/api.md](https://github.com/tenkyuai/tenkyu/blob/main/docs/api.md). This library stays open source (MIT).
- **Watch before you enforce.** A new hosted account starts in shadow mode for 7 days, and the library has `Guard(shadow=True)`. Nobody is refused. In the first week, label the signups you know and read your accuracy: [Your first week with Tenkyu](https://github.com/tenkyuai/tenkyu/blob/main/docs/first-week.md).

## First, find out what you already lost

Export your signups to CSV (an email column and a time column; IP, device id and user agent help) and replay them. Nothing leaves your machine. Try it on the bundled synthetic sample first:

```text
$ python -m tenkyu audit --sample --grant 3
Using the bundled sample (synthetic signups with known farms, not real users).
Audited 939 signups (replayed in time order, nothing left this machine)
  allow        689
  review        11
  block        239
Flagged for review or block: 26.6% (review is a softer check; some honest users land there)
Free credits these decisions would have withheld: $741.75 at $3 per grant
Main reason per flagged signup:
      110  this email (or an alias of it) already signed up
       51  N similar-looking emails from this IP
       14  N signups from this IP in Ns
       14  N signups from this IP in Nh
        5  N accounts on this device
        4  automation-style user agent (python-requests)
```

Then run it on your own export: `python -m tenkyu audit signups.csv --grant 3 --out flagged.csv`. The screen shows counts only; `flagged.csv` holds the flagged rows of your export with Tenkyu's action and reason. Common column names are matched (`created_at`, `timestamp`, `signup_ip`, `visitor_id`, `primary_email_address` and more), unused columns are listed, and `--link card=card_fingerprint` joins accounts on any column you have.

## Measure it on your own data

`audit` counts what Tenkyu would flag. `evaluate` tells you how often it is right. Add a `label` column to your export that says which accounts were farmers and which were real users, then run:

```text
$ python -m tenkyu evaluate labelled_signups.csv --grant 3
Evaluated 9 labelled signups (7 farmers, 2 real users), replayed in time order, preset default; nothing left this machine
Flagged = review or block:
  precision                         100.0%   of flagged accounts, the share that were farmers
  recall                             85.7%   of farmers, the share flagged
  honest users wrongly flagged        0.0%   0 of 2 real users
Blocked only:
  precision                         100.0%
  recall                             57.1%
  honest users wrongly blocked        0.0%   0 of 2 real users
Free credits at $3 per grant:
  withheld from farmers             $16.50
  withheld from real users           $0.00
By tactic (recall = farmers flagged; false positives = real users flagged):
  (none)               farmers      0  recall    n/a   real      2  false positives   0.0%
  alias                farmers      3  recall  66.7%   real      0  false positives    n/a
  bot                  farmers      4  recall 100.0%   real      0  false positives    n/a
```

That sample is a nine-row toy file; your numbers will come from your own rows. The file needs the same columns as `audit` (email and a time column; ip, device_id and user_agent help), plus `label`. Write `farmer` or `real` in it (`1`/`0`, `true`/`false`, `yes`/`no` and `fraud`/`legit` work too, in any case). Optional columns: `phone` and `card` (treated as verified links), and `tactic` for a breakdown by how each farmer operated.

Rows are replayed in time order, so each signup is judged only on what came before it. Flagged means review or block. "Withheld" dollars use the share of the grant Tenkyu would hold back, at the `--grant` you give, so a flagged real user shows up as lost money. Rows with an unreadable time or an unclear label are skipped and counted. Use `--preset ai` to test `Guard.for_ai_apps()` instead of the default settings; with it, "withheld from real users" includes the starter hold (half the grant for every unverified user), which is by design and not a false positive. Use `--json` for machine-readable output. From Python: `from tenkyu.evaluate import evaluate_csv, evaluate_rows`.

You can also score decisions you already recorded. `tenkyu evaluate --decisions export.csv` reads the hosted dashboard's labelled export (columns `action`, `would_action`, `shadow` and `label`, no email or IP) and reports the same precision, recall and honest-users-flagged lines; in shadow mode it judges what Tenkyu would have done. With a library `DecisionLog`, use `tenkyu shadow-report` instead (see [shadow mode](#trying-it-on-live-traffic-shadow-mode)).

## Quickstart

```python
from tenkyu import Guard, Signup

guard = Guard()  # in memory, so a restart forgets everyone. Reuse one Guard per process.
# In your app, keep the history on disk instead: Guard(store=SQLiteStore("tenkyu.db"))
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/120.0 Safari/537.36"

first = guard.check(Signup(email="john@gmail.com", ip="203.0.113.7", device_id="dev-1", user_agent=UA))
print(first.action, first.reasons)  # allow []

# Same person, new alias, hoping for another batch of free credits:
again = guard.check(Signup(email="j.o.h.n+free2@gmail.com", ip="203.0.113.7", device_id="dev-1", user_agent=UA))
print(again.action, again.reasons)  # block ['this email (or an alias of it) already signed up']

if again.action != "allow":
    print("withhold free credits; send to manual review" if again.action == "review" else "reject signup")
```

**Keep state on disk.** `Guard()` forgets everyone when the process stops. In your app, use `Guard(store=SQLiteStore("tenkyu.db"))` (`from tenkyu import SQLiteStore`). The history then survives restarts and is shared by all workers on one machine. For several machines, use `PostgresStore` (see [Several servers](#several-servers-postgresstore)).

**Pass the request headers through.** A missing user agent is itself a weak signal (`no_user_agent`, 0.25). `signup_from_request` reads the user agent and the client IP from your headers for you (see [Using it behind a web framework](#using-it-behind-a-web-framework)).

**Where `guard.check` goes.** Call it after you have validated the sign-up and before you grant credits. Do not block account creation on it unless you want to. Withhold or reduce the credits instead: `decision.credit_fraction` says how many to give.

**Use a recipe if you run Supabase or Vercel.** Webhooks carry no IP, so the recipes put a small endpoint in front of sign-up (a Supabase Edge Function, or a Next.js route). It sends the IP, user agent and a device id to Tenkyu and writes the grant. If Tenkyu cannot be reached, the client grants 25% of the credits, not all of them.

**Start in shadow mode.** `Guard(shadow=True)` (library) and every new hosted account (7 days, live keys only) score each signup but refuse nobody. Label what you can in the first week and run `tenkyu shadow-report`. The plan is in [docs/first-week.md](https://github.com/tenkyuai/tenkyu/blob/main/docs/first-week.md).

Install from source: `pip install -e .` (no dependencies; Python 3.11 or newer, tested on 3.11, 3.12 and 3.13).

## Try it

No code needed to see it work:

```
python -m tenkyu demo
```

```text
scene         email                      action  score  credits  main reason
------------  -------------------------  ------  -----  -------  -----------
honest user   ana@acme.io                ALLOW   0.00   100%     -
alias farmer  john@gmail.com             ALLOW   0.00   100%     -
alias farmer  j.o.h.n+free1@gmail.com    BLOCK   0.90   0%       this email (or an alias of it) already signed up
alias farmer  john+free2@googlemail.com  BLOCK   0.95   0%       this email (or an alias of it) already signed up
bot burst     bot0@acme.io               REVIEW  0.45   25%      automation-style user agent (python-requests)
bot burst     user1x@mailinator.com      BLOCK   0.89   0%       disposable email domain (mailinator.com)
bot burst     bot2@acme.io               BLOCK   0.97   0%       3 signups from this IP in 60s
bot burst     user3x@mailinator.com      BLOCK   1.00   0%       4 signups from this IP in 60s
```

Score a single signup from a shell or a script (`--db` remembers signups between runs; exit code is 0 allow, 10 review, 20 block, 3 if the `--db` file cannot be opened):

```
python -m tenkyu score --email j.o.h.n+x@gmail.com --ip 203.0.113.7 --db tenkyu.db
```

## What it checks

| Signal | Catches | Score |
|---|---|---|
| `alias_reuse` | `john@gmail.com`, `j.o.h.n+x@gmail.com`, `john@googlemail.com` are one person; also Yahoo `-keyword`, Fastmail `x@user.fastmail.com`, iCloud (`me.com`, `mac.com`) and Proton (`protonmail.com`, `protonmail.ch`, `pm.me`) families | 0.90 |
| `disposable_email` | Mailinator-style throwaway domains (and subdomains) | 0.80 |
| `pattern_emails` | `farmer1@`, `farmer2@`, `farmer3@` from one IP | 0.75 |
| `ip_burst` | 3+ signups from one IP in 60 seconds | 0.90 |
| `ip_velocity` | 3+ (0.50) or 6+ (0.90) signups from one IP in an hour | 0.50-0.90 |
| `device_reuse` | 2 (0.50) or 3+ (0.65) accounts on one device id | 0.50-0.65 |
| `automation_agent` | curl, python-requests, headless Chrome, Selenium, and similar | 0.45 |
| `no_user_agent` | no user agent sent | 0.25 |
| `invalid_email` | an email that was supplied but is malformed (blocked, no credit). A missing email gives no signal | 1.00 |
| `network_burst` | 15+ signups from one network in 10 minutes, or 5+ when another risk signal fires on the same signup | 0.40 |
| `network_daily_cap` | not a score: the 31st full grant in 24 hours from one /24 (IPv4) or /48 (IPv6) network. A signup that would be `allow` becomes `review` (partial credit) instead. It stops a slow farm that rotates IPs inside one network; never a block. Change it with `Guard(network_daily_cap=...)`, or `None` to turn it off | `allow` to `review` |
| `network_day` | 4+ (0.15) or 10+ (0.40) signups from one network in a day, only next to another risk signal | 0.15-0.40 |
| `lookalike_domain` | an email domain that imitates a big provider: Cyrillic or Greek letters (`gmаil.com`), `0` for `o`, `rn` for `m`, one typo (`gmial.com`), a swapped ending (`gmail.con`, `gmailc.om`). Uses a bundled subset of the Unicode TR39 confusables table. The look-alike is flagged; it is not merged with the real address | 0.75 |
| `mixed_script_email` | the name before the `@` mixes Latin letters with Cyrillic or Greek ones | 0.50 |
| `obfuscated_email` | invisible characters hidden between Latin letters (0.75), fullwidth letters (0.60), other invisible or fullwidth characters (0.30), or a made-up subdomain of a known provider (0.60) | 0.30-0.75 |
| `device_farm` | 6+ accounts on one device id in 7 days, seen from 3+ different /24 networks (a farm moving through proxies). The same device on one network stays `device_reuse` | 0.75 |
| `wide_spread`, `regular_timing` | rotating residential proxies: many signups from one ISP range over many /24s with one shared user agent, or signups arriving on a timer. Soft: they count only next to another real signal, and never block alone | 0.35-0.50, 0.40 |
| `odd_domain`, `domain_burst` | a domain that looks auto-generated (random letters, digit soup, throwaway words), or many distinct addresses from one small unknown domain in a day. Soft; `domain_burst` alone never costs credit, because a company signs up that way | 0.30, 0.30-0.50 |

IPv6 addresses are counted per /64 network, because one network hands out 2^64 addresses and a farmer could otherwise rotate for free; the network rules group IPv6 per /48 and IPv4 per /24. `::ffff:1.2.3.4` counts as `1.2.3.4`, and a port (`1.2.3.4:5000`) is dropped. A shared device (`device_reuse`) and network volume alone never block: with only those signals the action is at most `review`. A shared device on one network (a school lab, a family) stays soft; the same device across 3+ networks with 6+ accounts in a week is `device_farm`.

**Crowds behind one IP.** Schools, offices and mobile carriers put many honest people behind one address. When a signup is a clean, distinct identity (a plain address at a big mailbox provider, a school or a normal-looking own domain, a normal browser, no other risk signal), IP volume alone costs partial credit (`review`) but never a block, up to 60 signups an hour and 200 a day from that IP. Past that, the IP rules apply in full again, crowd or not. The cost: a farmer with many clean-looking, distinct addresses behind one IP gets partial credit for them. The worst case measured for one IP is about 17 grants an hour. Any real signal on the signup (an alias, a disposable domain, a look-alike, a shared inbox) switches the normal IP rules back on.

Each signal gives a score from 0 to 1. Tenkyu combines them so that several weak signals add up and one strong signal is enough on its own. (In math terms this is a noisy-or: the final score is 1 minus the product of (1 minus each signal's score).) A score under `0.35` is `allow`, `0.35` to `0.7` is `review`, and `0.7` and up is `block`. Every `Decision` carries the list of reasons, so you can show them to your support team or log them.

## Tuning

```python
guard = Guard(review_at=0.4, block_at=0.8)          # thresholds
decision = guard.check(signup, record=False)         # dry run: score without remembering the signup
print(decision.to_dict())                            # JSON-friendly, includes every signal
```

Add your own signal (more in [`docs/signals.md`](https://github.com/tenkyuai/tenkyu/blob/main/docs/signals.md)). It receives `(signup, store, now)` and returns a `Signal` or `None`; a signal that raises is ignored, so it can never break your signup flow:

```python
from tenkyu import Signal
def blocked_country(signup, store, now):
    return Signal("country", 0.6, "signup from an unsupported region") if getattr(signup, "country", None) == "XX" else None
guard = Guard(extra_signals=[blocked_country])
```

## Using it behind a web framework

`signup_from_request` builds a `Signup` from your request and gets the client IP right. Full FastAPI and Flask handlers are in [`examples/`](https://github.com/tenkyuai/tenkyu/tree/main/examples), including how to pass a Fingerprint/Stytch/Castle visitor id as `device_id`.

```python
from tenkyu import Guard, signup_from_request

guard = Guard()
headers = {"User-Agent": "Mozilla/5.0 Chrome/120.0", "X-Forwarded-For": "1.1.1.1, 198.51.100.7"}  # a spoofed first entry
signup = signup_from_request("new@acme.io", headers, remote_addr="10.0.0.1", trusted_proxies=1)
print(signup.ip, guard.check(signup).action)  # 198.51.100.7 allow
```

Set `trusted_proxies` to the number of reverse proxies **you** run in front of the app. With the default `0` the `X-Forwarded-For` header is ignored, so nobody can spoof their way around IP limits; with the wrong number you either trust attacker-supplied addresses or see every user as one IP. If honest users get blocked after going live behind a load balancer or CDN, check this setting first: every signup arriving from the proxy's address looks like one IP (in a test, 10 honest users behind one proxy address were blocked from the third on). Entries with a port (`198.51.100.7:51234`, `[2001:db8::7]:443`, as some proxies append) are accepted; an entry that is not an address makes Tenkyu fall back to the socket address. If there is no `X-Forwarded-For`, the standard `Forwarded: for=...` header is read the same way (`for=unknown` or an obfuscated `_token` is not an address, so Tenkyu falls back); when both are present, `X-Forwarded-For` wins. Only set `trusted_proxies` above 0 if your proxies set one of these headers, otherwise a client can send its own.

## Graduated credit unlock

Don't hand the whole free grant to a borderline signup. Every decision carries a `credit_fraction`: the share of the free credits to release now.

```python
from tenkyu import Guard, Signup

guard = Guard(credit_fractions={"review": 0.2})   # defaults: allow 1.0, review 0.25, block 0.0
d = guard.check(Signup(email="new.user@acme.io", ip="198.51.100.4", user_agent="python-requests/2.31"))
grant = int(500 * d.credit_fraction)              # 500 free credits * 0.2 = 100 now
print(d.action, grant)                            # review 100
```

Release the rest after the user verifies something that costs a farmer effort (a card on file, a phone number).

## Stopping fresh-account farms

A farmer with a new inbox, home IP and device for every account shares nothing Tenkyu can link. Three settings, all off by default, limit what such a farm gets:

- `links`: pass stable ids your app already has, such as a Stripe card fingerprint or a verified phone. Accounts that share one are flagged as the same person (`link_reuse`). Values are stored as hashes, never in the clear, but a plain SHA-256 of a guessable value (a phone number, a short id) can be checked by anyone who holds the store and tries candidates. Pass `Guard(link_secret=<16+ secret bytes>)` so they are keyed with HMAC-SHA256 instead (Tenkyu warns once if you use `links` without it).
- `starter_fraction`: signups without a `phone` or `card` link get at most this share of the free grant until they verify.
- `surge_threshold`: when this many unverified signups were released credits within an hour, the next ones get none until they verify. A real launch day can trip it too, so start high and watch `limit` in the decisions.

```python
from tenkyu import Guard, Signup

guard = Guard(starter_fraction=0.5, surge_threshold=50)
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/120.0 Safari/537.36"

new = guard.check(Signup(email="ana@outlook.com", ip="203.0.113.7", user_agent=UA))
print(new.credit_fraction, new.limit)  # 0.5 starter budget until verified

paid = guard.check(Signup(email="ben@proton.me", ip="198.51.100.4", user_agent=UA, links={"card": "fp_9xK2"}))
print(paid.credit_fraction, paid.limit)  # 1.0 None

again = guard.check(Signup(email="cara@fastmail.com", ip="192.0.2.55", user_agent=UA, links={"card": "fp_9xK2"}))
print(again.action, again.reasons[0])  # block shares a card with another account
```

## Trying it on live traffic: shadow mode

`Guard(shadow=True)` scores and records every signup but always returns `allow` with the full grant. The real decision is in `decision.shadow_action`. Add a `DecisionLog` and read the result with `tenkyu report`. The log keeps a short hash of each email and never the raw email, IP or device id.

On the hosted service, shadow mode is the default. A new account is in shadow mode for 7 days (live keys only; test keys always show the real decision), and you end it in Settings with "Switch to live now" or let it run out. Each log line carries a random `decision_id` (the hosted service has its own decision ids), so you can label decisions without the log ever holding an email.

New to this? [Your first week with Tenkyu](https://github.com/tenkyuai/tenkyu/blob/main/docs/first-week.md) is a day-by-day plan: label what you can, then run `tenkyu shadow-report decisions.jsonl labels.csv` to see your accuracy before you enforce. On the hosted dashboard, label with one click or upload a CSV, then open "Your accuracy".

```python
# not run in tests: needs TENKYU_LOG_SECRET
import os
from tenkyu import DecisionLog, Guard

guard = Guard(shadow=True, log=DecisionLog("decisions.jsonl", secret=os.environ["TENKYU_LOG_SECRET"].encode()))
```

The log holds no raw email, IP or device id. With `secret` (16+ bytes, kept out of the log) each line also carries `email_hash`, an HMAC of the email, so `tenkyu report` can count the same inbox signing up again; without a secret there is no `email_hash` and that count is "not tracked". An unkeyed hash of an email can be confirmed by anyone who guesses the address, so there is none.

After a week or two:

```text
$ python -m tenkyu report decisions.jsonl --grant 3
5 signups (shadow mode: nobody was refused; this is what Tenkyu would have done)
  allow       2   40.0%
  review      0    0.0%
  block       3   60.0%
Same inbox signing up again: 2
Free grants withheld from flagged signups: 3 = $9.00 at $3 per grant
Top reasons:
      2  this email (or an alias of it) already signed up
      1  N signups from this IP in Ns
      ...
```

## Clerk and Supabase webhooks

If Clerk or Supabase Auth creates your users, score them from the signup webhook. `verify_webhook` checks the signature (Svix and Standard Webhooks, stdlib only) and rejects stale or forged requests. The webhook fires after the account exists, so use `credit_fraction` to decide how many free credits to grant. A verified phone and OAuth account ids are passed as `links` automatically. Users who sign up by phone have no email (Clerk gives `email=None`, Supabase `""`): a missing email is not an error and produces no email signal, so they are scored on the other signals; only a supplied but malformed address is blocked.

**Webhooks carry no IP address and no device id, so only email-based signals work there** (plus phone and OAuth links). If you want the IP and device signals, call Guard from your own signup endpoint: right after the user submits the form and you have validated it, before you grant any credits. See [`examples/fastapi_app.py`](https://github.com/tenkyuai/tenkyu/blob/main/examples/fastapi_app.py).

A complete FastAPI route is in [`examples/fastapi-clerk/app.py`](https://github.com/tenkyuai/tenkyu/blob/main/examples/fastapi-clerk/app.py). It reads the raw body, checks the `svix-*` headers with the secret in `CLERK_WEBHOOK_SECRET`, scores the user with a `SQLiteStore` guard and saves the grant in your own database. The core of it:

```python
# not run in tests: key lines of examples/fastapi-clerk/app.py (needs FastAPI)
@app.post("/webhooks/clerk")
async def clerk_webhook(request: Request):
    body = await request.body()  # raw bytes: the signature covers them exactly
    try:
        verify_webhook(os.environ["CLERK_WEBHOOK_SECRET"], request.headers, body)  # svix-id, -timestamp, -signature
    except WebhookError:
        return Response(status_code=400)  # forged or wrong secret
    event = json.loads(body)
    signup = signup_from_clerk(event)  # None unless this is user.created
    if signup is None:
        return Response(status_code=200)  # not ours; 2xx so Clerk stops
    decision = guard.check(signup)  # guard = Guard(store=SQLiteStore("tenkyu.db"))
    grant_credits(request.headers["svix-id"], event["data"]["id"], int(GRANT * decision.credit_fraction))  # your DB
    return Response(status_code=200)  # saved: tell Clerk "done"
```

What to return:

- **2xx** means "handled, do not send this again". Use it when the grant is saved, and for events you ignore.
- **Anything else (4xx, 5xx, a timeout)** means "failed". Clerk retries with growing delays, so return one only when you want a retry. If your database write fails, let the error become a 500.
- A repeat delivery carries the same `svix-id`. Save the grant under that id and return 200 if you already have it, so nobody is scored or paid twice.

For Supabase, point a database webhook at INSERT on `auth.users` and use `signup_from_supabase(payload)` the same way.

A Supabase database webhook carries no IP address or device, so only the email-based checks work on that path. For full protection, call Tenkyu from your own sign-up endpoint and pass the visitor's IP and user agent along with the email. Two ready recipes do this for the hosted API: [`examples/supabase-edge-function`](https://github.com/tenkyuai/tenkyu/tree/main/examples/supabase-edge-function) (Deno tests, no network) and [`examples/vercel-nextjs`](https://github.com/tenkyuai/tenkyu/tree/main/examples/vercel-nextjs). Both create the account first and call Tenkyu only for a new one, return the same `{ok: true}` for a new, existing or blocked email, and never send Tenkyu's reasons to the browser. They have not been run on a real Supabase or Vercel project. The Supabase recipe's client-IP header is unverified, and its README says how to check it.

## Keeping the disposable-email list fresh

Tenkyu bundles a small starter list. Throwaway services rotate domains constantly, so load a fresher list yourself. Tenkyu does not bundle third-party lists, so check the license of whichever one you download.

```python
# not run in tests: needs a file you downloaded
from tenkyu import Guard, load_domains

guard = Guard(extra_disposable=load_domains("disposable-domains.txt"))  # one domain per line, # comments ok
```

## Surviving restarts

The default store lives in memory, so a restart forgets everyone. `SQLiteStore` keeps the history in a file (standard library only), and several threads or processes on one machine can share it. Each check reads the history and records the signup as one step, so a farmer sending many aliases of one person in parallel gets only the first one through. If another process holds the file's write lock longer than `SQLiteStore(path, timeout=5.0)` seconds, `check` raises `sqlite3.OperationalError` (the CLI prints one line and exits 3). It never quietly allows the signup, so decide in your handler whether to fail open or closed, and lower `timeout` if a signup request must not wait that long.

```python
import os, tempfile
from tenkyu import Guard, SQLiteStore, Signup

path = os.path.join(tempfile.mkdtemp(), "tenkyu.db")   # in your app: "tenkyu.db"
Guard(store=SQLiteStore(path)).check(Signup(email="john@gmail.com", ip="203.0.113.7"))

# ...your app restarts; a new Guard on the same file still knows john:
again = Guard(store=SQLiteStore(path)).check(Signup(email="j.o.h.n+2@gmail.com", ip="198.51.100.9"))
print(again.action)  # block
```

### Several servers: `PostgresStore`

`SQLiteStore` is per machine. When signups land on more than one server, point them all at one Postgres database with `PostgresStore`. It has the same interface and semantics as the other stores, and it needs the optional driver (`pip install "tenkyu[postgres]"`, which installs `psycopg` 3). `import tenkyu` still has no dependencies; the driver is imported when you create the store.

```python
# not run in tests: needs a Postgres server
import os
from tenkyu import Guard, PostgresStore, Signup

# key_secret: 16+ secret bytes, kept out of the database. Make one with
#   python -c "import secrets; print(secrets.token_hex(32))"
store = PostgresStore(os.environ["DATABASE_URL"], prefix="acme",     # prefix: one namespace per customer
                      key_secret=os.environ["TENKYU_KEY_SECRET"].encode())
Guard(store=store).check(Signup(email="john@gmail.com", ip="203.0.113.7"))
```

- The table `tenkyu_events` is created on first use (safe when many processes start at once). If your database role cannot create tables, open a store once with an admin role to create it, and the app role then only needs `SELECT, INSERT, UPDATE, DELETE` on it and `USAGE` on its sequence.
- `key_secret` is required: without it the table would hold raw emails, IP addresses and device ids, so `PostgresStore` refuses to start (pass `allow_raw_keys=True` only for a throwaway database or a test). With it, every key and value is stored as HMAC-SHA256 and the table holds no raw identifiers; see [`docs/shared-store.md`](https://github.com/tenkyuai/tenkyu/blob/main/docs/shared-store.md). Keep the secret stable and out of the database; a new secret forgets the history.
- `prefix` namespaces every key. Stores with different prefixes share the database but never see, count, expire or wait for each other's events. Use one prefix per customer if you host Tenkyu for several.
- Each `check` runs as one Postgres transaction under an advisory lock for its prefix, so parallel requests for one person, on any server, let only the first through. Requests with the same prefix queue behind each other (a check takes a few milliseconds). If the lock is not free within `PostgresStore(..., timeout=5.0)` seconds, `check` raises `psycopg.errors.LockNotAvailable` (an `OperationalError`) and records nothing. It never quietly allows the signup, so decide in your handler whether to fail open or closed.
- Timestamps come from your app servers' clocks (or the `clock` you pass), so keep them in sync (NTP). Expired rows are deleted on open and every 1,000 adds, for your prefix only.
- Tested against Postgres 16 with `psycopg` 3. Run the Postgres tests yourself with `TENKYU_TEST_PG_DSN=postgresql://user@host/db python -m pytest tests/test_postgres_store.py`; they skip when the variable is not set.

## What the settings cost and save

Flags are not the whole story. What matters is how many free grants farmers walk away with. This table uses the synthetic data set described in the next section (every extra account a farmer opens would get one full grant without Tenkyu):

| Setting | Free grants farmers' extra accounts got | ...of which residential | Honest users' average grant | Honest users with the full grant |
|---|---|---|---|---|
| Tenkyu (default) | 20% | 100% | 97% | 96% |
| Guard.for_ai_apps() | 10% | 49% | 49% | 0% |
| Guard.for_ai_apps() + card link | 0% | 0% | 97% | 96% |

- The default setting catches cheap tricks but gives residential farmers (new inbox, home IP and device each time) everything.
- `Guard.for_ai_apps()` halves what residential farmers get, but honest users also start at half a grant until they verify a phone or card. Unlock the rest on verification.
- The card-link row assumes every signup carries a card fingerprint, each farmer reuses one card, and each honest user has their own. It only applies to apps that take a card. Run `PYTHONPATH=src python benchmarks/farming_bench.py` to reproduce.

## How well does it work?

Short answer: well on lazy farming, not at all on determined farming, and it costs some households a partial grant.

### Held-out test (now a tuning set)

Read this first: this was the fair test on night 1, and it is not any more. The detector was frozen first ([benchmarks/FROZEN.md](https://github.com/tenkyuai/tenkyu/blob/main/benchmarks/FROZEN.md)). Then a separate AI agent, not a separate human author, wrote a data generator from a plain description of farming tactics and honest-user behaviour, without seeing the rules, the old benchmark or the bundled disposable list. On night 2 we tuned new rules on these same seeds (1-20), so the table below is a tuning-set result, not an unseen-data estimate. The frozen detector scored 70% of farmer accounts flagged and 39% blocked on it; the figures below are after tuning. For an unseen-data result, see the blind v2 test after this section.

The data has its own farming tactics and harder honest users: launch-day spikes, a campus network, carrier NAT, families on one device, people with a work and a personal account. Results over 20 seeds, `Guard()` defaults, all synthetic. Every farmer account counts, including each farmer's first one, which no signup check can catch. Reproduce with `PYTHONPATH=src python benchmarks/heldout_eval.py`; full table in [docs/heldout-benchmark.md](https://github.com/tenkyuai/tenkyu/blob/main/docs/heldout-benchmark.md).

How to read the numbers:

- The figure is the mean over 20 simulated datasets from one generator; the range in brackets is the 2.5th to 97.5th percentile across those 20 datasets. It is not a confidence interval for real traffic.
- 18 of the generator's 35 disposable domains (the well-known ones, such as mailinator.com) are also in the bundled list. On the 17 it does not share, disposable recall is 65% (46 to 81), against 100% on the shared ones.
- The test passes phone and card to Tenkyu as links on only part of the rows. `card_or_phone_reuse` depends on them, and a default `Guard()` without links will not catch that tactic.
- Farmers are about 25% of the accounts. Precision rises and falls with that share, so expect a different figure if farming is rarer in your traffic.

| What | Result (tuning set) |
|---|---|
| Farmer accounts flagged (review or block) | 78% (69 to 85) |
| Farmer accounts blocked | 55% (43 to 66) |
| Flagged accounts that really were farmers | 85% (82 to 87) |
| Honest users flagged | 4.6% (3.9 to 5.4) |
| Honest users blocked | 1.2% (0.7 to 2.0) |
| Free credit withheld from farmers, per 1,000 signups at $3 | $541 ($477 to $598) |
| Free credit held back from honest users, per 1,000 signups at $3 | $85 ($71 to $98) |

Where it is weak, by group: patient farmers with a fresh home IP, device and inbox each day (0.5% flagged) and farms rotating residential proxies (46%). Look-alike Unicode emails went from 38% to 96% flagged, but the rules for that were written while looking at this data, so treat the 96% as optimistic. Where honest people pay: families sharing one device (60% flagged, mostly partial credit), a second work or personal account on the same device (50%), and carrier NAT (11%). `Guard.for_ai_apps()` catches the same accounts but also holds back half of every new user's grant until they verify, which is why its "held back from honest users" figure is much higher by design. That starter hold is released when the user verifies, so the hosted dashboard counts it separately ("Also held back until verification") and keeps it out of its "Free credit withheld from likely farmers" figure.

### Blind v2 test (the fair one)

A second generator ([benchmarks/heldout_gen_v2.py](https://github.com/tenkyuai/tenkyu/blob/main/benchmarks/heldout_gen_v2.py)) was written by a separate AI agent that never saw the detection rules. Seeds 101-120 were run once, after the rules were frozen, and were not used to pick any rule. All data is synthetic. Reproduce with `PYTHONPATH=src python benchmarks/heldout_eval_v2.py`; the raw record is [benchmarks/FROZEN-v2.md](https://github.com/tenkyuai/tenkyu/blob/main/benchmarks/FROZEN-v2.md). Mean over 20 seeds, with the 2.5th to 97.5th percentile in brackets, `Guard()` defaults. Before is the detector at the start of the night; after is the final one.

| Metric / group | Before | After |
|---|---|---|
| Farmer accounts flagged | 52.8% (40.1 to 67.2) | 65.0% (52.7 to 79.3) |
| Farmer accounts blocked | 24.6% (13.2 to 36.4) | 43.6% (27.7 to 59.9) |
| Honest users flagged | 20.4% (19.0 to 22.7) | 20.4% (19.0 to 22.7) |
| Honest users blocked | 12.5% (11.6 to 13.2) | 1.6% (1.0 to 2.1) |
| Look-alike domains (farmers) | 38.6% (0.0 to 79.3) | 87.0% (57.3 to 100.0) |
| Unicode tricks in the name (farmers) | 51.8% (0.0 to 77.5) | 99.1% (95.0 to 100.0) |
| Rotating proxies (farmers) | 34.1% (0.0 to 92.1) | 34.1% (0.0 to 92.1) |
| Shared-VPN farmers | 31.9% (5.9 to 81.7) | 31.9% (5.9 to 81.7) |
| Disposable domains (farmers) | 45.8% (0.0 to 95.2) | 46.5% (0.0 to 95.2) |
| School behind one IP (honest) | 96.7% | 96.7% |
| Carrier NAT (honest) | 59.9% (46.2 to 72.6) | 59.9% (46.2 to 72.6) |
| Families on one device (honest) | 66.8% (62.6 to 71.2) | 66.8% (62.6 to 71.2) |

Read honestly: honest users blocked fell from 12.5% to 1.6%, because schools, carrier NAT and universities now get partial credit instead of a block. Honest users flagged did not move (20.4%): that is partial credit, not a block, and a school behind one IP is 97% reviewed. Farmers blocked rose from 24.6% to 43.6%. The biggest gains are in look-alike domains (38.6% to 87.0% flagged) and Unicode tricks in the name (51.8% to 99.1%). Rotating proxies (34%), shared-VPN farmers (32%) and unlisted disposable domains (46%) did not improve. Nothing was tuned after this run. This generator is still one AI agent's guess at farmers and honest users, so it says how the rules behave on those guesses, not how they will behave on your traffic.

These numbers are still synthetic. To measure on your own users, label a past export and run `tenkyu evaluate` (above).

### First benchmark (built by the same author as the rules)

We first measured it on a **synthetic** data set that we generated ourselves: 2,000 real users and 200 farmers opening 863 extra accounts across five strategies, over one simulated week. "Flagged" means `review` or `block`. This shows how Tenkyu compares with naive rules on behaviours we imagined; it is **not** a measurement of real-world accuracy. Reproduce it with `PYTHONPATH=src python benchmarks/farming_bench.py`.

| Method | Precision | Recall | F1 | Real users wrongly flagged |
|---|---|---|---|---|
| exact email dedupe | 0.81 | 0.01 | 0.03 | 0.0% |
| email dedupe + disposable list | 0.81 | 0.20 | 0.32 | 0.0% |
| Tenkyu (default) | 0.84 | 0.81 | 0.82 | 3.7% |

Share of extra accounts caught, by farming strategy:

| Strategy | Exact email dedupe | Tenkyu |
|---|---|---|
| alias gmail | 3% | 100% |
| bot burst | 0% | 100% |
| disposable | 0% | 96% |
| lookalike | 3% | 78% |
| residential | 0% | 0% |

Read the last row: farmers who use a fresh home IP, a fresh device and an unrelated Gmail address for every account get through completely. That is the "honest limits" section below in numbers. None of the 2,000 real users were blocked outright; 75 (3.7%) went to `review`, and every one was a person sharing a device id with other accounts (a household computer, in this data). Shared device ids alone never reach `block`, by design: one weak signal should not punish a real person. The cost is that a farmer who only reuses one device gets `review`, which releases 25% of the free credits, not 0. Shared office IPs did not trigger anything.

The headline F1 depends on how many farmers are the hard kind, and we chose that mix. If half of the farmers used the residential strategy instead of 15%, F1 would be about 0.58, not 0.82 (`PYTHONPATH=src python benchmarks/sensitivity.py`). The same script shows that in this data the device signal adds under one point of recall (0.806 vs 0.798 with a fresh device id on every signup) and accounts for all of the 75 review cases, so it is the signal to doubt first. The data is ours, so this says how the rules behave on our assumptions, not how much a shared device id is worth in your traffic.

## When not to use Tenkyu

- **You already use Clerk.** Its [subaddress blocking](https://clerk.com/docs/guides/protect/protections/block-email-subaddresses) and [disposable-email blocking](https://clerk.com/docs/guides/protect/protections/block-disposable-email-addresses) are on every plan and off by default. Turn them on first; that covers two of Tenkyu's signals with no code.
- **You want a hosted service with device fingerprinting.** [Kaidn](https://pypi.org/project/kaidn/) is a hosted API with a key that returns allow, review or block with reasons, and it lists a free tier of 10,000 events a month.

Tenkyu's difference is narrow: it runs inside your process, needs no key, makes no network calls, and takes your own checks as plain functions. Checked against those pages on 2026-11-18; vendors change plans often.

## Honest limits

What was attacked, what held and what did not: [docs/threat-model-and-attacks.md](https://github.com/tenkyuai/tenkyu/blob/main/docs/threat-model-and-attacks.md).

- It is a speed bump for farmers, not a wall. At default settings, a farmer who uses a new inbox, home IP and device for every account gets every grant (see "What the settings cost and save"). `Guard.for_ai_apps()` halves that at a cost to honest users; only a link the farmer must reuse (a card, a verified phone) closes it. No heuristic library stops a patient farmer with real identities.
- Clerk and Supabase webhooks carry no visitor IP, so IP signals are off on that path. Without an email (phone-auth users) the email signals are off too, so pass a verified phone as a link.
- Phone links are compared by digits, and a leading `00` counts like `+` and a `(0)` trunk marker is dropped (`+44 (0)20 7946 0958` and `0044 20 7946 0958` match). Pass provider-verified E.164 numbers (`+97250...`). National formats such as `050...` cannot be matched with `+97250...` without knowing the country.
- The default store is in memory and per process. `SQLiteStore` survives restarts and is shared by processes on one machine, but several servers still each see only their own traffic. `PostgresStore` (optional extra) shares one history across servers; a Redis store is not built yet, and the design note is in [`docs/shared-store.md`](https://github.com/tenkyuai/tenkyu/blob/main/docs/shared-store.md).
- The disposable-domain list is small and bundled. Extend `tenkyu.signals.DISPOSABLE_DOMAINS` for your needs.
- Shared IPs (offices, schools, mobile carriers) can look like bursts. Use `review`, not `block`, for borderline cases, and tune the thresholds. A school behind one IP gets partial credit for nearly everyone (97% reviewed in the blind v2 data), because a clean-looking crowd is told apart from a farm only by volume caps (60 an hour, 200 a day per IP).
- Farmers who copy a crowd: distinct, clean-looking addresses at a big provider from one IP get partial credit, not a block, up to about 17 grants an hour per IP in the worst case measured.
- A word-like catch-all domain (a made-up company name that accepts any address) spread over many networks looks like a big company and is allowed: `domain_burst` alone never costs credit.
- Rotating residential proxies with a fresh device, inbox and IP each time stay hard: 34% to 46% flagged on the synthetic sets, and only when something else links the accounts.
- Never rely on one signal to punish a real person. Review queues exist for a reason.
- Emails are normalized with Unicode NFKC and invisible characters are removed. Look-alike letters from other scripts (a Cyrillic "а" in `gmаil.com`) are flagged as look-alikes (`lookalike_domain`, `mixed_script_email`) but are still not merged with the real address, because that mapping cannot be made safely. A look-alike the confusables subset does not know can still pass. Verify email ownership (a confirmation link or code) before you rely on an address, in particular before you release credits.
- An inbox that was used with an alias first and then with the plain address gets two credited accounts at most: the second (the owner) is `review` with partial credit rather than blocked, so a squatter's alias never locks the real owner out.
- The surge guard (`surge_threshold`, on in `Guard.for_ai_apps()`, off by default) can be triggered by a farmer: enough unverified signups in an hour switch it on, and honest new users then get reduced credit (0) until verified, for up to an hour. It is opt-in; verified phone or card links are exempt.
- Proton dot and hyphen behaviour is unverified, so Proton addresses are not merged on dots or hyphens (`jo.hn@proton.me` and `john@proton.me` count as two inboxes); only `protonmail.com`, `protonmail.ch` and `pm.me` map to `proton.me`.

## Development

```
PYTHONPATH=src python -m pytest -q tests
```

See [CONTRIBUTING.md](https://github.com/tenkyuai/tenkyu/blob/main/CONTRIBUTING.md), [CHANGELOG.md](https://github.com/tenkyuai/tenkyu/blob/main/CHANGELOG.md) and [SECURITY.md](https://github.com/tenkyuai/tenkyu/blob/main/SECURITY.md).

MIT licensed.
