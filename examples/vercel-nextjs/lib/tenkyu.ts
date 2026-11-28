import "server-only";
import { Tenkyu } from "tenkyu";

// Server-only: importing this file from a client component fails the build ("server-only"),
// so the API key cannot end up in a browser bundle. Never name the variable NEXT_PUBLIC_*.

/** Base credit grant for a clean signup. Tenkyu's credit_fraction scales it. */
export const GRANT = positiveInt(process.env.SIGNUP_CREDITS, 100);

/**
 * Largest share of GRANT a degraded (unscored) signup gets: Tenkyu timeout, network error, 5xx, 429, or
 * a 200 the server flagged `degraded`. Default 0.25 (env FAIL_OPEN_CREDIT_FRACTION,
 * 0 to 1). The signup still works, but an outage or a flooded Tenkyu rate limit is not a free full grant.
 * Honest users who signed up during an outage got a quarter; find them by tenkyuDecisionId === null and
 * top them up later if you like. A 4xx from Tenkyu (our request was wrong) grants nothing instead.
 */
export const FAIL_OPEN_CREDIT_FRACTION = fraction(process.env.FAIL_OPEN_CREDIT_FRACTION, 0.25);

// 1500 ms is the client default. Tenkyu fails open when it is exceeded: the signup is allowed
// with FAIL_OPEN_CREDIT_FRACTION of the grant and decision.degraded === true.
//
// Cold starts: if the hosted API runs on Cloud Run with --min-instances 0 and a sleeping Neon
// database, the first check after a quiet spell can take a few seconds, so with 1500 ms that
// signup is let through unchecked (hosted/README.md, "Cold starts"). Measure once with
// `time curl https://<your-tenkyu-url>/healthz` after 20 idle minutes. If it is slow, set
// TENKYU_TIMEOUT_MS=4000 for now (a signup waits longer in the worst case), or ask for a warm instance.
// This timer is separate from Vercel's own function cold start: it starts when the request to
// Tenkyu is sent.
const TIMEOUT_MS = positiveInt(process.env.TENKYU_TIMEOUT_MS, 1500);

let instance: Tenkyu | undefined;

/** One client per server instance, created on first use so `next build` does not need the key. */
export function getTenkyu(): Tenkyu {
  if (instance === undefined) {
    const apiKey = process.env.TENKYU_API_KEY;
    if (apiKey === undefined || apiKey === "") throw new Error("TENKYU_API_KEY is not set");
    instance = new Tenkyu({
      apiKey,
      timeoutMs: TIMEOUT_MS,
      failOpen: true,
      failOpenCreditFraction: FAIL_OPEN_CREDIT_FRACTION,
      ...(process.env.TENKYU_BASE_URL ? { baseUrl: process.env.TENKYU_BASE_URL } : {}),
    });
  }
  return instance;
}

function positiveInt(raw: string | undefined, fallback: number): number {
  const n = Number(raw);
  return raw !== undefined && Number.isFinite(n) && n > 0 ? Math.round(n) : fallback;
}

function fraction(raw: string | undefined, fallback: number): number {
  const n = Number(raw);
  return raw !== undefined && raw !== "" && Number.isFinite(n) && n >= 0 && n <= 1 ? n : fallback;
}
