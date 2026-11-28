# Supabase Edge Function: sign-up through Tenkyu

Supabase database webhooks fire after the user exists and carry no IP, user agent or device, so Tenkyu only sees the email. This recipe puts a small Edge Function in front of sign-up. The browser posts `{email, password, device_id}` to it. The function validates the input locally (email shape, password length: 8 characters to 72 UTF-8 bytes, bcrypt's limit), **creates the account first**, and only for a newly created account reads the IP and user agent from the request, asks Tenkyu `POST /v1/check`, and writes a `credits` row of `round(GRANT * credit_fraction)` (allow 1, review 0.25, block 0; at most 0.25 when Tenkyu could not score the sign-up; a 0-credit row when Tenkyu rejected our request or something in front of it refused the call).

The browser only gets `{ok: true}` (the same for a new account and for an existing email), or a validation error (`invalid_request`, `weak_password`, `signup_failed`, `rate_limited`). Tenkyu's reasons, score and decision are never sent back, so a farmer cannot learn the rules by probing.

## Files

| Path | What |
| --- | --- |
| `supabase/functions/tenkyu-signup/index.ts` | Entry point: reads env, builds the two Supabase clients, `Deno.serve` |
| `supabase/functions/tenkyu-signup/handler.ts` | All the logic (validation, rate limit, Tenkyu call, sign-up, credits) |
| `supabase/functions/tenkyu-signup/guards.ts` | Strict and global-address IP check, rate-limit key (IPv6 /64), token bucket, capped body read, request id |
| `supabase/functions/tenkyu-signup/handler_test.ts` | Deno tests, no network |
| `browser/device-id.ts` | Random id in localStorage (works in old WebViews without `crypto.randomUUID`), plus a `signUp()` helper |
| `browser/device-id_test.ts` | Deno tests for the browser snippet |
| `sql/credits.sql` | `credits` table and RLS |

## Deploy (when you are ready)

1. Copy `supabase/functions/tenkyu-signup/` into your project's `supabase/functions/`, and run `sql/credits.sql` (SQL editor or a migration).
2. Secrets (server side only):
   ```
   supabase secrets set TENKYU_API_KEY=tk_live_... ALLOWED_ORIGIN=https://app.example.com GRANT=300
   ```
   Optional: `NO_CREDITS_MESSAGE` (empty leaves the `notice_pending` flag of a 0-credit account off; the notice text itself lives in your app), `TENKYU_URL` (default `https://api.tenkyu.dev`; must be `https`, `http` only for localhost, 127.0.0.1 and [::1]: the function refuses to start otherwise, because the bearer key must not travel in clear text), `TENKYU_ALLOW_INLINE_SCORING=1` (production without `waitUntil`, see "Timing without `waitUntil`"), `PASSWORD_MIN_LENGTH` (default 8; keep it at or above your Supabase Auth minimum), `TRUSTED_PROXY_HOPS`, `CLIENT_IP_HEADER`, `DEBUG_IP_HEADERS=1`, `FAIL_OPEN_CREDIT_FRACTION` (0 to 1, default 0.25), `RATE_IP_BURST` / `RATE_IP_PER_MIN` (default 20 / 15), `RATE_DEVICE_BURST` / `RATE_DEVICE_PER_MIN` (default 5 / 5).
3. `supabase functions deploy tenkyu-signup`
4. In the app, call `signUp()` from `browser/device-id.ts` instead of `supabase.auth.signUp`. It sends the public anon key as the bearer token, which the default JWT check accepts for a pre-login request.
5. Check the client IP (next section), then unset `DEBUG_IP_HEADERS`. After every deploy and key rotation, also look at the function logs for `Tenkyu API key rejected: check TENKYU_API_KEY` (see "Honest limits").
6. Grant credits only from the `credits` table. Do not add a database default or trigger that grants credits on `auth.users` insert, or the bypass below stops being harmless.

Tests: `deno test --allow-env supabase/functions/tenkyu-signup/ browser/device-id_test.ts` (all the handler suites, including the attack tests).

## Which IP to trust (UNVERIFIED)

**Hosting the app on Vercel? Use the [Vercel + Next.js recipe](../vercel-nextjs) instead.** It reads `x-real-ip`, which Vercel sets itself and documents as the client IP, so its IP signal is verified. This function's IP rule is a guess about Supabase's edge that you have to test yourself, and a wrong guess feeds Tenkyu a wrong address (or none). Supabase Auth with a Vercel front end works fine with the Vercel recipe plus its `lib/supabase-auth.ts` adapter. Use this Edge Function when your sign-up has to run on Supabase, and keep the `DEBUG_IP_HEADERS` check below.

I could not check how the Supabase edge fills `x-forwarded-for`, so this is not confirmed on a real project. Each proxy appends to the right end of that header; everything to the left can be forged by the visitor. The function therefore never uses the first entry. By default it uses the last (`TRUSTED_PROXY_HOPS=1`). If Supabase puts a second proxy of its own behind the one that saw the visitor, the visitor is second from the right (`2`). If the platform sets a dedicated header such as `cf-connecting-ip` (also UNVERIFIED), set `CLIENT_IP_HEADER`.

To verify: set `DEBUG_IP_HEADERS=1`, sign up once from a phone on mobile data (you know that IP), read `supabase functions logs tenkyu-signup`, and choose the setting that yields that IP. The entry must be a strict, global IPv4 or IPv6 address: a port (`1.2.3.4:80`), brackets, a zone id, `999.999.999.999` or `:::::` all fail validation. So do private (`10/8`, `172.16/12`, `192.168/16`), loopback, link-local, CGNAT (`100.64/10`), IPv6 unique-local (`fc00::/7`), unspecified and multicast addresses, including their `::ffff:` forms: a hop count that is off by one lands on one of those, and using it would put every visitor into one rate-limit bucket and one Tenkyu "network". In all these cases the function sends no IP and uses no IP bucket, and Tenkyu scores email and device id only.

## Why `signUp` and not `admin.createUser`

The function uses `auth.signUp` on a client built with the anon key. It keeps Supabase's normal flow: the confirmation email is sent, password policy and auth rate limits apply, and an already-registered email gets the obfuscated response. `admin.createUser` sends no email by itself, so you would have to build confirmation yourself.

The service-role key is used for one thing, inserting the `credits` row, and only inside the function. It is never sent to or readable by the browser. RLS gives the browser read access to its own row and no write access, and column privileges limit that read to `user_id` and `granted`: `decision_id` (Tenkyu's verdict handle) is not readable by users. Select explicit columns in your app; `select *` on `credits` is denied. `user_id` is the primary key, so parallel sign-ups cannot create several grant rows (a second insert is rejected and logged as expected).

Error handling and timing: only a password-policy refusal from `signUp` (`weak_password`, or Supabase's "Password ..." text) is returned to the browser, as `400 signup_failed`. Every other `signUp` error (an email-send rate limit, an invalid-address check, an outage) gets the same `{ ok: true }` as a duplicate email, with no Tenkyu call and no credits; the function logs the error code, never the message (it can contain the email). Email-send rate limits only occur for new addresses, so showing them would list which emails are unregistered. Also note that `signUp` itself can take longer for a new address because it sends the confirmation email. Enable asynchronous email sending (an Auth send-email hook that queues the mail) so `signUp` returns at the same speed either way, or pad the duplicate answer; the built-in padding only covers the Tenkyu step.

Cost of this choice: Supabase's own `/auth/v1/signup` stays open, and a farmer can skip your function. They then get an account with no credits row, so no free credits (see deploy step 6), and Tenkyu never sees them. If you want the function to be the only door, switch off public sign-ups in the Auth settings and replace `signUp` with `admin.createUser({ email, password })` plus your own confirmation email. Not tested here.

## Links: verified phone or card

Tenkyu holds a starter sign-up at 50% of the grant until something real vouches for it. A verified phone or a card fingerprint is what lifts an honest user above that 50% starter hold. The recipe can pass one on: give `createHandler` a `verifiedLinks({ email })` function that returns `{ phone: "+14155550100" }` for a phone YOUR app verified (an OTP you ran). It is sent to Tenkyu as `links: { phone }`. It must come from your server-side state, never from the browser body: the function ignores any `phone` or `links` in the body, because a browser could claim any number. The value must be E.164 or it is dropped, and a lookup error just means no links.

## Release the rest after email verification

A `review` decision writes 25% of the grant, and many honest users land there because they share a network (a school, a carrier, a family). The `/v1/check` answer carries `earn_back.requires`: `"email"` when everything that fired is what a shared network produces, `"phone_or_card"` otherwise. Once your app has confirmed the user's email (Supabase sets `email_confirmed_at`), call `POST /v1/earn-back` from server-side code with the same `request_id` this function sent to `/v1/check`, `{"request_id": "...", "proof": "email_verified"}`. The answer `{"extra_credit_fraction": 0.75, "granted": true}` is what to add to the user's `credits` row (`round(GRANT * extra_credit_fraction)`). It is idempotent per sign-up, so top up once. `"phone_or_card"` decisions need `"proof": "phone_verified"` or `"card_verified"` plus `"links": {"phone": "+14155550100"}` from a phone your app verified (an email is refused for them). Never take the proof from the browser. See `hosted/API.md`, "POST /v1/earn-back".

## Order of operations, request id, duplicates

1. Validate locally: JSON shape, email (trimmed and lowercased once, then shape and control-character checks), password length (`PASSWORD_MIN_LENGTH`), device id. A failure answers `400` and nothing else runs.
2. `auth.signUp`. This creates the account (or finds that the email is taken).
3. **Only for a newly created account**: `POST /v1/check` with `request_id = sha256("tenkyu-signup:" + user_id)`, then the `credits` row.

The old design derived `request_id` from the email and day and called Tenkyu before creating the account. That had two holes. A retry with a different device, IP or letter case reached Tenkyu with the same id and a different body, got a 400, and a 400 was treated as degraded, which gave a free 25% grant. And anyone could submit a victim's email first and burn that id. Now the id comes from the new account, so it is unique per person, a repeat for the same account is idempotent, and nobody else can pre-empt it.

- **Existing email, both confirmation modes.** With email confirmation on, Supabase returns a fake user with no identities. With confirmation off it returns an error (`code: user_already_exists`, text "User already registered"). Both are mapped to the same `{ok: true}` as a brand-new account: no Tenkyu call, no credits, and Supabase's wording never reaches the browser (so the function cannot be used to list which emails have accounts). The success answer carries no `user_id` for the same reason. Timing is covered too: `index.ts` passes `EdgeRuntime.waitUntil`, so the answer goes out first for both and the Tenkyu check plus the `credits` insert run after it. The row therefore appears a second or so after the answer (up to the 1500 ms timeout): show "setting up your credits" for a signed-in user with no row yet. Without `waitUntil` the handler pads the duplicate answer to the median of the last nine check times instead (see "Timing without `waitUntil`").
- **Bad or expired API key grants the small fail-open share.** Tenkyu answers a bad key with exactly status 401, a JSON content-type and the body `{"error":{"code":"unauthorized","message":"missing or invalid API key"}}`, compared as raw text (not as parsed JSON: a gateway that echoes the farmer's user agent into its own 401 JSON can write a duplicate `"code":"unauthorized"` key that parses fine). Only that exact answer is treated as our misconfiguration, like an outage: `FAIL_OPEN_CREDIT_FRACTION` of the grant, `decision_id` null, and one error line, `Tenkyu API key rejected: check TENKYU_API_KEY`, at most once a minute per instance. Alert on that line. A missing `TENKYU_API_KEY` gets the same quarter and its own once-a-minute error line.
- **Any other 401/403, and any 2xx that is not a JSON decision, fails closed.** Tenkyu never sends a 403, a 401 with another body, or an HTML page. One that arrives comes from something in front of it (a gateway or WAF; a block page answered `200 text/html` is common), and a farmer can trigger a WAF rule with his user agent or email, so failing open would turn a block into a 25% grant. A 2xx whose media type is not JSON (a missing header or `text/plain` is tolerated, but the whole body must then still be a valid decision) or whose body is not a valid decision is treated the same way. The function inserts a 0-credit row (`notice_pending`) and logs `Tenkyu call refused by something in between (status N)` at most once a minute per instance. Look at your gateway rules.
- **Other Tenkyu 4xx fails closed.** A 400 (bad body) or 404 means our request was wrong, not that Tenkyu is down. The function logs it at error level and inserts a 0-credit row with `notice_pending` (so the user sees the notice and you can find the row); grant by hand after fixing the cause. Network errors, timeouts, 5xx, 429, redirects and a rejected key fail open (see "Honest limits").
- **Submitting an unconfirmed email twice does not burn the grant.** With email confirmation on, Supabase answers a second sign-up for a not-yet-confirmed email with the SAME user, so the second check reuses the `request_id` with another IP, device or user agent, and Tenkyu answers `400 request_id reused with a different body`. That 400 is recognised and writes nothing: the first check's `credits` row stands, and nothing else (no 0-credit row) can take its place. It is logged as a warning (`request_id conflict`).
- **`degraded: true` from Tenkyu.** A 200 flagged `degraded` was answered from an empty in-memory engine, not a real score. It is capped at `FAIL_OPEN_CREDIT_FRACTION` like any other unscored sign-up, and its `decision_id` is stored as `null`.
- **Blocked sign-ups.** The account exists before the check, so a block can no longer prevent it (the old `REJECT_BLOCKED` switch is gone). A blocked sign-up gets an account with 0 credits and `notice_pending` (see "A real user got no credits").

## A real user got no credits

When the account is created but gets 0 credits (Tenkyu said `block`, or the check failed closed), the sign-up answer is still the plain `{ok: true}`. A `notice` in that answer would tell a caller whose sign-ups are blocked which emails are new (notice) and which are already registered (no notice). Instead the function writes `notice_pending = true` on the `credits` row (run the updated `sql/credits.sql`: it adds the column and lets the signed-in user read it). After sign-in, your app selects `user_id, granted, notice_pending` for the user and shows your notice when `notice_pending` is true: "We couldn't give free credits to this account automatically. Contact support to get them." (`DEFAULT_NO_CREDITS_NOTICE` in `handler.ts` has that text). It says nothing about the verdict, score or reasons. Set `NO_CREDITS_MESSAGE` empty to leave the flag off and show your own empty-balance message.

When that person writes to support:

1. Grant the credits by hand (SQL editor or your admin screen, service role): `update public.credits set granted = 300 where user_id = '<their id>';`.
2. Tell Tenkyu it was a real user so it learns. Get the id with `select decision_id from public.credits where user_id = '<their id>';` (null for a degraded grant, which cannot take an outcome), then from your server: `curl -X POST https://api.tenkyu.dev/v1/outcome -H "authorization: Bearer $TENKYU_API_KEY" -H "content-type: application/json" -d '{"id":"dec_...","outcome":"real"}'`. The API value is `real` (the opposite is `farmer`); one outcome per decision, a later call replaces it. Never show the decision id to the user.

## Shadow mode

While your Tenkyu account is in shadow mode (7 days from when the account was created, not from when you integrated; extend it in Settings), `action` is `allow` and `credit_fraction` is 1, plus `shadow: true`, `would_action` and `would_credit_fraction`. The function grants by `credit_fraction` as before, and logs one `tenkyu shadow` line per sign-up (decision id and the would-be values, no email) so you can see what enforcement will do. They are never sent to the browser.

## Timing without `waitUntil`

`index.ts` passes `EdgeRuntime.waitUntil` when it exists. Without it a new sign-up waits for Tenkyu and a duplicate email would not, so the handler pads the duplicate answer instead. That padding is a guess, so:

- **In production the function refuses to start without `waitUntil`** (it throws at boot: `NODE_ENV` or `DENO_ENV` is `production`, or `DENO_DEPLOYMENT_ID` is set; set `NODE_ENV=production` as a secret if you are not sure your runtime sets one of them) **unless you set `TENKYU_ALLOW_INLINE_SCORING=1`**, which accepts the approximate padding. A `waitUntil` that exists but throws is not fatal: the work is awaited inline and the answer is the same, and from then on duplicates are padded too.
- Only genuine Tenkyu answer times enter the window of the last nine: a failure, a timeout, a refusal, a conflict or a degraded decision does not, so a farmer cannot shrink the median with sign-ups that a WAF refuses in a few milliseconds. Jitter of up to 4 ms is added. There is no floor once the window has samples: the median is the time a new sign-up takes, and padding a fast Tenkyu up to a floor would make a duplicate slower than a new sign-up.
- A cold instance (a burst of parallel requests starts them on purpose) has no sample yet. Its first duplicate measures one: a no-op `POST /v1/outcome` for the decision id `dec_timing_probe`, which Tenkyu answers 404 and changes nothing, one authenticated round trip. The pad is at least 150 ms plus up to 50 ms of jitter. It is still approximate: use `waitUntil`.

## If the function is killed

An isolate that is shut down, or hits its wall-clock limit, before `waitUntil`'s work finishes leaves an account with no `credits` row, and nothing that runs later can say so. So the function logs one line with the user id (never the email) BEFORE it replies, `tenkyu-signup scoring pending { user_id }`, and another when the work ends, `tenkyu-signup scoring done { user_id }`. A `pending` with no `done` is an account to reconcile. Your UI shows "setting up your credits" for a signed-in user with no row; do not let that last forever. Reconcile on a schedule (a cron, or `pg_cron`) with:

```sql
-- Accounts older than 10 minutes with no credits row: the function was killed, or the insert failed.
select u.id, u.created_at
from auth.users u
left join public.credits c on c.user_id = u.id
where c.user_id is null
  and u.created_at < now() - interval '10 minutes';
```

For each one, either grant the fail-open share (it was never scored; `decision_id` stays null so it is found with the other outage grants), or re-check it:

```sql
insert into public.credits (user_id, granted, decision_id)
values ('<user id>', round(300 * 0.25), null)   -- GRANT x FAIL_OPEN_CREDIT_FRACTION
on conflict (user_id) do nothing;
```

A re-check with the same `request_id` only returns the stored decision if the body is identical; with a different IP or device it answers 400 `request_id reused`, so re-check with a fresh `request_id` (and only if the sign-up IP and device are still known), otherwise grant the quarter. Users who signed up by another route than this function also appear in the query: filter them out if you have any.

## Rate limit and body cap

- **Rate limit.** A token bucket per client IP (burst 20, 15 per minute) and per device id (burst 5, 5 per minute) runs before anything else, so a flood is answered `429` here and cannot push Tenkyu into its own 429s. IPv6 is keyed by its /64 (one subscriber usually owns a whole /64 and can rotate through 2^64 addresses in it), and `::ffff:a.b.c.d` is keyed as the IPv4 address. If there is no valid global IP there is no IP bucket (a shared "unknown" bucket would throttle everyone). **The buckets live in memory, and serverless instances do not share memory**: every instance counts alone, so this is an example, not a defence against a distributed flood. For production keep the counters in a shared store: Upstash Redis, or a Supabase table updated by an RPC function, behind the same `take(key)` shape (`guards.ts`).
- **Body cap.** The body is read as a stream and cut off at 8 KB, so a chunked upload with no or a false `Content-Length` is not buffered.

## Honest limits

- It fails open, with a reduced grant, for outages only. If Tenkyu times out (1500 ms), the network fails, it returns 5xx or 429, or it answers a decision flagged `degraded`, the user keeps the account and gets at most `FAIL_OPEN_CREDIT_FRACTION` of the grant (default 0.25: 75 of 300, `decision_id` null) and `tenkyu unreachable` is logged (a flagged 200 is logged as a decision). A rejected key (Tenkyu's 401 + `unauthorized`) or a missing key gets the same quarter, plus one error log a minute. A 401/403 from something in between, a 2xx that is not a JSON decision (an HTML block page answered 200, a `credit_fraction` that is not a finite number from 0 to 1) and any other 4xx are different: they fail closed with 0 credits (see above). A full grant here would let anyone who can slow Tenkyu down, or flood its rate limit, collect 25 x 300 = 7,500 credits from 25 sign-ups; a quarter gives 1,875. The trade-off: an honest user who signs up during an outage gets a quarter grant. Find them (`decision_id is null and granted > 0`) and top them up later if you like. A bad or expired API key also gets only the quarter, not zero, but then every sign-up goes unscored, so watch the logs for `Tenkyu API key rejected` after every deploy and key rotation.
- The IP rule above is unconfirmed until you test it. A wrong hop count can feed Tenkyu an internal address and make unrelated users look like one network.
- The device id is a random string in localStorage. Clearing storage or a new browser profile gives a new one. It links lazy repeats, not careful farmers.
- A blocked sign-up gets an account with 0 credits and a server-side `notice_pending` flag. The cost is that a real person wrongly blocked has an account and no credits until support grants them (see above).
- Credits are granted at sign-up, before the email is confirmed. Tenkyu cannot verify inbox ownership; let users spend credits only once `email_confirmed_at` is set.
- The rate limit is per instance and in memory (see above); use a shared store in production.
- The function creates accounts before it checks them, so an attacker who wants to hold an email address can still register it first (with confirmation on, it stays unconfirmed and the real owner can reset it). That is how any sign-up form behaves; what changed is that doing so can no longer burn the owner's Tenkyu check or earn anyone credits.
- If the function is killed before the background work finishes, see "If the function is killed" below.
- If the credits insert fails after the user exists, the sign-up still succeeds, and the error is logged with the user id for a manual fix.
- CORS stops other websites in a browser. It is not authentication: curl and scripts bypass it.
- Tests use fakes, so they do not prove the Supabase or Tenkyu wire formats. Run one sign-up against a `tk_test_` key and a test project first.

### Why the IP header is still marked unverified (checked 2026-11-27)

Supabase does not document which `x-forwarded-for` entry its edge adds for Edge Functions, and a Supabase community
thread shows the header can carry client-supplied values (https://github.com/orgs/supabase/discussions/34647).
So the function trusts the rightmost entry by default (the one the last proxy appended) and never the leftmost.
Before relying on IP signals, deploy once with `DEBUG_IP_HEADERS=1`, call it from your phone, and check which entry
holds your real address.
