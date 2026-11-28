// Night-2 red-team round 3 (attacks3): the after()/padding/bad-key changes of commit 9017005. Each test FAILS today
// because the attack works; it passes once the named fix is in. No network: Tenkyu's fetch and the user store are fakes.
//
// Run from examples/vercel-nextjs:
//   DENO_NO_PACKAGE_JSON=1 deno test --allow-env --import-map=test/import_map.json --unstable-sloppy-imports test/signup_attacks3_test.ts
import assert from "node:assert/strict";
import { Tenkyu } from "tenkyu";
import { NextRequest } from "next/server";
import { createSignupHandler, type SignupDeps } from "../lib/signup";
import type { CreditGrant } from "../lib/app-auth";

const GRANT = 100;
const DEVICE = "0b6f1f2e-4c1d-4a55-9a0e-1f2d3c4b5a69";
const DEVICE_2 = "1c7a2a3f-5d2e-4b66-8b1f-2a3e4d5c6b7a";
const ALLOW = () => new Response(JSON.stringify({
  id: "dec_ok", action: "allow", score: 0.1, credit_fraction: 1, reasons: [], signals: [], network_match: false,
  credits_exhausted: false, mode: "live",
}), { status: 200 });
const TRIGGER = /<script/i;

interface Opts {
  deps?: Partial<SignupDeps>;
  known?: Map<string, string>;
  /** Re-submitting an email whose account is not confirmed yet returns the SAME user (Supabase does this). */
  unconfirmedResubmit?: boolean;
  /** Virtual clock: fake fetch advances it by the delay, monotonic reads it, sleep advances it. */
  clock?: { t: number };
}

function setup(tenkyu: (rawBody: string, ua: string) => Response | Promise<Response>, o: Opts = {}) {
  const grants: CreditGrant[] = [];
  const stored = new Map<string, CreditGrant>(); // primary key user_id: the first grant wins
  const logs: unknown[][] = [];
  const known = o.known ?? new Map<string, string>();
  const client = new Tenkyu({
    apiKey: "tk_live_secret", baseUrl: "https://tenkyu.test", timeoutMs: 500, failOpenCreditFraction: 0.25,
    fetch: ((_u: string, init?: RequestInit) => {
      const body = String(init?.body);
      return Promise.resolve(tenkyu(body, (JSON.parse(body) as { user_agent?: string }).user_agent ?? ""));
    }) as unknown as typeof fetch,
    logger: { error: (m) => { logs.push([m]); } },
  });
  const works: Promise<void>[] = [];
  const clock = o.clock;
  const POST = createSignupHandler({
    tenkyu: () => client,
    createUser: (u) => {
      const existing = known.get(u.email);
      if (existing !== undefined) {
        return Promise.resolve(o.unconfirmedResubmit ? ({ ok: true, userId: existing } as const) : ({ ok: false, reason: "email_taken" } as const));
      }
      const id = `u${known.size + 1}`;
      known.set(u.email, id);
      return Promise.resolve({ ok: true, userId: id } as const);
    },
    grantCredits: (g) => { grants.push(g); if (!stored.has(g.userId)) stored.set(g.userId, g); return Promise.resolve(); },
    verifiedLinks: () => Promise.resolve(undefined),
    grant: GRANT,
    failOpenCreditFraction: 0.25,
    rateLimit: { perIp: { capacity: 1e6, perMinute: 1e6 }, perDevice: { capacity: 1e6, perMinute: 1e6 } },
    log: { log: (...a) => { logs.push(a); }, warn: (...a) => { logs.push(a); }, error: (...a) => { logs.push(a); } },
    ...(clock ? { monotonic: () => clock.t, sleep: (ms: number) => { clock.t += ms; return Promise.resolve(); } } : {}),
    ...o.deps,
  });
  return { POST, grants, stored, logs, works, known };
}

let n = 0;
let ipn = 0;
function req(email: string, headers: Record<string, string> = {}, device = DEVICE): NextRequest {
  return new NextRequest("https://app.example.com/api/signup", {
    method: "POST",
    headers: {
      "content-type": "application/json", "user-agent": "TestUA/1.0", "x-real-ip": `203.0.113.${(++ipn % 250) + 1}`,
      cookie: `tk_did=${device}`, ...headers,
    },
    body: JSON.stringify({ email, password: "correct horse battery" }),
  });
}
const fresh = () => `farm${++n}@gmail.com`;
const grantOf = (t: ReturnType<typeof setup>, email: string) => t.stored.get(t.known.get(email) as string)?.credits ?? -1;

// ---------- A. a gateway can still turn a block into the 25% grant ----------
// lib/signup.ts:235 (isOutage includes invalid_response) + clients/js/src/index.ts:184. A block page answered
// 200 text/html is read as a "malformed answer" outage. Fix: JS client throws `refused` for a 2xx with a
// non-JSON content-type; the recipe already fails `refused` closed (lib/signup.ts:243).
Deno.test("attack3 A: a WAF block page answered 200 text/html (user-agent trigger) gets the 25% fail-open grant", async () => {
  const t = setup((_b, ua) => TRIGGER.test(ua)
    ? new Response("<html>Request blocked</html>", { status: 200, headers: { "content-type": "text/html" } })
    : ALLOW());
  const control = fresh();
  await t.POST(req(control));
  assert.equal(grantOf(t, control), GRANT, "control: no trigger, full grant");
  const farmer = fresh();
  await t.POST(req(farmer, { "user-agent": "Mozilla <script>alert(1)</script>" }));
  assert.equal(grantOf(t, farmer), 0, "a gateway's HTML block page must not become a quarter grant");
});

// A2. A gateway that reflects the user agent into its own 401 JSON lets the farmer write Tenkyu's
// "unauthorized" code himself (JSON.parse keeps the last duplicate key). Fix: compare the raw body text with
// Tenkyu's exact error serialization in clients/js/src/index.ts toError (and handler.ts isTenkyuUnauthorized).
Deno.test("attack3 A2: a 401 that reflects the user agent unescaped forges Tenkyu's unauthorized body (25% grant, 'key rejected' log)", async () => {
  const t = setup((_b, ua) => new Response(`{"error":{"code":"waf_blocked","message":"blocked agent: ${ua}"}}`, {
    status: 401, headers: { "content-type": "application/json" },
  }));
  const farmer = fresh();
  await t.POST(req(farmer, { "user-agent": 'x","code":"unauthorized","message":"missing or invalid API key' }));
  assert.equal(grantOf(t, farmer), 0, "forged unauthorized body must not grant");
  assert.ok(!JSON.stringify(t.logs).includes("API key rejected"), "and must not blame the API key");
});

// ---------- B. background-grant failure modes ----------
// B1. lib/signup.ts:179-180: `deps.defer(work)` is not guarded. On Next < 15.1 `after` is undefined (route.ts:16
// "after is not a function"), or `after` is called outside a request scope: it throws AFTER createUser made the
// account. New sign-ups then answer 500 while duplicates answer 200 {ok:true}: the enumeration oracle the
// change removed, and the user retries into a duplicate. Fix: try { deps.defer(work) } catch { await work }.
Deno.test("attack3 B1: a throwing defer (older Next, no after) makes a new sign-up answer 500 while a duplicate answers 200", async () => {
  const t = setup(ALLOW, { deps: { defer: () => { throw new TypeError("after is not a function"); } } });
  const email = fresh();
  let fresh_: string;
  try {
    const res = await t.POST(req(email));
    fresh_ = `${res.status} ${await res.text()}`;
  } catch {
    fresh_ = "threw (500)";
  }
  const dup = await t.POST(req(email));
  const dupText = `${dup.status} ${await dup.text()}`;
  assert.equal(fresh_, dupText, `new: ${fresh_} / registered: ${dupText}`);
});

// B2. A function killed before after() finishes (deploy, timeout, crash) leaves an account with no credits row
// and not one log line: the only failure log is in .catch(), which never runs for a killed process, and the
// README tells the UI to treat a missing row as "still setting up" forever. Fix: log {userId} BEFORE replying
// (or write the 0-credit pending row first) so a reconciliation can find accounts with no row.
Deno.test("attack3 B2: killed before after() completes leaves no row and no log naming the account", async () => {
  const works: Promise<void>[] = [];
  const t = setup(() => new Promise<Response>(() => {}) /* Tenkyu never answers: the platform freezes us first */, {
    deps: { defer: (w) => { works.push(w); } },
  });
  const email = fresh();
  await t.POST(req(email));
  const userId = t.known.get(email) as string;
  // "Killed" = nothing that runs later counts. Look at what exists the moment the response is gone.
  assert.ok(
    t.grants.length > 0 || JSON.stringify(t.logs).includes(userId),
    "no credits row and no log line with the user id: an unscored account cannot be found or fixed",
  );
}, );

// B3. Re-submitting an email whose account is still unconfirmed returns the same user id; its second check reuses
// request_id with a different body (other IP, device or user agent) and Tenkyu answers 400 at once
// (hosted/api/service.py:500). That 400 fails closed and its 0-credit row reaches the primary key BEFORE the
// first, slower check finishes: the real user's grant is lost for good (first row wins). Before after() both
// checks ran inside their own requests, so the user could not resubmit this fast. Fix: scoreAndGrant must not
// write a fail-closed row for a request_id conflict (400 "reused with a different body"), or grantCredits
// must upsert when the stored row has needsReview/noticePending and the new decision is a real one.
Deno.test("attack3 B3: a second sign-up for an unconfirmed email burns the real user's grant (first row wins)", async () => {
  const seen = new Map<string, string>();
  const works: Promise<void>[] = [];
  const t = setup(async (raw) => {
    const b = JSON.parse(raw) as { request_id: string; user_agent?: string; device_id?: string; ip?: string };
    const canonical = JSON.stringify({ u: b.user_agent, d: b.device_id, i: b.ip });
    const prev = seen.get(b.request_id);
    if (prev !== undefined && prev !== canonical) {
      return new Response(JSON.stringify({ error: { code: "invalid_request", message: "request_id reused with a different body" } }), { status: 400 });
    }
    seen.set(b.request_id, canonical);
    await new Promise((r) => setTimeout(r, 80)); // a normal check
    return ALLOW();
  }, { unconfirmedResubmit: true, deps: { defer: (w) => { works.push(w); } } });
  const victim = fresh();
  await t.POST(req(victim)); // the real user
  await t.POST(req(victim, { "user-agent": "OtherUA/2.0" }, DEVICE_2)); // a retry from another device, or an attacker who knows the email
  await Promise.all(works);
  assert.equal(grantOf(t, victim), GRANT, "the account must end with the real grant");
});

// ---------- C. the duplicate-email padding as a side channel (no `defer`: older runtime, custom wiring, tests) ----------
// C1. lib/signup.ts:185,191-195: every new sign-up's duration enters the 9-sample window, including FAST failures
// a farmer can cause (a WAF refusing his user agent answers in a few ms). Five of those make the median tiny,
// so duplicates answer at once while normal new sign-ups still wait for Tenkyu: the timing oracle is back, and
// he can keep the window poisoned. Fix: record a duration only for a check that got a real Tenkyu answer
// (not for failedClosed or not-configured), and never pad below a floor such as timeoutMs / 2.
Deno.test("attack3 C1: five fast farmer-caused failures poison the padding median and expose registered emails", async () => {
  const clock = { t: 0 };
  const t = setup((_b, ua) => {
    if (TRIGGER.test(ua)) { clock.t += 3; return new Response("<html>403</html>", { status: 403 }); }
    clock.t += 300;
    return ALLOW();
  }, { clock });
  const timed = async (email: string, headers: Record<string, string> = {}) => {
    const t0 = clock.t;
    await t.POST(req(email, headers));
    return clock.t - t0;
  };
  for (let i = 0; i < 9; i++) await timed(fresh()); // honest traffic: median 300
  const victim = fresh();
  await timed(victim);
  const control = await timed(victim);
  assert.ok(Math.abs(control - 300) < 5, `control: a duplicate is padded to ${control} ms`);
  for (let i = 0; i < 5; i++) await timed(fresh(), { "user-agent": "<script>" }); // the farmer poisons the window
  const dup = await timed(victim);
  const brandNew = await timed(fresh());
  assert.ok(Math.abs(dup - brandNew) < 50, `registered ${dup} ms vs new ${brandNew} ms`);
});

// C2. First requests on a cold instance (serverless instances do not share `durations`): the window is empty, so a
// duplicate waits exactly DEFAULT_PAD_MS = 100 while a new sign-up takes a real check. A burst of parallel
// requests makes the platform start fresh instances, so the attacker gets cold ones on purpose. Fix: refuse to
// run without `defer` outside tests (throw in createSignupHandler when NODE_ENV === "production"), or pad a
// cold duplicate to the Tenkyu timeout.
Deno.test("attack3 C2: a cold instance pads a duplicate to a fixed 100 ms while a new sign-up takes the real check time", async () => {
  const known = new Map<string, string>();
  const clock = { t: 0 };
  const a = setup(() => { clock.t += 400; return ALLOW(); }, { known, clock });
  const victim = fresh();
  await a.POST(req(victim)); // registered through instance A
  const cold = setup(() => { clock.t += 400; return ALLOW(); }, { known, clock }); // instance B: empty window
  const t0 = clock.t;
  await cold.POST(req(victim));
  const dup = clock.t - t0;
  const t1 = clock.t;
  await cold.POST(req(fresh()));
  const brandNew = clock.t - t1;
  assert.ok(Math.abs(dup - brandNew) < 50, `registered ${dup} ms vs new ${brandNew} ms`);
});
