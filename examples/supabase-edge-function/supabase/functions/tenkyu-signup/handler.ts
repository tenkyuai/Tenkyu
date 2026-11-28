// Sign-up logic for the tenkyu-signup Edge Function. index.ts wires in the real
// Supabase clients and env; tests wire in fakes. No Deno globals are used here.

import { createRateLimiter, isGlobalIp, isStrictIp, rateLimitKey, readBodyCapped, requestIdForUser, type RateLimit } from "./guards.ts";

export type Action = "allow" | "review" | "block";
export interface Decision {
  id: string;
  action: Action;
  credit_fraction: number;
  degraded?: boolean;
  /**
   * Shadow mode only (your Tenkyu account's first 7 days, extendable in Settings): `action` is "allow" and
   * `credit_fraction` 1, and these two say what Tenkyu would have done. Logged, never sent to the browser.
   */
  shadow?: boolean;
  would_action?: Action;
  would_credit_fraction?: number;
}

export interface SignUpResult {
  data: { user: { id: string; identities?: unknown[] | null } | null };
  error: { message: string; code?: string; status?: number } | null;
}
/** Client built with the ANON key. Only used to call auth.signUp. */
export interface AuthClient {
  auth: { signUp(args: { email: string; password: string }): Promise<SignUpResult> };
}
/** Client built with the SERVICE-ROLE key. Server only; only used to insert credits. */
export interface AdminClient {
  from(table: "credits"): {
    insert(row: CreditRow): PromiseLike<{ error: { message: string; code?: string } | null }>;
  };
}
export interface CreditRow {
  user_id: string;
  granted: number;
  decision_id: string | null;
  /**
   * Only when the account got 0 credits: your app shows the "we couldn't give free credits, contact support"
   * notice after sign-in. Never part of the sign-up response (see DEFAULT_NO_CREDITS_NOTICE). Needs the
   * `notice_pending` column of sql/credits.sql.
   */
  notice_pending?: true;
}

export interface Config {
  tenkyuApiKey: string;
  tenkyuUrl: string;
  timeoutMs: number;
  grant: number;
  /** Exact origin of your app, e.g. https://app.example.com. Empty = no CORS headers. */
  allowedOrigin: string;
  /** Which x-forwarded-for entry to trust, counted from the right. See clientIp(). */
  trustedProxyHops: number;
  /** Optional header that carries the client IP instead of x-forwarded-for. */
  clientIpHeader: string;
  /** Log the IP-related request headers so you can verify the trust rule on your project. */
  debugIpHeaders: boolean;
  /**
   * Share of GRANT given when Tenkyu could not score the sign-up (timeout, network error, 5xx, 429, or a 200
   * flagged `degraded`). Default 0.25 (env FAIL_OPEN_CREDIT_FRACTION). The sign-up still works; it just is not a
   * free full grant. A 4xx from Tenkyu (our request was wrong), and a 2xx that is not a JSON decision (a gateway's
   * block page), grant nothing.
   */
  failOpenCreditFraction?: number;
  /** Local password policy, checked before any account is created. Default 8 (env PASSWORD_MIN_LENGTH). */
  passwordMinLength?: number;
  /**
   * The sign-up response NEVER carries a notice (it would tell a caller whose sign-ups are blocked which
   * emails are new and which are registered). When the account got 0 credits (a block, or a failed check) the
   * credits row gets `notice_pending = true` and your app shows a notice after sign-in. Any text (default
   * DEFAULT_NO_CREDITS_NOTICE, env NO_CREDITS_MESSAGE) keeps the flag on; "" turns the flag off (you show
   * your own empty-balance message). The text itself lives in your UI.
   */
  noCreditsNotice?: string;
  /** Per-IP and per-device limits in front of the Tenkyu check. Defaults below. */
  rateLimit?: { perIp?: RateLimit; perDevice?: RateLimit };
}

export const DEFAULT_FAIL_OPEN_CREDIT_FRACTION = 0.25;
export const DEFAULT_PASSWORD_MIN_LENGTH = 8;
export const DEFAULT_NO_CREDITS_NOTICE =
  "We couldn't give free credits to this account automatically. Contact support to get them.";
const LOG_INTERVAL_MS = 60_000;
const KEY_REJECTED_MESSAGE = "Tenkyu API key rejected: check TENKYU_API_KEY";
const NOT_CONFIGURED_MESSAGE = "TENKYU_API_KEY is not set: granting only the fail-open share until it is";
/**
 * Duplicate-email padding (only without `waitUntil`): the median of recent GENUINE Tenkyu answer times plus a
 * little jitter, never above the max. On a cold instance, with no sample yet, it is at least the floor.
 */
const PAD_FLOOR_MS = 150;
const MAX_PAD_MS = 5000;
const PAD_SAMPLES = 9;
/** Jitter added to a median pad: up to this many ms. A cold pad (the floor) gets up to a third of the floor. */
const PAD_JITTER_MS = 4;
/** What hosted/api answers for a bad key, byte for byte (app.py error_response + service.py unauthorized()). */
const TENKYU_UNAUTHORIZED_BODY = '{"error":{"code":"unauthorized","message":"missing or invalid API key"}}';
/** A decision id that does not exist: the cold-instance timing probe (POST /v1/outcome) answers 404 and changes nothing. */
const TIMING_PROBE_ID = "dec_timing_probe";
/** Set to 1 to run in production WITHOUT waitUntil (index.ts reads it): the duplicate-email padding is then approximate. */
export const ALLOW_INLINE_SCORING_ENV = "TENKYU_ALLOW_INLINE_SCORING";
export const DEFAULT_RATE_LIMIT = {
  // A school or office can sign up a class from one IP, so the IP bucket is wide.
  perIp: { capacity: 20, perMinute: 15 },
  // One browser retrying a weak password a few times is fine; a script hammering is not.
  perDevice: { capacity: 5, perMinute: 5 },
};

/** Server-side, app-verified links for the Tenkyu check. Never taken from the browser body. */
export interface VerifiedLinks {
  /** E.164 phone number the app has verified (OTP). Lifts an honest user above the starter hold. */
  phone?: string;
}

export interface Deps {
  config: Config;
  fetch: typeof fetch;
  authClient: AuthClient;
  adminClient: AdminClient;
  log?: Pick<Console, "log" | "warn" | "error">;
  /** Test hook: clock for the rate limiter and the once-a-minute logs. */
  now?: () => number;
  /**
   * Run the Tenkyu check and the credits insert AFTER the response is sent (index.ts passes
   * EdgeRuntime.waitUntil), so a new sign-up and a duplicate email answer in the same time. Without it the
   * check runs before the answer and a duplicate email waits about as long as a recent check took instead
   * (tests leave it unset so the row exists when the handler resolves). The promise never rejects.
   * If it throws, the work is awaited inline and the answer is the same.
   */
  waitUntil?: (work: Promise<void>) => void;
  /**
   * True when running in production (index.ts: NODE_ENV or DENO_ENV is "production", or DENO_DEPLOYMENT_ID is
   * set). Without `waitUntil` the handler then REFUSES to start, unless `allowInlineScoring` is true.
   */
  production?: boolean;
  /** Opt in to production without `waitUntil` (env TENKYU_ALLOW_INLINE_SCORING=1): duplicate padding is approximate. */
  allowInlineScoring?: boolean;
  /** Test hook: random number in [0, 1) for the padding jitter (default Math.random). */
  random?: () => number;
  /** Test hooks for the duplicate-email padding: a monotonic clock in ms (default performance.now) and a sleep. */
  monotonic?: () => number;
  sleep?: (ms: number) => Promise<void>;
  /**
   * Optional: return links the APP has verified server-side (e.g. a phone that passed an OTP) for this
   * email. Sent to Tenkyu as `links`. Do NOT read them from the request body: the browser could claim any
   * phone. Errors here are ignored (the check runs without links).
   */
  verifiedLinks?: (input: { email: string }) => Promise<VerifiedLinks | undefined> | VerifiedLinks | undefined;
}

export const MAX_BODY_BYTES = 8192;
const PHONE_E164 = /^\+[1-9]\d{6,14}$/;
const EMAIL_MAX = 254;
const PASSWORD_MAX = 256;
/** bcrypt (Supabase Auth) reads only the first 72 BYTES: a longer password would be silently truncated. */
export const PASSWORD_MAX_BYTES = 72;
const DEVICE_ID_RE = /^[A-Za-z0-9_-]{16,64}$/;
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
// Control characters (incl. NUL) and lone surrogates make Tenkyu answer 400; refuse them before anything runs.
const CONTROL_CHARS = /[\u0000-\u001f\u007f]/;
/** The same answer for a new account and for an email that already has one. No user id, no credit amount. */
const GENERIC_OK = { ok: true } as const;
const UA_MAX = 512;

export function createHandler(deps: Deps): (req: Request) => Promise<Response> {
  const { config } = deps;
  // The bearer key must never travel in clear text (the JS client refuses http the same way).
  checkedTenkyuUrl(config.tenkyuUrl);
  if (deps.waitUntil === undefined && deps.production === true && deps.allowInlineScoring !== true) {
    // Without waitUntil a duplicate email is only padded to a guess of the check time: a timing oracle for which
    // emails are registered. Fail at startup, not silently in production.
    throw new Error(
      `tenkyu-signup: no EdgeRuntime.waitUntil in production. Fix the runtime, or set ${ALLOW_INLINE_SCORING_ENV}=1 to accept an approximate duplicate-email padding`,
    );
  }
  const log = deps.log ?? console;
  const random = deps.random ?? Math.random;
  const now = deps.now ?? Date.now;
  const failOpenFraction = clampFraction(config.failOpenCreditFraction, DEFAULT_FAIL_OPEN_CREDIT_FRACTION);
  const flagNotice = (config.noCreditsNotice ?? DEFAULT_NO_CREDITS_NOTICE) !== "";
  const monotonic = deps.monotonic ?? (() => performance.now());
  const sleep = deps.sleep ?? ((ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms)));
  // Recent durations of "check Tenkyu + insert credits" that got a GENUINE Tenkyu answer, to pad a duplicate email
  // to a similar time. Failures a farmer can cause quickly (a WAF refusing his user agent) never enter it.
  const durations: number[] = [];
  // Set when waitUntil threw once: from then on new sign-ups wait inline, so duplicates must be padded too.
  let waitUntilBroken = false;
  /**
   * Wait about as long as a new sign-up does, for a duplicate email (no waitUntil). With no genuine sample yet (a
   * cold instance, which a burst of parallel requests starts on purpose) it measures one now: a no-op outcome
   * call for a decision id that does not exist (404) is one authenticated round trip to Tenkyu and changes nothing.
   * No floor once there are samples: the median IS the time a new sign-up takes, and padding a fast Tenkyu up to a
   * floor would make a duplicate slower than a new sign-up, an oracle of its own.
   */
  const padDuplicate = async (): Promise<void> => {
    if (durations.length === 0) {
      const t0 = monotonic();
      await probeTenkyu(deps);
      const waited = monotonic() - t0;
      if (waited < PAD_FLOOR_MS) await sleep(PAD_FLOOR_MS - waited + random() * (PAD_FLOOR_MS / 3));
      return;
    }
    const sorted = [...durations].sort((x, y) => x - y);
    await sleep(Math.min(MAX_PAD_MS, (sorted[Math.floor(sorted.length / 2)] ?? 0) + random() * PAD_JITTER_MS));
  };
  // One clear error per cause at most once a minute (per instance): a bad key, a missing key, or something
  // in between (gateway, WAF) refusing the call.
  const lastLogged = new Map<string, number>();
  const logThrottled: Throttled = (kind, ...message) => {
    const t = now();
    if (t - (lastLogged.get(kind) ?? Number.NEGATIVE_INFINITY) < LOG_INTERVAL_MS) return;
    lastLogged.set(kind, t);
    log.error(...message);
  };
  const passwordMinLength = Math.max(1, Math.floor(config.passwordMinLength ?? DEFAULT_PASSWORD_MIN_LENGTH));
  const ipLimiter = createRateLimiter(config.rateLimit?.perIp ?? DEFAULT_RATE_LIMIT.perIp, now);
  const deviceLimiter = createRateLimiter(config.rateLimit?.perDevice ?? DEFAULT_RATE_LIMIT.perDevice, now);

  return async function handle(req: Request): Promise<Response> {
    const cors = corsHeaders(req, config.allowedOrigin);

    if (req.method === "OPTIONS") return new Response(null, { status: 204, headers: cors });
    if (req.method !== "POST") return json({ ok: false, error: "method_not_allowed" }, 405, cors);

    // CORS only stops other websites' scripts in a browser. It is not authentication;
    // scripts and curl send no Origin header and pass. A wrong Origin is refused.
    const origin = req.headers.get("origin");
    if (origin !== null && origin !== config.allowedOrigin) {
      return json({ ok: false, error: "forbidden" }, 403, cors);
    }

    const declared = Number(req.headers.get("content-length") ?? "0");
    if (declared > MAX_BODY_BYTES) return json({ ok: false, error: "payload_too_large" }, 413, cors);

    const ip = clientIp(req.headers, config);
    // Per-IP limit first: it costs nothing and runs before the body is read. IPv6 is bucketed by /64. No valid
    // (global) IP, no IP bucket.
    if (ip !== undefined && !ipLimiter.take(rateLimitKey(ip))) return rateLimited(cors);

    // Stream the body with a hard cap: Content-Length can be missing or false.
    const raw = await readBodyCapped(req, MAX_BODY_BYTES);
    if (raw === null) return json({ ok: false, error: "payload_too_large" }, 413, cors);

    const parsed = parseInput(raw, passwordMinLength);
    if (!parsed.ok) {
      const message = parsed.error === "weak_password" ? `Password must be ${passwordMinLength} characters to ${PASSWORD_MAX_BYTES} bytes` : undefined;
      return json({ ok: false, error: parsed.error, ...(message ? { message } : {}) }, 400, cors);
    }
    const input = parsed.input;
    if (!deviceLimiter.take(input.deviceId)) return rateLimited(cors);

    if (config.debugIpHeaders) {
      log.log("tenkyu-signup ip headers", {
        "x-forwarded-for": req.headers.get("x-forwarded-for"),
        "cf-connecting-ip": req.headers.get("cf-connecting-ip"),
        "x-real-ip": req.headers.get("x-real-ip"),
      });
    }

    // 1. Create the account FIRST. Tenkyu is only asked about an account that is new, and the request_id is
    //    derived from that account's id, so nobody can pre-empt it and a retry cannot collide with a
    //    different body. signUp, not admin.createUser: see README "Why signUp".
    const signUp = await deps.authClient.auth.signUp({ email: input.email, password: input.password });
    if (signUp.error) {
      // Confirmations off: Supabase says so in plain text. Answer exactly as for a new account.
      if (isAlreadyRegistered(signUp.error)) {
        log.log("tenkyu-signup existing email: answered generically, no check, no credits");
        if (deps.waitUntil === undefined || waitUntilBroken) await padDuplicate();
        return json(GENERIC_OK, 200, cors);
      }
      // Only a password-policy refusal is shown (it depends on the password alone). Anything else (an email-send
      // rate limit, an invalid-address check, an outage) can depend on whether the address is new, so it gets the
      // duplicate answer. Log the code, never the message: it can carry the email.
      if (isPasswordPolicy(signUp.error, input.email)) {
        return json({ ok: false, error: "signup_failed", message: signUp.error.message }, 400, cors);
      }
      log.warn("tenkyu-signup auth provider error: answered generically, no check, no credits", {
        code: signUp.error.code ?? (signUp.error.status !== undefined ? `status_${signUp.error.status}` : "unknown"),
      });
      if (deps.waitUntil === undefined || waitUntilBroken) await padDuplicate();
      return json(GENERIC_OK, 200, cors);
    }
    const user = signUp.data.user;
    if (!user) return json({ ok: false, error: "signup_failed", message: "signup failed" }, 400, cors);

    // Confirmations on: Supabase hides "email already registered" by returning a fake user with no
    // identities. Do not check or grant anything for it; answer exactly as for success.
    if (Array.isArray(user.identities) && user.identities.length === 0) {
      log.log("tenkyu-signup existing email (obfuscated user): answered generically, no check, no credits");
      if (deps.waitUntil === undefined || waitUntilBroken) await padDuplicate();
      return json(GENERIC_OK, 200, cors);
    }

    // 2. A real new account: ask Tenkyu, then grant. The answer is the same {ok:true} on every path (new,
    //    duplicate, blocked, outage) and carries no notice. With waitUntil the answer goes out first and this
    //    runs after it; without it the answer waits and the duplicate paths above pad to a similar time.
    //    A function killed before the background work finishes (isolate shutdown, wall-clock limit) leaves an
    //    account with no credits row, and nothing runs later to say so. So a one-line marker with the user id
    //    (never the email) is logged BEFORE the reply and another after the work: a "pending" with no "done" is
    //    an account to reconcile (README, "If the function is killed").
    const result = { genuine: false };
    log.log("tenkyu-signup scoring pending", { user_id: user.id });
    const work = scoreAndGrant(user.id, input, ip, req.headers.get("user-agent"))
      .then((genuine) => { result.genuine = genuine; })
      .catch(() => {
        // Never let a background failure become an unhandled rejection. User id only.
        log.error("tenkyu-signup scoring failed, fix by hand", { user_id: user.id });
      })
      .then(() => { log.log("tenkyu-signup scoring done", { user_id: user.id }); });
    let deferred = false;
    if (deps.waitUntil !== undefined && !waitUntilBroken) {
      try {
        deps.waitUntil(work);
        deferred = true;
      } catch {
        // EdgeRuntime is present but refused the task. The account already exists, so do not answer 500 (a
        // duplicate answers 200: that difference lists registered emails). Wait for the work here instead.
        waitUntilBroken = true;
        log.warn("tenkyu-signup waitUntil unavailable: scoring inline");
      }
    }
    if (!deferred) {
      const t0 = monotonic();
      await work;
      if (result.genuine) {
        durations.push(monotonic() - t0);
        if (durations.length > PAD_SAMPLES) durations.shift();
      }
    }
    return json(GENERIC_OK, 200, cors);
  };

  /** Resolves true when Tenkyu gave a genuine, scored answer (the only kind whose duration may feed the padding). */
  async function scoreAndGrant(userId: string, input: Input, ip: string | undefined, rawUserAgent: string | null): Promise<boolean> {
    const userAgent = (rawUserAgent ?? "").slice(0, UA_MAX) || undefined;
    const decision = await checkWithTenkyu(deps, {
      email: input.email,
      ip,
      device_id: input.deviceId,
      user_agent: userAgent,
      links: await appLinks(deps, input.email),
      request_id: await requestIdForUser(userId),
    }, logThrottled);
    if (decision === "conflict") {
      // The same unconfirmed account was submitted twice (another device or user agent) and Tenkyu answered 400
      // "request_id reused with a different body". The FIRST check owns the grant: write nothing, so this one
      // cannot win the primary key with a 0 row.
      log.warn("tenkyu-signup request_id conflict: same account submitted twice, this check writes nothing", { user_id: userId });
      return false;
    }
    if (decision === null) {
      // Fail closed: Tenkyu answered a 4xx other than 429 that is not its own bad-key answer (our request was
      // wrong, or a gateway/WAF in front of it refused the call). Already logged by checkWithTenkyu. The
      // account gets a 0-credit row so the app can show the notice after sign-in; grant by hand once the
      // cause is fixed. (A rejected key and a missing key are not here: they get the small fail-open grant.)
      log.error("tenkyu-signup no credits granted: Tenkyu did not score this sign-up", { user_id: userId });
      await insertCredits({ user_id: userId, granted: 0, decision_id: null, ...(flagNotice ? { notice_pending: true as const } : {}) });
      return false;
    }

    // A degraded decision (local fallback, or a 200 the server flagged `degraded`) was not scored: cap it,
    // and store no decision id so outage grants can be found with `decision_id is null and granted > 0`.
    const degraded = decision.degraded === true;
    const fraction = degraded ? Math.min(decision.credit_fraction, failOpenFraction) : decision.credit_fraction;
    const granted = grantAmount(config.grant, fraction);
    await insertCredits({
      user_id: userId,
      granted,
      decision_id: degraded ? null : decision.id || null,
      // Recorded server-side for the app to show after sign-in: never put it in the sign-up response.
      ...(granted === 0 && flagNotice ? { notice_pending: true as const } : {}),
    });
    return !degraded;
  }

  async function insertCredits(row: CreditRow): Promise<void> {
    const { error } = await deps.adminClient.from("credits").insert(row);
    if (error && isUniqueViolation(error)) {
      // The primary key kept the first grant. Expected on a repeated call for the same user.
      log.log("tenkyu-signup credits already granted", { user_id: row.user_id });
    } else if (error) {
      // The account exists, so do not fail the signup. Fix by hand from the log.
      log.error("tenkyu-signup credits insert failed", { user_id: row.user_id, granted: row.granted, message: error.message });
    }
  }
}

export function grantAmount(grant: number, creditFraction: number): number {
  return Math.round(grant * clampFraction(creditFraction, 0));
}

function clampFraction(value: number | undefined, fallback: number): number {
  if (typeof value !== "number" || !Number.isFinite(value)) return fallback;
  return Math.min(1, Math.max(0, value));
}

/** Supabase with email confirmation off answers a duplicate sign-up with an error instead of a fake user. */
function isAlreadyRegistered(error: { message: string; code?: string }): boolean {
  return error.code === "user_already_exists" || /already (been )?registered|already exists/i.test(error.message);
}

/** `weak_password`, or Supabase's own "Password ..." text, and never a message that repeats the email. */
function isPasswordPolicy(error: { message: string; code?: string }, email: string): boolean {
  if (email !== "" && error.message.toLowerCase().includes(email.toLowerCase())) return false;
  return error.code === "weak_password" || /^password\b/i.test(error.message);
}

function isUniqueViolation(error: { message: string; code?: string }): boolean {
  return error.code === "23505" || /duplicate key|unique constraint/i.test(error.message);
}

function rateLimited(cors: Headers): Response {
  const headers = new Headers(cors);
  headers.set("retry-after", "10");
  return json({ ok: false, error: "rate_limited" }, 429, headers);
}

async function appLinks(deps: Deps, email: string): Promise<{ phone: string } | undefined> {
  if (!deps.verifiedLinks) return undefined;
  try {
    const links = await deps.verifiedLinks({ email });
    return typeof links?.phone === "string" && PHONE_E164.test(links.phone) ? { phone: links.phone } : undefined;
  } catch {
    return undefined; // a broken lookup must not break sign-up
  }
}

/**
 * Client IP from request headers. UNVERIFIED: how the Supabase edge fills x-forwarded-for.
 *
 * Each proxy appends the address it saw to the RIGHT end of x-forwarded-for; anything
 * further left was written by the client or an earlier hop and can be forged. So the only
 * trustworthy entry is the one added by the nearest proxy you trust. With one trusted proxy
 * that is the LAST entry (trustedProxyHops = 1). If Supabase puts another proxy of its own
 * behind the one that saw the visitor, the visitor is the second from the right (set 2).
 * Never use the first entry: the client controls it.
 *
 * Or must check on the deployed project: set DEBUG_IP_HEADERS=1, sign up once from a phone on
 * mobile data (IP known to you), read the function logs, and pick the hop count (or set
 * CLIENT_IP_HEADER, e.g. cf-connecting-ip, if the platform sets one; also UNVERIFIED).
 * If the chosen entry is not a strict IPv4/IPv6 address, or is not a global one (private, loopback, link-local,
 * CGNAT 100.64/10, IPv6 unique-local fc00::/7, unspecified), no IP is sent and no IP bucket is used: a wrong
 * hop count that lands on an internal proxy address would otherwise put every visitor into one bucket and
 * make unrelated people look like one network. Tenkyu still scores email and device id.
 */
export function clientIp(headers: Headers, config: Pick<Config, "clientIpHeader" | "trustedProxyHops">): string | undefined {
  let candidate: string | undefined;
  if (config.clientIpHeader) {
    candidate = headers.get(config.clientIpHeader)?.trim();
  } else {
    const parts = (headers.get("x-forwarded-for") ?? "").split(",").map((p) => p.trim()).filter(Boolean);
    const hops = Math.max(1, Math.floor(config.trustedProxyHops));
    candidate = parts[parts.length - hops];
  }
  // Strict parse: a port, brackets, a zone id or garbage means "no IP", never a forwarded junk string.
  return candidate !== undefined && isStrictIp(candidate) && isGlobalIp(candidate) ? candidate : undefined;
}

interface Input {
  email: string;
  password: string;
  deviceId: string;
}

type ParsedInput = { ok: true; input: Input } | { ok: false; error: "invalid_request" | "weak_password" };
const INVALID = { ok: false, error: "invalid_request" } as const;

/** All local validation. The email is normalized (trim, lowercase) here, once; everything downstream uses it. */
function parseInput(raw: string, passwordMinLength: number): ParsedInput {
  let body: unknown;
  try {
    body = JSON.parse(raw);
  } catch {
    return INVALID;
  }
  if (typeof body !== "object" || body === null || Array.isArray(body)) return INVALID;
  const { email: rawEmail, password, device_id } = body as Record<string, unknown>;
  if (typeof rawEmail !== "string" || rawEmail.length > EMAIL_MAX + 64) return INVALID;
  const email = rawEmail.trim().toLowerCase();
  if (email.length === 0 || email.length > EMAIL_MAX || !EMAIL_RE.test(email)) return INVALID;
  if (typeof password !== "string" || password.length === 0 || password.length > PASSWORD_MAX) return INVALID;
  if (typeof device_id !== "string" || !DEVICE_ID_RE.test(device_id)) return INVALID;
  if (CONTROL_CHARS.test(email) || !email.isWellFormed()) return INVALID;
  if (password.length < passwordMinLength || new TextEncoder().encode(password).length > PASSWORD_MAX_BYTES) {
    return { ok: false, error: "weak_password" };
  }
  return { ok: true, input: { email, password, deviceId: device_id } };
}

interface CheckBody {
  email: string;
  ip: string | undefined;
  device_id: string;
  user_agent: string | undefined;
  links: { phone: string } | undefined;
  request_id: string;
}

type Throttled = (kind: string, ...message: unknown[]) => void;

/**
 * POST /v1/check for a newly created account. Never throws.
 *  - Network error, timeout, 5xx, 429 or a 3xx: fail open with a reduced grant. Returns action allow,
 *    degraded true, credit_fraction = failOpenFraction.
 *  - Tenkyu's own bad-key answer, exactly status 401, a JSON content-type and the body
 *    {"error":{"code":"unauthorized","message":"missing or invalid API key"}} compared as RAW TEXT
 *    (hosted/api/app.py error_response, service.py unauthorized()), and a missing TENKYU_API_KEY: OUR
 *    misconfiguration. It must not give every real user 0 credits, so it fails open small like an outage
 *    (degraded, failOpenFraction), after one clear error log at most once a minute ("Tenkyu API key rejected:
 *    check TENKYU_API_KEY", or the missing-key one). Raw text, not the parsed object: a gateway that echoes the
 *    farmer's user agent into its own 401 JSON can write a duplicate "code":"unauthorized" key, which parses fine.
 *  - Any other 401/403, or a 2xx that is not a JSON decision (another media type such as an HTML block page
 *    answered 200, or a body that is not a valid decision): Tenkyu never sends those. Something in front of it
 *    (gateway, WAF) answered, and a farmer can trigger that with a user agent or email the WAF dislikes. Failing
 *    open would turn his block into a 25% grant, so: null (grant nothing) and a distinct once-a-minute error,
 *    "Tenkyu call refused by something in between (status N)".
 *  - 400 "request_id reused with a different body": the same account was checked twice. Returns "conflict":
 *    the caller writes nothing and the first check's grant stands.
 *  - Any other 4xx (400 bad body): OUR request was wrong, not Tenkyu being down. Logged at error level and
 *    returned as null, which means grant nothing. Never a free full grant for a client error.
 */
async function checkWithTenkyu(deps: Deps, body: CheckBody, logThrottled: Throttled): Promise<Decision | "conflict" | null> {
  const { config } = deps;
  const log = deps.log ?? console;
  const failOpenFraction = clampFraction(config.failOpenCreditFraction, DEFAULT_FAIL_OPEN_CREDIT_FRACTION);
  const failOpen = (cause: string): Decision => {
    log.warn("tenkyu unreachable", { cause });
    return { id: "", action: "allow", credit_fraction: failOpenFraction, degraded: true };
  };

  if (config.tenkyuApiKey === "") {
    logThrottled("config", NOT_CONFIGURED_MESSAGE);
    return { id: "", action: "allow", credit_fraction: failOpenFraction, degraded: true };
  }

  const controller = new AbortController();
  const timedOut = new Promise<never>((_, reject) => {
    controller.signal.addEventListener("abort", () => reject(new Error("timeout")));
  });
  const timer = setTimeout(() => controller.abort(), config.timeoutMs);
  try {
    // The race also bounds a body that stalls after the headers arrive.
    const work = (async (): Promise<Decision | "conflict" | null> => {
      const res = await deps.fetch(config.tenkyuUrl.replace(/\/+$/, "") + "/v1/check", {
        method: "POST",
        headers: { authorization: `Bearer ${config.tenkyuApiKey}`, "content-type": "application/json", accept: "application/json" },
        body: JSON.stringify(body),
        signal: controller.signal,
      });
      if (!res.ok) {
        if (res.status === 401 || res.status === 403) {
          if (res.status === 401 && (await isTenkyuUnauthorized(res))) {
            logThrottled("key", KEY_REJECTED_MESSAGE);
            return { id: "", action: "allow", credit_fraction: failOpenFraction, degraded: true };
          }
          logThrottled("refused", `Tenkyu call refused by something in between (status ${res.status}): no credits will be granted`);
          return null;
        }
        if (res.status === 400 && (await isRequestIdConflict(res))) return "conflict";
        if (res.status >= 400 && res.status < 500 && res.status !== 429) {
          log.error("tenkyu rejected the request (client error): no credits will be granted", { status: res.status });
          return null;
        }
        return failOpen(`http_${res.status}`);
      }
      // Tenkyu always answers JSON. Another media type (text/html, xml, ...) on a 2xx is a gateway's page, not an
      // outage: refuse. No header or text/plain is tolerated, but the whole body must then still be a decision.
      const mediaType = (res.headers.get("content-type") ?? "").split(";")[0]?.trim().toLowerCase() ?? "";
      const refused = (): null => {
        logThrottled("refused", `Tenkyu call refused by something in between (status ${res.status}): no credits will be granted`);
        return null;
      };
      if (!isJsonMediaType(mediaType) && mediaType !== "" && mediaType !== "text/plain") return refused();
      let d: (Partial<Decision> & { degraded?: unknown }) | null;
      try {
        d = JSON.parse(await res.text()) as typeof d;
      } catch (err) {
        if (controller.signal.aborted) throw err; // a stalled body is a timeout (outage), not a gateway page
        return refused();
      }
      if (
        !d || typeof d !== "object" || typeof d.id !== "string" || !["allow", "review", "block"].includes(d.action as string) ||
        typeof d.credit_fraction !== "number" || !Number.isFinite(d.credit_fraction) ||
        d.credit_fraction < 0 || d.credit_fraction > 1
      ) {
        return refused();
      }
      // Reasons and signals are deliberately dropped here; they stay on the server.
      log.log("tenkyu decision", { id: d.id, action: d.action, credit_fraction: d.credit_fraction, degraded: d.degraded === true });
      const out: Decision = { id: d.id, action: d.action as Action, credit_fraction: d.credit_fraction, degraded: d.degraded === true };
      // Shadow fields are informational: keep each only if it has the right type, and log what Tenkyu WOULD
      // have done (decision id and values only, no email). The grant still follows credit_fraction (1 in shadow).
      if (d.shadow === true) {
        out.shadow = true;
        if (d.would_action === "allow" || d.would_action === "review" || d.would_action === "block") out.would_action = d.would_action;
        if (
          typeof d.would_credit_fraction === "number" && Number.isFinite(d.would_credit_fraction) &&
          d.would_credit_fraction >= 0 && d.would_credit_fraction <= 1
        ) out.would_credit_fraction = d.would_credit_fraction;
        log.log("tenkyu shadow", { id: d.id, would_action: out.would_action, would_credit_fraction: out.would_credit_fraction });
      }
      return out;
    })();
    work.catch(() => {}); // if the timeout wins the race, do not leave an unhandled rejection behind
    return await Promise.race([work, timedOut]);
  } catch (err) {
    return failOpen(controller.signal.aborted ? "timeout" : err instanceof Error ? err.name : "error");
  } finally {
    clearTimeout(timer);
  }
}

/**
 * True only for Tenkyu's own bad-key answer: a JSON content-type and the EXACT body hosted/api serializes. A gateway's
 * HTML, other JSON, or JSON that echoes request text (and so can carry a forged duplicate "code") is not.
 */
async function isTenkyuUnauthorized(res: Response): Promise<boolean> {
  try {
    if (!isJsonMediaType((res.headers.get("content-type") ?? "").split(";")[0]?.trim().toLowerCase() ?? "")) return false;
    return (await res.text()).trim() === TENKYU_UNAUTHORIZED_BODY;
  } catch {
    return false;
  }
}

/** Tenkyu's 400 for a request_id reused with a different body (hosted/api/service.py): the account was checked twice. */
async function isRequestIdConflict(res: Response): Promise<boolean> {
  try {
    const b = JSON.parse(await res.text()) as { error?: { code?: unknown; message?: unknown } } | null;
    return b?.error?.code === "invalid_request" && typeof b.error.message === "string" && /request_id/i.test(b.error.message);
  } catch {
    return false;
  }
}

function isJsonMediaType(mediaType: string): boolean {
  return /^application\/(?:[\w.+-]+\+)?json$/.test(mediaType);
}

/** `https`, or `http` only for localhost, 127.0.0.1 and [::1]: the bearer key must never travel in clear text. */
export function checkedTenkyuUrl(raw: string): string {
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    throw new Error("tenkyu-signup: TENKYU_URL is not a valid URL (it must be https)");
  }
  const local = url.hostname === "localhost" || url.hostname === "127.0.0.1" || url.hostname === "[::1]";
  if (url.protocol !== "https:" && !(url.protocol === "http:" && local)) {
    throw new Error("tenkyu-signup: TENKYU_URL must use https (http is allowed only for localhost, 127.0.0.1 and [::1])");
  }
  return raw;
}

/** One authenticated round trip, to time a cold instance: POST /v1/outcome for an id that does not exist (404). Never throws. */
async function probeTenkyu(deps: Deps): Promise<void> {
  const { config } = deps;
  if (config.tenkyuApiKey === "") return;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), config.timeoutMs);
  try {
    const res = await deps.fetch(config.tenkyuUrl.replace(/\/+$/, "") + "/v1/outcome", {
      method: "POST",
      headers: { authorization: `Bearer ${config.tenkyuApiKey}`, "content-type": "application/json", accept: "application/json" },
      body: JSON.stringify({ id: TIMING_PROBE_ID, outcome: "real" }),
      signal: controller.signal,
    });
    await res.text();
  } catch {
    // any failure just means the floor applies
  } finally {
    clearTimeout(timer);
  }
}

function corsHeaders(req: Request, allowedOrigin: string): Headers {
  const h = new Headers({ vary: "Origin" });
  if (allowedOrigin !== "" && req.headers.get("origin") === allowedOrigin) {
    h.set("access-control-allow-origin", allowedOrigin);
    h.set("access-control-allow-methods", "POST, OPTIONS");
    h.set("access-control-allow-headers", "content-type, authorization, apikey, x-client-info");
    h.set("access-control-max-age", "600");
  }
  return h;
}

function json(body: unknown, status: number, extra: Headers): Response {
  const headers = new Headers(extra);
  headers.set("content-type", "application/json");
  return new Response(JSON.stringify(body), { status, headers });
}
