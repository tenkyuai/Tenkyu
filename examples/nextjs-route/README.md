# Next.js route example

Minimal App Router routes that call Tenkyu when a user signs up.

For a Vercel-ready direct signup flow (first-party device cookie, input validation, server-only client, honest limits) use [`examples/vercel-nextjs`](../vercel-nextjs). This folder keeps the Clerk and Supabase webhook sketches.

- `app/api/signup/route.ts`: direct signup check (email from body, IP from `x-forwarded-for`, device id from an `x-device-id` header your client sets).
- `app/api/webhooks/clerk/route.ts`: Clerk `user.created`, verified with Svix.
- `app/api/webhooks/supabase/route.ts`: Supabase database webhook, verified with a shared secret header.
- `lib-tenkyu.ts`: one server-side client instance. Place it wherever fits your project and fix the import paths.

Credits granted = `Math.round(GRANT * decision.credit_fraction)`. If Tenkyu is unreachable the client fails open with a reduced grant (`failOpenCreditFraction`, default 0.25, `decision.degraded === true`), so signup never breaks and an outage is not a free-credit window. For a fuller recipe see `examples/vercel-nextjs/`.

## Environment variables (server only)

| Name | Purpose |
| --- | --- |
| `TENKYU_API_KEY` | `tk_live_...` or `tk_test_...` key |
| `SIGNUP_CREDITS` | Base grant, default 100 |
| `CLERK_WEBHOOK_SECRET` | Svix signing secret (`whsec_...`) from the Clerk dashboard |
| `SUPABASE_WEBHOOK_SECRET` | Random string you also set as the `x-webhook-secret` header on the webhook |

No secrets go to the browser. Do not prefix any of these with `NEXT_PUBLIC_`, and never call Tenkyu from client components: the API key would ship in your bundle. Browsers only send the email and a device id to your own route.

`x-forwarded-for` is only trustworthy behind Vercel (or another proxy you control). Elsewhere a client can forge it.

This example is not installed or built in the Tenkyu repo; only the client is type-checked.
