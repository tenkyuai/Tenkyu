import { degradedDecision, TenkyuError } from "tenkyu";
import type { Decision, SignupCheck } from "tenkyu";
import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { createRateLimiter, isStrictIp, rateLimitKey, readBodyCapped, requestIdForUser } from "./guards";
import type { RateLimit } from "./guards";
import type { CreateUserResult, CreditGrant, NewUser } from "./app-auth";

// The sign-up logic behind app/api/signup/route.ts. It lives here, not in route.ts, because Next.js
// only allows HTTP-method exports from a route file, and this way tests can inject fakes.

export const MAX_BODY_BYTES = 8192;
const MAX_EMAIL_LENGTH = 254;
/** Password policy, checked before an account exists. 72 is bcrypt's limit (Supabase Auth hashes with bcrypt) in UTF-8 bytes. */
export const PASSWORD_MIN_LENGTH = 8;
export const PASSWORD_MAX_BYTES = 72;
/**
 * Shown by YOUR UI, after sign-in, when the account's credits row has `noticePending` (a real user who got no
 * credits). Says nothing about why. It is never part of the sign-up response: that would tell a caller whose
 * sign-ups are blocked whether an email is new or already registered.
 */
export const DEFAULT_NO_CREDITS_NOTICE =
  "We couldn't give free credits to this account automatically. Contact support to get them.";
const LOG_INTERVAL_MS = 60_000;
const KEY_REJECTED_MESSAGE = "Tenkyu API key rejected: check TENKYU_API_KEY";
const NOT_CONFIGURED_MESSAGE =
  "Tenkyu is not configured (TENKYU_API_KEY missing or the client could not be created): granting only the fail-open share until fixed";
/**
 * Duplicate-email padding (only without `defer`): the median of recent GENUINE Tenkyu answer times (see
 * `scoreAndGrant`) plus a little jitter, never above the max. On a cold instance, with no sample yet, it is
 * at least the floor.
 */
const PAD_FLOOR_MS = 150;
const MAX_PAD_MS = 5000;
const PAD_SAMPLES = 9;
/** Jitter added to a median pad: up to this many ms. A cold pad (the floor) gets up to a third of the floor. */
const PAD_JITTER_MS = 4;
/** Set this to 1 to run in production WITHOUT `defer`: the duplicate-email timing oracle is then only approximately closed. */
export const ALLOW_INLINE_SCORING_ENV = "TENKYU_ALLOW_INLINE_SCORING";
/** A decision id that does not exist: the cold-instance timing probe (POST /v1/outcome) answers 404 and changes nothing. */
const TIMING_PROBE_ID = "dec_timing_probe";
const MAX_USER_AGENT_LENGTH = 512;
const EMAIL_SHAPE = /^[^\s@]{1,64}@[^\s@]+\.[^\s@]+$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const PHONE_E164 = /^\+[1-9]\d{6,14}$/;
// Characters the Tenkyu API rejects with a 400: control characters (incl. NUL) and lone surrogates.
const BAD_CHARS = /[\u0000-\u001f\u007f]|[\ud800-\udbff](?![\udc00-\udfff])|(?<![\ud800-\udbff])[\udc00-\udfff]/g;

export const DEFAULT_RATE_LIMIT: { perIp: RateLimit; perDevice: RateLimit } = {
  // A school or office can sign up a class from one IP, so the IP bucket is wide.
  perIp: { capacity: 20, perMinute: 15 },
  // One browser retrying a few times is fine; a script hammering is not.
  perDevice: { capacity: 5, perMinute: 5 },
};

export interface SignupDeps {
  /** The Tenkyu client: check(), and outcome() only for the cold-instance timing probe (optional). */
  tenkyu: () => { check(signup: SignupCheck): Promise<Decision>; outcome?(id: string, outcome: "farmer" | "real"): Promise<unknown> };
  /**
   * Create the account FIRST, before Tenkyu is called. Return `{ok: false, reason: "email_taken"}` for an
   * existing email: the route then calls Tenkyu for nothing and grants nothing.
   */
  createUser: (user: NewUser) => Promise<CreateUserResult>;
  /** Store the credits and the Tenkyu decision for an account createUser just made. Make it idempotent per userId. */
  grantCredits: (grant: CreditGrant) => Promise<void>;
  /** Links your app verified server-side. See getVerifiedLinks in app-auth.ts. */
  verifiedLinks: (input: { email: string }) => Promise<{ phone?: string } | undefined>;
  /** Base credits for a clean signup. */
  grant: number;
  /** Largest share of grant a degraded (unscored) decision can carry. */
  failOpenCreditFraction: number;
  rateLimit?: { perIp?: RateLimit; perDevice?: RateLimit };
  /**
   * Run the Tenkyu check and the credit grant AFTER the response is sent, so a new sign-up and a duplicate
   * email answer in the same time (no timing oracle). Wire Next's `after` (route.ts does). Without it the
   * check runs before the answer, and a duplicate email waits about as long as a recent check took instead.
   * The promise never rejects. Tests leave it unset so the grant exists when POST() resolves.
   * If it throws (Next before 15.1 has no `after`; or it was called outside a request), the work is awaited
   * inline and the answer is the same. In production (NODE_ENV=production) createSignupHandler REFUSES to
   * start without it, unless env TENKYU_ALLOW_INLINE_SCORING=1 (see README, "Timing").
   */
  defer?: (work: Promise<void>) => void;
  /** Test hook: the environment (default process.env). Read for NODE_ENV and TENKYU_ALLOW_INLINE_SCORING. */
  env?: Record<string, string | undefined>;
  /** Test hook: random number in [0, 1) for the padding jitter (default Math.random). */
  random?: () => number;
  /**
   * The sign-up response never carries a notice (that would reveal which emails exist). When the account got
   * 0 credits the grant carries `noticePending: true`, so your UI can show a notice after sign-in (README,
   * "A real user got no credits"). `null` leaves the flag off: you show your own empty-balance message.
   * Any string (including the default) sets the flag; the text itself lives in your UI.
   */
  noCreditsNotice?: string | null;
  now?: () => number;
  /** Test hooks for the duplicate-email padding: a monotonic clock in ms (default performance.now) and a sleep. */
  monotonic?: () => number;
  sleep?: (ms: number) => Promise<void>;
  log?: { log?: Console["log"]; warn: Console["warn"]; error: Console["error"] };
}

const GENERIC_OK = { ok: true } as const;

export function createSignupHandler(deps: SignupDeps): (request: NextRequest) => Promise<Response> {
  const env = deps.env ?? (typeof process !== "undefined" ? process.env : {});
  if (deps.defer === undefined && env.NODE_ENV === "production" && env[ALLOW_INLINE_SCORING_ENV] !== "1") {
    // Without defer a duplicate email is only padded to a guess of the check time: a timing oracle for which
    // emails are registered. Fail at startup, not silently in production.
    throw new Error(
      `createSignupHandler: no defer (Next after()) in production. Pass defer, or set ${ALLOW_INLINE_SCORING_ENV}=1 to accept an approximate duplicate-email padding`,
    );
  }
  const now = deps.now ?? Date.now;
  const log = deps.log ?? console;
  const random = deps.random ?? Math.random;
  const failOpenFraction = clampFraction(deps.failOpenCreditFraction, 0.25);
  const flagNotice = deps.noCreditsNotice !== null && deps.noCreditsNotice !== "";
  const info = (...args: unknown[]): void => log.log?.(...args);
  const monotonic = deps.monotonic ?? (() => performance.now());
  const sleep = deps.sleep ?? ((ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms)));
  // Recent durations of "check Tenkyu + grant" that got a GENUINE Tenkyu answer, to pad a duplicate email to a
  // similar time (no `defer`). Failures a farmer can cause quickly (a WAF refusing his user agent) never enter it.
  const durations: number[] = [];
  // Set when defer threw once: from then on new sign-ups wait inline, so duplicates must be padded too.
  let deferBroken = false;
  // One clear error per cause at most once a minute (per server instance): a bad key, a missing key, or
  // something in between (gateway, WAF) refusing the call.
  const lastLogged = new Map<string, number>();
  const logThrottled = (kind: string, ...message: unknown[]): void => {
    const t = now();
    if (t - (lastLogged.get(kind) ?? Number.NEGATIVE_INFINITY) < LOG_INTERVAL_MS) return;
    lastLogged.set(kind, t);
    log.error(...message);
  };
  // In-memory buckets: fine for an example, wrong for production. See guards.ts.
  const ipLimiter = createRateLimiter(deps.rateLimit?.perIp ?? DEFAULT_RATE_LIMIT.perIp, now);
  const deviceLimiter = createRateLimiter(deps.rateLimit?.perDevice ?? DEFAULT_RATE_LIMIT.perDevice, now);

  return async function POST(request: NextRequest): Promise<Response> {
    // 1. Cheap checks first: content type, declared size, per-IP rate limit.
    if (!(request.headers.get("content-type") ?? "").includes("application/json")) {
      return NextResponse.json({ error: "invalid_request" }, { status: 400 });
    }
    const declared = Number(request.headers.get("content-length") ?? "0");
    if (Number.isFinite(declared) && declared > MAX_BODY_BYTES) {
      return NextResponse.json({ error: "invalid_request" }, { status: 413 });
    }
    const ip = clientIp(request);
    if (ip !== undefined && !ipLimiter.take(rateLimitKey(ip))) return rateLimited();

    // 2. Read the body with a hard cap. Content-Length can be missing or false; request.text() has no cap.
    //    All local validation happens here, before an account exists. The email comes back normalized
    //    (trimmed, lowercased); nothing downstream normalizes it again. The password is checked against the
    //    policy and never echoed back or logged.
    const raw = await readBodyCapped(request, MAX_BODY_BYTES);
    if (raw === null) return NextResponse.json({ error: "invalid_request" }, { status: 413 });
    const parsed = parseInput(raw);
    if (!parsed.ok) {
      return NextResponse.json(
        parsed.error === "weak_password"
          ? { error: "weak_password", message: `Password must be ${PASSWORD_MIN_LENGTH} to ${PASSWORD_MAX_BYTES} characters` }
          : { error: "invalid_request" },
        { status: 400 },
      );
    }
    const { email, password } = parsed;

    // 3. Signals. Everything except the email comes from request metadata, never the body, so a client
    //    cannot choose its own IP, device id or links by editing the JSON.
    const userAgent = clean(request.headers.get("user-agent") ?? "").slice(0, MAX_USER_AGENT_LENGTH);
    const cookieId = request.cookies.get("tk_did")?.value; // set by middleware.ts
    const deviceId = cookieId !== undefined && UUID.test(cookieId) ? cookieId : undefined;
    if (deviceId !== undefined && !deviceLimiter.take(deviceId)) return rateLimited();

    // 4. Create the account FIRST. Tenkyu is asked only about an account that is new. An existing email
    //    answers exactly like success (a different answer would tell anyone which emails have accounts):
    //    no Tenkyu call, no credits. Send the real owner an "you already have an account" email from your
    //    own mailer instead.
    let created: CreateUserResult;
    try {
      created = await deps.createUser({ email, password });
    } catch (err) {
      // Error name only: the message could carry the email or password.
      log.error("createUser failed:", err instanceof Error ? err.name : "unexpected error");
      return NextResponse.json({ error: "server_error" }, { status: 500 });
    }
    if (!created.ok) {
      if (created.reason === "rejected") {
        // The auth provider refused this input (for example its own password policy). Nothing exists yet, so
        // saying so reveals nothing about other accounts. The message is the provider's, never Tenkyu's.
        return NextResponse.json({ error: "signup_failed", ...(created.message ? { message: created.message } : {}) }, { status: 400 });
      }
      if (created.reason === "provider_error") {
        // Not about the input: could depend on the address being new (email-send rate limit), so answer as for a
        // duplicate. Code only in the log: the provider's message can carry the email.
        log.warn("signup: auth provider error, answered generically, no check, no credits", { code: created.code ?? "unknown" });
      } else {
        log.warn("signup for an existing email: answered generically, no check, no credits");
      }
      // Without `defer` a new sign-up waits for Tenkyu and a duplicate would not: pad it to a similar time.
      if (deps.defer === undefined || deferBroken) await padDuplicate();
      return NextResponse.json(GENERIC_OK);
    }
    const userId = created.userId;

    // 5-6. Ask Tenkyu and store the credits. The browser's answer is the same generic {ok:true} on every path
    //    (new, duplicate, blocked, outage), so nothing about the verdict, the score or the credit amount can
    //    be read from it, and it carries no notice (see DEFAULT_NO_CREDITS_NOTICE).
    //    With `defer` (Next's after()) the answer goes out first and this runs afterwards; without it the
    //    answer waits, and the duplicate path above pads to a similar time.
    //    A function killed before the background work finishes (deploy, timeout, crash) leaves an account
    //    with no credits row, and nothing runs later to say so. So a one-line marker with the user id (never
    //    the email) is logged BEFORE the reply and another after the work: a "pending" with no "done" is an
    //    account to reconcile (README, "If the function is killed").
    const result = { genuine: false };
    (log.log ?? log.warn).call(log, "signup scoring pending", { userId });
    const work = scoreAndGrant({ userId, email, ip, deviceId, userAgent })
      .then((genuine) => { result.genuine = genuine; })
      .catch(() => {
        // Never let a background failure become an unhandled rejection. User id only: no email, no error text.
        log.error("signup scoring failed, fix by hand:", { userId });
      })
      .then(() => { (log.log ?? log.warn).call(log, "signup scoring done", { userId }); });
    let deferred = false;
    if (deps.defer !== undefined && !deferBroken) {
      try {
        deps.defer(work);
        deferred = true;
      } catch {
        // Next before 15.1 has no after(), or it was called outside a request scope. The account already
        // exists, so do not answer 500 (a duplicate answers 200: that difference lists registered emails).
        deferBroken = true;
        log.warn("defer unavailable: scoring inline (check the Next.js version, README, Timing)");
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
    return NextResponse.json(GENERIC_OK);
  };

  /**
   * Wait about as long as a new sign-up does, for a duplicate email (no `defer`). Median of the recent GENUINE
   * check+grant durations plus jitter. With no genuine sample yet (a cold instance,
   * which a burst of parallel requests starts on purpose) it measures one now: a no-op outcome call for a
   * decision id that does not exist (404) is one authenticated round trip to Tenkyu, and changes nothing.
   */
  async function padDuplicate(): Promise<void> {
    if (durations.length === 0) {
      const t0 = monotonic();
      try {
        await deps.tenkyu().outcome?.(TIMING_PROBE_ID, "real");
      } catch {
        // 404 not_found is the expected answer; any failure just means the floor applies
      }
      const waited = monotonic() - t0;
      if (waited < PAD_FLOOR_MS) await sleep(PAD_FLOOR_MS - waited + random() * (PAD_FLOOR_MS / 3));
      return;
    }
    const sorted = [...durations].sort((x, y) => x - y);
    const median = sorted[Math.floor(sorted.length / 2)] ?? 0;
    // No floor here: with genuine samples the median IS the time a new sign-up takes, and padding a fast Tenkyu
    // up to a floor would make a duplicate slower than a new sign-up, an oracle of its own.
    await sleep(Math.min(MAX_PAD_MS, median + random() * PAD_JITTER_MS));
  }

  /** Resolves true when Tenkyu gave a genuine, scored answer (the only kind whose duration may feed the padding). */
  async function scoreAndGrant(input: { userId: string; email: string; ip: string | undefined; deviceId: string | undefined; userAgent: string }): Promise<boolean> {
    const { userId, email, ip, deviceId, userAgent } = input;
    // request_id comes from the NEW user id: unique per account, so a retry for the same account is idempotent
    // and it cannot collide with another person's request (an email-derived id lets anyone burn a victim's id
    // by submitting their email first, and a retry with another device or case would be a 400).
    // Outages (timeout, network, 5xx, 429) fail open inside the client: reduced grant. A 2xx that is not a JSON
    // decision (an HTML block page answered 200) is NOT an outage: the client throws "refused", see below.
    // Our own misconfiguration also gets the reduced grant, with one clear error a minute: a rejected key
    // (status 401 AND Tenkyu's JSON code "unauthorized", nothing else) and a missing key / client that could
    // not be created. A real user must not pay for it.
    // Everything else fails CLOSED, with a 0-credit row (and noticePending) and an error log:
    //  - a 401/403 that is not Tenkyu's own bad-key answer (exact body, see the client), and a 2xx that is not a
    //    JSON decision. Tenkyu never sends those; a gateway or WAF in front of it does, and a farmer can
    //    trigger it with a user agent or email the WAF dislikes. Failing open would turn the block he should
    //    get into a 25% grant.
    //  - a 400 "request_id reused with a different body": the same unconfirmed account was submitted twice
    //    (another device or user agent). The FIRST check owns the grant; this one writes nothing, so it cannot
    //    win the primary key with a 0 row.
    //  - any other 4xx (400 invalid request): OUR request was wrong.
    const links = await appLinks(deps, email);
    let decision: Decision;
    let failedClosed = false;
    let genuine = false;
    let client: { check(signup: SignupCheck): Promise<Decision> } | undefined;
    try {
      client = deps.tenkyu();
    } catch {
      // No secret in the log: a fixed text, never the error's.
      logThrottled("config", NOT_CONFIGURED_MESSAGE);
    }
    if (client === undefined) {
      decision = degradedDecision(failOpenFraction, "tenkyu not configured");
    } else {
      try {
        decision = await client.check({
          email,
          ...(ip !== undefined ? { ip } : {}),
          ...(deviceId !== undefined ? { device_id: deviceId } : {}),
          ...(userAgent !== "" ? { user_agent: userAgent } : {}),
          ...(links !== undefined ? { links } : {}),
          request_id: await requestIdForUser(userId),
        });
      } catch (err) {
        // Log the error code only: no email, no IP, no key.
        if (err instanceof TenkyuError && isOutage(err)) {
          log.warn("tenkyu unreachable, degraded signup:", `${err.code} (${err.status})`);
          decision = degradedDecision(failOpenFraction);
        } else if (err instanceof TenkyuError && isBadKey(err)) {
          // The shipped client already does this itself (it never throws this with failOpen on); this covers a
          // client built with failOpen: false and any custom or older one.
          logThrottled("key", KEY_REJECTED_MESSAGE);
          decision = degradedDecision(failOpenFraction, "tenkyu api key rejected");
        } else if (err instanceof TenkyuError && isRequestIdConflict(err)) {
          // Leave the existing row (or the grant still being computed by the first request) untouched.
          log.warn("tenkyu request_id conflict: same account submitted twice, this check writes nothing", { userId });
          return false;
        } else if (err instanceof TenkyuError && (err.code === "refused" || err.status === 401 || err.status === 403)) {
          logThrottled("refused", `Tenkyu call refused by something in between (status ${err.status}): no credits granted`);
          decision = degradedDecision(0, "refused in between");
          failedClosed = true;
        } else {
          log.error(
            "tenkyu check rejected (client error or bug), no credits granted:",
            err instanceof TenkyuError ? `${err.code} (${err.status})` : "unexpected error",
          );
          decision = degradedDecision(0, "client error");
          failedClosed = true;
        }
      }
    }

    // Act on it, but never tell the browser. Tenkyu's reasons, signals and score stay on the server.
    // A block is NOT a 403: that would be an oracle ("am I flagged?"), and it would lock a real person
    // out. The account has 0 credits and the answer is the same generic {ok:true}.
    // A degraded decision (local fallback, or a 200 the server flagged `degraded` because it answered from
    // an empty in-memory engine) was not scored: cap it, and store no decision id so outage grants can be
    // found later by tenkyuDecisionId === null.
    const blocked = decision.action === "block";
    const degraded = decision.degraded === true;
    genuine = client !== undefined && !failedClosed && !degraded;
    if (degraded && !failedClosed) log.warn("tenkyu degraded: signup was not scored");
    // Shadow mode: `action` is allow and `credit_fraction` 1 whatever the engine decided. Grant by
    // credit_fraction as usual; record what Tenkyu WOULD have done so you can judge it before it enforces.
    const shadowed = decision.shadow === true;
    if (shadowed) info("tenkyu shadow", { decision: decision.id, would_action: decision.would_action, would_credit_fraction: decision.would_credit_fraction });
    const fraction = degraded ? Math.min(decision.credit_fraction, failOpenFraction) : decision.credit_fraction;
    const credits = blocked || failedClosed ? 0 : Math.round(deps.grant * clampFraction(fraction, 0));
    try {
      await deps.grantCredits({
        userId,
        credits,
        tenkyuDecisionId: degraded || decision.id === "" ? null : decision.id,
        needsReview: decision.action === "review" || failedClosed,
        blocked,
        // Recorded server-side for your UI to show after sign-in: never put it in the sign-up response.
        ...(credits === 0 && flagNotice ? { noticePending: true as const } : {}),
        ...(shadowed
          ? {
              shadow: true,
              ...(decision.would_action !== undefined ? { wouldAction: decision.would_action } : {}),
              ...(decision.would_credit_fraction !== undefined ? { wouldCreditFraction: decision.would_credit_fraction } : {}),
            }
          : {}),
      });
    } catch {
      // The account exists, so do not fail the signup. No email or error text in the log; fix by hand.
      log.error("grantCredits failed, fix by hand:", { userId, credits });
    }
    return genuine;
  }
}

/** Tenkyu's 400 for a request_id reused with a different body (hosted/api/service.py): the same account was checked twice. */
function isRequestIdConflict(err: TenkyuError): boolean {
  return err.status === 400 && err.code === "invalid_request" && /request_id/i.test(err.message);
}

/** Tenkyu's own bad-key answer: status 401 AND its exact JSON error body (the shipped client checks both). */
function isBadKey(err: TenkyuError): boolean {
  return err.status === 401 && err.code === "unauthorized";
}

/** Failures that mean "Tenkyu is slow or down", not "our request was wrong". */
function isOutage(err: TenkyuError): boolean {
  return (
    err.code === "network_error" || err.code === "timeout" || err.status === 429 || err.status >= 500
  );
}

function rateLimited(): Response {
  return NextResponse.json({ error: "rate_limited" }, { status: 429, headers: { "retry-after": "10" } });
}

async function appLinks(deps: SignupDeps, email: string): Promise<{ phone: string } | undefined> {
  try {
    const links = await deps.verifiedLinks({ email });
    return typeof links?.phone === "string" && PHONE_E164.test(links.phone) ? { phone: links.phone } : undefined;
  } catch {
    return undefined; // a broken lookup must not break signup
  }
}

function clampFraction(value: number, fallback: number): number {
  return Number.isFinite(value) ? Math.min(1, Math.max(0, value)) : fallback;
}

type Parsed = { ok: true; email: string; password: string } | { ok: false; error: "invalid_request" | "weak_password" };

/**
 * Parse and validate the body: the normalized (trimmed, lowercased) email and the password. A password
 * outside the policy is `weak_password`; it is never copied into an error, a log line or a response.
 */
function parseInput(raw: string): Parsed {
  const invalid = { ok: false, error: "invalid_request" } as const;
  let body: unknown;
  try {
    body = JSON.parse(raw);
  } catch {
    return invalid;
  }
  if (typeof body !== "object" || body === null || !("email" in body)) return invalid;
  const value = (body as { email: unknown }).email;
  if (typeof value !== "string" || value.length > MAX_EMAIL_LENGTH + 64) return invalid;
  const email = value.trim().toLowerCase();
  if (email.length > MAX_EMAIL_LENGTH || email !== clean(email) || !EMAIL_SHAPE.test(email)) return invalid;
  const password = (body as { password?: unknown }).password;
  if (typeof password !== "string") return invalid;
  if ([...password].length < PASSWORD_MIN_LENGTH || new TextEncoder().encode(password).length > PASSWORD_MAX_BYTES) {
    return { ok: false, error: "weak_password" };
  }
  return { ok: true, email, password };
}

// Which IP to send on Vercel: `x-real-ip` and nothing else.
// Checked 2026-11-27 against https://vercel.com/docs/headers/request-headers (Vercel overwrites
// X-Forwarded-For and does not forward external IPs, to prevent spoofing) and @vercel/functions, whose
// IP_HEADER_NAME is 'x-real-ip' ("Client IP as calculated by Vercel Proxy"). Not checked against a real
// deployment, so confirm before relying on it.
//   - There is deliberately NO fallback to the first `x-forwarded-for` entry: off Vercel, or behind a proxy
//     that appends, that entry is whatever the client wrote. If `x-real-ip` is absent or is not a strict
//     IPv4/IPv6 address, we send no IP at all rather than a forgeable one; Tenkyu still scores email and
//     device.
//   - If you put another proxy or CDN (Cloudflare, a corporate gateway) in front of Vercel, `x-real-ip` holds
//     that proxy's IP. Use the header your outer proxy sets (for example `cf-connecting-ip`) instead.
//   - Carrier NAT and shared offices put many people behind one IP; Tenkyu treats IP as a soft signal.
function clientIp(request: NextRequest): string | undefined {
  const candidate = (request.headers.get("x-real-ip") ?? "").trim();
  return isStrictIp(candidate) ? candidate : undefined;
}

function clean(value: string): string {
  return value.replace(BAD_CHARS, "");
}
