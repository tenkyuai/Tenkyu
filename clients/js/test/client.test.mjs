import { test } from "node:test";
import assert from "node:assert/strict";
import { Tenkyu, TenkyuError, degradedDecision } from "../dist/index.js";

const KEY = "tk_test_SECRETSECRETSECRETSECRETSECRETSECRET123";
const DECISION = {
  id: "dec_1", action: "block", score: 0.9, credit_fraction: 0, reasons: ["alias_reuse"],
  signals: [{ name: "alias_reuse", score: 0.9 }], network_match: false, credits_exhausted: false, mode: "test",
};

function reply(status, body, headers = {}) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json", ...headers } });
}
function fakeFetch(handler) {
  const calls = [];
  const fn = async (url, init) => { calls.push({ url, init }); return handler(calls.length, url, init); };
  fn.calls = calls;
  return fn;
}
const BAD_KEY = { error: { code: "unauthorized", message: "missing or invalid API key" } }; // hosted/api/service.py unauthorized()
const make = (f, extra = {}) => new Tenkyu({ apiKey: KEY, baseUrl: "https://x.test", fetch: f, ...extra });

test("check success returns the decision", async () => {
  const f = fakeFetch(() => reply(200, DECISION));
  assert.deepEqual(await make(f).check({ email: "a@b.co" }), DECISION);
});

test("request shape: url, method, headers, body", async () => {
  const f = fakeFetch(() => reply(200, DECISION));
  const signup = { email: "a@b.co", ip: "203.0.113.7", links: { phone: "+1" }, request_id: "r1" };
  await make(f).check(signup);
  const { url, init } = f.calls[0];
  assert.equal(url, "https://x.test/v1/check");
  assert.equal(init.method, "POST");
  assert.equal(init.headers.authorization, `Bearer ${KEY}`);
  assert.equal(init.headers["content-type"], "application/json");
  assert.deepEqual(JSON.parse(init.body), signup);
  await make(f).outcome("dec_1", "farmer");
  assert.equal(f.calls[1].url, "https://x.test/v1/outcome");
  assert.deepEqual(JSON.parse(f.calls[1].init.body), { id: "dec_1", outcome: "farmer" });
  await make(fakeFetch((n, u, i) => { assert.equal(i.method, "GET"); assert.equal(i.body, undefined); return reply(200, {}); })).usage();
});

test("earnBack() posts request_id and proof (and links) to /v1/earn-back and returns the answer", async () => {
  const answer = { extra_credit_fraction: 0.75, granted: true, reason: "released" };
  const f = fakeFetch(() => reply(200, answer));
  assert.deepEqual(await make(f).earnBack("r1", "email_verified"), answer);
  assert.equal(f.calls[0].url, "https://x.test/v1/earn-back");
  assert.equal(f.calls[0].init.method, "POST");
  assert.deepEqual(JSON.parse(f.calls[0].init.body), { request_id: "r1", proof: "email_verified" });
  await make(f).earnBack("r2", "phone_verified", { phone: "+14155550100" });
  assert.deepEqual(JSON.parse(f.calls[1].init.body), { request_id: "r2", proof: "phone_verified", links: { phone: "+14155550100" } });
});

test("earnBack() throws on 404 and never fails open", async () => {
  const f = fakeFetch(() => reply(404, { error: { code: "not_found", message: "no such request_id" } }));
  await assert.rejects(make(f).earnBack("nope", "email_verified"), (e) => e instanceof TenkyuError && e.status === 404);
  const down = fakeFetch(() => { throw new Error("down"); });
  await assert.rejects(make(down).earnBack("r1", "email_verified"));
});

test("timeout fails open with a degraded allow", async () => {
  const f = fakeFetch((n, u, init) => new Promise((_, rej) => init.signal.addEventListener("abort", () => rej(new Error("aborted")))));
  const d = await make(f, { timeoutMs: 20 }).check({ ip: "1.2.3.4" });
  assert.deepEqual(
    { a: d.action, c: d.credit_fraction, g: d.degraded, r: d.reasons },
    { a: "allow", c: 0.25, g: true, r: ["tenkyu unreachable"] },
  );
});

test("network error and 5xx fail open; failOpen:false throws", async () => {
  const boom = fakeFetch(() => { throw new Error("ECONNRESET"); });
  assert.equal((await make(boom).check({ ip: "1.2.3.4" })).degraded, true);
  const five = fakeFetch(() => reply(500, { error: { code: "server_error", message: "x" } }));
  assert.equal((await make(five).check({ ip: "1.2.3.4" })).degraded, true);
  await assert.rejects(make(five, { failOpen: false }).check({ ip: "1.2.3.4" }), (e) => e.code === "server_error" && e.status === 500);
});

test("Tenkyu's own bad-key answer (401 + unauthorized JSON) fails open small (25%) and logs one clear error; failOpen:false still throws", async () => {
  for (const status of [401]) {
    const logged = [];
    const f = fakeFetch(() => reply(status, BAD_KEY));
    const client = make(f, { logger: { error: (m) => logged.push(m) } });
    const d = await client.check({ ip: "1.2.3.4" });
    assert.deepEqual(
      { a: d.action, c: d.credit_fraction, g: d.degraded, r: d.reasons },
      { a: "allow", c: 0.25, g: true, r: ["tenkyu api key rejected"] },
      String(status),
    );
    assert.deepEqual(logged, ["Tenkyu API key rejected: check TENKYU_API_KEY"], "logged once, no key in it");
    assert.ok(!JSON.stringify(logged).includes(KEY));
    await assert.rejects(
      make(f, { failOpen: false, logger: { error() {} } }).check({ ip: "1.2.3.4" }),
      (e) => e instanceof TenkyuError && e.code === "unauthorized" && e.status === status,
    );
  }
  // configurable share
  const f = fakeFetch(() => reply(401, BAD_KEY));
  const d = await make(f, { failOpenCreditFraction: 0.5, logger: { error() {} } }).check({ ip: "1.2.3.4" });
  assert.equal(d.credit_fraction, 0.5);
});

test("a 403, or a 401 that is not Tenkyu's JSON, is 'refused' and never failed open or blamed on the key", async () => {
  const cases = [
    [403, BAD_KEY], [403, {}], [401, {}], [401, { error: { code: "invalid_request" } }],
    [401, "<html>nope</html>"], [403, "<html>nope</html>"],
  ];
  for (const [status, body] of cases) {
    const logged = [];
    for (const failOpen of [true, false]) {
      const client = make(fakeFetch(() => (typeof body === "string" ? new Response(body, { status }) : reply(status, body))), { failOpen, logger: { error: (m) => logged.push(m) } });
      await assert.rejects(
        client.check({ ip: "1.2.3.4" }),
        (e) => e instanceof TenkyuError && e.code === "refused" && e.status === status &&
          e.message === `Tenkyu call refused by something in between (status ${status})`,
        `${status} ${JSON.stringify(body)} failOpen=${failOpen}`,
      );
    }
    assert.deepEqual(logged, [], "not logged as a rejected key");
  }
});

test("the key-rejected error is logged at most once per minute per client", async () => {
  const realNow = Date.now;
  let clock = 1_000_000;
  Date.now = () => clock;
  try {
    const logged = [];
    const client = make(fakeFetch(() => reply(401, BAD_KEY)), { logger: { error: (m) => logged.push(m) } });
    for (let i = 0; i < 5; i++) await client.check({ ip: "1.2.3.4" });
    assert.equal(logged.length, 1);
    clock += 59_000;
    await client.check({ ip: "1.2.3.4" });
    assert.equal(logged.length, 1, "still inside the minute");
    clock += 2_000;
    await client.check({ ip: "1.2.3.4" });
    assert.equal(logged.length, 2, "a minute later it is logged again");
    // a throwing logger never breaks check()
    clock += 120_000;
    const bad = make(fakeFetch(() => reply(401, BAD_KEY)), { logger: { error() { throw new Error("log sink down"); } } });
    assert.equal((await bad.check({ ip: "1.2.3.4" })).degraded, true);
  } finally {
    Date.now = realNow;
  }
});

test("outcome() and usage() still throw on 401 (and log the key error)", async () => {
  const logged = [];
  const client = make(fakeFetch(() => reply(401, BAD_KEY)), { logger: { error: (m) => logged.push(m) } });
  await assert.rejects(client.outcome("dec_1", "real"), (e) => e.status === 401);
  await assert.rejects(client.usage(), (e) => e.status === 401);
  assert.equal(logged.length, 1);
});

test("other 4xx (400, 404, 422) still throw: only a rejected key fails open", async () => {
  for (const status of [400, 404, 422]) {
    const logged = [];
    const client = make(fakeFetch(() => reply(status, {})), { logger: { error: (m) => logged.push(m) } });
    await assert.rejects(client.check({ ip: "1.2.3.4" }), (e) => e instanceof TenkyuError && e.status === status);
    assert.deepEqual(logged, []);
  }
});

test("shadow fields pass through, and malformed ones are dropped instead of crashing", async () => {
  const shadowed = { ...GOOD, shadow: true, would_action: "block", would_credit_fraction: 0 };
  const d = await make(fakeFetch(() => reply(200, shadowed))).check({ ip: "1.2.3.4" });
  assert.deepEqual(
    { s: d.shadow, w: d.would_action, f: d.would_credit_fraction, a: d.action, c: d.credit_fraction },
    { s: true, w: "block", f: 0, a: "allow", c: 1 },
  );
  for (const junk of [
    { shadow: "yes", would_action: "nuke", would_credit_fraction: "0" },
    { shadow: true, would_action: 7, would_credit_fraction: 5 },
    { shadow: true, would_action: "review", would_credit_fraction: null },
    { shadow: false, would_action: "block", would_credit_fraction: 0 },
  ]) {
    const x = await make(fakeFetch(() => reply(200, { ...GOOD, ...junk }))).check({ ip: "1.2.3.4" });
    assert.equal(x.action, "allow");
    assert.equal(x.credit_fraction, 1);
    assert.ok(x.shadow === undefined || x.shadow === true);
    if (junk.shadow !== true) assert.ok(!("would_action" in x) && !("would_credit_fraction" in x));
    if (x.would_action !== undefined) assert.ok(["allow", "review", "block"].includes(x.would_action));
    if (x.would_credit_fraction !== undefined) assert.ok(x.would_credit_fraction >= 0 && x.would_credit_fraction <= 1);
  }
  // a plain live decision has none of them
  const live = await make(fakeFetch(() => reply(200, GOOD))).check({ ip: "1.2.3.4" });
  assert.ok(!("shadow" in live) && !("would_action" in live));
});

test("400 throws invalid_request", async () => {
  const f = fakeFetch(() => reply(400, { error: { code: "invalid_request", message: "need one id" } }));
  await assert.rejects(make(f).check({}), (e) => e.code === "invalid_request" && e.status === 400);
});

test("429 retries once after Retry-After and then succeeds", async () => {
  const f = fakeFetch((n) => n === 1 ? reply(429, { error: { code: "rate_limited", message: "slow" } }, { "retry-after": "0" }) : reply(200, DECISION));
  const d = await make(f).check({ ip: "1.2.3.4" });
  assert.equal(f.calls.length, 2);
  assert.equal(d.id, "dec_1");
});

test("429 twice fails open after exactly one retry", async () => {
  const f = fakeFetch(() => reply(429, { error: { code: "rate_limited", message: "slow" } }, { "retry-after": "0" }));
  const d = await make(f).check({ ip: "1.2.3.4" });
  assert.equal(f.calls.length, 2);
  assert.equal(d.degraded, true);
});

test("api key never appears in error messages or serialization", async () => {
  const cases = [
    fakeFetch(() => reply(401, { error: { code: "unauthorized", message: "bad key" } })),
    fakeFetch(() => reply(400, {})),
  ];
  for (const f of cases) {
    await assert.rejects(make(f, { failOpen: false, logger: { error() {} } }).check({}), (e) => {
      assert.ok(!String(e.message).includes(KEY) && !String(e.stack).includes(KEY) && !JSON.stringify(e).includes(KEY));
      return true;
    });
  }
  const netFail = fakeFetch(() => { throw new Error(`failed with Bearer ${KEY}`); });
  await assert.rejects(make(netFail, { failOpen: false }).check({ ip: "1.2.3.4" }), (e) => {
    assert.ok(!e.message.includes(KEY));
    return true;
  });
  const client = make(cases[0]);
  assert.ok(!JSON.stringify(client).includes(KEY));
  assert.ok(!JSON.stringify(Object.values(client)).includes(KEY));
});

const GOOD = { ...DECISION, action: "allow", credit_fraction: 1 };
const rawReply = (status, text) => new Response(text, { status, headers: { "content-type": "application/json" } });

test("every degraded cause grants failOpenCreditFraction (default 0.25), never a full grant", async () => {
  const causes = {
    timeout: fakeFetch((n, u, init) => new Promise((_, rej) => init.signal.addEventListener("abort", () => rej(new Error("x"))))),
    network: fakeFetch(() => { throw new Error("ECONNRESET"); }),
    "5xx": fakeFetch(() => reply(503, {})),
    "429 twice": fakeFetch(() => reply(429, {}, { "retry-after": "0" })),
  };
  for (const [name, f] of Object.entries(causes)) {
    const d = await make(f, { timeoutMs: 20 }).check({ ip: "1.2.3.4" });
    assert.deepEqual({ a: d.action, c: d.credit_fraction, g: d.degraded }, { a: "allow", c: 0.25, g: true }, name);
  }
});

test("failOpenCreditFraction is configurable, clamped to 0..1, and falls back on junk", async () => {
  const five = fakeFetch(() => reply(500, {}));
  assert.equal((await make(five, { failOpenCreditFraction: 0 }).check({ ip: "1.2.3.4" })).credit_fraction, 0);
  assert.equal((await make(five, { failOpenCreditFraction: 0.5 }).check({ ip: "1.2.3.4" })).credit_fraction, 0.5);
  assert.equal((await make(five, { failOpenCreditFraction: 7 }).check({ ip: "1.2.3.4" })).credit_fraction, 1);
  assert.equal((await make(five, { failOpenCreditFraction: -1 }).check({ ip: "1.2.3.4" })).credit_fraction, 0);
  assert.equal((await make(five, { failOpenCreditFraction: NaN }).check({ ip: "1.2.3.4" })).credit_fraction, 0.25);
  assert.equal(degradedDecision().credit_fraction, 0.25);
  assert.equal(degradedDecision(0.1).credit_fraction, 0.1);
});

test("a healthy decision is not touched by failOpenCreditFraction", async () => {
  const d = await make(fakeFetch(() => reply(200, GOOD)), { failOpenCreditFraction: 0 }).check({ ip: "1.2.3.4" });
  assert.equal(d.credit_fraction, 1);
  assert.equal(d.degraded, undefined);
});

test("200 with a non-JSON body, empty body, null or {} is refused (grant nothing), not a crash, NaN credits or an outage", async () => {
  for (const text of ["<html>ok</html>", "", "null", "{}", "[]", "42", '"x"']) {
    await assert.rejects(
      make(fakeFetch(() => rawReply(200, text))).check({ ip: "1.2.3.4" }),
      (e) => e instanceof TenkyuError && e.code === "refused" && e.status === 200,
      JSON.stringify(text),
    );
  }
});

test("a 200 with a non-finite or out-of-range credit_fraction is refused, never a NaN or a grant", async () => {
  const bad = ['"1"', "null", "1e999", "-1e999", "-0.1", "1.5", "true"];
  for (const v of bad) {
    const text = `{"id":"dec_1","action":"allow","credit_fraction":${v}}`;
    await assert.rejects(make(fakeFetch(() => rawReply(200, text))).check({ ip: "1.2.3.4" }), (e) => e instanceof TenkyuError && e.code === "refused", v);
  }
  for (const action of ["maybe", undefined, 7]) {
    await assert.rejects(
      make(fakeFetch(() => reply(200, { id: "dec_1", action, credit_fraction: 1 }))).check({ ip: "1.2.3.4" }),
      (e) => e instanceof TenkyuError && e.code === "refused",
    );
  }
});

test("a 2xx with another media type (HTML, XML) is refused, even when failOpen is on and even if the body parses", async () => {
  for (const type of ["text/html", "application/xml", "text/html; charset=utf-8"]) {
    const f = fakeFetch(() => new Response(JSON.stringify(GOOD), { status: 200, headers: { "content-type": type } }));
    await assert.rejects(make(f).check({ ip: "1.2.3.4" }), (e) => e instanceof TenkyuError && e.code === "refused" && e.status === 200, type);
    await assert.rejects(make(f).usage(), (e) => e instanceof TenkyuError && e.code === "refused", type);
  }
  const ok = fakeFetch(() => new Response(JSON.stringify(GOOD), { status: 200, headers: { "content-type": "application/json; charset=utf-8" } }));
  assert.equal((await make(ok).check({ ip: "1.2.3.4" })).credit_fraction, 1);
  // no header, or text/plain: tolerated only while the whole body is a valid decision
  for (const headers of [{}, { "content-type": "text/plain" }]) {
    assert.equal((await make(fakeFetch(() => new Response(JSON.stringify(GOOD), { status: 200, headers }))).check({ ip: "1.2.3.4" })).credit_fraction, 1);
    await assert.rejects(make(fakeFetch(() => new Response("<html>blocked</html>", { status: 200, headers }))).check({ ip: "1.2.3.4" }), (e) => e.code === "refused");
  }
});

test("a 401 counts as Tenkyu's bad-key answer only when the raw body is exactly Tenkyu's, with a JSON content-type", async () => {
  const exact = JSON.stringify(BAD_KEY);
  const cases = [
    [exact, "text/html"], [exact, ""],
    [exact.replace('"unauthorized"', '"unauthorized "'), "application/json"],
    [`{"error":{"code":"x","message":"y","code":"unauthorized","message":"missing or invalid API key"}}`, "application/json"],
    [`{"error":{"code":"unauthorized","message":"missing or invalid API key","extra":"echo"}}`, "application/json"],
    [`{"error":{"message":"missing or invalid API key","code":"unauthorized"}}`, "application/json"],
  ];
  for (const [text, type] of cases) {
    const logged = [];
    const f = fakeFetch(() => new Response(text, { status: 401, headers: type ? { "content-type": type } : {} }));
    await assert.rejects(
      make(f, { logger: { error: (m) => logged.push(m) } }).check({ ip: "1.2.3.4" }),
      (e) => e instanceof TenkyuError && e.code === "refused" && e.status === 401,
      text,
    );
    assert.deepEqual(logged, [], text);
  }
  const f = fakeFetch(() => new Response(exact + "\n", { status: 401, headers: { "content-type": "application/json" } }));
  assert.equal((await make(f, { logger: { error() {} } }).check({ ip: "1.2.3.4" })).reasons[0], "tenkyu api key rejected");
});

test("a flood that exhausts Tenkyu's rate limit does not turn into full grants", async () => {
  let tokens = 40;
  const f = fakeFetch(() => (tokens-- > 0 ? reply(200, { ...GOOD }) : reply(429, {}, { "retry-after": "0" })));
  const client = make(f);
  const ds = await Promise.all(Array.from({ length: 80 }, () => client.check({ ip: "1.2.3.4" })));
  const unscored = ds.filter((d) => d.degraded);
  assert.ok(unscored.length > 0);
  assert.ok(unscored.every((d) => d.credit_fraction === 0.25));
  assert.equal(ds.reduce((sum, d) => sum + d.credit_fraction * 300, 0) < 80 * 300, true);
});

// ---------- degraded 200 from the server ----------

test("a 200 with degraded:true and credit_fraction 1 is capped at failOpenCreditFraction", async () => {
  const body = { ...GOOD, degraded: true, credit_fraction: 1 };
  const d = await make(fakeFetch(() => reply(200, body))).check({ ip: "1.2.3.4" });
  assert.equal(d.degraded, true);
  assert.equal(d.credit_fraction, 0.25);
  assert.equal(d.action, "allow");
  assert.equal((await make(fakeFetch(() => reply(200, body)), { failOpenCreditFraction: 0.5 }).check({ ip: "1.2.3.4" })).credit_fraction, 0.5);
  assert.equal((await make(fakeFetch(() => reply(200, body)), { failOpenCreditFraction: 0 }).check({ ip: "1.2.3.4" })).credit_fraction, 0);
  // the cap applies with failOpen:false too: it is the decision that is degraded, not the transport
  assert.equal((await make(fakeFetch(() => reply(200, body)), { failOpen: false }).check({ ip: "1.2.3.4" })).credit_fraction, 0.25);
});

test("a degraded 200 below the cap keeps its own value; degraded:false and absent are untouched", async () => {
  const low = await make(fakeFetch(() => reply(200, { ...GOOD, degraded: true, credit_fraction: 0.1 }))).check({ ip: "1.2.3.4" });
  assert.equal(low.credit_fraction, 0.1);
  const no = await make(fakeFetch(() => reply(200, { ...GOOD, degraded: false, credit_fraction: 1 }))).check({ ip: "1.2.3.4" });
  assert.equal(no.credit_fraction, 1);
});

// ---------- one overall deadline ----------

test("one deadline: slow first attempt + Retry-After wait + retry never exceed timeoutMs", async () => {
  // 429 arrives after ~60 ms, Retry-After says 2 s. Budget is 150 ms, so the wait cannot fit: no retry.
  const f = fakeFetch(async () => {
    await new Promise((r) => setTimeout(r, 60));
    return reply(429, {}, { "retry-after": "2" });
  });
  const started = Date.now();
  const d = await make(f, { timeoutMs: 150 }).check({ ip: "1.2.3.4" });
  assert.ok(Date.now() - started < 400, `took ${Date.now() - started} ms`);
  assert.equal(f.calls.length, 1, "retry skipped: the wait is longer than the remaining budget");
  assert.equal(d.degraded, true);
  assert.equal(d.credit_fraction, 0.25);
});

test("the retry gets only the remaining budget, so the total stays within timeoutMs", async () => {
  const f = fakeFetch((n, u, init) => {
    if (n === 1) return reply(429, {}, { "retry-after": "0.1" });
    return new Promise((_, rej) => init.signal.addEventListener("abort", () => rej(new Error("aborted")))); // hangs
  });
  const started = Date.now();
  const d = await make(f, { timeoutMs: 300 }).check({ ip: "1.2.3.4" });
  const took = Date.now() - started;
  assert.equal(f.calls.length, 2);
  assert.equal(d.degraded, true);
  assert.ok(took < 450, `took ${took} ms, expected about 300`);
});

test("a retry that fits the budget still happens", async () => {
  const f = fakeFetch((n) => (n === 1 ? reply(429, {}, { "retry-after": "0.05" }) : reply(200, GOOD)));
  const d = await make(f, { timeoutMs: 1000 }).check({ ip: "1.2.3.4" });
  assert.equal(f.calls.length, 2);
  assert.equal(d.credit_fraction, 1);
});

test("without Retry-After the default 1 s wait is skipped when the budget is 500 ms", async () => {
  const f = fakeFetch(() => reply(429, {}));
  const started = Date.now();
  const d = await make(f, { timeoutMs: 500 }).check({ ip: "1.2.3.4" });
  assert.equal(f.calls.length, 1);
  assert.ok(Date.now() - started < 300);
  assert.equal(d.degraded, true);
});

test("baseUrl: https always; http only for localhost, 127.0.0.1 and [::1]; anything else throws", () => {
  const f = fakeFetch(() => reply(200, DECISION));
  for (const ok of ["https://api.tenkyu.dev", "http://localhost:8080", "http://127.0.0.1:8000/", "http://[::1]:8000"]) {
    assert.doesNotThrow(() => new Tenkyu({ apiKey: KEY, baseUrl: ok, fetch: f }), ok);
  }
  for (const bad of ["http://api.tenkyu.dev", "http://localhost.evil.com", "http://localhost@evil.com", "ftp://x.test", "not a url", ""]) {
    assert.throws(() => new Tenkyu({ apiKey: KEY, baseUrl: bad, fetch: f }), /https|baseUrl/i, bad);
  }
});
