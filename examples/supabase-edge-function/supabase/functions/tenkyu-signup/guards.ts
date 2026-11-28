// Small input guards used by handler.ts: strict IP check, rate-limit key, token-bucket rate limit, capped body read,
// idempotency key. Pure functions, no Deno globals, no imports.

// ---------- strict IP validation ----------

/** Dotted-quad IPv4: exactly four decimal octets 0-255, no leading zeros, no port, no spaces. */
export function isIPv4(s: string): boolean {
  const parts = s.split(".");
  if (parts.length !== 4) return false;
  return parts.every((p) => /^(0|[1-9]\d{0,2})$/.test(p) && Number(p) <= 255);
}

/**
 * IPv6 text form (RFC 4291): hex groups of 1-4 digits, at most one "::", optional trailing dotted
 * IPv4 (counts as two groups). No brackets, no zone id (%eth0), no port, no spaces.
 */
export function isIPv6(s: string): boolean {
  if (s.length < 2 || s.length > 45 || !/^[0-9a-fA-F:.]+$/.test(s) || !s.includes(":")) return false;
  const doubleColon = s.indexOf("::");
  if (doubleColon !== -1 && s.indexOf("::", doubleColon + 1) !== -1) return false; // ":::" or two "::"
  const halves = doubleColon === -1 ? [s] : [s.slice(0, doubleColon), s.slice(doubleColon + 2)];
  let groups = 0;
  for (let h = 0; h < halves.length; h++) {
    const half = halves[h] as string;
    if (half === "") continue;
    const items = half.split(":");
    for (let i = 0; i < items.length; i++) {
      const item = items[i] as string;
      const last = h === halves.length - 1 && i === items.length - 1;
      if (last && item.includes(".")) {
        if (!isIPv4(item)) return false;
        groups += 2;
      } else if (/^[0-9a-fA-F]{1,4}$/.test(item)) {
        groups += 1;
      } else {
        return false; // empty item (stray colon), too long, or not hex
      }
    }
  }
  return doubleColon === -1 ? groups === 8 : groups <= 7;
}

export function isStrictIp(s: string): boolean {
  return isIPv4(s) || isIPv6(s);
}

// ---------- address helpers: rate-limit key, global-address check ----------

/** Eight 16-bit groups of an IPv6 text address that already passed the strict check, or null. */
function ipv6Groups(ip: string): number[] | null {
  let head = ip;
  let tail: string[] = [];
  const dc = ip.indexOf("::");
  const toGroups = (part: string): number[] | null => {
    if (part === "") return [];
    const out: number[] = [];
    for (const item of part.split(":")) {
      if (item.includes(".")) {
        const o = item.split(".").map(Number);
        if (o.length !== 4 || o.some((n) => !Number.isInteger(n) || n < 0 || n > 255)) return null;
        out.push(((o[0] as number) << 8) | (o[1] as number), ((o[2] as number) << 8) | (o[3] as number));
      } else {
        const n = parseInt(item, 16);
        if (!/^[0-9a-fA-F]{1,4}$/.test(item) || Number.isNaN(n)) return null;
        out.push(n);
      }
    }
    return out;
  };
  if (dc !== -1) {
    head = ip.slice(0, dc);
    tail = [ip.slice(dc + 2)];
  }
  const first = toGroups(head);
  const last = dc === -1 ? [] : toGroups(tail[0] as string);
  if (first === null || last === null) return null;
  if (dc === -1) return first.length === 8 ? first : null;
  const fill = 8 - first.length - last.length;
  return fill < 1 ? null : [...first, ...new Array<number>(fill).fill(0), ...last];
}

/** The IPv4 inside an IPv4-mapped IPv6 address (::ffff:a.b.c.d or ::ffff:hhhh:hhhh), else null. */
function mappedIPv4(groups: number[]): string | null {
  if (groups.slice(0, 5).some((g) => g !== 0) || groups[5] !== 0xffff) return null;
  const hi = groups[6] as number;
  const lo = groups[7] as number;
  return `${hi >> 8}.${hi & 255}.${lo >> 8}.${lo & 255}`;
}

/**
 * Key for the per-IP rate limiter. IPv4: the address. IPv4-mapped IPv6 (::ffff:1.2.3.4): the IPv4 address,
 * so one client cannot get two buckets by switching notation. Other IPv6: the /64, because one subscriber
 * normally owns a whole /64 and can rotate through 2^64 addresses in it. Expects a strictly valid address;
 * anything unparseable is returned unchanged.
 */
export function rateLimitKey(ip: string): string {
  if (!ip.includes(":")) return ip;
  const groups = ipv6Groups(ip);
  if (groups === null) return ip;
  const v4 = mappedIPv4(groups);
  if (v4 !== null) return v4;
  return `${groups.slice(0, 4).map((g) => g.toString(16)).join(":")}::/64`;
}
function isGlobalIPv4(ip: string): boolean {
  const [a, b] = ip.split(".").map(Number) as [number, number];
  if (a === 0 || a === 10 || a === 127) return false; // "this network", private, loopback
  if (a === 100 && b >= 64 && b <= 127) return false; // CGNAT 100.64.0.0/10
  if (a === 169 && b === 254) return false; // link-local
  if (a === 172 && b >= 16 && b <= 31) return false; // private
  if (a === 192 && b === 168) return false; // private
  return a < 224; // multicast, reserved and broadcast are not client addresses
}

/**
 * false for addresses that cannot be a real visitor: private, loopback, link-local, CGNAT (100.64/10),
 * IPv6 unique-local (fc00::/7), unspecified, multicast. A proxy hop count that is off by one lands on
 * one of these, and using it would put every visitor into one rate-limit bucket and one Tenkyu "network".
 * IPv4-mapped IPv6 is judged by its IPv4 part. Documentation ranges (192.0.2/24, 2001:db8::/32) count as global.
 */
export function isGlobalIp(ip: string): boolean {
  if (!ip.includes(":")) return isIPv4(ip) && isGlobalIPv4(ip);
  const g = ipv6Groups(ip);
  if (g === null) return false;
  const v4 = mappedIPv4(g);
  if (v4 !== null) return isGlobalIPv4(v4);
  if (g.every((x) => x === 0) || (g.slice(0, 7).every((x) => x === 0) && g[7] === 1)) return false; // :: and ::1
  const first = g[0] as number;
  if ((first & 0xffc0) === 0xfe80) return false; // link-local fe80::/10
  if ((first & 0xfe00) === 0xfc00) return false; // unique-local fc00::/7
  if (first >> 8 === 0xff) return false; // multicast
  return true;
}

// ---------- token bucket ----------

export interface RateLimit {
  /** Requests allowed in a burst. */
  capacity: number;
  /** Sustained refill, requests per minute. */
  perMinute: number;
}

export interface RateLimiter {
  /** Spend one token for `key`. false = over the limit. */
  take(key: string): boolean;
}

/**
 * In-memory token bucket per key. EXAMPLE QUALITY: serverless instances do not share memory, so
 * each instance counts alone and a flood spread over many instances is only throttled per instance.
 * For production keep the buckets in a shared store: Upstash Redis / Vercel KV (INCR + EXPIRE or a
 * Lua token bucket), or a Supabase table updated by an RPC function. The take(key) shape stays.
 */
export function createRateLimiter(limit: RateLimit, now: () => number = Date.now, maxKeys = 10_000): RateLimiter {
  const buckets = new Map<string, { tokens: number; at: number }>();
  const refillPerMs = limit.perMinute / 60_000;
  return {
    take(key: string): boolean {
      const t = now();
      const b = buckets.get(key) ?? { tokens: limit.capacity, at: t };
      b.tokens = Math.min(limit.capacity, b.tokens + Math.max(0, t - b.at) * refillPerMs);
      b.at = t;
      const allowed = b.tokens >= 1;
      if (allowed) b.tokens -= 1;
      buckets.delete(key); // re-insert so the Map stays in least-recently-used order
      buckets.set(key, b);
      if (buckets.size > maxKeys) {
        const oldest = buckets.keys().next().value;
        if (oldest !== undefined) buckets.delete(oldest);
      }
      return allowed;
    },
  };
}

// ---------- capped body read ----------

/**
 * Read a request body but stop and return null once it exceeds maxBytes, instead of buffering
 * whatever a client streams (req.text() has no cap, and Content-Length can be absent or a lie).
 */
export async function readBodyCapped(req: Request, maxBytes: number): Promise<string | null> {
  if (req.body === null) return "";
  const reader = req.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      total += value.byteLength;
      if (total > maxBytes) {
        await reader.cancel().catch(() => {});
        return null;
      }
      chunks.push(value);
    }
  } catch {
    return null;
  }
  const all = new Uint8Array(total);
  let offset = 0;
  for (const c of chunks) {
    all.set(c, offset);
    offset += c.byteLength;
  }
  return new TextDecoder().decode(all);
}

// ---------- idempotency key ----------

/**
 * sha256("tenkyu-signup:" + user id), hex. The id comes from the account that was just created, so it is
 * unique per account: nobody else can submit it first, a different device or IP cannot collide with it, and
 * a repeated call for the same account (a retry after a crash) sends the same request_id and gets the same
 * decision back. It is NOT derived from the email: an email-derived id lets one person burn another's key.
 */
export async function requestIdForUser(userId: string): Promise<string> {
  const data = new TextEncoder().encode(`tenkyu-signup:${userId}`);
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", data));
  return Array.from(digest, (b) => b.toString(16).padStart(2, "0")).join("");
}
