import "server-only";

// ===== PLACEHOLDER: REPLACE WITH YOUR OWN AUTH / DATABASE =====
// This stands in for whatever creates users in your app (Auth.js, Clerk, Supabase Auth, your own table).
// It does nothing real. In production it throws on purpose, so you cannot ship the stub by accident.

/** The account to create. It is created BEFORE Tenkyu is called; credits come afterwards. */
export interface NewUser {
  /** Already trimmed and lowercased by the route. */
  email: string;
  /**
   * Plain password, 8 to 72 characters (UTF-8 bytes <= 72), already checked by the route. Pass it straight to
   * your auth provider and nowhere else: never log it, store it, or put it in an error message.
   */
  password: string;
}

/** What to store once Tenkyu has answered for a newly created account. */
export interface CreditGrant {
  userId: string;
  /** Free AI credits to grant: Math.round(GRANT * credit_fraction). May be 0 (always 0 when blocked). */
  credits: number;
  /**
   * Tenkyu decision id, or null when the check was degraded (Tenkyu unreachable, or the server flagged its
   * answer degraded). Keep it to report outcomes later; null marks outage grants you may want to top up.
   */
  tenkyuDecisionId: string | null;
  /** Tenkyu said "review": consider a manual look or delayed credits. */
  needsReview: boolean;
  /**
   * Tenkyu said "block". The account still exists, with 0 credits, so the person sees nothing different from
   * anyone else (a 403 would be an oracle and would lock a real person out). Keep it out of paid features,
   * or hold it for review, as you see fit.
   */
  blocked: boolean;
  /**
   * `true` when the account got 0 credits (a block, or a failed check): store it with the row (the Supabase
   * adapter writes `notice_pending`) and show your "we couldn't give free credits automatically, contact
   * support" notice in your app AFTER sign-in. The sign-up response never carries it: a notice there would
   * tell a caller whose sign-ups are blocked which emails are new and which are already registered.
   */
  noticePending?: true;
  /**
   * Present only while your Tenkyu account is in shadow mode (`credits` is then the full `credit_fraction`
   * grant). `wouldAction` and `wouldCreditFraction` are what Tenkyu would have done once it enforces: store
   * or log them to see what enforcement will do to your users before you turn it on.
   */
  shadow?: true;
  wouldAction?: "allow" | "review" | "block";
  wouldCreditFraction?: number;
}

/**
 * Return the links YOUR app verified server-side for this email, for Tenkyu's `links` field.
 * Only a phone that passed your own OTP check, or a card fingerprint from your payment provider,
 * belongs here. NEVER read it from the request body: the browser could claim any phone.
 * Verified phone or card links are what lift an honest user above Tenkyu's 50% starter hold.
 * Placeholder: returns nothing. Replace it when you have a verified phone.
 */
export async function getVerifiedLinks(input: { email: string }): Promise<{ phone?: string } | undefined> {
  void input;
  return undefined;
}

export type CreateUserResult =
  | { ok: true; userId: string }
  | { ok: false; reason: "email_taken" }
  /** The auth provider refused the input (for example its own password policy). `message` is shown to the user: the provider's, never Tenkyu's. */
  | { ok: false; reason: "rejected"; message?: string }
  /**
   * The provider failed for a reason that is NOT about the input (an email-send rate limit, an outage). Some of
   * these only happen for NEW addresses, so showing them would tell a caller which emails have no account: the
   * route answers exactly as for a duplicate. `code` is the provider's error code or status, never its message.
   */
  | { ok: false; reason: "provider_error"; code?: string };

/**
 * Create the account and return its id, or `email_taken` if the email already has one (a unique constraint on
 * the email is the reliable way to know; two parallel requests must not both succeed). No credits yet. How the
 * person signs in is your auth's business. The route has already checked the password policy (8 to 72 characters).
 *
 * Using Supabase Auth? Do not write this by hand: `lib/supabase-auth.ts` has a tested adapter that already
 * handles "email already registered" in both Supabase modes. Wire it like this (server only; the service-role
 * key stays in an env var WITHOUT the NEXT_PUBLIC_ prefix and never reaches the browser):
 *
 *   import { createClient } from "@supabase/supabase-js";
 *   import { supabaseAdapterFromEnv } from "./supabase-auth";
 *   const supabase = supabaseAdapterFromEnv(createClient); // reads SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_ROLE_KEY
 *   export const createUser = supabase.createUser;
 *   export const grantCredits = supabase.grantCredits;
 */
export async function createUser(user: NewUser): Promise<CreateUserResult> {
  if (process.env.NODE_ENV === "production") {
    throw new Error("lib/app-auth.ts is a placeholder: implement createUser first");
  }
  console.warn("[placeholder] createUser called; no user was stored");
  void user;
  return { ok: true, userId: "placeholder-user-id" };
}

/** Store the credits for the account createUser just made. Idempotent per userId (primary key). */
export async function grantCredits(grant: CreditGrant): Promise<void> {
  if (process.env.NODE_ENV === "production") {
    throw new Error("lib/app-auth.ts is a placeholder: implement grantCredits first");
  }
  console.warn("[placeholder] grantCredits called; nothing was stored");
  void grant;
}
