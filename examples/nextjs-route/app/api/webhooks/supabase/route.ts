import { timingSafeEqual } from "node:crypto";
import { tenkyu, GRANT } from "../../../../lib-tenkyu";

// Sketch: Supabase Database Webhook on INSERT into auth.users (or your profiles table).
// In the webhook config, add the HTTP header  x-webhook-secret: <SUPABASE_WEBHOOK_SECRET>.
function secretMatches(got: string | null): boolean {
  const a = Buffer.from(got ?? "");
  const b = Buffer.from(process.env.SUPABASE_WEBHOOK_SECRET!);
  return a.length === b.length && timingSafeEqual(a, b);
}

export async function POST(req: Request) {
  if (!secretMatches(req.headers.get("x-webhook-secret"))) {
    return new Response("unauthorized", { status: 401 });
  }
  const { type, record } = (await req.json()) as { type: string; record: { id: string; email?: string } };
  if (type !== "INSERT") return new Response("ignored");

  const decision = await tenkyu.check({ email: record.email, request_id: `supabase_${record.id}` });
  const credits = Math.round(GRANT * decision.credit_fraction);
  // ... update the row with `credits` and decision.id using the Supabase service-role client ...
  return Response.json({ ok: true, credits });
}
