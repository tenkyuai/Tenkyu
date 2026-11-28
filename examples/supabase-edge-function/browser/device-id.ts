// Browser snippet. Random id kept in localStorage; no fingerprinting library.
// It is a weak signal: a farmer clears storage or uses a fresh profile and gets a new id.
// It still links repeat sign-ups from the same browser and costs honest users nothing.

const KEY = "tenkyu_device_id";
const VALID = /^[A-Za-z0-9_-]{16,64}$/;

/**
 * UUID v4. crypto.randomUUID needs a secure context and is missing in older WebViews (before
 * Chrome 92 / Safari 15.4), where calling it throws and would break sign-up. Fall back to
 * crypto.getRandomValues, which those WebViews have, and as a last resort to Math.random
 * (the id is a weak signal, not a secret, so a poor random source is acceptable there).
 */
export function randomUuid(): string {
  const c = (globalThis as { crypto?: Partial<Crypto> }).crypto;
  if (typeof c?.randomUUID === "function") return c.randomUUID();
  const bytes = new Uint8Array(16);
  if (typeof c?.getRandomValues === "function") c.getRandomValues(bytes);
  else for (let i = 0; i < 16; i++) bytes[i] = Math.floor(Math.random() * 256);
  bytes[6] = ((bytes[6] as number) & 0x0f) | 0x40; // version 4
  bytes[8] = ((bytes[8] as number) & 0x3f) | 0x80; // RFC 4122 variant
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export function getDeviceId(): string {
  try {
    const existing = localStorage.getItem(KEY);
    if (existing && VALID.test(existing)) return existing;
    const id = randomUuid();
    localStorage.setItem(KEY, id);
    return id;
  } catch {
    return randomUuid(); // storage blocked (private mode): a one-off id
  }
}

export async function signUp(
  functionUrl: string, // https://<project-ref>.supabase.co/functions/v1/tenkyu-signup
  anonKey: string, // the PUBLIC anon key, never the service-role key
  email: string,
  password: string,
): Promise<{ ok: boolean; error?: string; message?: string; notice?: string }> { // notice is never sent any more (it would reveal which emails exist); show your notice after sign-in from credits.notice_pending
  const res = await fetch(functionUrl, {
    method: "POST",
    headers: { "content-type": "application/json", authorization: `Bearer ${anonKey}`, apikey: anonKey },
    body: JSON.stringify({ email, password, device_id: getDeviceId() }),
  });
  try {
    return await res.json();
  } catch {
    // A proxy error page or an empty body is not JSON; do not throw into the sign-up form.
    return { ok: false, error: res.status === 429 ? "rate_limited" : "bad_response" };
  }
}
