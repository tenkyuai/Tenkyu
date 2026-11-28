# Security policy

Tenkyu scores signups with fixed rules. It is a speed bump for abuse, not a security boundary, and it is early software (0.x). Do not rely on it alone to protect money, accounts or data. What has been attacked, what held and what did not is in [docs/threat-model-and-attacks.md](docs/threat-model-and-attacks.md).

## Reporting a vulnerability

Please report it privately, not in a public issue.

- Email: security@tenkyu.dev (TODO(Or): confirm this mailbox exists and is read before release.)
- Or use the repository's "Report a vulnerability" button on the Security tab (GitHub private vulnerability reporting).
- If neither works, open an issue that says only "security contact needed", with no details, and the maintainer will reply with a private way to send them.

Please include what you tested (version or commit), the steps to reproduce, and what you think the impact is. A short proof of concept is enough. Do not include real personal data.

## Expected response time

Tenkyu is maintained by one person. TODO(Or): confirm you can keep these.

- Acknowledgement within 3 days.
- First assessment (accepted, not a vulnerability, or need more information) within 7 days.
- A fix, or a dated plan for one, within 30 days for anything that exposes data, money or another customer's results. Lower-impact issues may wait for the next release.
- I will tell you when it is fixed and credit you in the changelog if you want that. There is no bug bounty.

Please keep details private until a fix is released or 90 days have passed, whichever is first. If I am slow, tell me and we will agree a date.

## Supported versions

Only the latest 0.x release of the library gets fixes. The hosted service, when it is running, is always the latest code. Older releases are not patched; upgrade.

## In scope

- The library (`src/tenkyu/`): crashes, hangs or unbounded memory or disk use from crafted input; `client_ip` trusting a spoofed header under default settings; raw personal data stored where it should be hashed; reading or corrupting data through the stores; webhook signature checks.
- The hosted API (`hosted/api/`): authentication and API keys, rate limits, idempotency, the shared farmer network (for example making one customer cause blocks for others, or probing whether an identity was reported), the credit ledger and payment webhooks, and anything that leaks one customer's data to another.
- The dashboard (`hosted/web/`): sign-in, sessions, CSRF, access between customers, injection (XSS, CSV formula injection), security headers.
- The in-browser audit page (`/audit`): anything that sends the user's CSV off their machine, runs script from another origin, or renders CSV text as HTML.
- The watcher (`hosted/watcher/`): prompt injection that makes it act, leak or send something it should not.

## Out of scope

- A farmer getting past the scoring rules. That is a detection gap, not a vulnerability. The known ones are listed under "Honest limits" in the README. Open a normal public issue for new ones. Bypassing the network's anti-poisoning rules is in scope (above).
- Testing against anyone else's deployment, or against a hosted instance run by me, without asking first. Run your own copy instead (see below).
- Load or denial-of-service testing against a host you do not own, social engineering, and physical attacks.
- Bugs in third-party services (GitHub, Lemon Squeezy, Neon, Google Cloud, Gemini) or in your own deployment's configuration.
- Findings that need a stolen secret (`MASTER_SECRET`, `NETWORK_SECRET`, a database login) with no other flaw.

## Safe harbour

If you act in good faith, follow this policy, test only on your own instance (a local run from `hosted/README.md`, or a deployment you own), and do not access or keep anyone else's data, I will not take or support legal action against you, and I will treat your research as authorized. This covers me as the maintainer. I cannot speak for third-party services or for people who run their own copy of Tenkyu.
