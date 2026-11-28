// Supabase Edge Function: sign-up through Tenkyu. Logic lives in handler.ts (testable).
// The service-role key is read here, on the server, and never leaves it.
import { createClient } from "npm:@supabase/supabase-js@2";
import { ALLOW_INLINE_SCORING_ENV, createHandler, type AdminClient, type AuthClient, type Config } from "./handler.ts";

function num(name: string, fallback: number): number {
  const n = Number(Deno.env.get(name));
  return Deno.env.get(name) !== undefined && Number.isFinite(n) && n >= 0 ? n : fallback;
}

/** 0..1 share; falls back when unset or not a finite number in range. */
function fraction(name: string, fallback: number): number {
  const raw = Deno.env.get(name);
  const n = Number(raw);
  return raw !== undefined && raw !== "" && Number.isFinite(n) && n >= 0 && n <= 1 ? n : fallback;
}

const config: Config = {
  tenkyuApiKey: Deno.env.get("TENKYU_API_KEY") ?? "",
  tenkyuUrl: Deno.env.get("TENKYU_URL") ?? "https://api.tenkyu.dev",
  timeoutMs: 1500,
  grant: num("GRANT", 300),
  allowedOrigin: Deno.env.get("ALLOWED_ORIGIN") ?? "",
  trustedProxyHops: num("TRUSTED_PROXY_HOPS", 1),
  clientIpHeader: Deno.env.get("CLIENT_IP_HEADER") ?? "",
  debugIpHeaders: Deno.env.get("DEBUG_IP_HEADERS") === "1",
  // Degraded decisions (Tenkyu timeout, network error, 5xx, 429, or a 200 flagged degraded) grant at most this
  // share of GRANT. Default 0.25. A rejected API key (Tenkyu's exact 401 body) gets the same share, with one error log a minute.
  // Any other Tenkyu 4xx (our request was wrong) grants nothing.
  failOpenCreditFraction: fraction("FAIL_OPEN_CREDIT_FRACTION", 0.25),
  // The browser never gets a notice in the sign-up answer (it would reveal which emails exist). An account with
  // 0 credits gets `notice_pending = true` on its credits row; your app shows the notice after sign-in.
  // NO_CREDITS_MESSAGE="" leaves that flag off. The notice text itself lives in your app.
  noCreditsNotice: Deno.env.get("NO_CREDITS_MESSAGE") ?? undefined,
  // Checked locally before an account is created. Keep it >= the minimum set in your Supabase Auth settings.
  passwordMinLength: Math.max(1, Math.floor(num("PASSWORD_MIN_LENGTH", 8))),
  // In-memory per instance: see guards.ts for the production note (shared store).
  rateLimit: {
    perIp: { capacity: num("RATE_IP_BURST", 20), perMinute: num("RATE_IP_PER_MIN", 15) },
    perDevice: { capacity: num("RATE_DEVICE_BURST", 5), perMinute: num("RATE_DEVICE_PER_MIN", 5) },
  },
};
if (config.tenkyuApiKey === "") console.error("TENKYU_API_KEY is not set: no sign-up is checked and every one gets only the small fail-open grant (also logged once a minute)");

// SUPABASE_URL, SUPABASE_ANON_KEY and SUPABASE_SERVICE_ROLE_KEY are provided to Edge Functions
// by Supabase (UNVERIFIED for your CLI version: if one is missing, set it with `supabase secrets set`).
const url = Deno.env.get("SUPABASE_URL")!;
const noSession = { auth: { persistSession: false, autoRefreshToken: false } };
const authClient = createClient(url, Deno.env.get("SUPABASE_ANON_KEY")!, noSession) as unknown as AuthClient;
const adminClient = createClient(url, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!, noSession) as unknown as AdminClient;

// Optional: pass `verifiedLinks: async ({ email }) => ({ phone })` with a phone YOUR app verified (OTP),
// looked up server-side. Never read it from the request body. See README, "Links".
//
// waitUntil: answer first, then ask Tenkyu and insert the credits in the background, so a new sign-up and a
// duplicate email answer in the same time (no timing oracle). EdgeRuntime.waitUntil is Supabase's background
// tasks API; without it (an old runtime) the handler pads duplicate answers to a similar time instead.
declare const EdgeRuntime: { waitUntil(promise: Promise<unknown>): void } | undefined;
const waitUntil = typeof EdgeRuntime !== "undefined" ? (work: Promise<void>) => EdgeRuntime.waitUntil(work) : undefined;
// In production the handler REFUSES to start without waitUntil (a duplicate email would be told apart by its
// response time), unless TENKYU_ALLOW_INLINE_SCORING=1. "Production" = NODE_ENV or DENO_ENV is "production", or
// DENO_DEPLOYMENT_ID is set (UNVERIFIED that Supabase sets it: set NODE_ENV=production as a secret to be sure).
const production =
  Deno.env.get("NODE_ENV") === "production" || Deno.env.get("DENO_ENV") === "production" || Deno.env.get("DENO_DEPLOYMENT_ID") !== undefined;
// TENKYU_URL must be https (http only for localhost): createHandler throws at startup otherwise.
Deno.serve(createHandler({
  config, fetch, authClient, adminClient, production, allowInlineScoring: Deno.env.get(ALLOW_INLINE_SCORING_ENV) === "1",
  ...(waitUntil ? { waitUntil } : {}),
}));
