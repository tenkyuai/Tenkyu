// Run: deno test browser/device-id_test.ts
import assert from "node:assert/strict";
import { getDeviceId, randomUuid, signUp } from "./device-id.ts";

const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const DEVICE_ID_RE = /^[A-Za-z0-9_-]{16,64}$/; // what the Edge Function accepts

function withCrypto(shape: Record<string, unknown> | undefined, fn: () => void | Promise<void>) {
  const original = Object.getOwnPropertyDescriptor(globalThis, "crypto")!;
  Object.defineProperty(globalThis, "crypto", { value: shape, configurable: true });
  try {
    return fn();
  } finally {
    Object.defineProperty(globalThis, "crypto", original);
  }
}

function withStorage(store: Map<string, string> | "throws", fn: () => void | Promise<void>) {
  const had = Object.getOwnPropertyDescriptor(globalThis, "localStorage");
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    value: {
      getItem: (k: string) => { if (store === "throws") throw new DOMException("denied", "SecurityError"); return store.get(k) ?? null; },
      setItem: (k: string, v: string) => { if (store === "throws") throw new DOMException("denied", "SecurityError"); store.set(k, v); },
    },
  });
  try {
    return fn();
  } finally {
    if (had) Object.defineProperty(globalThis, "localStorage", had); else delete (globalThis as Record<string, unknown>).localStorage;
  }
}

Deno.test("old WebView: no crypto.randomUUID -> getRandomValues-based v4 UUID, accepted by the function", () => {
  const real = globalThis.crypto;
  withCrypto({ getRandomValues: real.getRandomValues.bind(real) }, () => {
    const seen = new Set<string>();
    for (let i = 0; i < 200; i++) {
      const id = randomUuid();
      assert.match(id, UUID_V4);
      assert.match(id, DEVICE_ID_RE);
      seen.add(id);
    }
    assert.equal(seen.size, 200);
  });
});

Deno.test("fallback sets version and variant bits even for all-0xff and all-0x00 random bytes", () => {
  for (const fill of [0x00, 0xff]) {
    withCrypto({ getRandomValues: (a: Uint8Array) => a.fill(fill) }, () => assert.match(randomUuid(), UUID_V4));
  }
});

Deno.test("no crypto at all: still returns a valid id instead of crashing sign-up", () => {
  withCrypto(undefined, () => {
    assert.match(randomUuid(), UUID_V4);
    withStorage(new Map(), () => assert.match(getDeviceId(), DEVICE_ID_RE));
  });
});

Deno.test("getDeviceId in an old WebView stores one id and reuses it; blocked storage gives a one-off id", () => {
  const real = globalThis.crypto;
  withCrypto({ getRandomValues: real.getRandomValues.bind(real) }, () => {
    const store = new Map<string, string>();
    withStorage(store, () => {
      const a = getDeviceId();
      assert.equal(getDeviceId(), a);
      assert.equal(store.get("tenkyu_device_id"), a);
    });
    withStorage("throws", () => assert.match(getDeviceId(), DEVICE_ID_RE));
  });
});

Deno.test("modern browsers still use crypto.randomUUID", () => {
  withCrypto({ randomUUID: () => "11111111-2222-4333-8444-555555555555", getRandomValues: () => { throw new Error("unused"); } }, () => {
    assert.equal(randomUuid(), "11111111-2222-4333-8444-555555555555");
  });
});

Deno.test("signUp does not throw on a non-JSON error page", async () => {
  const realFetch = globalThis.fetch;
  globalThis.fetch = (() => Promise.resolve(new Response("<html>bad gateway</html>", { status: 502 }))) as typeof fetch;
  try {
    await withStorage(new Map(), async () => {
      assert.deepEqual(await signUp("https://x/f", "anon", "a@b.co", "pw"), { ok: false, error: "bad_response" });
    });
    globalThis.fetch = (() => Promise.resolve(new Response("", { status: 429 }))) as typeof fetch;
    await withStorage(new Map(), async () => {
      assert.deepEqual(await signUp("https://x/f", "anon", "a@b.co", "pw"), { ok: false, error: "rate_limited" });
    });
  } finally {
    globalThis.fetch = realFetch;
  }
});
