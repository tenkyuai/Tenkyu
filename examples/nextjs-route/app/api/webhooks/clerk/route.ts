import { Webhook } from "svix";
import { tenkyu, GRANT } from "../../../../lib-tenkyu";

// Sketch: Clerk "user.created" webhook, verified with Svix before anything else.
export async function POST(req: Request) {
  const raw = await req.text(); // Svix signs the exact raw body
  let event: { type: string; data: { id: string; email_addresses?: { email_address: string }[] } };
  try {
    event = new Webhook(process.env.CLERK_WEBHOOK_SECRET!).verify(raw, {
      "svix-id": req.headers.get("svix-id") ?? "",
      "svix-timestamp": req.headers.get("svix-timestamp") ?? "",
      "svix-signature": req.headers.get("svix-signature") ?? "",
    }) as typeof event;
  } catch {
    return new Response("invalid signature", { status: 401 });
  }
  if (event.type !== "user.created") return new Response("ignored");

  const decision = await tenkyu.check({
    email: event.data.email_addresses?.[0]?.email_address,
    request_id: `clerk_${event.data.id}`, // idempotent across webhook retries
  });
  const credits = Math.round(GRANT * decision.credit_fraction);
  // ... store `credits` and decision.id for event.data.id (e.g. in Clerk publicMetadata or your DB) ...
  return Response.json({ ok: true, credits });
}
