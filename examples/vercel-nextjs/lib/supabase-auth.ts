import "server-only";
import type { CreateUserResult, CreditGrant, NewUser } from "./app-auth";

// Supabase Auth adapter for lib/app-auth.ts (use it when your users live in Supabase). Server only.
//
// - createUser calls `auth.signUp` on a client built with the ANON key, so Supabase sends its normal
//   confirmation email and applies its password policy and auth rate limits. It runs BEFORE Tenkyu is asked
//   (the account-first flow in lib/signup.ts).
// - grantCredits inserts one row into the `credits` table with a client built with the SERVICE-ROLE key.
//   That key bypasses row-level security: it lives in a server env var WITHOUT the NEXT_PUBLIC_ prefix, and
//   this file imports "server-only", so a client component that imports it fails the build.
// - The table is the one in examples/supabase-edge-function/sql/credits.sql (user_id primary key, granted,
//   decision_id, notice_pending). Run that script in your project first.
//
// Ported from examples/supabase-edge-function/supabase/functions/tenkyu-signup/handler.ts.

/** Result of `auth.signUp`, as far as this adapter reads it. */
interface SignUpResult {
  data: { user: { id: string; identities?: unknown[] | null } | null };
  error: { message: string; code?: string; status?: number } | null;
}
/** Client built with the ANON key. */
export interface SupabaseAuthClient {
  auth: { signUp(args: { email: string; password: string }): Promise<SignUpResult> };
}
/** Client built with the SERVICE-ROLE key. */
export interface SupabaseAdminClient {
  from(table: "credits"): {
    insert(row: { user_id: string; granted: number; decision_id: string | null; notice_pending?: true }): PromiseLike<{
      error: { message: string; code?: string } | null;
    }>;
  };
}

export interface SupabaseAdapter {
  createUser(user: NewUser): Promise<CreateUserResult>;
  grantCredits(grant: CreditGrant): Promise<void>;
}

export function createSupabaseAdapter(clients: { auth: SupabaseAuthClient; admin: SupabaseAdminClient }): SupabaseAdapter {
  return {
    async createUser({ email, password }) {
      const { data, error } = await clients.auth.auth.signUp({ email, password });
      if (error) {
        // Email confirmation OFF: Supabase says "already registered" in plain text. Same answer as a new account.
        if (isAlreadyRegistered(error)) return { ok: false, reason: "email_taken" };
        // Only a password-policy refusal is shown: it depends on the password alone. Anything else (an email-send
        // rate limit, an invalid-address check, an outage) can depend on whether the address is new, so it gets the
        // duplicate answer; the route logs the code, never the message (it can contain the email).
        if (isPasswordPolicy(error, email)) return { ok: false, reason: "rejected", message: error.message };
        return { ok: false, reason: "provider_error", code: error.code ?? (error.status !== undefined ? `status_${error.status}` : undefined) };
      }
      const user = data.user;
      if (!user) return { ok: false, reason: "rejected" };
      // Email confirmation ON: Supabase hides "already registered" by returning a fake user with no identities.
      // Treat it as taken: no Tenkyu call, no credits, and the route answers exactly as for success.
      if (Array.isArray(user.identities) && user.identities.length === 0) return { ok: false, reason: "email_taken" };
      return { ok: true, userId: user.id };
    },

    async grantCredits(grant) {
      const { error } = await clients.admin.from("credits").insert({
        user_id: grant.userId,
        granted: grant.credits,
        decision_id: grant.tenkyuDecisionId,
        // Only when set, so an older table without the column keeps working for ordinary grants.
        ...(grant.noticePending === true ? { notice_pending: true as const } : {}),
      });
      // The primary key keeps the first grant: a repeat for the same user is expected, not an error.
      if (error && !isUniqueViolation(error)) {
        // Code only: the provider's message is not needed and could carry request details.
        throw new Error(`credits insert failed (${error.code ?? "no code"})`);
      }
    },
  };
}

/**
 * Build both Supabase clients from env and return the adapter. Pass `createClient` from
 * `@supabase/supabase-js` (npm install it in your app); it is a parameter so this file needs no import.
 * Env, all server only: SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_ROLE_KEY.
 */
export function supabaseAdapterFromEnv(
  createClient: (url: string, key: string, options: object) => unknown,
  env: Record<string, string | undefined> = process.env,
): SupabaseAdapter {
  const url = env.SUPABASE_URL;
  const anonKey = env.SUPABASE_ANON_KEY;
  const serviceRoleKey = env.SUPABASE_SERVICE_ROLE_KEY;
  if (!url || !anonKey || !serviceRoleKey) {
    throw new Error("Set SUPABASE_URL, SUPABASE_ANON_KEY and SUPABASE_SERVICE_ROLE_KEY (server env, no NEXT_PUBLIC_ prefix)");
  }
  const noSession = { auth: { persistSession: false, autoRefreshToken: false } };
  return createSupabaseAdapter({
    auth: createClient(url, anonKey, noSession) as SupabaseAuthClient,
    admin: createClient(url, serviceRoleKey, noSession) as SupabaseAdminClient,
  });
}

/** `user_already_exists` (sign-up with confirmation off), `email_exists` (admin API), or older GoTrue's text. */
function isAlreadyRegistered(error: { message: string; code?: string }): boolean {
  return (
    error.code === "user_already_exists" ||
    error.code === "email_exists" ||
    /already (been )?registered|already exists/i.test(error.message)
  );
}

/** `weak_password`, or Supabase's own "Password ..." text, and never a message that repeats the email. */
function isPasswordPolicy(error: { message: string; code?: string }, email: string): boolean {
  if (email !== "" && error.message.toLowerCase().includes(email.toLowerCase())) return false;
  return error.code === "weak_password" || /^password\b/i.test(error.message);
}

function isUniqueViolation(error: { message: string; code?: string }): boolean {
  return error.code === "23505" || /duplicate key|unique constraint/i.test(error.message);
}
