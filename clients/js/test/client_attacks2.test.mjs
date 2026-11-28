// Night-2 red-team regression tests (attacks2). Each test FAILS today because the attack works.
// No network: fetch is a fake.
import test from "node:test";
import assert from "node:assert/strict";
import { Tenkyu } from "../dist/index.js";

const gatewayHtml = (status) => async () =>
  new Response("<html><body>Forbidden</body></html>", { status, headers: { "content-type": "text/html" } });

// Tenkyu's own API only ever answers 401 with {"error":{"code":"unauthorized"}} (hosted/api/service.py:81).
// A 403, or a 401 with another body, comes from something in front of it (gateway, WAF, IAM) and can be
// triggered by request content the farmer controls (user_agent, email). src/index.ts:176 and :135-137 treat
// any 401/403 as "our key is bad": log that, and hand back allow + 25%. Fix: key rejected only for status 401
// AND body.error.code === "unauthorized"; anything else non-2xx 4xx is thrown as-is (grant nothing).
for (const status of [403, 401]) {
  test(`attack2 A: a gateway ${status} (HTML, not Tenkyu's error JSON) is not a free 25% allow and not 'API key rejected'`, async () => {
    const logged = [];
    const c = new Tenkyu({ apiKey: "tk_live_secret", baseUrl: "https://t.test", fetch: gatewayHtml(status), logger: { error: (m) => logged.push(m) } });
    let d;
    try {
      d = await c.check({ email: "farmer@gmail.com", user_agent: "<script>alert(1)</script>" });
    } catch {
      d = undefined; // throwing is fine: the caller grants nothing on a client error
    }
    assert.ok(d === undefined || d.credit_fraction === 0, `got ${JSON.stringify(d)}`);
    assert.deepEqual(logged, [], "must not blame the API key");
  });
}

// A key sent over plain http is readable and replaceable by anyone on the path (and a forged answer can carry
// credit_fraction 1). src/index.ts:128 accepts any baseUrl. Fix: throw for http:// unless the host is
// localhost/127.0.0.1/[::1].
test("attack2 E: an http:// baseUrl is refused (the bearer key would go in clear text)", async () => {
  let sent = false;
  const make = () => new Tenkyu({ apiKey: "tk_live_secret", baseUrl: "http://api.tenkyu.dev", fetch: async () => { sent = true; return new Response("{}", { status: 200 }); } });
  assert.throws(make, /https/i);
  assert.equal(sent, false);
});
