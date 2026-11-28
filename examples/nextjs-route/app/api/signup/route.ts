import { tenkyu, GRANT } from "../../../lib-tenkyu";

export async function POST(req: Request) {
  const { email } = await req.json();
  const decision = await tenkyu.check({
    email,
    // Behind Vercel the first hop is the real client IP; elsewhere it is spoofable.
    ip: req.headers.get("x-forwarded-for")?.split(",")[0]?.trim(),
    device_id: req.headers.get("x-device-id") ?? undefined, // set by your client-side fingerprint
    user_agent: req.headers.get("user-agent") ?? undefined,
  });
  if (decision.action === "block") return Response.json({ error: "signup_blocked" }, { status: 403 });
  const credits = Math.round(GRANT * decision.credit_fraction);
  // ... create the user and grant `credits` here ...
  return Response.json({ ok: true, credits, decision_id: decision.id });
}
