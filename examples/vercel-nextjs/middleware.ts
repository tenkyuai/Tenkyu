import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

export const DEVICE_COOKIE = "tk_did";

const ONE_YEAR_SECONDS = 60 * 60 * 24 * 365;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

// Gives every browser a first-party random id the first time it loads a page. The signup route
// forwards it to Tenkyu as `device_id`, so two accounts created from one browser can be linked.
//
// It is a plain random UUID: it identifies a browser profile, not a person, and carries nothing
// else. Clearing cookies, private windows and some privacy browsers produce a fresh id, so treat it
// as a weak signal that only catches careless farming (see README, "Honest limits").
//
// HttpOnly: only the server route reads this cookie, page JavaScript never needs it. HttpOnly keeps
// it out of document.cookie, so a script injected into your page (or a third-party script) cannot
// read it or copy it to another site. If you later want client-side code to read it, drop HttpOnly
// on purpose and say why.
// Secure: sent over HTTPS only. Vercel serves HTTPS everywhere. Local `next dev` over plain http on
// localhost works in Chrome and Firefox; Safari may refuse Secure cookies there, in which case use
// `next dev --experimental-https`.
// SameSite=Lax: sent on normal top-level navigations and same-site fetches, not on cross-site POSTs.
export function middleware(request: NextRequest) {
  const existing = request.cookies.get(DEVICE_COOKIE)?.value;
  if (existing !== undefined && UUID.test(existing)) return NextResponse.next();

  const response = NextResponse.next();
  response.cookies.set(DEVICE_COOKIE, crypto.randomUUID(), {
    maxAge: ONE_YEAR_SECONDS,
    httpOnly: true,
    secure: true,
    sameSite: "lax",
    path: "/",
  });
  return response;
}

// Pages only. Skip API routes (the cookie must already exist when the form is submitted) and static files.
export const config = {
  matcher: ["/((?!api/|_next/static|_next/image|favicon\\.ico).*)"],
};
