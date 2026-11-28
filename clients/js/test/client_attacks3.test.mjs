// Night-2 red-team round 3 (attacks3). Each test FAILS today because the attack works; it passes once the named
// fix is in. No network: fetch is a fake. Run: npm test (or node --test test/client_attacks3.test.mjs after build).
import test from "node:test";
import assert from "node:assert/strict";
import { Tenkyu } from "../dist/index.js";

const client = (fetch, logged = []) =>
  new Tenkyu({ apiKey: "tk_live_secret", baseUrl: "https://t.test", fetch, logger: { error: (m) => logged.push(m) } });

const grantOf = async (c, ua) => {
  try {
    return (await c.check({ email: "farmer@gmail.com", user_agent: ua })).credit_fraction;
  } catch {
    return 0; // throwing = the caller grants nothing
  }
};

// A. src/index.ts:362-368 trusts status 401 + JSON.parse(body).error.code === "unauthorized". JSON.parse keeps the
// LAST duplicate key, so a gateway/WAF that reflects the farmer's user_agent into its own 401 JSON without
// escaping lets him write `"code":"unauthorized"` himself. Result: a block becomes the 25% fail-open grant, and
// the developer is told "API key rejected". Fix: accept the bad-key answer only when the raw body text equals
// Tenkyu's exact serialization (hosted/api/app.py error_response), not merely parses to the right field.
test("attack3 A: a gateway 401 that reflects the user agent unescaped can forge Tenkyu's 'unauthorized' body", async () => {
  const waf = async (_u, init) => {
    const ua = JSON.parse(String(init.body)).user_agent ?? "";
    return new Response(`{"error":{"code":"waf_blocked","message":"blocked agent: ${ua}"}}`, {
      status: 401, headers: { "content-type": "application/json" },
    });
  };
  const logged = [];
  const forged = 'x","code":"unauthorized","message":"missing or invalid API key';
  assert.equal(await grantOf(client(waf, logged), forged), 0, "forged unauthorized body must not grant the 25% share");
  assert.deepEqual(logged, [], "and must not be logged as a rejected key");
});

// B. src/index.ts:259-265,184: a block page served with 200 + text/html (many WAFs, captive portals and
// "challenge" pages do this) fails JSON.parse, becomes `invalid_response`, and is treated as an outage: 25%.
// It is the same class as the 403 fix (a gateway the farmer can trigger turns a block into a grant) but with a
// status the fix did not cover. Fix: a 2xx whose content-type is not JSON is `refused` (grant nothing).
test("attack3 B: a gateway block page answered 200 text/html is not an outage (no free 25%)", async () => {
  const waf = async () => new Response("<html><body>Request blocked</body></html>", { status: 200, headers: { "content-type": "text/html" } });
  assert.equal(await grantOf(client(waf), "Mozilla <script>"), 0);
});
