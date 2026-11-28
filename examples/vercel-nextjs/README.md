# Vercel + Next.js (App Router) recipe

Scores each sign-up with Tenkyu (allow, review, block) and grants free AI credits scaled by `credit_fraction`.

- `middleware.ts`: sets a first-party `tk_did` device cookie (random UUID, 1 year, Secure, HttpOnly, SameSite=Lax).
- `app/api/signup/route.ts`: one line that wires `lib/signup.ts` to your app (a route file may only export HTTP methods).
- `lib/signup.ts`: validates the body and normalizes the email, rate-limits, **creates the account first**, then (only for a new account) collects IP, user agent and `tk_did`, calls Tenkyu, and grants `Math.round(GRANT * credit_fraction)`.
- `lib/guards.ts`: strict IP check, rate-limit key (IPv6 /64), token bucket, capped body read, request id.
- `lib/tenkyu.ts`: server-only client, one per server instance.
- `lib/app-auth.ts`: **placeholder** for your own `createUser`, `grantCredits` and verified-links lookup. Both throw in production on purpose.
- `lib/supabase-auth.ts`: a tested Supabase Auth adapter for `createUser` and `grantCredits` (see "Using Supabase Auth"). Optional.
- `test/`: Deno tests with a `next/server` shim, no network.

## Setup in 5 steps

1. Copy this folder's `middleware.ts`, `app/api/signup/`, and `lib/` (all six files; `lib/supabase-auth.ts` only if you use Supabase) into your Next.js app (keep `middleware.ts` at the project root, or in `src/` if you use it).
2. Install the client. **The `tenkyu` package is not published to npm yet, so `npm install tenkyu` will not work today.** Until it is, install it from a clone of this repo: `cd /path/to/tenkyu/clients/js && npm install && npm run build && npm pack` creates `tenkyu-0.1.0.tgz`; then in your app run `npm install /path/to/tenkyu/clients/js/tenkyu-0.1.0.tgz` (a tarball is safest on Vercel, because a `file:` link to a folder outside your repo does not exist at build time; commit the `.tgz` into your repo, for example under `vendor/`, and install that path). When it is published this becomes `npm install tenkyu`. Then delete the `paths` block in `tsconfig.json` (it only points at the local client so this repo can type-check the example) and delete `types/next-shims.d.ts` and `test/` if you do not want the tests.
3. In Vercel: Project, Settings, Environment Variables: add `TENKYU_API_KEY` (use a `tk_test_` key for Preview, `tk_live_` for Production). Locally, copy `.env.example` to `.env.local`.
4. Replace `createUser` and `grantCredits` in `lib/app-auth.ts` with your auth or database calls. `createUser` must return `email_taken` for an existing email (use a unique constraint). `grantCredits` must be idempotent per `userId`. Store `tenkyuDecisionId` with the grant. `createUser` receives `{ email, password }`; the route has already checked the password (8 to 72 characters, UTF-8 bytes at most 72 because Supabase and most auth libraries hash with bcrypt) before anything is created, and it never echoes or logs it. Pass it to your auth provider and nowhere else. (Magic-link or OAuth only? Remove the password check in `parseInput` and the field in `NewUser`.)
5. Have your sign-up form `fetch("/api/signup", { method: "POST", headers: {"content-type": "application/json"}, body: JSON.stringify({ email, password }) })`. The answer is always `{ "ok": true }` and the credits are stored a moment later (see "A real user got no credits"): after sign-in, read the user's credits row, treat a missing row as "still setting up", and show the notice when `noticePending` is set. Later, once you know a user was abusive (or real), report it server-side with `getTenkyu().outcome(decisionId, "farmer" | "real")`.

## Using Supabase Auth

`lib/supabase-auth.ts` is a ready `createUser` and `grantCredits` for Supabase, server side only, with no network in its tests. Run `../supabase-edge-function/sql/credits.sql` in your project, `npm install @supabase/supabase-js`, set `SUPABASE_URL`, `SUPABASE_ANON_KEY` and `SUPABASE_SERVICE_ROLE_KEY` in Vercel (**no `NEXT_PUBLIC_` prefix on the service-role key**: it bypasses row-level security, and `lib/supabase-auth.ts` imports `server-only` so a client component importing it fails the build), then in `lib/app-auth.ts`:

```ts
import { createClient } from "@supabase/supabase-js";
import { supabaseAdapterFromEnv } from "./supabase-auth";
const supabase = supabaseAdapterFromEnv(createClient);
export const createUser = supabase.createUser;       // replaces the placeholder function
export const grantCredits = supabase.grantCredits;   // replaces the placeholder function
```

How it behaves, ported from the Supabase Edge Function recipe: `createUser` uses `auth.signUp` on an anon-key client (Supabase sends its normal confirmation email and applies its password policy). The account is created first, then Tenkyu is asked, then one `credits` row is written (primary key `user_id`, so a repeat is harmless). An email that already exists is answered like a new sign-up in both Supabase modes, so the route cannot be used to list accounts: with confirmations on, Supabase returns a fake user with `identities.length === 0`; with confirmations off it returns `user_already_exists` (or `email_exists`, or older "already registered" text). Both become `email_taken`: no Tenkyu call, no credits. Only a password-policy refusal (`weak_password`, or Supabase's "Password ..." text) comes back as `400 { error: "signup_failed", message }`. Every other `signUp` error (an email-send rate limit, an invalid-address check, an outage) becomes `provider_error`: the route answers `{ "ok": true }` exactly as for a duplicate, makes no Tenkyu call, grants nothing, and logs the error code (never the message, which can contain the email). Showing it would be an oracle: email-send limits only happen for new addresses. **Timing:** `signUp` itself can take longer for a new address, because Supabase sends a confirmation email. Keep it out of the response path (enable Supabase's async email sending / an Auth send-email hook that queues the mail) or pad the duplicate answer; `defer` and the built-in padding only cover the Tenkyu step. The `credits` insert is the only use of the service-role key.

## Environment variables (server only, never `NEXT_PUBLIC_`)

| Name | Default | Purpose |
| --- | --- | --- |
| `TENKYU_API_KEY` | required | `tk_live_...` or `tk_test_...` |
| `SIGNUP_CREDITS` | 100 | Base grant for a clean signup |
| `FAIL_OPEN_CREDIT_FRACTION` | 0.25 | Share of the grant when Tenkyu could not score the signup (timeout, network error, 5xx, 429, or a decision flagged `degraded`). 0 to 1 |
| `TENKYU_TIMEOUT_MS` | 1500 | Per-check timeout. Raise to 4000 if the hosted API has cold starts |
| `TENKYU_BASE_URL` | `https://api.tenkyu.dev` | Override only for self-hosting or tests (https; http only for localhost) |
| `TENKYU_ALLOW_INLINE_SCORING` | unset | Set to `1` to run in production WITHOUT `defer` (see "Timing"). Otherwise `createSignupHandler` throws at startup when `NODE_ENV=production` and `defer` is missing |
| `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY` | none | Only for `lib/supabase-auth.ts`. The service-role key is server only |

## What the browser sees

Only `{ "ok": true }` or a validation error (`invalid_request`, `weak_password`, `signup_failed`, `rate_limited`). Tenkyu's reasons, signals, score and the credit amount stay on the server, so an attacker cannot tune a farm against them. `review` grants the scaled amount and sets `needsReview` for you to act on. `block` is **not** a 403: that would be an oracle ("am I flagged?") and would lock out a real person who was wrongly blocked. The account has 0 credits (`blocked: true` is passed to `grantCredits`) and the answer is the same `{ "ok": true }`; a `noticePending` flag is stored with the row instead (below). A duplicate email gets the same answer, in the same time, so the route cannot be used to find out which emails have accounts; send the real owner an "you already have an account" email from your own mailer.

## A real user got no credits

When the account is created but gets 0 credits (Tenkyu said `block`, or the check failed closed, see below), the sign-up answer is still the plain `{ "ok": true }`. A notice in that answer would tell a caller whose sign-ups are blocked which emails are new (notice) and which are already registered (no notice). Instead the route passes `noticePending: true` to `grantCredits`; the Supabase adapter stores it as `notice_pending` on the credits row (add the column, see `examples/supabase-edge-function/sql/credits.sql`). Your app shows the notice AFTER sign-in, to the signed-in user only, when their row says `notice_pending` (or `granted = 0`): `DEFAULT_NO_CREDITS_NOTICE`, "We couldn't give free credits to this account automatically. Contact support to get them.", or your own text. It says nothing about the verdict, score or reasons. Set `noCreditsNotice: null` in `createSignupHandler` to leave the flag off and show your own empty-balance message.

**Timing.** A new sign-up waits for Tenkyu and a duplicate email would not, which is a second way to list accounts. `route.ts` passes `defer: (work) => after(work)` (Next 15.1+), so the response goes out first for both and the check plus the grant run after it. The consequence: the credits row appears a second or so after the response (up to `TENKYU_TIMEOUT_MS`), so a freshly signed-in user may briefly have no row; show "setting up your credits" rather than 0. Without `defer` (tests, or a platform with no after/waitUntil) the check runs before the answer and a duplicate email waits for the median of the last nine check times instead. That padding is a guess, so:

- **In production `createSignupHandler` refuses to start without `defer`** (it throws at module load when `NODE_ENV=production`), **unless `TENKYU_ALLOW_INLINE_SCORING=1`**, which accepts the approximate padding.
- If `defer` exists but throws (Next before 15.1 has no `after`, or it is called outside a request scope), the work is awaited inline and the answer is the same generic `{ ok: true }`; from then on duplicates are padded too.
- Only genuine Tenkyu answer times enter the window of the last nine: a failure, timeout, refusal, request-id conflict or degraded decision does not, so a farmer cannot shrink the median with sign-ups a WAF refuses in a few milliseconds. Jitter of up to 4 ms is added. There is no floor once the window has samples (the median is the time a new sign-up takes; padding a fast Tenkyu up to a floor would make duplicates slower than new sign-ups).
- A cold instance (a burst of parallel requests starts them on purpose) has no sample. Its first duplicate measures one with a no-op `client.outcome("dec_timing_probe", "real")` (Tenkyu answers 404 and changes nothing: one authenticated round trip). The pad is at least 150 ms plus up to 50 ms of jitter. Still approximate: use `defer`.

### If the function is killed

A function killed before the `after()` work finishes (deploy, timeout, crash) leaves an account with no credits row, and nothing that runs later can say so. So the route logs one line with the user id (never the email) BEFORE it replies, `signup scoring pending { userId }`, and another when the work ends, `signup scoring done { userId }`. A `pending` with no `done` is an account to reconcile. Show "setting up your credits" for a signed-in user with no row, but do not let that last forever: reconcile on a schedule. With the Supabase adapter:

```sql
-- Accounts older than 10 minutes with no credits row: the function was killed, or the insert failed.
select u.id, u.created_at
from auth.users u
left join public.credits c on c.user_id = u.id
where c.user_id is null
  and u.created_at < now() - interval '10 minutes';
```

For each one, grant the fail-open share (it was never scored; `decision_id` stays null so it is found with the other outage grants): `insert into public.credits (user_id, granted, decision_id) values ('<id>', round(100 * 0.25), null) on conflict (user_id) do nothing;` (GRANT x `FAIL_OPEN_CREDIT_FRACTION`). Or re-check with a fresh `request_id`, only if the sign-up IP and device are still known: a re-check with the old `request_id` and a different body answers 400 `request_id reused`. With another store, the query is the same idea: users with no credits row older than N minutes.

When that person writes to support:

1. Grant the credits by hand through your own admin path (with the Supabase adapter: `update public.credits set granted = 100 where user_id = '...'`, or an admin screen that calls the same table).
2. Tell Tenkyu it was a real user, so it learns. You need the decision id you stored in `tenkyuDecisionId` (with the Supabase adapter, `credits.decision_id`; it is null for a degraded grant, which cannot take an outcome). From your server:
   ```ts
   await getTenkyu().outcome(decisionId, "real"); // the API value is "real"; the opposite is "farmer"
   ```
   or `curl -X POST https://api.tenkyu.dev/v1/outcome -H "authorization: Bearer $TENKYU_API_KEY" -H "content-type: application/json" -d '{"id":"dec_...","outcome":"real"}'`. One outcome per decision; a later call replaces it. Never show the decision id to the user.

## Links: verified phone or card

Tenkyu holds a new sign-up at 50% of the grant (the starter hold) until something real vouches for it. A verified phone or a card fingerprint is what lifts an honest user above that 50%. Implement `getVerifiedLinks({ email })` in `lib/app-auth.ts` to return `{ phone: "+14155550100" }` for a phone YOUR app verified (an OTP you ran). It is sent to Tenkyu as `links: { phone }`. It comes only from your server-side state, never from the request body (a browser could claim any number); the route ignores `phone` and `links` in the body. The value must be E.164 or it is dropped. The placeholder returns nothing.

## Release the rest after email verification

A `review` decision grants 25% of the grant, and many honest users land there because they share a network (a school, a carrier, a family). When the user later confirms their email, release the rest. The `check()` answer says what proof is needed: `decision.earn_back.requires` is `"email"` when everything that fired is what a shared network produces, and `"phone_or_card"` otherwise (then an email is not enough; send a phone or card YOUR app verified, see "Links" above). Call it from your own email-confirmation handler, never from a browser-supplied claim:

```ts
// in the handler that runs after the user clicked the confirmation link (server side)
import { GRANT, getTenkyu } from "@/lib/tenkyu";
import { requestIdForUser } from "@/lib/guards";

const result = await getTenkyu().earnBack(await requestIdForUser(userId), "email_verified");
if (result.granted) {
  await addCredits(userId, Math.round(GRANT * result.extra_credit_fraction)); // 0.75 of the grant for a plain review
}
```

It is idempotent per sign-up (a second call returns the same answer, so do the top-up once, keyed on your own record), throws on failure (the user keeps the 25% and you can retry), and returns `granted: false` for a decision that is not a review, whose proof does not satisfy `requires`, or that you marked as a farmer since. For `phone_or_card` pass the verified value: `earnBack(requestId, "phone_verified", { phone })`. While your account is in shadow mode the answer is `granted: false` with `would_extra_credit_fraction`, because the full grant was already given. API details: `hosted/API.md`, "POST /v1/earn-back".

## Order of operations, request id, duplicates

1. Validate locally: content type, size, JSON shape, the email (trimmed and lowercased once in `parseInput`, then shape, length and control-character checks) and the password (8 to 72 characters). A failure answers `400`/`413` and nothing else runs.
2. `createUser({ email, password })`. This creates the account, or says `email_taken`.
3. **Only for a newly created account**: `POST /v1/check` with `request_id = sha256("tenkyu-signup:" + userId)`, then `grantCredits`.

The older design derived `request_id` from the email and day and called Tenkyu before creating the account. A retry with a different device, IP or letter case then reached Tenkyu with the same id and a different body and got a 400. A 400 was treated as degraded, which was a free 25% grant. And anyone could submit a victim's email first and burn that id. Now the id comes from the new account: it is unique per person, a repeat for the same account is idempotent, and nobody else can pre-empt it.

- **Existing email.** `createUser` returns `email_taken`; the route answers `{ "ok": true }` like a new sign-up, calls Tenkyu for nothing and grants nothing (the one difference left is timing: this path skips the Tenkyu call).
- **Bad or expired API key grants the small fail-open share.** Tenkyu answers a bad key with exactly status 401, a JSON content-type and the body `{"error":{"code":"unauthorized","message":"missing or invalid API key"}}`, compared by the client as raw text (not as parsed JSON: a gateway that echoes the farmer's user agent into its own 401 JSON can write a duplicate `"code":"unauthorized"` key that parses fine). Only that exact answer is treated as our misconfiguration, like an outage: 25% of the grant, `tenkyuDecisionId` null, and one error line, `Tenkyu API key rejected: check TENKYU_API_KEY`, at most once a minute. Set an alert on that line: it means every signup is going unscored. A missing `TENKYU_API_KEY` (the client cannot be created) is handled the same way, with its own once-a-minute line naming `TENKYU_API_KEY`.
- **Any other 401/403, and any 2xx that is not a JSON decision, fails closed.** Tenkyu never sends a 403, a 401 with another body, or an HTML page. One that arrives comes from something in front of it (a gateway, WAF or proxy; a block page answered `200 text/html` is common), and a farmer can trigger a WAF rule with his user agent or email, so failing open would turn a block into a 25% grant. A 2xx counts as not a decision when its media type is not JSON (a missing header or `text/plain` is tolerated, but the whole body must then still be a valid decision) or its body is not a valid decision. The route grants 0 credits (a row with `needsReview` and `noticePending`) and logs `Tenkyu call refused by something in between (status N)` at most once a minute. Look at your gateway rules.
- **Other Tenkyu 4xx fails closed.** A 400 (bad body) or 404 means our request was wrong, not that Tenkyu is down. The route logs the code and status at error level and grants 0 credits (a row with `needsReview` and `noticePending`, so the user sees the notice and you can find the row); grant by hand after fixing the cause. Network errors, timeouts, 5xx, 429 and a rejected key fail open.
- **Submitting an unconfirmed email twice does not burn the grant.** With Supabase confirmations on, a second sign-up for a not-yet-confirmed email returns the SAME user id, so the second check reuses the `request_id` with another IP, device or user agent and Tenkyu answers `400 request_id reused with a different body`. The route recognises that 400 and writes nothing: the first check's row stands and no 0-credit row can take its place (it logs a warning, `request_id conflict`).
- **`degraded: true` from Tenkyu.** A 200 flagged `degraded` was answered from an empty in-memory engine, not a real score. The JS client caps it at `failOpenCreditFraction`, the route caps it again, and `tenkyuDecisionId` is `null`, so outage grants can be found later.
- **If `grantCredits` fails** after the account exists, the route still answers `{ "ok": true }` and logs the user id and amount (no email) for a manual fix.

## Rate limit and body cap

- **Rate limit.** A token bucket per client IP (burst 20, 15 per minute) and per `tk_did` cookie (burst 5, 5 per minute) runs before the Tenkyu check, so a flood gets `429` here and cannot push Tenkyu into its own rate limit. IPv6 is keyed by its /64 (one subscriber usually owns a whole /64 and can rotate through 2^64 addresses in it), and `::ffff:a.b.c.d` is keyed as the IPv4 address. No valid IP, no IP bucket. **The buckets live in memory and serverless instances do not share memory**, so each instance counts alone: this is an example, not a defence against a distributed flood. For production keep the counters in a shared store (Upstash Redis from the Vercel Marketplace, or a Supabase table) behind the same `take(key)` shape (`lib/guards.ts`, `createRateLimiter`). Change the numbers in `DEFAULT_RATE_LIMIT` (`lib/signup.ts`).
- **Body cap.** The body is read as a stream and cut off at 8 KB (`413`), even with no or a false `Content-Length`.

## Shadow mode

While your Tenkyu account is in shadow mode (7 days from when the account was created, not from when you integrated; extend it in Settings), `check()` returns `action: "allow"` and `credit_fraction: 1`, plus `shadow: true`, `would_action` and `would_credit_fraction`. The route grants by `credit_fraction` as usual, passes `shadow`, `wouldAction` and `wouldCreditFraction` to `grantCredits` so you can store them, and logs one `tenkyu shadow` line (decision id and the would-be values, no email) so you can see what enforcement will do before it starts.

## Tests

`npm test` from this folder (needs Deno): `DENO_NO_PACKAGE_JSON=1 deno test --allow-env --import-map=test/import_map.json --unstable-sloppy-imports test/` (all the suites, including the attack tests). A `next/server` shim stands in for Next.js and Tenkyu's `fetch` is a fake.

## Type-checking without installing Next.js

The repo does not install Next.js. `types/next-shims.d.ts` declares a minimal `next/server` (`NextRequest`, `NextResponse`), `server-only` and `process.env`, and `tsconfig.json` maps `tenkyu` to `clients/js/src`. From the repo root: `clients/js/node_modules/.bin/tsc --noEmit -p examples/vercel-nextjs` (plain `npx tsc` works only where TypeScript is already installed; otherwise npx would download it). This checks types only; the example has not been run on a real Next.js or Vercel deployment.

## Honest limits

- **Fails open, with a reduced grant, for outages only.** On a timeout, network error, 5xx, 429 or a decision flagged `degraded`, the signup gets at most `FAIL_OPEN_CREDIT_FRACTION` of the grant, 25 of 100 by default (`decision.degraded`, `tenkyuDecisionId` null). A full grant would give anyone who can slow Tenkyu down or flood its rate limit 25 x 100 = 2,500 credits from 25 signups; a quarter gives 625. The trade-off: an honest user who signs up during an outage gets a quarter grant; find them by the null decision id and top them up later if you like. A rejected key (Tenkyu's 401 + `unauthorized`) or a missing key gets the same quarter, plus a once-a-minute error. A 401/403 from something in between, a 2xx that is not a JSON decision (an HTML block page answered 200, a `credit_fraction` that is not a finite number from 0 to 1) and a request error (400, 404) are different: they fail closed with 0 credits and an error-level log. Watch for the `tenkyu degraded`, `Tenkyu API key rejected`, `Tenkyu call refused by something in between` and `tenkyu check rejected` log lines.
- **Cold starts.** If the hosted API sleeps when idle, the first check after a quiet spell can exceed 1500 ms and be let through unchecked. Measure it, then raise `TENKYU_TIMEOUT_MS` or keep an instance warm.
- **The cookie is a weak signal.** Privacy browsers, private windows, and anyone who clears cookies get a new id, so the device signal is missing or fresh for them. It catches careless repeat signups, not a determined farmer. Users who block all cookies have no `tk_did`, and the check runs on email and IP only.
- **Shared IPs.** Offices, campuses, VPNs and carrier NAT put many real people behind one IP. Tenkyu treats IP as a soft signal, but expect some false positives; use `review` rather than hard-blocking where you can.
- **IP header on Vercel.** The route uses `x-real-ip` only, validated as a strict IPv4/IPv6 address (`node:net` `isIP`); a port, garbage or a zone id is dropped. There is no fallback to `x-forwarded-for`: if `x-real-ip` is absent the route sends no IP rather than a forgeable one. Vercel's docs say it overwrites `X-Forwarded-For` and doesn't forward external IPs, to prevent spoofing ([request headers](https://vercel.com/docs/headers/request-headers)), and `@vercel/functions` reads the client IP from `x-real-ip`. If you put another proxy (e.g. Cloudflare) in front of Vercel, use that proxy's own client-IP header instead.
- **Double submit.** A second submit for the same email finds the account and gets the generic answer without a Tenkyu call. Still make `createUser` atomic per email (unique constraint) so two parallel requests cannot both create an account, and `grantCredits` idempotent per `userId`.
- **Account first.** An attacker can still register someone else's email before its owner does. That is how any sign-up form behaves; what changed is that it can no longer burn the owner's Tenkyu check or earn anyone credits. Confirm the email before the account can spend credits.

Related: the older [`examples/nextjs-route`](../nextjs-route) has Clerk and Supabase webhook variants.
