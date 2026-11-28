/** Client for the Tenkyu hosted API (v1). Zero dependencies, uses global fetch. */

export type Action = "allow" | "review" | "block";
export type Outcome = "farmer" | "real";
/** What your app verified for the user: an email it confirmed, or a phone (OTP) or card it verified. */
export type EarnBackProof = "email_verified" | "phone_verified" | "card_verified";
/** On a review decision: the proof that releases the rest of the grant. */
export interface EarnBackRequirement {
  /** "email": a verified email is enough. "phone_or_card": needs a verified phone or card. */
  requires: "email" | "phone_or_card";
  reason: string;
}
export interface EarnBackResult {
  /** The credit_fraction to ADD to what check() granted (0 when nothing was released). */
  extra_credit_fraction: number;
  granted: boolean;
  reason: string;
  /** Shadow mode only: nothing is released (the full grant was already given); these say what would have been. */
  shadow?: boolean;
  would_extra_credit_fraction?: number;
  would_grant?: boolean;
}
export type ErrorCode =
  | "unauthorized"
  | "invalid_request"
  | "rate_limited"
  | "not_found"
  | "server_error"
  /**
   * An answer that did not come from Tenkyu's own API: a 401 or 403 that is NOT Tenkyu's exact "bad API key"
   * answer (see `failOpen`), or a 2xx whose content-type is not JSON or whose JSON is not a decision (a
   * gateway, WAF or captive-portal block page). Never client-side generated, never retried, never failed
   * open: grant nothing.
   */
  | "refused";

/** At least one of email, ip, device_id must be present. */
export interface SignupCheck {
  email?: string;
  ip?: string;
  device_id?: string;
  user_agent?: string;
  /** Unix seconds. Defaults to server time. */
  ts?: number;
  links?: { phone?: string; card?: string; [name: string]: string | undefined };
  /** Idempotency key, honoured for 24 h. */
  request_id?: string;
}

export interface Signal {
  name: string;
  score: number;
}

export interface Decision {
  id: string;
  action: Action;
  score: number;
  credit_fraction: number;
  reasons: string[];
  signals: Signal[];
  network_match: boolean;
  credits_exhausted: boolean;
  mode: "live" | "test";
  /**
   * True while your account is in shadow mode (first 7 days after the ACCOUNT was created, extendable in
   * Settings). `action` is then "allow" and `credit_fraction` 1, so grant by `credit_fraction` as usual;
   * log `would_action` and `would_credit_fraction` to see what Tenkyu would have done. Absent once live.
   */
  shadow?: boolean;
  /** Shadow mode only: the action Tenkyu would have taken. */
  would_action?: Action;
  /** Shadow mode only: the credit_fraction (0 to 1) Tenkyu would have returned. */
  would_credit_fraction?: number;
  /** Review only: the proof that releases the rest of the grant (see `earnBack`). */
  earn_back?: EarnBackRequirement;
  /** Shadow mode only: the `earn_back` the real (review) decision would have carried. */
  would_earn_back?: EarnBackRequirement;
  /**
   * True when this decision was not a real score: synthesized locally because Tenkyu was unreachable or
   * rejected the API key (see `failOpenCreditFraction`), or flagged by the server.
   */
  degraded?: boolean;
}

export interface Usage {
  plan: "free" | "prepaid";
  period_start: string;
  checks_this_period: number;
  free_remaining: number;
  balance: number;
}

export interface TenkyuOptions {
  apiKey: string;
  baseUrl?: string;
  /**
   * Overall deadline for one call, in ms. Default 1500. It covers every attempt and the wait before a
   * 429 retry together, so check() never takes longer than this (plus timer jitter).
   */
  timeoutMs?: number;
  /**
   * Resolve to a degraded allow decision (grant `failOpenCreditFraction`) on network error, timeout, 5xx, 429,
   * or a rejected API key (Tenkyu's own answer: status 401, a JSON content-type and EXACTLY Tenkyu's error
   * body, byte for byte; this also logs "Tenkyu API key rejected: check TENKYU_API_KEY" at most once per
   * minute: a bad or expired key must not zero out every real user).
   * Everything else throws: a 400 (bad input); any other 401/403, or a 401 whose body is not exactly Tenkyu's
   * (a gateway or WAF that echoes request text can fake the code); and a 2xx that is not a JSON decision (an
   * HTML block page answered 200). Those come from something in front of Tenkyu and can be provoked by request
   * content a farmer controls. They throw a TenkyuError with code "refused" and the message "Tenkyu call
   * refused by something in between (status N)": log it in your own code, and grant nothing. Default true.
   */
  failOpen?: boolean;
  /**
   * Share of the base grant a degraded (unscored) decision carries, 0 to 1. Default 0.25.
   * Failing open keeps sign-up working, but it must not hand a full grant to anyone who can make
   * Tenkyu slow or flood its rate limit. Honest users who sign up during an outage get this share
   * and can be topped up later (use `decision.degraded` to find them).
   */
  failOpenCreditFraction?: number;
  /** Custom fetch, mainly for tests. Defaults to globalThis.fetch. */
  fetch?: typeof fetch;
  /**
   * Where the "API key rejected" error goes (once per minute at most, see `failOpen`). Defaults to
   * `console`. Only the fixed message is logged: never the key, the request or the response.
   */
  logger?: { error(message: string): void };
}

export class TenkyuError extends Error {
  /** "invalid_response" is no longer thrown (a malformed answer is now "refused"); kept so existing `switch`es compile. */
  readonly code: ErrorCode | "network_error" | "timeout" | "invalid_response";
  readonly status: number;
  constructor(code: TenkyuError["code"], status: number, message: string) {
    super(message);
    this.name = "TenkyuError";
    this.code = code;
    this.status = status;
  }
}

const DEFAULT_BASE_URL = "https://api.tenkyu.dev";
const MAX_RETRY_AFTER_MS = 2000;
const KEY_REJECTED_LOG_INTERVAL_MS = 60_000;
/** Logged when Tenkyu answers 401/403. Fixed text: it never contains the key. */
export const API_KEY_REJECTED_MESSAGE = "Tenkyu API key rejected: check TENKYU_API_KEY";
/** Default for `failOpenCreditFraction`: a degraded decision grants a quarter of the base grant. */
export const DEFAULT_FAIL_OPEN_CREDIT_FRACTION = 0.25;

/**
 * The decision used when Tenkyu could not score a sign-up. Still `allow` (sign-up never breaks),
 * but with a reduced `credit_fraction` (default 0.25) so an outage is not a free full grant.
 */
export function degradedDecision(
  creditFraction: number = DEFAULT_FAIL_OPEN_CREDIT_FRACTION,
  reason: string = "tenkyu unreachable",
): Decision {
  return {
    id: "",
    action: "allow",
    score: 0,
    credit_fraction: normalizeFraction(creditFraction),
    reasons: [reason],
    signals: [],
    network_match: false,
    credits_exhausted: false,
    mode: "live",
    degraded: true,
  };
}

interface Reply {
  status: number;
  body: unknown;
  /** The raw body text, exactly as received. */
  text: string;
  /** The content-type is JSON (application/json or application/*+json). */
  isJson: boolean;
  /** The bare media type, lowercased ("" when the header is absent). */
  mediaType: string;
  retryAfterMs: number | undefined;
}

export class Tenkyu {
  readonly #apiKey: string;
  readonly #baseUrl: string;
  readonly #timeoutMs: number;
  readonly #failOpen: boolean;
  readonly #failOpenCreditFraction: number;
  readonly #fetch: typeof fetch;
  readonly #logger: { error(message: string): void };
  #keyRejectedLoggedAt = Number.NEGATIVE_INFINITY;

  constructor(options: TenkyuOptions) {
    if (!options || typeof options.apiKey !== "string" || options.apiKey === "") {
      throw new TenkyuError("unauthorized", 0, "Tenkyu: apiKey is required");
    }
    this.#apiKey = options.apiKey;
    this.#baseUrl = checkedBaseUrl(options.baseUrl ?? DEFAULT_BASE_URL);
    this.#timeoutMs = options.timeoutMs ?? 1500;
    this.#failOpen = options.failOpen ?? true;
    this.#failOpenCreditFraction = normalizeFraction(options.failOpenCreditFraction);
    const f = options.fetch ?? globalThis.fetch;
    this.#fetch = (input, init) => f(input, init);
    this.#logger = options.logger ?? console;
  }

  /** Never reveal the key when the instance is logged or serialized. */
  toJSON(): { baseUrl: string } {
    return { baseUrl: this.#baseUrl };
  }

  async check(signup: SignupCheck): Promise<Decision> {
    try {
      return this.#capDegraded(parseDecision(await this.#request("POST", "/v1/check", signup)));
    } catch (err) {
      if (this.#failOpen && isRetryable(err)) return degradedDecision(this.#failOpenCreditFraction);
      // A bad or expired key is OUR misconfiguration, not the user's fault: grant the small fail-open share
      // instead of nothing, and tell the developer (#request already logged it).
      if (this.#failOpen && isKeyRejected(err)) {
        return degradedDecision(this.#failOpenCreditFraction, "tenkyu api key rejected");
      }
      throw err;
    }
  }

  /**
   * A 200 with `degraded: true` means the server answered from an empty in-memory engine: it is not a
   * real score, so it must not carry a full grant. Cap it at failOpenCreditFraction, like a local fallback.
   */
  #capDegraded(d: Decision): Decision {
    if (d.degraded !== true) return d;
    return { ...d, credit_fraction: Math.min(d.credit_fraction, this.#failOpenCreditFraction) };
  }

  async outcome(id: string, outcome: Outcome): Promise<{ ok: boolean }> {
    return (await this.#request("POST", "/v1/outcome", { id, outcome })) as { ok: boolean };
  }

  /**
   * Release the rest of a review decision's grant once your app has verified something about the user. `requestId` is
   * the `request_id` you sent to check(). `proof`: "email_verified" is enough when `decision.earn_back.requires` is
   * "email"; "phone_verified" or "card_verified" satisfy either requirement and need the verified value in `links`
   * (for example `{ phone: "+14155550100" }`, from your own server-side state). Idempotent: asking twice returns the
   * same release. Throws on failure and never fails open: the user simply keeps the partial grant.
   */
  async earnBack(requestId: string, proof: EarnBackProof, links?: { phone?: string; card?: string }): Promise<EarnBackResult> {
    const body: Record<string, unknown> = { request_id: requestId, proof };
    if (links !== undefined) body.links = links;
    return (await this.#request("POST", "/v1/earn-back", body)) as EarnBackResult;
  }

  async usage(): Promise<Usage> {
    return (await this.#request("GET", "/v1/usage")) as Usage;
  }

  async #request(method: "GET" | "POST", path: string, payload?: unknown): Promise<unknown> {
    // One deadline for the whole call: the first attempt, the Retry-After wait and the retry share it.
    const deadline = Date.now() + this.#timeoutMs;
    let reply = await this.#send(method, path, payload, this.#timeoutMs);
    if (reply.status === 429) {
      // Honour Retry-After once, but only if the wait fits in what is left of the deadline.
      const wait = Math.min(reply.retryAfterMs ?? 1000, MAX_RETRY_AFTER_MS);
      const remaining = deadline - Date.now();
      if (remaining > wait) {
        await sleep(wait);
        reply = await this.#send(method, path, payload, Math.max(1, deadline - Date.now()));
      }
    }
    if (reply.status >= 200 && reply.status < 300) {
      // Tenkyu always answers JSON. A 2xx with another media type (text/html, xml, ...) is a gateway's page (a
      // WAF block page served 200 is common): not an outage, not a decision. Grant nothing. A missing header
      // or text/plain is tolerated (some runtimes label a JSON string that way), but the whole body must then
      // still be a valid decision, which parseDecision checks.
      if (!reply.isJson && reply.mediaType !== "" && reply.mediaType !== "text/plain") throw refusedInBetween(reply.status);
      return reply.body;
    }
    const err = toError(reply);
    // Only Tenkyu's own "bad key" answer is logged as a key problem. A gateway's 403 is not.
    if (isKeyRejected(err)) this.#logKeyRejected();
    throw err;
  }

  /** One clear server-side error, at most once per minute per client, so a bad key is noticed but not spammed. */
  #logKeyRejected(): void {
    const t = Date.now();
    if (t - this.#keyRejectedLoggedAt < KEY_REJECTED_LOG_INTERVAL_MS) return;
    this.#keyRejectedLoggedAt = t;
    try {
      this.#logger.error(API_KEY_REJECTED_MESSAGE);
    } catch {
      // a broken logger must not break sign-up
    }
  }

  async #send(method: string, path: string, payload: unknown, budgetMs: number): Promise<Reply> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), budgetMs);
    try {
      const res = await this.#fetch(this.#baseUrl + path, {
        method,
        headers: {
          authorization: `Bearer ${this.#apiKey}`,
          accept: "application/json",
          ...(payload === undefined ? {} : { "content-type": "application/json" }),
        },
        body: payload === undefined ? undefined : JSON.stringify(payload),
        signal: controller.signal,
      });
      const text = await res.text();
      let body: unknown = undefined;
      try {
        body = text ? JSON.parse(text) : undefined;
      } catch {
        body = undefined;
      }
      return {
        status: res.status,
        body,
        text,
        isJson: isJsonContentType(res.headers.get("content-type")),
        mediaType: (res.headers.get("content-type") ?? "").split(";")[0]?.trim().toLowerCase() ?? "",
        retryAfterMs: parseRetryAfter(res.headers.get("retry-after")),
      };
    } catch (err) {
      if (controller.signal.aborted) {
        throw new TenkyuError("timeout", 0, `Tenkyu request timed out after ${this.#timeoutMs}ms`);
      }
      // Do not include the original error text; it could echo request details.
      throw new TenkyuError("network_error", 0, `Tenkyu request failed: ${err instanceof Error ? err.name : "unknown error"}`);
    } finally {
      clearTimeout(timer);
    }
  }
}

function isRetryable(err: unknown): boolean {
  if (!(err instanceof TenkyuError)) return false;
  return (
    err.code === "network_error" ||
    err.code === "timeout" ||
    err.status === 429 ||
    err.status >= 500
  );
}

/**
 * Tenkyu said the API key is wrong, expired or revoked: status 401, a JSON content-type and exactly its own
 * error body (hosted/api answers exactly that, and nothing else, for a bad key). A 403, or a 401 with any
 * other body, comes from something in between and is NOT a key problem: see `isRefusedInBetween`.
 */
export function isKeyRejected(err: unknown): boolean {
  return err instanceof TenkyuError && err.status === 401 && err.code === "unauthorized";
}

/** A 401/403 that is not Tenkyu's own bad-key answer, or a 2xx that is not a JSON decision: something in between answered. Grant nothing. */
export function isRefusedInBetween(err: unknown): boolean {
  return err instanceof TenkyuError && err.code === "refused";
}

/** `https`, or `http` only for localhost, 127.0.0.1 and [::1]: the bearer key must never travel in clear text. */
function checkedBaseUrl(raw: string): string {
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    throw new TenkyuError("invalid_request", 0, "Tenkyu: baseUrl is not a valid URL (it must be https)");
  }
  const local = url.hostname === "localhost" || url.hostname === "127.0.0.1" || url.hostname === "[::1]";
  if (url.protocol !== "https:" && !(url.protocol === "http:" && local)) {
    throw new TenkyuError("invalid_request", 0, "Tenkyu: baseUrl must use https (http is allowed only for localhost, 127.0.0.1 and [::1])");
  }
  return raw.replace(/\/+$/, "");
}

/** Clamp to 0..1; anything that is not a finite number falls back to the default. */
function normalizeFraction(value: number | undefined): number {
  if (typeof value !== "number" || !Number.isFinite(value)) return DEFAULT_FAIL_OPEN_CREDIT_FRACTION;
  return Math.min(1, Math.max(0, value));
}

/**
 * A 200 whose body is not JSON, or whose credit_fraction is missing, not a finite number or outside
 * 0..1, is not a decision: it did not come from Tenkyu's engine, so it is refused (grant nothing), not
 * treated as an outage. Without this check the caller would compute NaN credits or crash.
 */
function parseDecision(body: unknown): Decision {
  const d = body as Partial<Decision> | null | undefined;
  const ok =
    typeof d === "object" &&
    d !== null &&
    typeof d.id === "string" &&
    (d.action === "allow" || d.action === "review" || d.action === "block") &&
    typeof d.credit_fraction === "number" &&
    Number.isFinite(d.credit_fraction) &&
    d.credit_fraction >= 0 &&
    d.credit_fraction <= 1;
  if (!ok) throw refusedInBetween(200);
  return withShadowFields(d as Decision);
}

/**
 * The shadow fields are optional and informational. Keep each only if it has the right type, so a
 * malformed one is dropped instead of reaching your logs as junk (and never breaks the decision).
 */
function withShadowFields(d: Decision): Decision {
  const { shadow, would_action, would_credit_fraction, ...rest } = d;
  const out: Decision = { ...rest };
  if (shadow === true) {
    out.shadow = true;
    if (would_action === "allow" || would_action === "review" || would_action === "block") out.would_action = would_action;
    if (
      typeof would_credit_fraction === "number" && Number.isFinite(would_credit_fraction) &&
      would_credit_fraction >= 0 && would_credit_fraction <= 1
    ) out.would_credit_fraction = would_credit_fraction;
  }
  return out;
}

/** What hosted/api/app.py error_response serializes for a bad key (compact JSON), from service.py unauthorized(). */
const TENKYU_UNAUTHORIZED_BODY = '{"error":{"code":"unauthorized","message":"missing or invalid API key"}}';

function refusedInBetween(status: number): TenkyuError {
  return new TenkyuError("refused", status, `Tenkyu call refused by something in between (status ${status})`);
}

function isJsonContentType(value: string | null): boolean {
  return /^\s*application\/(?:[\w.+-]+\+)?json\s*(?:;|$)/i.test(value ?? "");
}

function toError(reply: Reply): TenkyuError {
  if (reply.status === 401 || reply.status === 403) {
    // Tenkyu answers a bad key with exactly 401, a JSON content-type and the body above, byte for byte (only
    // surrounding whitespace is ignored). Comparing the RAW text, not the parsed object: a gateway that echoes
    // the farmer's user agent into its own JSON can write a duplicate "code":"unauthorized" key, which
    // JSON.parse would accept (last key wins). Anything else with these statuses came from something in
    // front of Tenkyu. Fixed message: nothing from the body.
    if (reply.status === 401 && reply.isJson && reply.text.trim() === TENKYU_UNAUTHORIZED_BODY) {
      return new TenkyuError("unauthorized", 401, "missing or invalid API key");
    }
    return refusedInBetween(reply.status);
  }
  const e = (reply.body as { error?: { code?: string; message?: string } } | undefined)?.error;
  const code = (e?.code as ErrorCode | undefined) ?? fallbackCode(reply.status);
  const message = typeof e?.message === "string" ? e.message : `Tenkyu API error (HTTP ${reply.status})`;
  return new TenkyuError(code, reply.status, message);
}

function fallbackCode(status: number): ErrorCode {
  if (status === 400) return "invalid_request";
  if (status === 404) return "not_found";
  if (status === 429) return "rate_limited";
  return "server_error";
}

function parseRetryAfter(value: string | null): number | undefined {
  if (value === null) return undefined;
  const secs = Number(value);
  if (Number.isFinite(secs) && secs >= 0) return secs * 1000;
  const date = Date.parse(value);
  return Number.isNaN(date) ? undefined : Math.max(0, date - Date.now());
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
