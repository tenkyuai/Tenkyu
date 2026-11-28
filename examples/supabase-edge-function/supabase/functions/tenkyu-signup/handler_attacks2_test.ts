// Night-2 red-team regression tests (attacks2). Each test FAILS today because the attack works; it will pass
// once the named fix is in. No network: Tenkyu's fetch and both Supabase clients are fakes.
// Run: deno test supabase/functions/tenkyu-signup/handler_attacks2_test.ts
import assert from "node:assert/strict";
import { createHandler, DEFAULT_NO_CREDITS_NOTICE, type AdminClient, type AuthClient, type Config, type CreditRow } from "./handler.ts";

const ORIGIN = "https://app.example.com";
const DEVICE = "0b6f1f2e-4c1d-4a55-9a0e-1f2d3c4b5a69";
const config: Config = {
  tenkyuApiKey: "tk_live_secret", tenkyuUrl: "https://tenkyu.test", timeoutMs: 500, grant: 300,
  allowedOrigin: ORIGIN, trustedProxyHops: 1, clientIpHeader: "", debugIpHeaders: false,
  rateLimit: { perIp: { capacity: 1e6, perMinute: 1e6 }, perDevice: { capacity: 1e6, perMinute: 1e6 } },
};

/** Tenkyu behind a gateway/WAF. Tenkyu itself never answers 403 (hosted/api only raises 401 unauthorized JSON). */
const WAF = /<script|union\s+select|\.\.\/|%00/i;
const gateway = (delayMs = 0) => async (rawBody: string): Promise<Response> => {
  if (delayMs > 0) await new Promise((r) => setTimeout(r, delayMs));
  if (WAF.test(rawBody)) return new Response("<html>403 Forbidden</html>", { status: 403, headers: { "content-type": "text/html" } });
  return new Response(JSON.stringify({ id: "dec_blk", action: "block", credit_fraction: 0 }), { status: 200 });
};

function setup(tenkyu: (rawBody: string) => Promise<Response>, cfg: Partial<Config> = {}) {
  const credits: CreditRow[] = [];
  const logs: unknown[][] = [];
  const registered = new Set<string>();
  const signUps: { email: string; password: string }[] = [];
  const authClient: AuthClient = {
    auth: {
      signUp(args) {
        signUps.push(args);
        if (registered.has(args.email)) return Promise.resolve({ data: { user: { id: "fake", identities: [] } }, error: null });
        registered.add(args.email);
        return Promise.resolve({ data: { user: { id: `user-${registered.size}`, identities: [{}] } }, error: null });
      },
    },
  };
  const adminClient: AdminClient = { from: () => ({ insert: (row) => { credits.push(row); return Promise.resolve({ error: null }); } }) };
  const handler = createHandler({
    config: { ...config, ...cfg },
    fetch: ((_u: string, init?: RequestInit) => tenkyu(String(init?.body))) as unknown as typeof fetch,
    authClient, adminClient,
    log: { log: (...a: unknown[]) => { logs.push(a); }, warn: (...a: unknown[]) => { logs.push(a); }, error: (...a: unknown[]) => { logs.push(a); } },
  });
  return { handler, credits, logs, signUps };
}

let n = 0;
let ipn = 0;
const req = (body: Record<string, unknown>, headers: Record<string, string> = {}) =>
  new Request("https://x.supabase.co/functions/v1/tenkyu-signup", {
    method: "POST",
    headers: { "content-type": "application/json", origin: ORIGIN, "user-agent": "TestUA/1.0", "x-forwarded-for": `203.0.113.${(++ipn % 250) + 1}`, ...headers },
    body: JSON.stringify({ password: "correct horse battery", device_id: DEVICE, ...body }),
  });

// A. handler.ts:300-304 treats ANY 401/403 as a rejected key and returns degraded + 25%. A gateway 403 triggered
// by the farmer's user-agent turns a block into 75 credits (of 300). Fix: only status 401 with Tenkyu's JSON
// {"error":{"code":"unauthorized"}} is a key problem; any other 401/403 is a client error (null = grant nothing).
Deno.test("attack2 A: a gateway 403 triggered by the user-agent turns a block (0 credits) into the 25% fail-open grant", async () => {
  const t = setup(gateway());
  await (await t.handler(req({ email: `farm${++n}@gmail.com` }))).text();
  assert.equal(t.credits[0].granted, 0, "control: blocked without the trigger");
  await (await t.handler(req({ email: `farm${++n}@gmail.com` }, { "user-agent": "Mozilla <script>alert(1)</script>" }))).text();
  assert.equal(t.credits[1]?.granted ?? 0, 0, "a non-Tenkyu 403 must not grant the fail-open share");
  assert.ok(!JSON.stringify(t.logs).includes("API key rejected"), "and must not be logged as a rejected key");
});

// B. handler.ts:226 vs 248: a duplicate returns {ok:true} at once; a new account with 0 credits returns
// {ok:true, notice} after a Tenkyu round trip. Fix: no notice in the response (show it after sign-in), and do
// the Tenkyu check after answering (waitUntil) so both paths cost the same.
Deno.test("attack2 B: a blocked caller can tell a new email from a registered one by the notice", async () => {
  const t = setup(gateway());
  const victim = `victim${++n}@gmail.com`;
  await t.handler(req({ email: victim }));
  const dup = await (await t.handler(req({ email: victim }))).text();
  const fresh = await (await t.handler(req({ email: `fresh${++n}@gmail.com` }))).text();
  assert.equal(dup, fresh, `registered: ${dup} / unregistered: ${fresh}`);
  assert.ok(fresh.includes(DEFAULT_NO_CREDITS_NOTICE) === dup.includes(DEFAULT_NO_CREDITS_NOTICE));
});

Deno.test("attack2 B: timing tells a registered email from a new one even with the notice turned off", async () => {
  const DELAY = 30;
  const t = setup(gateway(DELAY), { noCreditsNotice: "" });
  const median = (xs: number[]) => [...xs].sort((a, b) => a - b)[Math.floor(xs.length / 2)];
  const timeIt = async (email: string) => {
    const t0 = performance.now();
    await t.handler(req({ email }));
    return performance.now() - t0;
  };
  const fresh: number[] = [];
  const dup: number[] = [];
  for (let i = 0; i < 7; i++) {
    const e = `timing${++n}@gmail.com`;
    fresh.push(await timeIt(e));
    dup.push(await timeIt(e));
  }
  assert.ok(Math.abs(median(fresh) - median(dup)) < DELAY / 3, `new ${median(fresh).toFixed(1)} ms vs registered ${median(dup).toFixed(1)} ms`);
});

// D. handler.ts:105,262: the policy is `password.length` (UTF-16 units) up to 256, with no 72-byte cap, unlike
// examples/vercel-nextjs. bcrypt reads only the first 72 bytes: a provider that truncates instead of refusing
// makes "<72 bytes>X" and "<72 bytes>Y" the same password. Fix: reject > 72 UTF-8 bytes locally as the recipe does.
Deno.test("attack2 D: a password over 72 UTF-8 bytes is refused before it reaches the auth provider", async () => {
  const t = setup(gateway());
  const base = "a".repeat(72);
  for (const pw of [base + "X", "é".repeat(37), "🙂".repeat(19)]) {
    const res = await t.handler(req({ email: `pw${++n}@gmail.com`, password: pw }));
    assert.equal(res.status, 400, `${pw.length} units, ${new TextEncoder().encode(pw).length} bytes`);
  }
  assert.equal(t.signUps.length, 0, "nothing past 72 bytes reaches signUp");
});
