// Run from examples/vercel-nextjs:
//   deno test --import-map=test/import_map.json --unstable-sloppy-imports test/signup_test.ts
// No network: Tenkyu's fetch and the app's user store are fakes.
import assert from "node:assert/strict";
import { Tenkyu } from "tenkyu";
import { NextRequest } from "next/server";
import { createSignupHandler, DEFAULT_NO_CREDITS_NOTICE, MAX_BODY_BYTES, type SignupDeps } from "../lib/signup";
import { createSupabaseAdapter, supabaseAdapterFromEnv, type SupabaseAdminClient, type SupabaseAuthClient } from "../lib/supabase-auth";
import { isStrictIp, rateLimitKey, requestIdForUser } from "../lib/guards";
import type { CreditGrant } from "../lib/app-auth";

const DEVICE = "0b6f1f2e-4c1d-4a55-9a0e-1f2d3c4b5a69";
const GRANT = 100;

type TenkyuFake = (body: Record<string, unknown>, init: RequestInit) => Promise<Response>;
const decision = (action: string, credit_fraction: number) => () =>
  Promise.resolve(new Response(JSON.stringify({
    id: "dec_abc", action, score: 0.5, credit_fraction, reasons: ["same inbox as another signup"],
    signals: [{ name: "alias_reuse", score: 0.9 }], network_match: false, credits_exhausted: false, mode: "live",
  }), { status: 200 }));
const text = (status: number, body: string) => () => Promise.resolve(new Response(body, { status }));

function setup(opts: { tenkyu?: TenkyuFake; deps?: Partial<SignupDeps>; now?: () => number; clientOptions?: { failOpen?: boolean; silent?: boolean } } = {}) {
  const created: CreditGrant[] = []; // grants, one per newly created account
  const accounts: string[] = []; // emails createUser was asked to create, in order
  const passwords: string[] = []; // passwords createUser received (to prove they are not logged or echoed)
  const known = new Map<string, string>();
  const calls: Record<string, unknown>[] = [];
  const logs: unknown[][] = [];
  const warns: unknown[][] = [];
  const errors: unknown[][] = [];
  const fakeFetch = (async (_url: string, init?: RequestInit) => {
    const body = JSON.parse(String(init?.body));
    calls.push(body);
    return await (opts.tenkyu ?? decision("allow", 1))(body, init ?? {});
  }) as unknown as typeof fetch;
  const client = new Tenkyu({
    apiKey: "tk_test_x", baseUrl: "https://tenkyu.test", fetch: fakeFetch, timeoutMs: 40, failOpenCreditFraction: 0.25,
    ...(opts.clientOptions?.failOpen === undefined ? {} : { failOpen: opts.clientOptions.failOpen }),
    // With a silent client the recipe's own once-a-minute log is the only one (a client that throws on 401).
    logger: { error: (m) => { if (opts.clientOptions?.silent !== true) { logs.push([m]); errors.push([m]); } } },
  });
  const POST = createSignupHandler({
    tenkyu: () => client,
    createUser: (u) => {
      accounts.push(u.email);
      passwords.push(u.password);
      if (known.has(u.email)) return Promise.resolve({ ok: false, reason: "email_taken" } as const);
      const userId = `u${known.size + 1}`;
      known.set(u.email, userId);
      return Promise.resolve({ ok: true, userId } as const);
    },
    grantCredits: (g) => {
      created.push(g);
      return Promise.resolve();
    },
    verifiedLinks: () => Promise.resolve(undefined),
    grant: GRANT,
    failOpenCreditFraction: 0.25,
    now: opts.now ?? (() => 0),
    log: { log: (...a) => { logs.push(a); }, warn: (...a) => { logs.push(a); warns.push(a); }, error: (...a) => { logs.push(a); errors.push(a); } },
    ...opts.deps,
  });
  return { POST, created, accounts, passwords, calls, logs, warns, errors };
}

let n = 0;
const email = () => `user${++n}@gmail.com`;
const PASSWORD = "correct horse battery";
function req(body: unknown, headers: Record<string, string> = {}): NextRequest {
  // Most tests do not care about the password: add a valid one unless the test sets (or omits) it on purpose.
  if (typeof body === "object" && body !== null && !("password" in body)) body = { password: PASSWORD, ...body };
  return new NextRequest("https://app.example.com/api/signup", {
    method: "POST",
    headers: { "content-type": "application/json", "user-agent": "TestUA/1.0", "x-real-ip": "203.0.113.7", cookie: `tk_did=${DEVICE}`, ...headers },
    body: typeof body === "string" ? body : JSON.stringify(body),
  });
}
const ok = { ok: true };

// ---------- block, duplicates: one generic answer ----------

Deno.test("block: account is created with 0 credits, 200 (no 403 oracle), a plain {ok:true} and a server-side noticePending flag, never the verdict", async () => {
  const t = setup({ tenkyu: decision("block", 0) });
  const res = await t.POST(req({ email: "farmer@gmail.com" }));
  assert.equal(res.status, 200);
  const blockedText = await res.text();
  assert.equal(blockedText, '{"ok":true}', "the notice is never in the sign-up response (it would be an enumeration oracle)");
  assert.equal(DEFAULT_NO_CREDITS_NOTICE, "We couldn't give free credits to this account automatically. Contact support to get them.");
  for (const secret of ["block", "dec_abc", "alias", "score", "reasons", "blocked", "farmer"]) {
    assert.ok(!blockedText.includes(secret), `the notice must not leak ${secret}`);
  }
  assert.deepEqual(t.created.map((c) => ({ user: c.userId, credits: c.credits, blocked: c.blocked, review: c.needsReview, id: c.tenkyuDecisionId, notice: c.noticePending })), [
    { user: "u1", credits: 0, blocked: true, review: false, id: "dec_abc", notice: true },
  ]);
  const allow = setup({ tenkyu: decision("allow", 1) });
  assert.equal(await (await allow.POST(req({ email: "real@gmail.com" }))).text(), '{"ok":true}');
  assert.equal(allow.created[0].credits, 100);
  assert.equal(allow.created[0].noticePending, undefined, "a grant needs no notice");
  // noCreditsNotice: null leaves the flag off (you show your own empty-balance message)
  const quiet = setup({ tenkyu: decision("block", 0), deps: { noCreditsNotice: null } });
  assert.equal(await (await quiet.POST(req({ email: "farmer@gmail.com" }))).text(), '{"ok":true}');
  assert.equal(quiet.created[0].noticePending, undefined);
});

Deno.test("allow / review grants scale; browser never sees reasons, score, decision id or credits", async () => {
  for (const [action, fraction, credits] of [["allow", 1, 100], ["review", 0.25, 25]] as const) {
    const t = setup({ tenkyu: decision(action, fraction) });
    const res = await t.POST(req({ email: email() }));
    const body = await res.text();
    assert.equal(body, '{"ok":true}');
    assert.equal(t.created[0].credits, credits);
    assert.equal(t.created[0].needsReview, action === "review");
  }
});

Deno.test("duplicate email answers 200 {ok:true} like a fresh signup (no enumeration): no Tenkyu call, no credits", async () => {
  const t = setup();
  const e = email();
  const first = await t.POST(req({ email: e }, { "x-real-ip": "203.0.113.1", cookie: "" }));
  const second = await t.POST(req({ email: e }, { "x-real-ip": "203.0.113.2", cookie: "" }));
  assert.equal(first.status, 200);
  assert.equal(second.status, 200);
  assert.equal(await first.text(), await second.text());
  assert.equal(t.created.length, 1);
  assert.equal(t.calls.length, 1, "Tenkyu is called for the new account only");
  assert.equal(t.errors.length, 0);
});

Deno.test("email is normalized once: trimmed and lowercased before createUser and before Tenkyu", async () => {
  const t = setup();
  await t.POST(req({ email: "  Maya.Lev@GMAIL.com " }));
  assert.deepEqual(t.accounts, ["maya.lev@gmail.com"]);
  assert.equal(t.calls[0].email, "maya.lev@gmail.com");
  for (const bad of ["a\u0001b@gmail.com", "a\0b@gmail.com", "a".repeat(250) + "@b.co"]) {
    assert.equal((await t.POST(req({ email: bad }))).status, 400, JSON.stringify(bad).slice(0, 30));
  }
  assert.equal(t.accounts.length, 1, "invalid input never creates an account");
});

Deno.test("the account is created before Tenkyu is called; a Tenkyu outage cannot stop it", async () => {
  const order: string[] = [];
  const t = setup({
    tenkyu: (b) => { order.push("check"); return decision("allow", 1)(); },
    deps: { createUser: (u) => { order.push("create"); return Promise.resolve({ ok: true, userId: "u9" }); } },
  });
  await t.POST(req({ email: email() }));
  assert.deepEqual(order, ["create", "check"]);
});

Deno.test("grantCredits failing does not fail the signup; it is logged by user id without the email", async () => {
  const e = "private.person@gmail.com";
  const t = setup({ deps: { grantCredits: () => Promise.reject(new Error(`db down for ${e}`)) } });
  const res = await t.POST(req({ email: e }));
  assert.equal(res.status, 200);
  assert.deepEqual(await res.json(), ok);
  assert.equal(t.errors.length, 1);
  assert.ok(!JSON.stringify(t.errors).includes(e));
  assert.ok(JSON.stringify(t.errors).includes("u1"));
});

// ---------- outage ----------

Deno.test("every degraded cause grants a quarter (25 of 100), never a full grant", async () => {
  const causes: Record<string, TenkyuFake> = {
    "500": text(500, "x"), "502 html": text(502, "<html>"), "429": () => Promise.resolve(new Response("{}", { status: 429, headers: { "retry-after": "0" } })),
    network: () => Promise.reject(new TypeError("fetch failed")),
    hang: (_b, init) => new Promise((_, rej) => init.signal?.addEventListener("abort", () => rej(new Error("aborted")))),
  };
  for (const [name, tenkyu] of Object.entries(causes)) {
    const t = setup({ tenkyu });
    const res = await t.POST(req({ email: email() }));
    assert.equal(res.status, 200, name);
    assert.deepEqual(await res.json(), ok, name);
    assert.deepEqual(t.created.map((c) => [c.credits, c.tenkyuDecisionId]), [[25, null]], name);
    assert.ok(Number.isFinite(t.created[0].credits), name);
  }
});

// A 2xx that is not a JSON decision is not an outage: something in between answered it, and a farmer can make a
// gateway do that. Grant nothing (a 0-credit row with the notice flag), never the quarter.
Deno.test("a 2xx that is not a decision (HTML block page, {}, null, a bad credit_fraction) grants nothing, not the 25%", async () => {
  const html = () => Promise.resolve(new Response("<html>blocked</html>", { status: 200, headers: { "content-type": "text/html" } }));
  const causes: Record<string, TenkyuFake> = {
    "200 html": html, "200 non-json": text(200, "<html>ok"), "200 {}": text(200, "{}"), "200 null": text(200, "null"),
    "200 1e999": text(200, '{"id":"d","action":"allow","credit_fraction":1e999}'),
    "200 string": text(200, '{"id":"d","action":"allow","credit_fraction":"1"}'),
  };
  for (const [name, tenkyu] of Object.entries(causes)) {
    const t = setup({ tenkyu });
    const res = await t.POST(req({ email: email() }));
    assert.equal(res.status, 200, name);
    assert.deepEqual(await res.json(), ok, name);
    assert.deepEqual(t.created.map((c) => [c.credits, c.tenkyuDecisionId, c.needsReview, c.noticePending]), [[0, null, true, true]], name);
  }
});

Deno.test("a 200 flagged degraded:true with credit_fraction 1 is capped at the fail-open share and stores no decision id", async () => {
  const flagged = (credit_fraction: number) => text(200, JSON.stringify({ id: "dec_empty", action: "allow", score: 0, credit_fraction, reasons: [], signals: [], network_match: false, credits_exhausted: false, mode: "live", degraded: true }));
  const t = setup({ tenkyu: flagged(1) });
  assert.equal((await t.POST(req({ email: email() }))).status, 200);
  assert.deepEqual(t.created.map((c) => [c.credits, c.tenkyuDecisionId]), [[25, null]]);
  assert.ok(t.warns.length >= 1, "logged as degraded");
  const low = setup({ tenkyu: flagged(0.1) });
  await low.POST(req({ email: email() }));
  assert.deepEqual(low.created.map((c) => [c.credits, c.tenkyuDecisionId]), [[10, null]]);
  // the recipe caps on its own too, even if the injected client did not (a plain client or a future change)
  const raw = setup({ deps: { tenkyu: () => ({ check: () => Promise.resolve({ id: "dec_x", action: "allow" as const, score: 0, credit_fraction: 1, reasons: [], signals: [], network_match: false, credits_exhausted: false, mode: "live" as const, degraded: true }) }) } });
  await raw.POST(req({ email: email() }));
  assert.deepEqual(raw.created.map((c) => [c.credits, c.tenkyuDecisionId]), [[25, null]]);
  // degraded:false is a real score
  const real = setup({ tenkyu: text(200, JSON.stringify({ id: "dec_real", action: "allow", score: 0, credit_fraction: 1, reasons: [], signals: [], network_match: false, credits_exhausted: false, mode: "live", degraded: false })) });
  await real.POST(req({ email: email() }));
  assert.deepEqual(real.created.map((c) => [c.credits, c.tenkyuDecisionId]), [[100, "dec_real"]]);
});

Deno.test("Tenkyu 4xx other than a rejected key (400, 404, 422) fails closed: a 0-credit row, error level, plain answer, signup still 200", async () => {
  for (const status of [400, 404, 422]) {
    const t = setup({ tenkyu: text(status, JSON.stringify({ error: { code: "invalid_request", message: "bad" } })) });
    const res = await t.POST(req({ email: email() }));
    assert.equal(res.status, 200, String(status));
    assert.deepEqual(await res.json(), ok, String(status));
    assert.equal(t.accounts.length, 1, "the account exists");
    assert.deepEqual(t.created.map((c) => [c.credits, c.tenkyuDecisionId, c.needsReview, c.noticePending]), [[0, null, true, true]], `${status}: no credits, not even the quarter grant; flagged for review and notice`);
    assert.ok(t.errors.length >= 1, `${status}: logged at error level`);
    assert.ok(JSON.stringify(t.errors).includes(String(status)), "status in the log, by code only");
    assert.equal(t.warns.length, 0, `${status} is not 'unreachable'`);
    assert.ok(!JSON.stringify(t.logs).includes("tk_test_x"));
  }
  // a bug inside check() (not a TenkyuError) is not an outage either: closed, with a row
  const bug = setup({ deps: { tenkyu: () => ({ check: () => Promise.reject(new TypeError("boom")) }) } });
  assert.equal((await bug.POST(req({ email: email() }))).status, 200);
  assert.deepEqual(bug.created.map((c) => c.credits), [0]);
  assert.ok(bug.errors.length >= 1);
});

Deno.test("a missing key / client that cannot be created is OUR misconfiguration: clear error once a minute, 25% grant like a rejected key", async () => {
  let clock = 0;
  const t = setup({ deps: { tenkyu: () => { throw new Error("TENKYU_API_KEY is not set"); } }, now: () => clock });
  for (let i = 0; i < 3; i++) assert.equal((await t.POST(req({ email: email() }, { "x-real-ip": `203.0.113.${i + 1}`, cookie: "" }))).status, 200);
  assert.deepEqual(t.created.map((c) => [c.credits, c.tenkyuDecisionId, c.noticePending]), Array(3).fill([25, null, undefined]));
  assert.equal(t.errors.filter((e) => String(e[0]).includes("TENKYU_API_KEY")).length, 1, "logged once for 3 signups");
  clock += 61_000;
  await t.POST(req({ email: email() }, { "x-real-ip": "203.0.113.9", cookie: "" }));
  assert.equal(t.errors.filter((e) => String(e[0]).includes("TENKYU_API_KEY")).length, 2, "again after a minute");
});

Deno.test("a 401/403 that is not Tenkyu's own bad-key JSON (gateway, WAF) grants nothing and is logged once a minute as 'refused in between'", async () => {
  const html = (status: number) => text(status, "<html>Forbidden</html>");
  const notTenkyu401 = text(401, JSON.stringify({ error: { code: "invalid_request" } }));
  const cases: [string, TenkyuFake][] = [["403 html", html(403)], ["401 html", html(401)], ["401 other json", notTenkyu401], ["403 + unauthorized json", text(403, JSON.stringify({ error: { code: "unauthorized" } }))]];
  for (const [name, tenkyu] of cases) {
    for (const failOpen of [true, false]) {
      let clock = 0;
      const t = setup({ tenkyu, clientOptions: { failOpen }, now: () => clock });
      for (let i = 0; i < 4; i++) {
        const res = await t.POST(req({ email: email() }, { "x-real-ip": `203.0.113.${i + 1}`, cookie: "" }));
        assert.deepEqual(await res.json(), ok, name);
      }
      assert.deepEqual(t.created.map((c) => [c.credits, c.tenkyuDecisionId, c.needsReview, c.noticePending]), Array(4).fill([0, null, true, true]), name);
      const refused = t.errors.filter((e) => String(e[0]).startsWith("Tenkyu call refused by something in between (status "));
      assert.equal(refused.length, 1, `${name}: logged once for 4 signups`);
      assert.ok(!JSON.stringify(t.logs).includes("API key rejected"), name);
      clock += 61_000;
      await t.POST(req({ email: email() }, { "x-real-ip": "203.0.113.99", cookie: "" }));
      assert.equal(t.errors.filter((e) => String(e[0]).startsWith("Tenkyu call refused")).length, 2, `${name}: again after a minute`);
    }
  }
});

Deno.test("defer: the answer goes out before Tenkyu is asked; the grant lands in the background; a duplicate is not padded", async () => {
  let release!: () => void;
  const gate = new Promise<void>((r) => { release = r; });
  const deferred: Promise<void>[] = [];
  const slept: number[] = [];
  const t = setup({
    tenkyu: async () => { await gate; return await decision("allow", 1)(); },
    deps: { defer: (w) => { deferred.push(w); }, sleep: (ms) => { slept.push(ms); return Promise.resolve(); } },
  });
  const e = email();
  const first = await t.POST(req({ email: e }));
  assert.equal(await first.text(), '{"ok":true}');
  assert.equal(t.created.length, 0, "Tenkyu has not answered yet, and the response is already out");
  const dup = await t.POST(req({ email: e }, { "x-real-ip": "203.0.113.2", cookie: "" }));
  assert.equal(await dup.text(), '{"ok":true}');
  assert.deepEqual(slept, [], "no padding when the check runs after the answer");
  release();
  await Promise.all(deferred);
  assert.deepEqual(t.created.map((c) => c.credits), [100]);
  // a failing background job is caught: the deferred promise never rejects
  const bad = setup({ deps: { defer: (w) => { deferred.push(w); }, tenkyu: () => ({ check: () => Promise.reject(new TypeError("boom")) }), grantCredits: () => Promise.reject(new Error("db")) } });
  await bad.POST(req({ email: email() }));
  await Promise.all(deferred);
});

Deno.test("no defer: a duplicate email is padded to the median time of recent new sign-ups (fake clock and sleep, so it is deterministic)", async () => {
  let clock = 0;
  const slept: number[] = [];
  const t = setup({
    tenkyu: async () => { clock += 40; return await decision("allow", 1)(); },
    deps: { monotonic: () => clock, sleep: (ms) => { slept.push(ms); return Promise.resolve(); }, random: () => 0 },
  });
  const e = email();
  await t.POST(req({ email: e }));
  await t.POST(req({ email: e }, { "x-real-ip": "203.0.113.2", cookie: "" }));
  assert.deepEqual(slept, [40]);
});

Deno.test("bad or expired API key (401/403): everyone still gets the 25% fail-open grant, one clear error a minute", async () => {
  // Tenkyu's exact bad-key answer (hosted/api service.py unauthorized() + app.py error_response): JSON content-type, compact body.
  const rejected = (status: number) => () => Promise.resolve(new Response(
    JSON.stringify({ error: { code: "unauthorized", message: "missing or invalid API key" } }), { status, headers: { "content-type": "application/json" } }));
  for (const status of [401]) { // Tenkyu's bad-key answer is 401 + {"error":{"code":"unauthorized"}} only; a 403 is not it (next test)
    for (const failOpen of [true, false]) {
      // failOpen true: the shipped client absorbs it. failOpen false: it throws and the recipe absorbs it.
      let clock = 0;
      const t = setup({ tenkyu: rejected(status), clientOptions: { failOpen, silent: !failOpen }, now: () => clock });
      for (let i = 0; i < 5; i++) {
        const res = await t.POST(req({ email: email() }, { "x-real-ip": `203.0.113.${i + 1}`, cookie: "" }));
        assert.equal(res.status, 200);
        assert.deepEqual(await res.json(), ok, "a quarter grant, so no notice");
      }
      const label = `${status} failOpen=${failOpen}`;
      assert.deepEqual(t.created.map((c) => [c.credits, c.tenkyuDecisionId, c.blocked]), Array(5).fill([25, null, false]), label);
      const keyErrors = t.errors.filter((e) => e[0] === "Tenkyu API key rejected: check TENKYU_API_KEY");
      assert.equal(keyErrors.length, 1, `${label}: logged once for 5 signups`);
      assert.ok(!JSON.stringify(t.logs).includes("tk_test_x"), "no key in logs");
      assert.equal(t.errors.length, 1, `${label}: no other error-level noise`);
      if (!failOpen) {
        clock += 61_000; // a minute later (the recipe's own throttle; the shipped client has its own)
        await t.POST(req({ email: email() }, { "x-real-ip": "203.0.113.50", cookie: "" }));
        assert.equal(t.errors.filter((e) => e[0] === "Tenkyu API key rejected: check TENKYU_API_KEY").length, 2, "again after a minute");
      }
    }
  }
});

Deno.test("25-account farm during an outage (distinct IPs): 25 x 25 = 625 credits (was 2500)", async () => {
  const t = setup({ tenkyu: text(500, "x") });
  for (let i = 0; i < 25; i++) {
    const res = await t.POST(req({ email: email() }, { "x-real-ip": `203.0.113.${i + 1}`, cookie: "" }));
    assert.equal(res.status, 200);
  }
  assert.equal(t.created.reduce((a, c) => a + c.credits, 0), 625);
});

// ---------- rate limit in front of the check ----------

Deno.test("per-IP limit: flood stops reaching Tenkyu, gets 429 + Retry-After, refills over time", async () => {
  let clock = 0;
  const t = setup({ now: () => clock, deps: { rateLimit: { perIp: { capacity: 3, perMinute: 6 }, perDevice: { capacity: 100, perMinute: 100 } } } });
  const statuses: number[] = [];
  for (let i = 0; i < 8; i++) statuses.push((await t.POST(req({ email: email() }))).status);
  assert.deepEqual(statuses, [200, 200, 200, 429, 429, 429, 429, 429]);
  assert.equal(t.calls.length, 3, "rejected requests cost no Tenkyu check");
  const limited = await t.POST(req({ email: email() }));
  assert.equal(limited.headers.get("retry-after"), "10");
  assert.deepEqual(await limited.json(), { error: "rate_limited" });
  clock += 10_000;
  assert.equal((await t.POST(req({ email: email() }))).status, 200);
  assert.equal((await t.POST(req({ email: email() }, { "x-real-ip": "198.51.100.9" }))).status, 200);
});

Deno.test("per-IP limit keys IPv6 by /64 and maps ::ffff:a.b.c.d to IPv4", async () => {
  const t = setup({ deps: { rateLimit: { perIp: { capacity: 2, perMinute: 1 }, perDevice: { capacity: 100, perMinute: 100 } } } });
  const from = (ip: string) => t.POST(req({ email: email() }, { "x-real-ip": ip, cookie: `tk_did=${crypto.randomUUID()}` }));
  assert.equal((await from("2001:db8:1:2:aaaa::1")).status, 200);
  assert.equal((await from("2001:db8:1:2:bbbb:cccc:dddd:eeee")).status, 200);
  assert.equal((await from("2001:0db8:0001:0002:0:0:0:7")).status, 429, "same /64 shares one bucket");
  assert.equal((await from("2001:db8:1:3::1")).status, 200, "another /64 has its own");
  const m = setup({ deps: { rateLimit: { perIp: { capacity: 2, perMinute: 1 }, perDevice: { capacity: 100, perMinute: 100 } } } });
  const via = (ip: string) => m.POST(req({ email: email() }, { "x-real-ip": ip, cookie: `tk_did=${crypto.randomUUID()}` }));
  assert.equal((await via("203.0.113.7")).status, 200);
  assert.equal((await via("::ffff:203.0.113.7")).status, 200);
  assert.equal((await via("::ffff:cb00:7107")).status, 429);
  assert.equal((await via("203.0.113.7")).status, 429);
  assert.equal(m.calls[0].ip, "203.0.113.7");
  assert.equal(m.calls[1].ip, "::ffff:203.0.113.7", "the real address, not the bucket key, goes to Tenkyu");
});

Deno.test("rateLimitKey", () => {
  assert.equal(rateLimitKey("203.0.113.7"), "203.0.113.7");
  assert.equal(rateLimitKey("::ffff:203.0.113.7"), "203.0.113.7");
  assert.equal(rateLimitKey("::ffff:cb00:7107"), "203.0.113.7");
  assert.equal(rateLimitKey("2001:db8:1:2:3:4:5:6"), rateLimitKey("2001:DB8:1:2::9"));
  assert.notEqual(rateLimitKey("2001:db8:1:2::1"), rateLimitKey("2001:db8:1:3::1"));
  assert.equal(rateLimitKey("2001:db8:1:2::1"), "2001:db8:1:2::/64");
  assert.equal(rateLimitKey("1:2:3:4:5:6:1.2.3.4"), "1:2:3:4::/64");
  assert.equal(rateLimitKey("::1"), "0:0:0:0::/64");
});

Deno.test("per-device limit: one cookie cannot hammer from rotating IPs", async () => {
  const t = setup({ deps: { rateLimit: { perIp: { capacity: 100, perMinute: 100 }, perDevice: { capacity: 2, perMinute: 1 } } } });
  const statuses: number[] = [];
  for (let i = 0; i < 5; i++) statuses.push((await t.POST(req({ email: email() }, { "x-real-ip": `203.0.113.${i + 1}` }))).status);
  assert.deepEqual(statuses, [200, 200, 429, 429, 429]);
  assert.equal(t.calls.length, 2);
});

Deno.test("default limits: a class of 20 from one IP and a user submitting 5 times both pass", async () => {
  const klass = setup();
  for (let i = 0; i < 20; i++) assert.equal((await klass.POST(req({ email: email() }, { cookie: `tk_did=${crypto.randomUUID()}` }))).status, 200, `kid ${i}`);
  const retry = setup();
  const e = email();
  for (let i = 0; i < 5; i++) assert.equal((await retry.POST(req({ email: e }))).status, 200);
});

// ---------- IP ----------

Deno.test("IP: x-real-ip only; strict IPv4/IPv6; ports, garbage, zone ids dropped; x-forwarded-for never used", async () => {
  const sent = async (headers: Record<string, string | null>) => {
    const t = setup();
    const hdrs = new Headers({ "content-type": "application/json", "user-agent": "T", cookie: "" });
    for (const [k, v] of Object.entries(headers)) if (v !== null) hdrs.set(k, v);
    await t.POST(new NextRequest("https://a.test/api/signup", { method: "POST", headers: hdrs, body: JSON.stringify({ email: email(), password: PASSWORD }) }));
    return t.calls[0].ip;
  };
  assert.equal(await sent({ "x-real-ip": "203.0.113.7" }), "203.0.113.7");
  assert.equal(await sent({ "x-real-ip": "2001:db8::1" }), "2001:db8::1");
  for (const bad of ["999.999.999.999", ":::::", "1.2.3.4:5678", "[::1]", "fe80::1%eth0", "deadbeef", "....", "1234", "1.2.3.4.5.6", "01.2.3.4", "1.1.1.1 x", ""]) {
    assert.equal(await sent({ "x-real-ip": bad }), undefined, bad);
  }
  // no fallback to a forgeable x-forwarded-for, valid or not, with or without x-real-ip
  assert.equal(await sent({ "x-forwarded-for": "6.6.6.6, 203.0.113.7" }), undefined);
  assert.equal(await sent({ "x-forwarded-for": "6.6.6.6" }), undefined);
  assert.equal(await sent({ "x-real-ip": "", "x-forwarded-for": "6.6.6.6" }), undefined);
  assert.equal(await sent({ "x-real-ip": "203.0.113.7", "x-forwarded-for": "6.6.6.6" }), "203.0.113.7");
  assert.equal(await sent({ "cf-connecting-ip": "6.6.6.6" }), undefined);
});

Deno.test("isStrictIp edge cases", () => {
  for (const good of ["0.0.0.0", "255.255.255.255", "::", "::1", "::ffff:1.2.3.4", "FE80::ABCD"]) assert.equal(isStrictIp(good), true, good);
  for (const bad of ["256.0.0.0", "1.2.3", "1:2:3:4:5:6:7:8:9", "1::2::3", "", " ", "1.2.3.4/24", "a".repeat(46)]) assert.equal(isStrictIp(bad), false, bad);
});

// ---------- request_id: derived from the new account ----------

const sha256Hex = async (text: string) =>
  Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text))), (x) => x.toString(16).padStart(2, "0")).join("");

Deno.test("request_id is sha256 of the new user id: unique per account, stable for one account, not the email", async () => {
  const t = setup();
  await t.POST(req({ email: "a@gmail.com" }));
  await t.POST(req({ email: "b@gmail.com" }, { "x-real-ip": "198.51.100.1", cookie: "" }));
  const [a, b] = t.calls.map((c) => String(c.request_id));
  assert.match(a, /^[0-9a-f]{64}$/);
  assert.notEqual(a, b);
  assert.equal(a, await sha256Hex("tenkyu-signup:u1"));
  assert.equal(b, await sha256Hex("tenkyu-signup:u2"));
  assert.equal(await requestIdForUser("u1"), a, "same account, same id: a repeat is idempotent");
  assert.ok(!a.includes("a@gmail"));
});

Deno.test("case change and changed device/IP on a retry: a duplicate, never a Tenkyu 400 turned into a grant", async () => {
  // Tenkyu answers 400 if a request_id is replayed with a different body, exactly like production.
  const seen = new Map<string, string>();
  const tenkyu: TenkyuFake = (body) => {
    const sig = JSON.stringify({ ...body, request_id: undefined });
    const prev = seen.get(String(body.request_id));
    if (prev !== undefined && prev !== sig) return Promise.resolve(new Response('{"error":{"code":"invalid_request","message":"request_id reused"}}', { status: 400 }));
    seen.set(String(body.request_id), sig);
    return Promise.resolve(new Response(JSON.stringify({ id: "dec_1", action: "allow", credit_fraction: 1, score: 0, reasons: [], signals: [], network_match: false, credits_exhausted: false, mode: "live" }), { status: 200 }));
  };
  const t = setup({ tenkyu });
  const first = await t.POST(req({ email: "Maya@Gmail.com" }));
  const retry = await t.POST(req({ email: " maya@GMAIL.com " }, { "x-real-ip": "198.51.100.4", cookie: `tk_did=${crypto.randomUUID()}` }));
  assert.equal(first.status, 200);
  assert.equal(retry.status, 200);
  assert.equal(await first.text(), await retry.text());
  assert.equal(t.calls.length, 1, "the retry never reaches Tenkyu");
  assert.deepEqual(t.created.map((c) => [c.userId, c.credits, c.tenkyuDecisionId]), [["u1", 100, "dec_1"]]);
  assert.equal(t.errors.length, 0);
  assert.ok(t.warns.every((w) => String(w[0]).includes("existing email")), "only the generic existing-email note; no degraded 25% grant");
});

Deno.test("griefing: submitting a victim's email first cannot burn the victim's check or hand anyone a free grant", async () => {
  const t = setup();
  const attacker = await t.POST(req({ email: "victim@gmail.com" }, { "x-real-ip": "198.51.100.66", cookie: `tk_did=${crypto.randomUUID()}` }));
  const victim = await t.POST(req({ email: "Victim@Gmail.com" }, { "x-real-ip": "203.0.113.50", cookie: `tk_did=${DEVICE}` }));
  assert.equal(attacker.status, 200);
  assert.equal(await victim.text(), '{"ok":true}');
  assert.equal(t.calls.length, 1, "one check, keyed by the new account");
  assert.equal(t.created.length, 1, "one grant");
  assert.equal(t.errors.length + t.warns.length, 1, "only the generic existing-email warning");
  // and two different people never share a request_id
  await t.POST(req({ email: "someone.else@gmail.com" }, { "x-real-ip": "198.51.100.77", cookie: "" }));
  assert.notEqual(t.calls[0].request_id, t.calls[1].request_id);
});

// ---------- body cap ----------

Deno.test("body cap: chunked stream with no Content-Length is cut off, not buffered; cap is exact", async () => {
  const t = setup();
  let pulled = 0;
  const chunk = new Uint8Array(1024).fill(97);
  const body = new ReadableStream<Uint8Array>({ pull(c) { if (pulled >= 5000) c.close(); else { pulled++; c.enqueue(chunk); } } });
  const res = await t.POST(new NextRequest("https://a.test/api/signup", {
    method: "POST", headers: { "content-type": "application/json", "x-real-ip": "203.0.113.7" }, body, // @ts-ignore deno
    duplex: "half",
  }));
  assert.equal(res.status, 413);
  assert.ok(pulled <= MAX_BODY_BYTES / 1024 + 4, `read ${pulled} KB of 5000`);
  assert.equal(t.calls.length, 0);
  const base = new TextEncoder().encode(JSON.stringify({ email: "a@b.co", password: PASSWORD, pad: "" })).length;
  const padded = (extra: number) => JSON.stringify({ email: "a@b.co", password: PASSWORD, pad: "x".repeat(MAX_BODY_BYTES - base + extra) });
  assert.equal((await setup().POST(req(padded(0)))).status, 200);
  assert.equal((await setup().POST(req(padded(1)))).status, 413);
  assert.equal((await setup().POST(req("x".repeat(MAX_BODY_BYTES) + "y", { "content-length": "1" }))).status, 413);
});

// ---------- links ----------

Deno.test("links: only an app-verified phone goes to Tenkyu; body-supplied links/phone are ignored; lookup errors are harmless", async () => {
  const t = setup({ deps: { verifiedLinks: ({ email: e }) => Promise.resolve(e === "maya@gmail.com" ? { phone: "+14155550100" } : undefined) } });
  await t.POST(req({ email: "maya@gmail.com", links: { phone: "+19999999999" }, phone: "+19999999999" }));
  assert.deepEqual(t.calls[0].links, { phone: "+14155550100" });
  await t.POST(req({ email: "nobody@gmail.com", links: { phone: "+19999999999" } }));
  assert.equal(t.calls[1].links, undefined);
  const junk = setup({ deps: { verifiedLinks: () => Promise.resolve({ phone: "123; drop" }) } });
  await junk.POST(req({ email: email() }));
  assert.equal(junk.calls[0].links, undefined);
  const boom = setup({ deps: { verifiedLinks: () => Promise.reject(new Error("db down")) } });
  assert.equal((await boom.POST(req({ email: email() }))).status, 200);
  assert.equal(boom.calls[0].links, undefined);
});

Deno.test("input validation unchanged: wrong content type, bad email, junk json", async () => {
  const t = setup();
  assert.equal((await t.POST(req({ email: "a@b.co" }, { "content-type": "text/plain" }))).status, 400);
  assert.equal((await t.POST(req({ email: "nope" }))).status, 400);
  assert.equal((await t.POST(req("not json"))).status, 400);
  assert.equal((await t.POST(req({ email: "a@b.co" }, { "content-length": String(MAX_BODY_BYTES + 1) }))).status, 413);
  assert.equal(t.calls.length, 0);
});

// ---------- shadow mode ----------

Deno.test("shadow: grants by credit_fraction (full), passes would_* to grantCredits and logs them, no email in the log", async () => {
  const shadowed = text(200, JSON.stringify({
    id: "dec_sh", action: "allow", score: 0.9, credit_fraction: 1, reasons: ["shadow mode: allowed; would have been block"], signals: [],
    network_match: false, credits_exhausted: false, mode: "live", shadow: true, would_action: "block", would_credit_fraction: 0,
  }));
  const t = setup({ tenkyu: shadowed });
  const res = await t.POST(req({ email: "shadow.person@gmail.com" }));
  assert.deepEqual(await res.json(), ok);
  const g = t.created[0];
  assert.equal(g.credits, 100, "granted by credit_fraction, which is 1 in shadow mode");
  assert.equal(g.blocked, false);
  assert.deepEqual([g.shadow, g.wouldAction, g.wouldCreditFraction, g.tenkyuDecisionId], [true, "block", 0, "dec_sh"]);
  const line = JSON.stringify(t.logs);
  assert.ok(line.includes("tenkyu shadow") && line.includes("dec_sh") && line.includes("block"));
  assert.ok(!line.includes("shadow.person"));
  // outside shadow mode the fields are absent
  const live = setup();
  await live.POST(req({ email: email() }));
  assert.ok(!("shadow" in live.created[0]) && !("wouldAction" in live.created[0]));
});

// ---------- password ----------

Deno.test("password policy: 8 to 72 chars; weak ones are 400 before anything is created; never echoed or logged", async () => {
  const t = setup();
  const secret = "hunter2!!!hunter2!!!";
  for (const bad of ["short1", "1234567", "", "é".repeat(37), "a".repeat(73)]) {
    const res = await t.POST(req({ email: email(), password: bad }));
    assert.equal(res.status, 400, JSON.stringify(bad.slice(0, 10)));
    const body = await res.text();
    assert.equal(JSON.parse(body).error, "weak_password");
    assert.ok(!body.includes(bad) || bad === "");
  }
  for (const wrongType of [undefined, null, 12345678, ["abcdefgh"], { p: "abcdefgh" }]) {
    assert.equal((await t.POST(req({ email: email(), password: wrongType }))).status, 400);
  }
  assert.equal((await t.POST(req({ email: email() }))).status, 200, "req() adds a default password");
  assert.equal((await t.POST(req(JSON.stringify({ email: email() })))).status, 400, "a body with no password at all");
  assert.equal(t.accounts.length, 1, "only the valid one created an account");
  assert.equal(t.calls.length, 1);
  for (const ok of ["12345678", "a".repeat(72), "é".repeat(36), "🙂".repeat(18)]) {
    const r = await t.POST(req({ email: email(), password: ok }));
    assert.equal(r.status, 200, ok.slice(0, 10));
  }
  const s2 = setup({ tenkyu: decision("block", 0) });
  const res = await s2.POST(req({ email: email(), password: secret }));
  assert.deepEqual(s2.passwords, [secret], "the adapter gets the password unchanged");
  assert.ok(!(await res.text()).includes(secret));
  assert.ok(!JSON.stringify(s2.logs).includes(secret));
  assert.ok(!JSON.stringify(s2.calls).includes(secret), "Tenkyu never sees the password");
  // a password failure in the auth provider is shown, but the password is not in the log
  const rej = setup({ deps: { createUser: () => Promise.resolve({ ok: false, reason: "rejected", message: "Password is too common" }) } });
  const r = await rej.POST(req({ email: email(), password: secret }));
  assert.equal(r.status, 400);
  assert.deepEqual(await r.json(), { error: "signup_failed", message: "Password is too common" });
  assert.equal(rej.calls.length, 0, "no Tenkyu call for an account that was not created");
  assert.ok(!JSON.stringify(rej.logs).includes(secret));
  // a throwing adapter: 500, error name only, no password or email in the log
  const boom = setup({ deps: { createUser: (u) => Promise.reject(new Error(`db down for ${u.email} ${u.password}`)) } });
  const b = await boom.POST(req({ email: "who@gmail.com", password: secret }));
  assert.equal(b.status, 500);
  assert.ok(!JSON.stringify(boom.logs).includes(secret) && !JSON.stringify(boom.logs).includes("who@"));
});

// ---------- Supabase adapter (mocks, no network) ----------

function supabaseMocks(opts: { signUp?: SupabaseAuthClient["auth"]["signUp"]; insertError?: { message: string; code?: string } } = {}) {
  const signUps: { email: string; password: string }[] = [];
  const rows: Record<string, unknown>[] = [];
  const registered = new Set<string>();
  const auth: SupabaseAuthClient = {
    auth: {
      signUp: (args) => {
        signUps.push(args);
        if (opts.signUp) return opts.signUp(args);
        if (registered.has(args.email)) return Promise.resolve({ data: { user: { id: "fake-obfuscated", identities: [] } }, error: null });
        registered.add(args.email);
        return Promise.resolve({ data: { user: { id: `sb-${registered.size}`, identities: [{}] } }, error: null });
      },
    },
  };
  const admin: SupabaseAdminClient = {
    from: () => ({ insert: (row) => { if (opts.insertError) return Promise.resolve({ error: opts.insertError }); rows.push(row); return Promise.resolve({ error: null }); } }),
  };
  return { adapter: createSupabaseAdapter({ auth, admin }), signUps, rows };
}

Deno.test("supabase adapter: new user, then identities.length === 0 (confirmations on) is email_taken", async () => {
  const m = supabaseMocks();
  assert.deepEqual(await m.adapter.createUser({ email: "a@gmail.com", password: PASSWORD }), { ok: true, userId: "sb-1" });
  assert.deepEqual(m.signUps, [{ email: "a@gmail.com", password: PASSWORD }]);
  assert.deepEqual(await m.adapter.createUser({ email: "a@gmail.com", password: PASSWORD }), { ok: false, reason: "email_taken" });
  // identities missing entirely is a real user, not a duplicate
  const noField = supabaseMocks({ signUp: () => Promise.resolve({ data: { user: { id: "x1" } }, error: null }) });
  assert.deepEqual(await noField.adapter.createUser({ email: "b@gmail.com", password: PASSWORD }), { ok: true, userId: "x1" });
});

Deno.test("supabase adapter: user_already_exists / email_exists / old 'already registered' text (confirmations off) is email_taken", async () => {
  for (const error of [
    { message: "User already registered", code: "user_already_exists", status: 422 },
    { message: "A user with this email address has already been registered", code: "email_exists", status: 422 },
    { message: "User already registered" },
  ]) {
    const m = supabaseMocks({ signUp: () => Promise.resolve({ data: { user: null }, error }) });
    assert.deepEqual(await m.adapter.createUser({ email: "a@gmail.com", password: PASSWORD }), { ok: false, reason: "email_taken" });
  }
});

Deno.test("supabase adapter: other errors are 'rejected' with Supabase's text; no user is rejected; the route answers 400 and no Tenkyu call", async () => {
  const weak = supabaseMocks({ signUp: () => Promise.resolve({ data: { user: null }, error: { message: "Password should contain a digit", code: "weak_password", status: 422 } }) });
  assert.deepEqual(await weak.adapter.createUser({ email: "a@gmail.com", password: PASSWORD }), { ok: false, reason: "rejected", message: "Password should contain a digit" });
  const nobody = supabaseMocks({ signUp: () => Promise.resolve({ data: { user: null }, error: null }) });
  assert.deepEqual(await nobody.adapter.createUser({ email: "a@gmail.com", password: PASSWORD }), { ok: false, reason: "rejected" });
  const t = setup({ deps: { createUser: weak.adapter.createUser } });
  const res = await t.POST(req({ email: email() }));
  assert.equal(res.status, 400);
  assert.equal((await res.json()).error, "signup_failed");
  assert.equal(t.calls.length, 0);
});

Deno.test("supabase adapter: a rate-limit or any other non-password error is NOT shown: it answers like a duplicate (email-send limits hit new addresses only)", async () => {
  const errs = [
    { message: "email rate limit exceeded", code: "over_email_send_rate_limit", status: 429 },
    { message: "For security purposes, you can only request this after 49 seconds.", status: 429 },
    { message: "Error sending confirmation email", code: "unexpected_failure", status: 500 },
    { message: 'Email address "fresh@gmail.com" is invalid', code: "email_address_invalid", status: 400 },
  ];
  const newAccount = setup({ deps: { createUser: supabaseMocks().adapter.createUser } });
  const expected = await (await newAccount.POST(req({ email: "fresh@gmail.com" }))).text();
  for (const error of errs) {
    const m = supabaseMocks({ signUp: () => Promise.resolve({ data: { user: null }, error }) });
    const r = await m.adapter.createUser({ email: "fresh@gmail.com", password: PASSWORD });
    assert.equal(r.ok, false);
    assert.notEqual((r as { reason: string }).reason, "rejected", error.message);
    assert.ok(!JSON.stringify(r).includes("rate limit") && !JSON.stringify(r).includes("fresh@"), "no provider text, no email");
    const t = setup({ deps: { createUser: m.adapter.createUser } });
    const res = await t.POST(req({ email: "fresh@gmail.com" }));
    assert.equal(res.status, 200);
    assert.equal(await res.text(), expected, "same answer as a new account");
    assert.equal(t.calls.filter((c) => "email" in c).length, 0, "no Tenkyu check"); // the timing probe is not a check
    assert.equal(t.created.length, 0, "no credits");
    assert.ok(!JSON.stringify([t.logs, t.warns, t.errors]).includes("fresh@"), "the email is never logged");
    assert.ok(t.logs.length + t.warns.length + t.errors.length > 0, "the failure is logged server side");
  }
  // password-policy errors still reach the user
  for (const error of [{ message: "Password should contain a digit", code: "weak_password", status: 422 }, { message: "Password is too short", status: 422 }]) {
    const m = supabaseMocks({ signUp: () => Promise.resolve({ data: { user: null }, error }) });
    assert.deepEqual(await m.adapter.createUser({ email: "a@gmail.com", password: PASSWORD }), { ok: false, reason: "rejected", message: error.message });
  }
});

Deno.test("supabase adapter end to end: account first, then Tenkyu, then one credits row; a duplicate answers like success with no row", async () => {
  const m = supabaseMocks();
  const t = setup({ deps: { createUser: m.adapter.createUser, grantCredits: m.adapter.grantCredits } });
  const e = email();
  const first = await t.POST(req({ email: e }));
  const second = await t.POST(req({ email: e }, { "x-real-ip": "203.0.113.2", cookie: "" }));
  assert.equal(await first.text(), await second.text(), "no email enumeration");
  assert.equal(t.calls.length, 1, "Tenkyu is asked for the new account only");
  assert.deepEqual(m.rows, [{ user_id: "sb-1", granted: 100, decision_id: "dec_abc" }]);
  // a degraded grant stores no decision id
  const d = supabaseMocks();
  const t2 = setup({ tenkyu: text(500, "x"), deps: { createUser: d.adapter.createUser, grantCredits: d.adapter.grantCredits } });
  await t2.POST(req({ email: email() }));
  assert.deepEqual(d.rows, [{ user_id: "sb-1", granted: 25, decision_id: null }]);
  // a 0-credit account records the notice flag server-side (notice_pending), for the app to show after sign-in
  const b = supabaseMocks();
  const t3 = setup({ tenkyu: decision("block", 0), deps: { createUser: b.adapter.createUser, grantCredits: b.adapter.grantCredits } });
  assert.equal(await (await t3.POST(req({ email: email() }))).text(), '{"ok":true}');
  assert.deepEqual(b.rows, [{ user_id: "sb-1", granted: 0, decision_id: "dec_abc", notice_pending: true }]);
});

Deno.test("supabase adapter: credits insert hitting the primary key is fine, any other error throws without the provider's text", async () => {
  const dup = supabaseMocks({ insertError: { message: 'duplicate key value violates unique constraint "credits_pkey"', code: "23505" } });
  await dup.adapter.grantCredits({ userId: "u", credits: 10, tenkyuDecisionId: null, needsReview: false, blocked: false });
  const bad = supabaseMocks({ insertError: { message: "permission denied for table credits secret-detail", code: "42501" } });
  await assert.rejects(
    bad.adapter.grantCredits({ userId: "u", credits: 10, tenkyuDecisionId: null, needsReview: false, blocked: false }),
    (err: Error) => err.message.includes("42501") && !err.message.includes("secret-detail"),
  );
});

Deno.test("supabase adapter from env: service-role key only for the admin client; missing env throws", () => {
  const made: { url: string; key: string }[] = [];
  const fake = (url: string, key: string) => { made.push({ url, key }); return { auth: { signUp: () => Promise.reject(new Error("unused")) }, from: () => ({}) }; };
  supabaseAdapterFromEnv(fake, { SUPABASE_URL: "https://p.supabase.co", SUPABASE_ANON_KEY: "anon", SUPABASE_SERVICE_ROLE_KEY: "service" });
  assert.deepEqual(made, [{ url: "https://p.supabase.co", key: "anon" }, { url: "https://p.supabase.co", key: "service" }]);
  assert.throws(() => supabaseAdapterFromEnv(fake, { SUPABASE_URL: "https://p.supabase.co", SUPABASE_ANON_KEY: "anon" }), /SUPABASE_SERVICE_ROLE_KEY/);
});

Deno.test("production without defer refuses to start unless TENKYU_ALLOW_INLINE_SCORING=1", () => {
  assert.throws(() => setup({ deps: { env: { NODE_ENV: "production" } } }), /no defer/);
  assert.doesNotThrow(() => setup({ deps: { env: { NODE_ENV: "production", TENKYU_ALLOW_INLINE_SCORING: "1" } } }));
  assert.doesNotThrow(() => setup({ deps: { env: { NODE_ENV: "production" }, defer: () => {} } }));
  assert.doesNotThrow(() => setup({ deps: { env: { NODE_ENV: "development" } } }));
});

Deno.test("a pending marker with the user id (no email) is logged before the answer, a done marker after the work", async () => {
  const deferred: Promise<void>[] = [];
  const t = setup({ deps: { defer: (w) => { deferred.push(w); } } });
  const e = email();
  await t.POST(req({ email: e }));
  const text = () => JSON.stringify(t.logs);
  assert.ok(text().includes("signup scoring pending") && text().includes("u1"), "pending before the work finished");
  assert.ok(!text().includes("signup scoring done"));
  await Promise.all(deferred);
  assert.ok(text().includes("signup scoring done"));
  assert.ok(!text().includes(e), "no email in any log line");
});
