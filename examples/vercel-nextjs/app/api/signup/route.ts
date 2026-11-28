import { after } from "next/server";
import { createSignupHandler } from "../../../lib/signup";
import { createUser, getVerifiedLinks, grantCredits } from "../../../lib/app-auth";
import { FAIL_OPEN_CREDIT_FRACTION, GRANT, getTenkyu } from "../../../lib/tenkyu";

// All the logic is in lib/signup.ts (a route file may only export HTTP methods).
export const POST = createSignupHandler({
  tenkyu: getTenkyu,
  createUser: (user) => createUser(user),
  grantCredits: (grant) => grantCredits(grant),
  verifiedLinks: (input) => getVerifiedLinks(input),
  grant: GRANT,
  failOpenCreditFraction: FAIL_OPEN_CREDIT_FRACTION,
  // Answer first, then ask Tenkyu and store the credits: a new sign-up and a duplicate email take the same
  // time, so the response time cannot be used to find out which emails have accounts. Needs Next 15.1+.
  defer: (work) => after(work),
});
