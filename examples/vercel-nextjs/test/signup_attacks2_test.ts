// Night-2 red-team regression tests (attacks2). Each test FAILS today because the attack works; it will pass
// once the named fix is in. No network: Tenkyu's fetch and the app's user store are fakes.
//
// Run from examples/vercel-nextjs:
//   DENO_NO_PACKAGE_JSON=1 deno test --allow-env --import-map=test/import_map.json --unstable-sloppy-imports test/signup_attacks2_test.ts
import assert from "node:assert/strict";
import { Tenkyu } from "tenkyu";
import { NextRequest } from "next/server";
import { createSignupHandler, DEFAULT_NO_CREDITS_NOTICE, type SignupDeps } from "../lib/signup";
import type { CreditGrant } from "../lib/app-auth";

const DEVICE = "0b6f1f2e-4c1d-4a55-9a0e-1f2d3c4b5a69";
const GRANT = 100;

/**
 * Tenkyu behind a gateway (Cloud Run front end, CDN or WAF). Tenkyu itself never answers 403 (hosted/api only
 * raises 401 {"error":{"code":"unauthorized"}}); a gateway does, for any request body that trips a rule. A real
 * Tenkyu block is what the farmer would get without the trigger.
 */
const WAF = /<script|union\s+select|\.\.\/|%00/i;
const gateway = (delayMs = 0) => async (rawBody: string): Promise<Response> => {
  if (delayMs > 0) await new Promise((r) => setTimeout(r, delayMs));
  if (WAF.test(rawBody)) {
    return new Response("<html><body><h1>403 Forbidden</h1></body></html>", { status: 403, headers: { "content-type": "text/html" } });
  }
  return new Response(JSON.stringify({
    id: "dec_blk", action: "block", score: 0.99, credit_fraction: 0, reasons: ["known farm"], signals: [],
    network_match: true, credits_exhausted: false, mode: "live",
  }), { status: 200 });
};

function setup(tenkyu: (rawBody: string) => Promise<Response>, deps: Partial<SignupDeps> = {}) {
  const grants: CreditGrant[] = [];
  const errors: unknown[][] = [];
  const logs: unknown[][] = [];
  const known = new Set<string>();
  const client = new Tenkyu({
    apiKey: "tk_live_secret", baseUrl: "https://tenkyu.test", timeoutMs: 500, failOpenCreditFraction: 0.25,
    fetch: ((_u: string, init?: RequestInit) => tenkyu(String(init?.body))) as unknown as typeof fetch,
    logger: { error: (m) => { logs.push([m]); errors.push([m]); } },
  });
  const POST = createSignupHandler({
    tenkyu: () => client,
    createUser: (u) => {
      if (known.has(u.email)) return Promise.resolve({ ok: false, reason: "email_taken" } as const);
      known.add(u.email);
      return Promise.resolve({ ok: true, userId: `u${known.size}` } as const);
    },
    grantCredits: (g) => { grants.push(g); return Promise.resolve(); },
    verifiedLinks: () => Promise.resolve(undefined),
    grant: GRANT,
    failOpenCreditFraction: 0.25,
    rateLimit: { perIp: { capacity: 1000, perMinute: 1000 }, perDevice: { capacity: 1000, perMinute: 1000 } },
    log: { log: (...a) => { logs.push(a); }, warn: (...a) => { logs.push(a); }, error: (...a) => { logs.push(a); errors.push(a); } },
    ...deps,
  });
  return { POST, grants, errors, logs };
}

let n = 0;
let ipn = 0;
function req(body: Record<string, unknown>, headers: Record<string, string> = {}): NextRequest {
  return new NextRequest("https://app.example.com/api/signup", {
    method: "POST",
    headers: {
      "content-type": "application/json", "user-agent": "TestUA/1.0", "x-real-ip": `203.0.113.${(++ipn % 250) + 1}`,
      cookie: `tk_did=${DEVICE}`, ...headers,
    },
    body: JSON.stringify({ password: "correct horse battery", ...body }),
  });
}

// ---------- A. a farmer-triggered gateway 403 is read as "our API key is bad": block becomes a 25% grant ----------
// lib/signup.ts:179 (401/403 branch) and clients/js/src/index.ts:176 (isKeyRejected) accept ANY 403, whatever
// the body, as a rejected key. The farmer controls the user-agent and the email (both are in the JSON the
// gateway inspects), so a WAF trigger string turns the block they would get into degraded + 25 credits.
// Smallest fix: key rejected only when status is 401 AND body.error.code === "unauthorized" (Tenkyu's own
// JSON); any other 401/403 is a client error (grant nothing, log at error level).
Deno.test("attack2 A: a gateway 403 triggered through the user-agent turns a block (0 credits) into the 25% fail-open grant", async () => {
  const t = setup(gateway());
  const control = await t.POST(req({ email: `farm${++n}@gmail.com` }));
  assert.equal(control.status, 200);
  assert.equal(t.grants[0].credits, 0, "control: without the trigger the farmer is blocked");
  const attack = await t.POST(req({ email: `farm${++n}@gmail.com` }, { "user-agent": "Mozilla/5.0 <script>alert(1)</script>" }));
  assert.equal(attack.status, 200);
  assert.equal(t.grants[1].credits, 0, "a non-Tenkyu 403 must not grant the fail-open share");
});

Deno.test("attack2 A: the same trigger through the email local part", async () => {
  const t = setup(gateway());
  await t.POST(req({ email: `<script>${++n}@gmail.com` }));
  assert.equal(t.grants[0].credits, 0);
});

Deno.test("attack2 A: a gateway 403 is not logged as 'API key rejected' (it points the developer at the wrong cause)", async () => {
  const t = setup(gateway());
  await t.POST(req({ email: `farm${++n}@gmail.com` }, { "user-agent": "x ../../etc/passwd" }));
  assert.ok(!JSON.stringify(t.logs).includes("API key rejected"), JSON.stringify(t.logs));
});

// ---------- B. email enumeration: a duplicate answers faster and (for a blocked caller) without the notice ----------
// lib/signup.ts:189-190: the duplicate path returns before Tenkyu; a new account that gets 0 credits returns
// {ok:true, notice}. Both are visible to the caller. The notice is the bigger leak: a caller whose signups are
// blocked (flagged device/IP, or Tenkyu 4xx) sees notice = "new", plain = "already registered".
// Smallest fix: never put the notice in the signup response (always {"ok":true}); show it in the app after
// sign-in when credits === 0. Timing: do the same work on both paths (answer before Tenkyu via waitUntil/after()).
Deno.test("attack2 B: a blocked caller can tell a new email from a registered one by the response body (notice)", async () => {
  const t = setup(async () => new Response(JSON.stringify({
    id: "dec_blk", action: "block", score: 1, credit_fraction: 0, reasons: [], signals: [], network_match: true, credits_exhausted: false, mode: "live",
  }), { status: 200 }));
  const victim = `victim${++n}@gmail.com`;
  const fresh = `fresh${++n}@gmail.com`;
  await t.POST(req({ email: victim })); // the victim's account exists now
  const dup = await (await t.POST(req({ email: victim }))).text(); // attacker probes a registered email
  const brandNew = await (await t.POST(req({ email: fresh }))).text(); // attacker probes an unregistered email
  assert.equal(dup, brandNew, `registered: ${dup} / unregistered: ${brandNew}`);
  assert.ok(brandNew.includes(DEFAULT_NO_CREDITS_NOTICE) === dup.includes(DEFAULT_NO_CREDITS_NOTICE));
});

Deno.test("attack2 B: timing tells a registered email from a new one even with the notice turned off", async () => {
  const DELAY = 30; // a plausible Tenkyu round trip
  const t = setup(async (b) => {
    await new Promise((r) => setTimeout(r, DELAY));
    return await gateway()(b);
  }, { noCreditsNotice: null });
  const median = (xs: number[]) => [...xs].sort((a, b) => a - b)[Math.floor(xs.length / 2)];
  const timeIt = async (email: string) => {
    const t0 = performance.now();
    await t.POST(req({ email }));
    return performance.now() - t0;
  };
  const fresh: number[] = [];
  const dup: number[] = [];
  for (let i = 0; i < 7; i++) {
    const e = `timing${++n}@gmail.com`;
    fresh.push(await timeIt(e));
    dup.push(await timeIt(e));
  }
  const gap = median(fresh) - median(dup);
  assert.ok(Math.abs(gap) < DELAY / 3, `new ${median(fresh).toFixed(1)} ms vs registered ${median(dup).toFixed(1)} ms`);
});

// ---------- C. careless developer: TENKYU_API_KEY missing -> every real user gets 0 credits, log hides the cause ----------
// lib/tenkyu.ts:36 throws a plain Error, not a TenkyuError; lib/signup.ts:195 files it under "client error or
// bug" and logs only "unexpected error", then grants 0 to everyone. The commit's promise is "a bad key never
// zeroes every real user". Smallest fix: catch non-TenkyuError from deps.tenkyu() as a key/config error: log
// the message (it holds no secret) and grant the fail-open share, or at least log "TENKYU_API_KEY".
Deno.test("attack2 C: a missing TENKYU_API_KEY is named in the log (and does not zero every signup silently)", async () => {
  const saved = process.env.TENKYU_API_KEY;
  process.env.TENKYU_API_KEY = "";
  try {
    const { getTenkyu } = await import("../lib/tenkyu");
    const t = setup(gateway(), { tenkyu: getTenkyu });
    const res = await t.POST(req({ email: `dev${++n}@gmail.com` }));
    assert.equal(res.status, 200);
    const logged = JSON.stringify(t.logs);
    assert.ok(logged.includes("TENKYU_API_KEY") || (t.grants[0]?.credits ?? 0) > 0, `credits ${t.grants[0]?.credits ?? "none (no grant row at all)"}, log: ${logged}`);
  } finally {
    if (saved === undefined) delete process.env.TENKYU_API_KEY; else process.env.TENKYU_API_KEY = saved;
  }
});
