// Run: deno test supabase/functions/tenkyu-signup/handler_test.ts
// No network: fetch and both Supabase clients are fakes.
import assert from "node:assert/strict";
import { isIP } from "node:net";
import { requestIdForUser, rateLimitKey, isGlobalIp, isStrictIp as isIPv4Or6 } from "./guards.ts";
import {
  createHandler, clientIp, MAX_BODY_BYTES, DEFAULT_NO_CREDITS_NOTICE,
  type AdminClient, type Deps, type AuthClient, type Config, type CreditRow,
} from "./handler.ts";

const ORIGIN = "https://app.example.com";
const DEVICE = "0b6f1f2e-4c1d-4a55-9a0e-1f2d3c4b5a69";
const config: Config = {
  tenkyuApiKey: "tk_test_x", tenkyuUrl: "https://tenkyu.test", timeoutMs: 30, grant: 300,
  allowedOrigin: ORIGIN, trustedProxyHops: 1, clientIpHeader: "", debugIpHeaders: false,
};

function setup(opts: {
  tenkyu: (url: string, init: RequestInit) => Promise<Response>;
  cfg?: Partial<Config>;
  /** "on" (default): a duplicate gets a fake user with no identities. "off": a user_already_exists error. */
  confirmations?: "on" | "off";
  now?: () => number;
  /** Extra handler deps: waitUntil, monotonic, sleep. */
  deps?: Partial<Pick<Deps, "waitUntil" | "monotonic" | "sleep" | "random" | "production" | "allowInlineScoring">>;
  verifiedLinks?: Deps["verifiedLinks"];
  insertError?: { message: string; code?: string };
  signUp?: AuthClient["auth"]["signUp"];
  /** Receives every `log.log` call, for assertions. */
  onLog?: (args: unknown[]) => void;
}) {
  const credits: CreditRow[] = [];
  const signUps: { email: string; password: string }[] = [];
  const tenkyuCalls: { url: string; body: Record<string, unknown>; auth: string | null }[] = [];
  const warnings: unknown[][] = [];
  // A tiny user store, so tests can sign the same email up twice. Like Supabase, it lowercases the email.
  const registered = new Set<string>();
  const authClient: AuthClient = {
    auth: {
      signUp(args) {
        signUps.push(args);
        if (opts.signUp) return opts.signUp(args);
        const key = args.email.toLowerCase();
        if (registered.has(key)) {
          if (opts.confirmations === "off") {
            return Promise.resolve({ data: { user: null }, error: { message: "User already registered", code: "user_already_exists", status: 422 } });
          }
          return Promise.resolve({ data: { user: { id: `fake-${signUps.length}`, identities: [] } }, error: null });
        }
        registered.add(key);
        return Promise.resolve({ data: { user: { id: `user-${registered.size}`, identities: [{}] } }, error: null });
      },
    },
  };
  const adminClient: AdminClient = {
    from: () => ({
      insert: (row) => {
        if (opts.insertError) return Promise.resolve({ error: opts.insertError });
        credits.push(row);
        return Promise.resolve({ error: null });
      },
    }),
  };
  const errors: unknown[][] = [];
  const fakeFetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const headers = new Headers(init?.headers);
    // The cold-instance timing probe (POST /v1/outcome for a decision id that does not exist) is not a check.
    if (!String(input).endsWith("/v1/outcome")) tenkyuCalls.push({ url: String(input), body: JSON.parse(String(init?.body)), auth: headers.get("authorization") });
    return await opts.tenkyu(String(input), init ?? {});
  }) as typeof fetch;
  const handler = createHandler({
    config: { ...config, ...opts.cfg }, fetch: fakeFetch, authClient, adminClient,
    log: { log: (...a: unknown[]) => { opts.onLog?.(a); }, error: (...a: unknown[]) => { errors.push(a); }, warn: (...a: unknown[]) => { warnings.push(a); } },
    now: opts.now, verifiedLinks: opts.verifiedLinks, ...opts.deps,
  });
  return { handler, credits, signUps, tenkyuCalls, warnings, errors };
}

async function sha256Hex(text: string): Promise<string> {
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text)));
  return Array.from(digest, (b) => b.toString(16).padStart(2, "0")).join("");
}

const decision = (action: string, credit_fraction: number) => () =>
  Promise.resolve(new Response(JSON.stringify({
    id: "dec_abc", action, score: 0.5, credit_fraction,
    reasons: ["same inbox as another signup"], signals: [{ name: "alias_reuse", score: 0.9 }],
    network_match: false, credits_exhausted: false, mode: "live",
  }), { status: 200, headers: { "content-type": "application/json" } }));

function post(body: unknown, headers: Record<string, string> = {}): Request {
  return new Request("https://x.supabase.co/functions/v1/tenkyu-signup", {
    method: "POST",
    headers: { "content-type": "application/json", origin: ORIGIN, "user-agent": "TestUA/1.0", "x-forwarded-for": "9.9.9.9, 203.0.113.7", ...headers },
    body: typeof body === "string" ? body : JSON.stringify(body),
  });
}
const valid = { email: "maya@gmail.com", password: "correct horse", device_id: DEVICE };

for (const [action, fraction, expected] of [["allow", 1, 300], ["review", 0.25, 75], ["block", 0, 0]] as const) {
  Deno.test(`${action}: grants ${expected} credits and forwards ip, user agent, device id`, async () => {
    const t = setup({ tenkyu: decision(action, fraction) });
    const res = await t.handler(post(valid));
    assert.equal(res.status, 200);
    assert.deepEqual(await res.json(), { ok: true });
    assert.deepEqual(t.credits, [{ user_id: "user-1", granted: expected, decision_id: "dec_abc", ...(expected === 0 ? { notice_pending: true } : {}) }]);
    const call = t.tenkyuCalls[0];
    assert.equal(call.url, "https://tenkyu.test/v1/check");
    assert.equal(call.auth, "Bearer tk_test_x");
    assert.deepEqual({ ...call.body, request_id: undefined }, {
      email: valid.email, ip: "203.0.113.7", device_id: DEVICE, user_agent: "TestUA/1.0", request_id: undefined,
    });
    assert.match(String(call.body.request_id), /^[0-9a-f]{64}$/);
    assert.equal(call.body.request_id, await requestIdForUser("user-1"), "request_id comes from the new user id");
    assert.ok(!JSON.stringify(call.body).includes("correct horse"), "password never goes to Tenkyu");
  });
}

Deno.test("block: the account exists with 0 credits, status 200, a plain {ok:true}; the notice is a server-side flag, never in the answer", async () => {
  const blocked = setup({ tenkyu: decision("block", 0) });
  const allowed = setup({ tenkyu: decision("allow", 1) });
  const a = await blocked.handler(post(valid));
  const b = await allowed.handler(post(valid));
  assert.equal(a.status, 200);
  assert.equal(await b.text(), '{"ok":true}');
  const text = await a.text();
  assert.equal(text, '{"ok":true}');
  assert.equal(DEFAULT_NO_CREDITS_NOTICE, "We couldn't give free credits to this account automatically. Contact support to get them.");
  for (const leak of ["block", "dec_abc", "score", "reasons", "signals", "farmer", "same inbox"]) assert.ok(!text.includes(leak), `leaked ${leak}`);
  assert.equal(blocked.signUps.length, 1);
  assert.deepEqual(blocked.credits, [{ user_id: "user-1", granted: 0, decision_id: "dec_abc", notice_pending: true }]);
  // "" leaves the flag off (the app shows its own empty-balance message)
  const quiet = setup({ tenkyu: decision("block", 0), cfg: { noCreditsNotice: "" } });
  assert.equal(await (await quiet.handler(post(valid))).text(), '{"ok":true}');
  assert.deepEqual(quiet.credits, [{ user_id: "user-1", granted: 0, decision_id: "dec_abc" }]);
});

Deno.test("a duplicate email never gets the notice: same plain {ok:true} as a new allowed sign-up (no enumeration)", async () => {
  const t = setup({ tenkyu: decision("block", 0) });
  await t.handler(post(valid)); // first: blocked, flagged notice_pending server-side
  const dup = await t.handler(post(valid, { "x-forwarded-for": "198.51.100.9" }));
  assert.equal(await dup.text(), '{"ok":true}');
});

Deno.test("GRANT env scales the grant", async () => {
  const t = setup({ tenkyu: decision("review", 0.25), cfg: { grant: 1000 } });
  await t.handler(post(valid));
  assert.equal(t.credits[0].granted, 250);
});

Deno.test("Tenkyu timeout fails open: quarter grant, logs 'tenkyu unreachable'", async () => {
  const hang = (_url: string, init: RequestInit) =>
    new Promise<Response>((_, reject) => {
      init.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
    });
  const t = setup({ tenkyu: hang });
  const res = await t.handler(post(valid));
  assert.equal(res.status, 200);
  assert.deepEqual(t.credits, [{ user_id: "user-1", granted: 75, decision_id: null }]);
  assert.equal(t.warnings[0][0], "tenkyu unreachable");
});

Deno.test("Tenkyu 500 and network error also fail open (quarter grant)", async () => {
  for (const tenkyu of [
    () => Promise.resolve(new Response("boom", { status: 500 })),
    () => Promise.reject(new TypeError("connection refused")),
  ]) {
    const t = setup({ tenkyu });
    assert.equal((await t.handler(post(valid))).status, 200);
    assert.equal(t.credits[0].granted, 75);
    assert.equal(t.warnings[0][0], "tenkyu unreachable");
  }
});

Deno.test("oversized or malformed input is rejected before Tenkyu is called", async () => {
  const cases: [unknown, number][] = [
    [{ ...valid, password: "x".repeat(MAX_BODY_BYTES) }, 413],
    [{ ...valid, password: "x".repeat(257) }, 400],
    [{ ...valid, email: "a".repeat(250) + "@b.co" }, 400],
    [{ ...valid, email: "not-an-email" }, 400],
    [{ ...valid, device_id: "short" }, 400],
    [{ ...valid, email: "a\u0001b@gmail.com" }, 400],
    [{ ...valid, email: "a\0b@gmail.com" }, 400],
    [{ ...valid, password: "short" }, 400],
    [{ email: valid.email, password: valid.password }, 400],
    ["not json", 400],
    [[1, 2], 400],
  ];
  for (const [body, status] of cases) {
    const t = setup({ tenkyu: decision("allow", 1) });
    const res = await t.handler(post(body));
    assert.equal(res.status, status, JSON.stringify(body).slice(0, 60));
    assert.equal(t.tenkyuCalls.length, 0);
    assert.equal(t.signUps.length, 0);
  }
  const t = setup({ tenkyu: decision("allow", 1) });
  const big = post(valid, { "content-length": String(MAX_BODY_BYTES + 1) });
  assert.equal((await t.handler(big)).status, 413);
});

Deno.test("reasons, signals, score and decision id are never echoed to the browser", async () => {
  for (const [action, fraction] of [["allow", 1], ["review", 0.25], ["block", 0]] as const) {
    const t = setup({ tenkyu: decision(action, fraction) });
    const res = await t.handler(post(valid));
    const text = await res.text();
    assert.deepEqual(JSON.parse(text), { ok: true });
    for (const leak of ["same inbox", "alias_reuse", "reasons", "signals", "score", "dec_abc", action === "block" ? "never" : "credit", action === "allow" ? "never" : action]) {
      assert.ok(!text.includes(leak), `leaked ${leak}`);
    }
  }
});

Deno.test("weak password: 400 weak_password before anything is created or checked", async () => {
  const t = setup({ tenkyu: decision("allow", 1) });
  const res = await t.handler(post({ ...valid, password: "1234567" }));
  assert.equal(res.status, 400);
  const body = await res.json();
  assert.equal(body.error, "weak_password");
  assert.match(body.message, /8 characters/);
  assert.equal(t.signUps.length, 0);
  assert.equal(t.tenkyuCalls.length, 0);
  const ok = setup({ tenkyu: decision("allow", 1), cfg: { passwordMinLength: 12 } });
  assert.equal((await ok.handler(post({ ...valid, password: "elevenchars" }))).status, 400);
  assert.equal((await ok.handler(post({ ...valid, password: "twelve chars" }))).status, 200);
});

Deno.test("Supabase's own rejection (e.g. its stricter policy) is a 400 signup_failed with no Tenkyu call", async () => {
  const t = setup({
    tenkyu: decision("allow", 1),
    signUp: () => Promise.resolve({ data: { user: null }, error: { message: "Password is known to be weak", code: "weak_password", status: 422 } }),
  });
  const res = await t.handler(post(valid));
  assert.equal(res.status, 400);
  assert.deepEqual(await res.json(), { ok: false, error: "signup_failed", message: "Password is known to be weak" });
  assert.equal(t.tenkyuCalls.length, 0);
  assert.equal(t.credits.length, 0);
});

Deno.test("signUp rate limit / any non-password error is NOT shown: same {ok:true} as a new account, no Tenkyu call, logged without the email", async () => {
  const fresh = setup({ tenkyu: decision("allow", 1) });
  const expected = await (await fresh.handler(post(valid))).text();
  for (const error of [
    { message: "email rate limit exceeded", code: "over_email_send_rate_limit", status: 429 },
    { message: "For security purposes, you can only request this after 49 seconds.", status: 429 },
    { message: "Error sending confirmation email", code: "unexpected_failure", status: 500 },
    { message: 'Email address "maya@gmail.com" is invalid', code: "email_address_invalid", status: 400 },
  ]) {
    const t = setup({ tenkyu: decision("allow", 1), signUp: () => Promise.resolve({ data: { user: null }, error }) });
    const res = await t.handler(post(valid));
    const text = await res.text();
    assert.equal(res.status, 200, error.message);
    assert.equal(text, expected, "indistinguishable from a new account");
    assert.ok(!text.includes("rate") && !text.includes("maya@"));
    assert.equal(t.tenkyuCalls.length, 0);
    assert.equal(t.credits.length, 0);
    const logged = JSON.stringify([t.errors, t.warnings]);
    assert.ok(t.errors.length + t.warnings.length > 0, "logged server side");
    assert.ok(!logged.includes("maya@"), "never the email");
  }
});

// ---------- duplicate sign-ups: no enumeration, no check, no credits ----------

for (const confirmations of ["on", "off"] as const) {
  Deno.test(`duplicate email, confirmations ${confirmations}: same {ok:true} answer as a new account, no Tenkyu call, no credits`, async () => {
    const t = setup({ tenkyu: decision("allow", 1), confirmations });
    const first = await t.handler(post(valid));
    const second = await t.handler(post(valid, { "x-forwarded-for": "9.9.9.9, 198.51.100.4" }));
    assert.equal(first.status, 200);
    assert.equal(second.status, 200);
    assert.equal(await first.text(), await second.text(), "new and existing are indistinguishable");
    assert.equal(t.tenkyuCalls.length, 1, "Tenkyu is called for the new account only");
    assert.deepEqual(t.credits, [{ user_id: "user-1", granted: 300, decision_id: "dec_abc" }]);
    assert.equal(t.errors.length, 0);
  });
}

Deno.test("confirmations off: 'User already registered' text and code never reach the browser", async () => {
  const t = setup({ tenkyu: decision("allow", 1), confirmations: "off" });
  await t.handler(post(valid));
  const dup = await t.handler(post(valid));
  const text = await dup.text();
  assert.equal(dup.status, 200);
  assert.equal(text, '{"ok":true}');
  for (const leak of ["already", "registered", "user_already_exists", "message"]) assert.ok(!text.includes(leak), leak);
});

Deno.test("already-exists is recognised by code or by message (older GoTrue has no code)", async () => {
  for (const error of [
    { message: "something else entirely", code: "user_already_exists" },
    { message: "User already registered" },
    { message: "A user with this email address has already been registered" },
  ]) {
    const t = setup({ tenkyu: decision("allow", 1), signUp: () => Promise.resolve({ data: { user: null }, error }) });
    const res = await t.handler(post(valid));
    assert.equal(res.status, 200, error.message);
    assert.deepEqual(await res.json(), { ok: true });
    assert.equal(t.tenkyuCalls.length, 0);
    assert.equal(t.credits.length, 0);
  }
});

Deno.test("CORS: only ALLOWED_ORIGIN; wrong origin refused; preflight works", async () => {
  const t = setup({ tenkyu: decision("allow", 1) });
  const ok = await t.handler(post(valid));
  assert.equal(ok.headers.get("access-control-allow-origin"), ORIGIN);
  const bad = await t.handler(post(valid, { origin: "https://evil.example" }));
  assert.equal(bad.status, 403);
  assert.equal(bad.headers.get("access-control-allow-origin"), null);
  const pre = await t.handler(new Request("https://x/f", { method: "OPTIONS", headers: { origin: ORIGIN } }));
  assert.equal(pre.status, 204);
  assert.equal(pre.headers.get("access-control-allow-origin"), ORIGIN);
  assert.equal(t.tenkyuCalls.length, 1);
});

Deno.test("clientIp: trusts the right-most entry by default, never the client-written first", () => {
  const h = (v: string) => new Headers({ "x-forwarded-for": v });
  assert.equal(clientIp(h("6.6.6.6, 203.0.113.7"), { clientIpHeader: "", trustedProxyHops: 1 }), "203.0.113.7");
  assert.equal(clientIp(h("203.0.113.7, 198.51.100.1"), { clientIpHeader: "", trustedProxyHops: 2 }), "203.0.113.7");
  assert.equal(clientIp(h("garbage"), { clientIpHeader: "", trustedProxyHops: 1 }), undefined);
  assert.equal(clientIp(new Headers(), { clientIpHeader: "", trustedProxyHops: 1 }), undefined);
  assert.equal(clientIp(new Headers({ "cf-connecting-ip": "2001:db8::1" }), { clientIpHeader: "cf-connecting-ip", trustedProxyHops: 1 }), "2001:db8::1");
});

Deno.test("clientIp: private, loopback, link-local, CGNAT, ULA and unspecified addresses are never a client IP", () => {
  const cfg = { clientIpHeader: "", trustedProxyHops: 1 };
  const ip = (v: string) => clientIp(new Headers({ "x-forwarded-for": v }), cfg);
  for (const internal of [
    "10.0.0.1", "10.255.255.255", "172.16.0.1", "172.31.255.255", "192.168.1.1", "127.0.0.1", "169.254.169.254", "100.64.0.1", "100.127.255.255",
    "0.0.0.0", "255.255.255.255", "224.0.0.1", "::", "::1", "fc00::1", "fd12:3456:789a::1", "fe80::1", "febf::1", "ff02::1",
    "::ffff:10.0.0.1", "::ffff:127.0.0.1", "::ffff:192.168.0.1", "::ffff:a00:1",
  ]) assert.equal(ip(internal), undefined, internal);
  for (const global of ["8.8.8.8", "172.15.255.255", "172.32.0.1", "100.63.255.255", "100.128.0.1", "169.253.0.1", "192.169.0.1", "11.0.0.1", "203.0.113.7", "2001:db8::1", "2606:4700::1111", "fec0::1", "::ffff:8.8.8.8", "::ffff:808:808"]) {
    assert.equal(ip(global), global, global);
  }
  // a wrong hop count lands on an internal proxy: no IP is better than a shared one
  assert.equal(clientIp(new Headers({ "x-forwarded-for": "203.0.113.7, 10.0.0.1" }), cfg), undefined);
  assert.equal(clientIp(new Headers({ "x-forwarded-for": "203.0.113.7, 10.0.0.1" }), { ...cfg, trustedProxyHops: 2 }), "203.0.113.7");
  assert.equal(clientIp(new Headers({ "cf-connecting-ip": "100.64.1.1" }), { clientIpHeader: "cf-connecting-ip", trustedProxyHops: 1 }), undefined);
  assert.equal(isGlobalIp("not an ip"), false);
});

Deno.test("an internal address means no IP bucket and no ip sent: everyone behind a wrong hop count is not one bucket", async () => {
  const t = setup({ tenkyu: decision("allow", 1), now: () => 0, cfg: { rateLimit: { perIp: { capacity: 1, perMinute: 1 }, perDevice: { capacity: 100, perMinute: 100 } } } });
  for (let i = 0; i < 4; i++) {
    const res = await t.handler(post({ ...valid, email: `p${i}@gmail.com` }, { "x-forwarded-for": "203.0.113.7, 10.0.0.1" }));
    assert.equal(res.status, 200);
  }
  assert.equal(t.tenkyuCalls.length, 4);
  assert.ok(t.tenkyuCalls.every((c) => c.body.ip === undefined));
});

// ---------- degraded decisions: outage is not a full grant ----------

const j = (status: number, text: string) => () => Promise.resolve(new Response(text, { status }));
/** Tenkyu's exact bad-key answer (hosted/api app.py error_response + service.py unauthorized()). */
const BAD_KEY = () => Promise.resolve(new Response('{"error":{"code":"unauthorized","message":"missing or invalid API key"}}', { status: 401, headers: { "content-type": "application/json" } }));

Deno.test("every degraded cause grants FAIL_OPEN_CREDIT_FRACTION, never a full grant", async () => {
  const causes: Record<string, (url: string, init: RequestInit) => Promise<Response>> = {
    "500": j(500, "x"), "502 html": j(502, "<html>"), "429": j(429, "{}"), "302": j(302, ""),
    network: () => Promise.reject(new TypeError("fetch failed")),
  };
  for (const [name, tenkyu] of Object.entries(causes)) {
    const t = setup({ tenkyu });
    const res = await t.handler(post(valid));
    assert.equal(res.status, 200, name);
    assert.deepEqual(t.credits, [{ user_id: "user-1", granted: 75, decision_id: null }], name);
  }
});

Deno.test("a 200 flagged degraded:true with credit_fraction 1 is capped, and stores no decision id", async () => {
  const flagged = (credit_fraction: number) => () =>
    Promise.resolve(new Response(JSON.stringify({ id: "dec_empty", action: "allow", credit_fraction, degraded: true }), { status: 200 }));
  const full = setup({ tenkyu: flagged(1) });
  assert.equal((await full.handler(post(valid))).status, 200);
  assert.deepEqual(full.credits, [{ user_id: "user-1", granted: 75, decision_id: null }]);
  const lower = setup({ tenkyu: flagged(0.1) });
  await lower.handler(post(valid));
  assert.deepEqual(lower.credits, [{ user_id: "user-1", granted: 30, decision_id: null }], "a lower fraction is kept");
  const half = setup({ tenkyu: flagged(1), cfg: { failOpenCreditFraction: 0.5 } });
  await half.handler(post(valid));
  assert.equal(half.credits[0].granted, 150);
  // degraded:false (or absent) is a real score and keeps its full fraction and its decision id
  const real = setup({ tenkyu: () => Promise.resolve(new Response(JSON.stringify({ id: "dec_real", action: "allow", credit_fraction: 1, degraded: false }), { status: 200 })) });
  await real.handler(post(valid));
  assert.deepEqual(real.credits, [{ user_id: "user-1", granted: 300, decision_id: "dec_real" }]);
});

Deno.test("Tenkyu 4xx other than a rejected key (400, 404, 422) fails closed: a 0-credit row, error logged, plain answer, signup still 200", async () => {
  for (const status of [400, 404, 422]) {
    const t = setup({ tenkyu: j(status, JSON.stringify({ error: { code: "invalid_request", message: "bad" } })) });
    const res = await t.handler(post(valid));
    assert.equal(res.status, 200, String(status));
    assert.deepEqual(await res.json(), { ok: true });
    assert.equal(t.signUps.length, 1, "the account exists");
    assert.deepEqual(t.credits, [{ user_id: "user-1", granted: 0, decision_id: null, notice_pending: true }], `${status}: nothing granted, not even the quarter grant`);
    assert.ok(t.errors.length >= 1, `${status}: logged at error level`);
    assert.ok(JSON.stringify(t.errors).includes(String(status)));
    assert.equal(t.warnings.length, 0, `${status} is not 'unreachable'`);
    assert.ok(!JSON.stringify(t.errors).includes("tk_test_x"), "key never logged");
  }
});

// A 2xx that is not a JSON decision is not an outage: something in between answered it. Grant nothing (0-credit row
// with the notice flag), never the quarter, and log it as a refusal.
Deno.test("a 2xx that is not a decision (HTML block page, {}, empty, a bad credit_fraction) grants nothing, not the 25%", async () => {
  const causes: Record<string, (url: string, init: RequestInit) => Promise<Response>> = {
    "200 html": () => Promise.resolve(new Response("<html>blocked</html>", { status: 200, headers: { "content-type": "text/html" } })),
    "200 json-typed html": () => Promise.resolve(new Response("<html>blocked</html>", { status: 200, headers: { "content-type": "application/json" } })),
    "200 non-json": j(200, "<html>ok"), "200 {}": j(200, "{}"), "200 empty": j(200, ""),
    "200 string fraction": j(200, JSON.stringify({ id: "d", action: "allow", credit_fraction: "1" })),
    "200 NaN-ish (1e999)": j(200, '{"id":"d","action":"allow","credit_fraction":1e999}'),
    "200 fraction 5": j(200, JSON.stringify({ id: "d", action: "allow", credit_fraction: 5 })),
    "200 fraction -1": j(200, JSON.stringify({ id: "d", action: "allow", credit_fraction: -1 })),
  };
  for (const [name, tenkyu] of Object.entries(causes)) {
    const t = setup({ tenkyu });
    const res = await t.handler(post(valid));
    assert.equal(res.status, 200, name);
    assert.deepEqual(t.credits, [{ user_id: "user-1", granted: 0, decision_id: null, notice_pending: true }], name);
    assert.ok(t.errors.some((e) => String(e[0]).includes("refused by something in between")), name);
  }
});

Deno.test("bad or expired API key (401/403): a quarter grant for everyone, one clear error a minute, no key in logs", async () => {
  const KEY_ERROR = "Tenkyu API key rejected: check TENKYU_API_KEY";
  for (const status of [401]) { // Tenkyu's bad-key answer is 401 + {"error":{"code":"unauthorized"}} only; see the 'refused' test
    let clock = 0;
    const t = setup({ tenkyu: BAD_KEY, now: () => clock });
    for (let i = 0; i < 5; i++) {
      const res = await t.handler(post({ ...valid, email: `u${i}@gmail.com` }, { "x-forwarded-for": `9.9.9.9, 203.0.113.${i + 1}` }));
      assert.equal(res.status, 200);
      assert.deepEqual(await res.json(), { ok: true }, "a quarter grant is not a zero: no notice");
    }
    assert.deepEqual(t.credits.map((c) => [c.granted, c.decision_id]), Array(5).fill([75, null]), `${status}: 25% of 300, no decision id`);
    assert.equal(t.errors.filter((e) => e[0] === KEY_ERROR).length, 1, `${status}: logged once for 5 sign-ups`);
    assert.equal(t.errors.length, 1, "nothing else at error level");
    assert.ok(!JSON.stringify([t.errors, t.warnings]).includes("tk_test_x"), "key never logged");
    clock += 59_000;
    await t.handler(post({ ...valid, email: "later@gmail.com" }, { "x-forwarded-for": "9.9.9.9, 203.0.113.90" }));
    assert.equal(t.errors.length, 1, "still inside the minute");
    clock += 2_000;
    await t.handler(post({ ...valid, email: "later2@gmail.com" }, { "x-forwarded-for": "9.9.9.9, 203.0.113.91" }));
    assert.equal(t.errors.filter((e) => e[0] === KEY_ERROR).length, 2, "logged again after a minute");
  }
  // a configured share applies
  const half = setup({ tenkyu: BAD_KEY, cfg: { failOpenCreditFraction: 0.5 } });
  await half.handler(post(valid));
  assert.deepEqual(half.credits, [{ user_id: "user-1", granted: 150, decision_id: null }]);
});

Deno.test("shadow mode: grants by credit_fraction, logs would_action and would_credit_fraction (no email), junk shadow fields are dropped", async () => {
  const shadowed = (extra: Record<string, unknown>) => j(200, JSON.stringify({ id: "dec_sh", action: "allow", credit_fraction: 1, shadow: true, ...extra }));
  const logged: unknown[][] = [];
  const t = setup({ tenkyu: shadowed({ would_action: "block", would_credit_fraction: 0 }), onLog: (a) => logged.push(a) });
  const res = await t.handler(post(valid));
  assert.deepEqual(await res.json(), { ok: true }, "shadow is invisible to the browser");
  assert.deepEqual(t.credits, [{ user_id: "user-1", granted: 300, decision_id: "dec_sh" }]);
  const line = logged.find((a) => a[0] === "tenkyu shadow");
  assert.deepEqual(line?.[1], { id: "dec_sh", would_action: "block", would_credit_fraction: 0 });
  assert.ok(!JSON.stringify(logged).includes("maya@gmail.com"));
  const junk = setup({ tenkyu: shadowed({ would_action: "nuke", would_credit_fraction: 7 }), onLog: (a) => logged.push(a) });
  assert.equal((await junk.handler(post(valid))).status, 200);
  assert.deepEqual(junk.credits, [{ user_id: "user-1", granted: 300, decision_id: "dec_sh" }]);
});

Deno.test("env-style override: FAIL_OPEN_CREDIT_FRACTION 0 and 0.5; junk falls back to 0.25", async () => {
  for (const [value, expected] of [[0, 0], [0.5, 150], [undefined, 75], [Number.NaN, 75], [9, 300], [-1, 0]] as const) {
    const t = setup({ tenkyu: j(500, "x"), cfg: { failOpenCreditFraction: value } });
    await t.handler(post(valid));
    // 9 and -1 are clamped by grantAmount only if they get through; the handler clamps them to 1 and 0.
    assert.equal(t.credits[0].granted, expected, String(value));
  }
});

Deno.test("25-account farm during an outage: 25 x 75 = 1875 (was 7500), and one IP is throttled anyway", async () => {
  const t = setup({ tenkyu: j(500, "x") });
  for (let i = 0; i < 25; i++) {
    const res = await t.handler(post(
      { ...valid, email: `farm${i}@gmail.com`, device_id: `farmdevice-${String(i).padStart(10, "0")}` },
      { "x-forwarded-for": `203.0.113.${i + 1}` },
    ));
    assert.equal(res.status, 200);
  }
  assert.equal(t.credits.reduce((a, c) => a + c.granted, 0), 25 * 75);
});

Deno.test("a stalled Tenkyu body is bounded by the timeout", async () => {
  const stalled = () => Promise.resolve(new Response(new ReadableStream({ start() {} }), { status: 200 }));
  const t = setup({ tenkyu: stalled });
  const started = performance.now();
  const res = await t.handler(post(valid));
  assert.equal(res.status, 200);
  assert.ok(performance.now() - started < 500);
  assert.equal(t.credits[0].granted, 75);
});

// ---------- rate limit in front of the check ----------

Deno.test("per-IP limit: a flood from one IP stops reaching Tenkyu and gets 429", async () => {
  let clock = 0;
  const t = setup({
    tenkyu: decision("allow", 1), now: () => clock,
    cfg: { rateLimit: { perIp: { capacity: 3, perMinute: 6 }, perDevice: { capacity: 100, perMinute: 100 } } },
  });
  const statuses: number[] = [];
  for (let i = 0; i < 10; i++) statuses.push((await t.handler(post({ ...valid, device_id: `device-number-${String(i).padStart(8, "0")}`, email: `a${i}@gmail.com` }))).status);
  assert.deepEqual(statuses, [200, 200, 200, 429, 429, 429, 429, 429, 429, 429]);
  assert.equal(t.tenkyuCalls.length, 3, "rejected requests never cost a Tenkyu check");
  const limited = await t.handler(post(valid));
  assert.equal(limited.headers.get("retry-after"), "10");
  assert.deepEqual(await limited.json(), { ok: false, error: "rate_limited" });
  clock += 10_000; // 10 s at 6/min refills one token
  assert.equal((await t.handler(post({ ...valid, email: "later@gmail.com", device_id: "device-number-later00000" }))).status, 200);
  // another IP is unaffected
  const other = await t.handler(post({ ...valid, email: "o@gmail.com", device_id: "device-number-other0000" }, { "x-forwarded-for": "198.51.100.9" }));
  assert.equal(other.status, 200);
});

Deno.test("per-IP limit keys IPv6 by /64: addresses in one /64 share a bucket, another /64 does not", async () => {
  const t = setup({
    tenkyu: decision("allow", 1), now: () => 0,
    cfg: { rateLimit: { perIp: { capacity: 2, perMinute: 1 }, perDevice: { capacity: 100, perMinute: 100 } } },
  });
  const from = (ip: string, i: number) => t.handler(post({ ...valid, email: `v6-${i}@gmail.com` }, { "x-forwarded-for": ip }));
  assert.equal((await from("2001:db8:1:2:aaaa::1", 0)).status, 200);
  assert.equal((await from("2001:db8:1:2:bbbb:cccc:dddd:eeee", 1)).status, 200);
  assert.equal((await from("2001:0db8:0001:0002:0:0:0:7", 2)).status, 429, "same /64, different spelling and interface id");
  assert.equal((await from("2001:db8:1:3::1", 3)).status, 200, "next /64 has its own bucket");
  assert.equal(t.tenkyuCalls.length, 3);
  // the real address, not the bucket key, is what Tenkyu receives
  assert.equal(t.tenkyuCalls[0].body.ip, "2001:db8:1:2:aaaa::1");
});

Deno.test("per-IP limit: ::ffff:a.b.c.d shares the IPv4 bucket", async () => {
  const t = setup({
    tenkyu: decision("allow", 1), now: () => 0,
    cfg: { rateLimit: { perIp: { capacity: 2, perMinute: 1 }, perDevice: { capacity: 100, perMinute: 100 } } },
  });
  const from = (ip: string, i: number) => t.handler(post({ ...valid, email: `m-${i}@gmail.com` }, { "x-forwarded-for": ip }));
  assert.equal((await from("203.0.113.7", 0)).status, 200);
  assert.equal((await from("::ffff:203.0.113.7", 1)).status, 200);
  assert.equal((await from("::ffff:cb00:7107", 2)).status, 429);
  assert.equal((await from("203.0.113.7", 3)).status, 429);
});

Deno.test("rateLimitKey", () => {
  assert.equal(rateLimitKey("203.0.113.7"), "203.0.113.7");
  assert.equal(rateLimitKey("::ffff:203.0.113.7"), "203.0.113.7");
  assert.equal(rateLimitKey("::ffff:cb00:7107"), "203.0.113.7");
  assert.equal(rateLimitKey("2001:db8:1:2:3:4:5:6"), rateLimitKey("2001:DB8:1:2::9"));
  assert.notEqual(rateLimitKey("2001:db8:1:2::1"), rateLimitKey("2001:db8:1:3::1"));
  assert.equal(rateLimitKey("2001:db8:1:2::1"), "2001:db8:1:2::/64");
  assert.equal(rateLimitKey("::1.2.3.4"), "0:0:0:0::/64");
  assert.equal(rateLimitKey("1:2:3:4:5:6:1.2.3.4"), "1:2:3:4::/64");
  assert.equal(rateLimitKey("2001:db8::"), "2001:db8:0:0::/64");
});

Deno.test("per-device limit: one device id cannot hammer from rotating IPs", async () => {
  const t = setup({
    tenkyu: decision("allow", 1), now: () => 0,
    cfg: { rateLimit: { perIp: { capacity: 100, perMinute: 100 }, perDevice: { capacity: 2, perMinute: 1 } } },
  });
  const statuses: number[] = [];
  for (let i = 0; i < 5; i++) statuses.push((await t.handler(post({ ...valid, email: `d${i}@gmail.com` }, { "x-forwarded-for": `203.0.113.${i + 1}` }))).status);
  assert.deepEqual(statuses, [200, 200, 429, 429, 429]);
  assert.equal(t.tenkyuCalls.length, 2);
});

Deno.test("default limits let a classroom of 20 sign up from one IP and let a user retry several times", async () => {
  const t = setup({ tenkyu: decision("allow", 1), now: () => 0 });
  for (let i = 0; i < 20; i++) {
    const res = await t.handler(post({ ...valid, email: `kid${i}@school.example`, device_id: `kiddevice-${String(i).padStart(10, "0")}` }));
    assert.equal(res.status, 200, `kid ${i}`);
  }
  const retry = setup({ tenkyu: decision("allow", 1), now: () => 0 });
  for (let i = 0; i < 5; i++) assert.equal((await retry.handler(post(valid))).status, 200);
});

Deno.test("no valid IP: no per-IP bucket (shared 'unknown' bucket would throttle everyone), device bucket still applies", async () => {
  const t = setup({ tenkyu: decision("allow", 1), now: () => 0, cfg: { rateLimit: { perIp: { capacity: 1, perMinute: 1 }, perDevice: { capacity: 100, perMinute: 100 } } } });
  for (let i = 0; i < 4; i++) {
    const res = await t.handler(post({ ...valid, email: `n${i}@gmail.com` }, { "x-forwarded-for": "garbage" }));
    assert.equal(res.status, 200);
  }
  assert.equal(t.tenkyuCalls[0].body.ip, undefined);
});

// ---------- IP parsing ----------

Deno.test("strict IP: ports, garbage, out-of-range and malformed IPv6 are dropped (no IP sent)", () => {
  const cfg = { clientIpHeader: "", trustedProxyHops: 1 };
  const ip = (v: string) => clientIp(new Headers({ "x-forwarded-for": v }), cfg);
  for (const bad of [
    "999.999.999.999", ":::::", "::::::::", "1.2.3.4:5678", "[::1]:443", "[::1]", "fe80::1%eth0", "1.2.3", "1.2.3.4.5", "01.2.3.4",
    "256.1.1.1", "1:2", "abcd:", ":abcd", "1:2:3:4:5:6:7:8:9", "1::2::3", "12345::", "g::1", "1.2.3.4::", "deadbeef", "....", "0x7f.0.0.1",
    "1.1.1.1 x", "-1.1.1.1",
  ]) assert.equal(ip(bad), undefined, bad);
  for (const good of ["203.0.113.7", "2001:db8::1", "1::", "::ffff:203.0.113.7", "2001:0db8:0000:0000:0000:0000:0000:0001", "1:2:3:4:5:6:7:8", "1:2:3:4:5:6:1.2.3.4"]) {
    assert.equal(ip(good), good, good);
  }
  // strictly valid, but not a global address: dropped (see the internal-address test)
  for (const internal of ["0.0.0.0", "255.255.255.255", "::", "::1", "FE80::ABCD"]) {
    assert.equal(isIPv4Or6(internal), true, internal);
    assert.equal(ip(internal), undefined, internal);
  }
});

Deno.test("strict IP validator agrees with node:net isIP on a corpus (no zone ids, no brackets)", () => {
  const corpus = [
    "1.2.3.4", "1.2.3.04", "1.2.3.256", "1.2.3", "::", "::1", "1::", "1:2:3:4:5:6:7::", "::2:3:4:5:6:7:8", "1:2:3:4:5:6:7:8:9", "1::8:9:a:b:c:d:e",
    "::ffff:1.2.3.4", "1.2.3.4::", ":1::2", "1:::2", "1::2::3", "fe80::1", "FFFF:FFFF:FFFF:FFFF:FFFF:FFFF:FFFF:FFFF", "10000::", "-1::1", "a:b:c:d:e:f:1.2.3.4",
    "a:b:c:d:e:f:g:h", "1:2:3:4:5:6:7", "::1.2.3.4", "::1.2.3", "64:ff9b::192.0.2.33", "1.2.3.4.5", "", ":", "...", "0.0.0.0", "00.0.0.0",
  ];
  const strict = (s: string) => isIPv4Or6(s);
  for (const c of corpus) assert.equal(strict(c), isIP(c) !== 0, JSON.stringify(c));
});
// ---------- request_id: derived from the new account, not from the email ----------

Deno.test("request_id is sha256 of the new user id: unique per account, stable for the same account", async () => {
  const t = setup({ tenkyu: decision("allow", 1) });
  await t.handler(post({ ...valid, email: "a@gmail.com" }));
  await t.handler(post({ ...valid, email: "b@gmail.com" }));
  const [a, b] = t.tenkyuCalls.map((c) => String(c.body.request_id));
  assert.match(a, /^[0-9a-f]{64}$/);
  assert.notEqual(a, b, "two accounts never share a request_id");
  assert.equal(a, await sha256Hex("tenkyu-signup:user-1"));
  assert.equal(b, await sha256Hex("tenkyu-signup:user-2"));
  assert.equal(await requestIdForUser("user-1"), a, "same account, same id: a repeat is idempotent");
  assert.ok(!a.includes("user-1") && !JSON.stringify(t.tenkyuCalls[0].body).includes("tenkyu-signup:"));
});

Deno.test("case change on a retry: the second submit is a duplicate, never a Tenkyu 400 turned into a grant", async () => {
  // Tenkyu answers 400 if a request_id is replayed with a different body, exactly like production.
  const seen = new Map<string, string>();
  const tenkyu = (_u: string, init: RequestInit) => {
    const body = JSON.parse(String(init.body));
    const sig = JSON.stringify({ ...body, request_id: undefined });
    const prev = seen.get(body.request_id);
    if (prev !== undefined && prev !== sig) return Promise.resolve(new Response('{"error":{"code":"invalid_request"}}', { status: 400 }));
    seen.set(body.request_id, sig);
    return Promise.resolve(new Response(JSON.stringify({ id: "dec_1", action: "allow", credit_fraction: 1 }), { status: 200 }));
  };
  for (const confirmations of ["on", "off"] as const) {
    seen.clear();
    const t = setup({ tenkyu, confirmations });
    const first = await t.handler(post({ ...valid, email: "Maya@Gmail.com" }));
    const retry = await t.handler(post({ ...valid, email: " maya@GMAIL.com " }, { "x-forwarded-for": "9.9.9.9, 198.51.100.4" }));
    assert.equal(first.status, 200);
    assert.equal(retry.status, 200);
    assert.deepEqual(t.signUps.map((x) => x.email), ["maya@gmail.com", "maya@gmail.com"], "email is normalized once, before sign-up");
    assert.equal(t.tenkyuCalls.length, 1, "the retry never reaches Tenkyu");
    assert.deepEqual(t.credits, [{ user_id: "user-1", granted: 300, decision_id: "dec_1" }]);
    assert.equal(t.errors.length, 0);
  }
});

Deno.test("griefing: submitting a victim's email first cannot burn the victim's check or hand anyone a free grant", async () => {
  const tenkyu = decision("allow", 1);
  for (const confirmations of ["on", "off"] as const) {
    const t = setup({ tenkyu, confirmations });
    const attacker = await t.handler(post({ ...valid, email: "victim@gmail.com", device_id: "attacker-device-0001" }, { "x-forwarded-for": "198.51.100.66" }));
    assert.equal(attacker.status, 200);
    const victim = await t.handler(post({ ...valid, email: "VICTIM@gmail.com", device_id: "victim-device-000001" }, { "x-forwarded-for": "203.0.113.50" }));
    assert.equal(victim.status, 200);
    assert.equal(await victim.text(), '{"ok":true}');
    // Only one check ran, keyed by the new account. The victim's submit created nothing and granted nothing.
    assert.equal(t.tenkyuCalls.length, 1);
    assert.equal(t.credits.length, 1);
    assert.equal(t.errors.length, 0);
    assert.equal(t.warnings.length, 0, "no degraded 25% grant either");
  }
});

Deno.test("two different people with the same-looking input never collide on request_id", async () => {
  const t = setup({ tenkyu: decision("allow", 1) });
  await t.handler(post({ ...valid, email: "one@gmail.com" }));
  await t.handler(post({ ...valid, email: "two@gmail.com" }, { "x-forwarded-for": "198.51.100.1" }));
  assert.notEqual(t.tenkyuCalls[0].body.request_id, t.tenkyuCalls[1].body.request_id);
});

Deno.test("retry after a weak password: rejected locally with no Tenkyu call, the corrected retry is checked once", async () => {
  const t = setup({ tenkyu: decision("allow", 1) });
  const weak = await t.handler(post({ ...valid, password: "weak" }));
  assert.equal(weak.status, 400);
  assert.equal(t.tenkyuCalls.length, 0);
  assert.equal(t.signUps.length, 0);
  const retry = await t.handler(post(valid));
  assert.equal(retry.status, 200);
  assert.equal(t.tenkyuCalls.length, 1);
  assert.deepEqual(t.credits, [{ user_id: "user-1", granted: 300, decision_id: "dec_abc" }]);
});

// ---------- verified links ----------

Deno.test("links: only an app-verified phone is forwarded; a phone in the browser body is ignored", async () => {
  const t = setup({ tenkyu: decision("allow", 1), verifiedLinks: ({ email }) => (email === valid.email ? { phone: "+14155550100" } : undefined) });
  await t.handler(post({ ...valid, links: { phone: "+19999999999" }, phone: "+19999999999" }));
  assert.deepEqual(t.tenkyuCalls[0].body.links, { phone: "+14155550100" });
  const none = setup({ tenkyu: decision("allow", 1) });
  await none.handler(post({ ...valid, links: { phone: "+19999999999" } }));
  assert.equal(none.tenkyuCalls[0].body.links, undefined);
  const junk = setup({ tenkyu: decision("allow", 1), verifiedLinks: () => ({ phone: "not a phone" }) });
  await junk.handler(post(valid));
  assert.equal(junk.tenkyuCalls[0].body.links, undefined);
  const throws = setup({ tenkyu: decision("allow", 1), verifiedLinks: () => { throw new Error("db down"); } });
  assert.equal((await throws.handler(post(valid))).status, 200);
  assert.equal(throws.tenkyuCalls[0].body.links, undefined);
});

// ---------- body cap ----------

Deno.test("body cap: a chunked stream with no Content-Length is cut off at the cap, not buffered", async () => {
  const t = setup({ tenkyu: decision("allow", 1) });
  let pulled = 0;
  const chunk = new Uint8Array(1024).fill(97);
  const body = new ReadableStream<Uint8Array>({
    pull(c) { if (pulled >= 5000) c.close(); else { pulled++; c.enqueue(chunk); } },
  });
  const req = new Request("https://x/f", { method: "POST", headers: { origin: ORIGIN, "x-forwarded-for": "203.0.113.7" }, body, // @ts-ignore deno
    duplex: "half" });
  const res = await t.handler(req);
  assert.equal(res.status, 413);
  assert.ok(pulled <= MAX_BODY_BYTES / 1024 + 4, `read only ${pulled} KB of 5000`);
  assert.equal(t.tenkyuCalls.length, 0);
  // exactly at the cap is accepted by the reader; one byte over is not
  const pad = (n: number) => JSON.stringify({ ...valid, pad: "x".repeat(n) });
  const base = new TextEncoder().encode(pad(0)).length;
  assert.equal((await setup({ tenkyu: decision("allow", 1) }).handler(post(pad(MAX_BODY_BYTES - base)))).status, 200);
  assert.equal((await setup({ tenkyu: decision("allow", 1) }).handler(post(pad(MAX_BODY_BYTES - base + 1)))).status, 413);
});

Deno.test("credits insert hitting the primary key (parallel or repeated sign-up) is logged as expected, not as an error", async () => {
  const t = setup({ tenkyu: decision("allow", 1), insertError: { message: "duplicate key value violates unique constraint", code: "23505" } });
  assert.equal((await t.handler(post(valid))).status, 200);
  assert.equal(t.errors.length, 0);
  const real = setup({ tenkyu: decision("allow", 1), insertError: { message: "connection refused" } });
  assert.equal((await real.handler(post(valid))).status, 200);
  assert.equal(real.errors.length, 1);
});

Deno.test("a 401/403 that is not Tenkyu's own bad-key JSON (gateway, WAF) grants nothing and is logged once a minute as 'refused in between'", async () => {
  const cases: [string, ReturnType<typeof j>][] = [
    ["403 html", j(403, "<html>Forbidden</html>")], ["401 html", j(401, "<html>nope</html>")], ["401 {}", j(401, "{}")],
    ["401 other code", j(401, JSON.stringify({ error: { code: "invalid_request" } }))],
    ["403 + unauthorized json", j(403, JSON.stringify({ error: { code: "unauthorized" } }))],
  ];
  for (const [name, tenkyu] of cases) {
    let clock = 0;
    const t = setup({ tenkyu, now: () => clock });
    for (let i = 0; i < 4; i++) {
      const res = await t.handler(post({ ...valid, email: `r${i}@gmail.com` }, { "x-forwarded-for": `9.9.9.9, 203.0.113.${i + 1}` }));
      assert.deepEqual(await res.json(), { ok: true }, name);
    }
    assert.deepEqual(t.credits.map((c) => [c.granted, c.decision_id, c.notice_pending]), Array(4).fill([0, null, true]), name);
    assert.equal(t.errors.filter((e) => String(e[0]).startsWith("Tenkyu call refused by something in between (status ")).length, 1, `${name}: once for 4 sign-ups`);
    assert.ok(!JSON.stringify(t.errors).includes("API key rejected"), name);
    clock += 61_000;
    await t.handler(post({ ...valid, email: "later@gmail.com" }, { "x-forwarded-for": "9.9.9.9, 203.0.113.99" }));
    assert.equal(t.errors.filter((e) => String(e[0]).startsWith("Tenkyu call refused")).length, 2, `${name}: again after a minute`);
  }
});

Deno.test("a missing TENKYU_API_KEY: a clear error once a minute and the 25% grant, no call to Tenkyu", async () => {
  let clock = 0;
  const t = setup({ tenkyu: decision("allow", 1), cfg: { tenkyuApiKey: "" }, now: () => clock });
  for (let i = 0; i < 3; i++) await t.handler(post({ ...valid, email: `k${i}@gmail.com` }, { "x-forwarded-for": `9.9.9.9, 203.0.113.${i + 1}` }));
  assert.deepEqual(t.credits.map((c) => [c.granted, c.decision_id, c.notice_pending]), Array(3).fill([75, null, undefined]));
  assert.equal(t.tenkyuCalls.length, 0);
  assert.equal(t.errors.filter((e) => String(e[0]).includes("TENKYU_API_KEY")).length, 1);
  clock += 61_000;
  await t.handler(post({ ...valid, email: "later@gmail.com" }, { "x-forwarded-for": "9.9.9.9, 203.0.113.99" }));
  assert.equal(t.errors.filter((e) => String(e[0]).includes("TENKYU_API_KEY")).length, 2);
});

Deno.test("waitUntil: the answer goes out before Tenkyu is asked, the row lands in the background, a duplicate is not padded", async () => {
  let release!: () => void;
  const gate = new Promise<void>((r) => { release = r; });
  const pending: Promise<void>[] = [];
  const slept: number[] = [];
  const inner = decision("allow", 1);
  const t = setup({
    tenkyu: async () => { await gate; return await inner(); },
    deps: { waitUntil: (w) => { pending.push(w); }, sleep: (ms) => { slept.push(ms); return Promise.resolve(); } },
  });
  const first = await t.handler(post(valid));
  assert.equal(await first.text(), '{"ok":true}');
  assert.equal(t.credits.length, 0, "Tenkyu has not answered yet and the response is already out");
  const dup = await t.handler(post(valid, { "x-forwarded-for": "198.51.100.9" }));
  assert.equal(await dup.text(), '{"ok":true}');
  assert.deepEqual(slept, []);
  release();
  await Promise.all(pending);
  assert.deepEqual(t.credits, [{ user_id: "user-1", granted: 300, decision_id: "dec_abc" }]);
});

Deno.test("no waitUntil: a duplicate email is padded to the median time of recent new sign-ups (fake clock and sleep)", async () => {
  let clock = 0;
  const slept: number[] = [];
  const inner = decision("allow", 1);
  const t = setup({
    tenkyu: async () => { clock += 40; return await inner(); },
    deps: { monotonic: () => clock, sleep: (ms) => { slept.push(ms); return Promise.resolve(); }, random: () => 0 },
  });
  await t.handler(post(valid));
  await t.handler(post(valid, { "x-forwarded-for": "198.51.100.9" })); // confirmations on: obfuscated user
  assert.deepEqual(slept, [40]);
  const off = setup({ tenkyu: inner, confirmations: "off", deps: { sleep: (ms) => { slept.push(ms); return Promise.resolve(); } } });
  await off.handler(post(valid));
  await off.handler(post(valid, { "x-forwarded-for": "198.51.100.9" })); // confirmations off: already-registered error
  assert.equal(slept.length, 2);
});

Deno.test("password over 72 UTF-8 bytes is weak_password (bcrypt truncates it); exactly 72 bytes passes", async () => {
  const t = setup({ tenkyu: decision("allow", 1) });
  for (const pw of ["a".repeat(73), "é".repeat(37), "🙂".repeat(19)]) {
    const res = await t.handler(post({ ...valid, password: pw }));
    assert.equal(res.status, 400);
    assert.equal((await res.json()).error, "weak_password");
  }
  assert.equal(t.signUps.length, 0);
  assert.equal((await t.handler(post({ ...valid, password: "a".repeat(72) }))).status, 200);
  assert.equal((await t.handler(post({ ...valid, email: "b@gmail.com", password: "é".repeat(36) }))).status, 200);
});

Deno.test("production without waitUntil refuses to start unless allowInlineScoring; an http TENKYU_URL is refused except localhost", () => {
  assert.throws(() => setup({ tenkyu: decision("allow", 1), deps: { production: true } }), /no EdgeRuntime.waitUntil/);
  assert.doesNotThrow(() => setup({ tenkyu: decision("allow", 1), deps: { production: true, allowInlineScoring: true } }));
  assert.doesNotThrow(() => setup({ tenkyu: decision("allow", 1), deps: { production: true, waitUntil: () => {} } }));
  assert.doesNotThrow(() => setup({ tenkyu: decision("allow", 1), deps: { production: false } }));
  for (const bad of ["http://api.tenkyu.dev", "ftp://x.test", "not a url", ""]) {
    assert.throws(() => setup({ tenkyu: decision("allow", 1), cfg: { tenkyuUrl: bad } }), /TENKYU_URL/, bad);
  }
  for (const ok of ["https://api.tenkyu.dev", "http://localhost:8080", "http://127.0.0.1:8000", "http://[::1]:8000"]) {
    assert.doesNotThrow(() => setup({ tenkyu: decision("allow", 1), cfg: { tenkyuUrl: ok } }), ok);
  }
});

Deno.test("a pending marker with the user id (no email) is logged before the answer, a done marker after the work", async () => {
  const works: Promise<void>[] = [];
  const logged: unknown[][] = [];
  const t = setup({ tenkyu: decision("allow", 1), onLog: (a) => logged.push(a), deps: { waitUntil: (w) => { works.push(w); } } });
  await t.handler(post(valid));
  assert.ok(JSON.stringify(logged).includes("scoring pending") && JSON.stringify(logged).includes("user-1"));
  assert.ok(!JSON.stringify(logged).includes("scoring done"));
  await Promise.all(works);
  assert.ok(JSON.stringify(logged).includes("scoring done"));
  assert.ok(!JSON.stringify([logged, t.errors, t.warnings]).includes(valid.email));
});
