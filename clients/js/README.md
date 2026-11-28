# tenkyu

> TODO(Or): checked 2026-11-27: `npm view tenkyu` says the name was published before and unpublished on 2026-10-16 (by you or someone else), and `@tenkyuai/tenkyu` is free. An unpublished name can normally be claimed again after 24 hours, so try `npm publish` with `tenkyu` first; if npm refuses, rename to `@tenkyuai/tenkyu` in `package.json` (`publishConfig.access` is already `public`) and change the import below.

Install: `npm install tenkyu` (Node 18 or newer). The hosted API is launching soon; see [pricing](https://github.com/tenkyuai/tenkyu/blob/main/docs/pricing.md).

JS/TS client for the Tenkyu hosted API. Zero dependencies, uses global `fetch` (Node 18+, edge runtimes). Server-side only: never ship your API key to a browser.

```ts
import { Tenkyu } from "tenkyu";

const tenkyu = new Tenkyu({ apiKey: process.env.TENKYU_API_KEY! });
const d = await tenkyu.check({ email, ip, device_id });
const credits = Math.round(GRANT * d.credit_fraction);
if (d.action === "block") { /* refuse or grant nothing */ }
await tenkyu.outcome(d.id, "farmer"); // later, once you know
await tenkyu.earnBack(requestId, "email_verified"); // review decisions: release the rest after your app verified the email
```

While your account is in shadow mode (7 days from when the account was created, not from when you integrated; extend it in Settings), the decision has `shadow: true` with `would_action` and `would_credit_fraction` (what Tenkyu would have done); `action` is `allow` and `credit_fraction` is 1, so the code above works unchanged: grant by `credit_fraction` and log the would-be values to see what enforcement will do.

```ts
if (d.shadow) console.log("tenkyu shadow", d.id, d.would_action, d.would_credit_fraction); // ids and numbers only, no email
```

`shadow`, `would_action` and `would_credit_fraction` are optional on the `Decision` type. The client keeps each only if it has the right type (`shadow` is `true`, `would_action` is `allow|review|block`, `would_credit_fraction` is a number from 0 to 1), so a malformed one never reaches your code. Once shadow mode ends they are absent and `action` and `credit_fraction` are the real ones.

Options: `apiKey`, `baseUrl` (default `https://api.tenkyu.dev`; must be `https`, plain `http` is accepted only for `localhost`, `127.0.0.1` and `[::1]`, otherwise the constructor throws: the bearer key must not travel in clear text), `timeoutMs` (1500, one overall deadline per call), `failOpen` (true), `failOpenCreditFraction` (0.25), `logger` (default `console`; only receives the key-rejected message below).

Behaviour:
- Network error, timeout, 5xx or a second 429 with `failOpen` on: `check()` resolves `{action: "allow", credit_fraction: 0.25, degraded: true, reasons: ["tenkyu unreachable"]}`, so signup never breaks.
- Tenkyu's own bad-key answer (status 401, a JSON content-type and exactly Tenkyu's error body `{"error":{"code":"unauthorized","message":"missing or invalid API key"}}`, compared as raw text: a gateway that echoes the farmer's user agent into its own JSON can write the `unauthorized` code into a parsed object, but not match the whole text) with `failOpen` on: logs `Tenkyu API key rejected: check TENKYU_API_KEY` through `logger.error` at most once per minute per client (never the key), and `check()` resolves the same small-grant degraded decision as an outage (`credit_fraction` 0.25, `degraded: true`, `reasons: ["tenkyu api key rejected"]`). A misconfigured key must not give every real user 0 credits, and the log line is how you find out. Watch for it after every deploy and key rotation. With `failOpen: false` it throws instead. `outcome()` and `usage()` always throw.
- Any other 401 or 403, a 401 whose body is not exactly Tenkyu's, and any 2xx whose content-type is not JSON or whose JSON is not a valid decision (missing `action`, `credit_fraction` not a finite number between 0 and 1): a gateway, WAF or captive portal in front of Tenkyu, which a farmer can trigger with an odd user agent or email (an HTML block page answered `200` is common). It always throws `TenkyuError` with `code: "refused"` and the message `Tenkyu call refused by something in between (status N)`, even with `failOpen` on. It is not blamed on the key and not failed open, because failing open would turn the block a farmer should get into a 25% grant. Catch it, log it (throttled) and grant nothing. A decision the server itself flags `degraded: true` is still capped (below). `isKeyRejected(err)` and `isRefusedInBetween(err)` tell the two apart.
- Any other 4xx (400 invalid request, 404) throws `TenkyuError` with `code` and `status`: that is a bug in your request, not an outage.
- 429: waits for `Retry-After` once and retries, but only if the wait fits in what is left of `timeoutMs`; otherwise it fails open at once. The first attempt, the wait and the retry share one deadline, so `check()` takes at most about `timeoutMs`.
- A 200 with `degraded: true` (the server answered from an empty in-memory engine, not a real score) has its `credit_fraction` capped at `failOpenCreditFraction`, like a local fallback.
- `outcome()` and `usage()` throw on failure; they never fail open.

### Fail-open trade-off

A degraded decision still says `allow`, but it carries `failOpenCreditFraction` (default 0.25), not 1. If a full grant were the fail-open answer, anyone who can make Tenkyu slow, or who floods its rate limit, would get a free full grant on every sign-up (25 sign-ups with a 300-credit grant would be 7,500 credits). With 0.25 the same 25 get 1,875. The cost: an honest user who signs up during an outage gets a quarter grant. Find them with `decision.degraded` (or a null decision id) and top them up later if you like. Set `failOpenCreditFraction: 1` to restore the old behaviour, or `0` to grant nothing until scored. `degradedDecision(fraction?)` is exported for your own fallback paths.

Recipes:
- [Vercel + Next.js App Router](https://github.com/tenkyuai/tenkyu/tree/main/examples/vercel-nextjs): signup route, device cookie, server-only client.
- [Supabase Edge Function](https://github.com/tenkyuai/tenkyu/tree/main/examples/supabase-edge-function): check a signup from Supabase.

Build: `npm run build` (ESM + `.d.ts` in `dist/`). Test: `npm test`.

Publish (when you are ready; this repo has not published it): `npm test && npm pack --dry-run`, then `npm publish`. `prepublishOnly` rebuilds and re-runs the tests first.
