// Night-2 red-team round 3 (attacks3): the waitUntil/padding/bad-key changes of commit 9017005. Each test FAILS
// today because the attack works; it passes once the named fix is in. No network: Tenkyu's fetch and both
// Supabase clients are fakes.
// Run: deno test supabase/functions/tenkyu-signup/handler_attacks3_test.ts
import assert from "node:assert/strict";
import { createHandler, type AdminClient, type AuthClient, type Config, type CreditRow } from "./handler.ts";

const ORIGIN = "https://app.example.com";
const config: Config = {
  tenkyuApiKey: "tk_live_secret", tenkyuUrl: "https://tenkyu.test", timeoutMs: 500, grant: 300,
  allowedOrigin: ORIGIN, trustedProxyHops: 1, clientIpHeader: "", debugIpHeaders: false,
  rateLimit: { perIp: { capacity: 1e6, perMinute: 1e6 }, perDevice: { capacity: 1e6, perMinute: 1e6 } },
};
const ALLOW = () => new Response(JSON.stringify({ id: "dec_ok", action: "allow", credit_fraction: 1 }), { status: 200 });
const TRIGGER = /<script/i;

interface Opts {
  cfg?: Partial<Config>;
  waitUntil?: (w: Promise<void>) => void;
  registered?: Map<string, string>;
  /** An unconfirmed account re-submitted: Supabase returns the same user (identities non-empty). */
  unconfirmedResubmit?: boolean;
  clock?: { t: number };
}

function setup(tenkyu: (rawBody: string, ua: string, url: string, auth: string) => Response | Promise<Response>, o: Opts = {}) {
  const rows: CreditRow[] = [];
  const stored = new Map<string, CreditRow>(); // primary key user_id: the first row wins
  const logs: unknown[][] = [];
  const registered = o.registered ?? new Map<string, string>();
  const authClient: AuthClient = {
    auth: {
      signUp(args) {
        const id = registered.get(args.email);
        if (id !== undefined) {
          return Promise.resolve({ data: { user: { id: o.unconfirmedResubmit ? id : "fake", identities: o.unconfirmedResubmit ? [{}] : [] } }, error: null });
        }
        const nid = `user-${registered.size + 1}`;
        registered.set(args.email, nid);
        return Promise.resolve({ data: { user: { id: nid, identities: [{}] } }, error: null });
      },
    },
  };
  const adminClient: AdminClient = {
    from: () => ({ insert: (row) => { rows.push(row); if (!stored.has(row.user_id)) stored.set(row.user_id, row); return Promise.resolve({ error: null }); } }),
  };
  const clock = o.clock;
  const handler = createHandler({
    config: { ...config, ...o.cfg },
    fetch: ((u: string, init?: RequestInit) => {
      const body = String(init?.body);
      const auth = new Headers(init?.headers).get("authorization") ?? "";
      return Promise.resolve(tenkyu(body, (JSON.parse(body) as { user_agent?: string }).user_agent ?? "", String(u), auth));
    }) as unknown as typeof fetch,
    authClient, adminClient,
    log: { log: (...a: unknown[]) => { logs.push(a); }, warn: (...a: unknown[]) => { logs.push(a); }, error: (...a: unknown[]) => { logs.push(a); } },
    ...(o.waitUntil ? { waitUntil: o.waitUntil } : {}),
    ...(clock ? { monotonic: () => clock.t, sleep: (ms: number) => { clock.t += ms; return Promise.resolve(); } } : {}),
  });
  return { handler, rows, stored, logs, registered };
}

let n = 0;
let ipn = 0;
const req = (email: string, headers: Record<string, string> = {}, device = "0b6f1f2e-4c1d-4a55-9a0e-1f2d3c4b5a69") =>
  new Request("https://x.supabase.co/functions/v1/tenkyu-signup", {
    method: "POST",
    headers: { "content-type": "application/json", origin: ORIGIN, "user-agent": "TestUA/1.0", "x-forwarded-for": `203.0.113.${(++ipn % 250) + 1}`, ...headers },
    body: JSON.stringify({ email, password: "correct horse battery", device_id: device }),
  });
const fresh = () => `farm${++n}@gmail.com`;
const grantOf = (t: ReturnType<typeof setup>, email: string) => t.stored.get(t.registered.get(email) as string)?.granted ?? -1;

// A. handler.ts:467: a block page answered 200 text/html is a "bad_response" outage (25%).
// Fix: a 2xx whose content-type is not JSON is refused (return null: grant nothing).
Deno.test("attack3 A: a WAF block page answered 200 text/html (user-agent trigger) gets the 25% fail-open grant", async () => {
  const t = setup((_b, ua) => TRIGGER.test(ua)
    ? new Response("<html>Request blocked</html>", { status: 200, headers: { "content-type": "text/html" } })
    : ALLOW());
  const control = fresh();
  await t.handler(req(control));
  assert.equal(grantOf(t, control), 300, "control: no trigger, full grant");
  const farmer = fresh();
  await t.handler(req(farmer, { "user-agent": "Mozilla <script>alert(1)</script>" }));
  assert.equal(grantOf(t, farmer), 0, "a gateway's HTML block page must not become a quarter grant");
});

// A2. handler.ts:501-503 isTenkyuUnauthorized accepts any 401 whose parsed body has error.code "unauthorized". A gateway that
// reflects the user agent unescaped into its own 401 JSON lets the farmer write that code (last duplicate key
// wins). Fix: compare the raw body text with Tenkyu's exact serialization (hosted/api/app.py error_response).
Deno.test("attack3 A2: a 401 that reflects the user agent unescaped forges Tenkyu's unauthorized body", async () => {
  const t = setup((_b, ua) => new Response(`{"error":{"code":"waf_blocked","message":"blocked agent: ${ua}"}}`, {
    status: 401, headers: { "content-type": "application/json" },
  }));
  const farmer = fresh();
  await t.handler(req(farmer, { "user-agent": 'x","code":"unauthorized","message":"missing or invalid API key' }));
  assert.equal(grantOf(t, farmer), 0, "forged unauthorized body must not grant");
  assert.ok(!JSON.stringify(t.logs).includes("API key rejected"), "and must not blame the API key");
});

// B1. handler.ts:245-246: `deps.waitUntil(work)` is unguarded. If it throws (EdgeRuntime present but waitUntil
// refused, a wrapper outside a request) the handler rejects AFTER signUp created the account: new = 500, duplicate
// = 200 {ok:true}. Fix: try { deps.waitUntil(work) } catch { await work }.
Deno.test("attack3 B1: a throwing waitUntil makes a new sign-up fail while a duplicate answers 200", async () => {
  const t = setup(ALLOW, { waitUntil: () => { throw new Error("EdgeRuntime.waitUntil unavailable"); } });
  const email = fresh();
  let first: string;
  try {
    const res = await t.handler(req(email));
    first = `${res.status} ${await res.text()}`;
  } catch {
    first = "threw (500)";
  }
  const dup = await t.handler(req(email));
  const dupText = `${dup.status} ${await dup.text()}`;
  assert.equal(first, dupText, `new: ${first} / registered: ${dupText}`);
});

// B2. Killed before waitUntil's work finishes (isolate shutdown, wall-clock limit): the account exists, there is no
// credits row, and no log names it (the only failure log is in .catch()). README says treat a missing row as
// "setting up" forever. Fix: log { user_id } before replying, or insert the 0-credit pending row first.
Deno.test("attack3 B2: killed before waitUntil completes leaves no row and no log naming the account", async () => {
  const works: Promise<void>[] = [];
  const t = setup(() => new Promise<Response>(() => {}), { waitUntil: (w) => { works.push(w); } });
  const email = fresh();
  await t.handler(req(email));
  const userId = t.registered.get(email) as string;
  assert.ok(
    t.rows.length > 0 || JSON.stringify(t.logs).includes(userId),
    "no credits row and no log line with the user id: an unscored account cannot be found or fixed",
  );
});

// B3. An unconfirmed email re-submitted gets the same user id; its check reuses request_id with a different body and
// Tenkyu answers 400 at once (hosted/api/service.py:500): the fail-closed 0-credit row reaches the primary key
// before the first, slower check finishes, and the real user's grant is lost (first row wins). Fix: no
// fail-closed row for a request_id conflict, or upsert when the stored row is a pending/0 row.
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
    await new Promise((r) => setTimeout(r, 80));
    return ALLOW();
  }, { unconfirmedResubmit: true, waitUntil: (w) => { works.push(w); } });
  const victim = fresh();
  await t.handler(req(victim));
  await t.handler(req(victim, { "user-agent": "OtherUA/2.0" }, "1c7a2a3f-5d2e-4b66-8b1f-2a3e4d5c6b7a"));
  await Promise.all(works);
  assert.equal(grantOf(t, victim), 300, "the account must end with the real grant");
});

// C1. handler.ts:249-251,154-158 records every new sign-up's duration, including the fast failures a farmer causes (his
// user agent trips the WAF: a 403 in a few ms). Five of them make the median tiny: duplicates answer at once while
// normal new sign-ups still wait for Tenkyu. Fix: record only real Tenkyu answers and pad no lower than a floor.
Deno.test("attack3 C1: five fast farmer-caused failures poison the padding median and expose registered emails", async () => {
  const clock = { t: 0 };
  const t = setup((_b, ua) => {
    if (TRIGGER.test(ua)) { clock.t += 3; return new Response("<html>403</html>", { status: 403 }); }
    clock.t += 300;
    return ALLOW();
  }, { clock });
  const timed = async (email: string, headers: Record<string, string> = {}) => {
    const t0 = clock.t;
    await t.handler(req(email, headers));
    return clock.t - t0;
  };
  for (let i = 0; i < 9; i++) await timed(fresh());
  const victim = fresh();
  await timed(victim);
  const control = await timed(victim);
  assert.ok(Math.abs(control - 300) < 5, `control: a duplicate is padded to ${control} ms`);
  for (let i = 0; i < 5; i++) await timed(fresh(), { "user-agent": "<script>" });
  const dup = await timed(victim);
  const brandNew = await timed(fresh());
  assert.ok(Math.abs(dup - brandNew) < 50, `registered ${dup} ms vs new ${brandNew} ms`);
});

// C2. A cold instance (empty window) pads a duplicate to a fixed 100 ms (DEFAULT_PAD_MS) while a new sign-up takes
// the real check time; parallel bursts start cold instances. Fix: refuse to run without waitUntil in production,
// or pad a cold duplicate to config.timeoutMs.
Deno.test("attack3 C2: a cold instance pads a duplicate to a fixed 100 ms while a new sign-up takes the real check time", async () => {
  const registered = new Map<string, string>();
  const clock = { t: 0 };
  const a = setup(() => { clock.t += 400; return ALLOW(); }, { registered, clock });
  const victim = fresh();
  await a.handler(req(victim));
  const cold = setup(() => { clock.t += 400; return ALLOW(); }, { registered, clock });
  const t0 = clock.t;
  await cold.handler(req(victim));
  const dup = clock.t - t0;
  const t1 = clock.t;
  await cold.handler(req(fresh()));
  const brandNew = clock.t - t1;
  assert.ok(Math.abs(dup - brandNew) < 50, `registered ${dup} ms vs new ${brandNew} ms`);
});

// E. index.ts:20 / handler.ts:446: TENKYU_URL is not checked. The JS client (and so the Vercel recipe) now refuses
// http:// (except localhost); this recipe still sends `Authorization: Bearer tk_live_...` over plain http, where
// anyone on the path reads the key and can answer credit_fraction 1. Fix: in createHandler (or index.ts) throw unless
// the URL is https, or http on localhost / 127.0.0.1 / [::1], as clients/js/src/index.ts checkedBaseUrl does.
Deno.test("attack3 E: an http:// TENKYU_URL still gets the bearer key in clear text", async () => {
  const sentInClear: string[] = [];
  let built = true;
  try {
    const t = setup((_b, _ua, url, auth) => { if (url.startsWith("http://")) sentInClear.push(auth); return ALLOW(); }, { cfg: { tenkyuUrl: "http://api.tenkyu.dev" } });
    await t.handler(req(fresh()));
  } catch {
    built = false; // refusing to start is the fix
  }
  assert.deepEqual(sentInClear, [], `built=${built}: the key must never travel over http`);
});
