# Stress test: arguing that Tenkyu fails (2026-11-18)

One red-team agent was told to argue the product fails and cite sources. I then checked its top claims myself. This page keeps the two apart. Nothing here is a forecast. Competitor facts come from vendor pages and change often.

## Verdict
Tenkyu is a credible small open-source library, and a poor bet as a venture-scale business **as currently planned**. The code does what it says. The weak points are the market position and the paid plan, not the engineering.

## Verified by me (I opened the page or ran the code)

| # | Finding | How I checked | What it means |
|---|---|---|---|
| V1 | Clerk blocks email subaddresses (`+tags`) and treats Gmail dots as the same mailbox, and also blocks disposable domains. Both are "included on every plan" and off by default. | Fetched both Clerk docs pages | If an app already uses Clerk, two of Tenkyu's best signals are free and need no code. A section "when not to use Tenkyu" is honest and useful. |
| V2 | kaidn (PyPI, MIT client, v1.1.0, 2026-08-21) returns allow / review / block with reasons, a 0-100 score and per-check evidence. It needs an API key for the hosted Kaidn service, which has a free tier of 10,000 events a month, and it does device fingerprinting through a separate browser library. | Fetched the PyPI page | "No open-source equivalent" would be false. The agent called this a paid API; the page shows a 10,000-event free tier, which is more generous than the 1,000-event free tiers in my notes. Tenkyu's real difference is local-only (no network, no key), not "free". |
| V3 | The headline F1 depends on the farmer mix, which the author chose. With 50% residential farmers instead of 15%, F1 falls from 0.822 to 0.578 (recall 0.806 to 0.466). | Ran `benchmarks/sensitivity.py` (new) | The "0.82" is conditional on an easy mix. README now says so and shows the command. |
| V4 | The agent's claim that `device_id` is the load-bearing signal is **not supported by this benchmark**. With a fresh device id on every signup, recall barely moves (0.806 to 0.798) and F1 rises to 0.855, because device reuse is the only source of the 75 honest users sent to review. | Same script, variant B | In synthetic data, device reuse buys under one point of recall and costs 3.7% review rate. That is partly my own assumption (4% of honest users share a device, farmers reuse devices rarely). It does not show a shared device is worthless in real traffic. It does mean the signal needs real data before anyone trusts it. README now says so. |
| V5 | `tenkyu` was free on PyPI at 01:08Z. | `GET pypi.org/pypi/tenkyu/json` returned 404 | Re-check before uploading. |

## Agent claims I did not verify (treat as unverified)
- Stripe: "1 in 6 AI-company registrations are bad actors known to Stripe"; WorkOS Radar free for 1,000 checks then $100 per 50k; Fingerprint $99 for 20k calls. Vendor marketing pages.
- Disposable-domain lists are free and widely maintained (5.7k-star CC0 repo and several others), so a disposable list cannot be a differentiator. Plausible; I did not open the repo.
- trial-abuse-guard (npm, MIT) and ScopeBlind and email-normalize overlap with parts of Tenkyu. Not opened by me.
- GitHub searches for "free trial abuse" show only tiny repos (0 to 4 stars). Could mean an empty niche or low demand; the agent's own searches, not repeatable from a web page.
- "OSS to paid converts about 1%" (one CEO quote) and the resulting 3,500 users for $1,000 monthly. The arithmetic follows from the quote, but the quote is one data point.
- Agent reasoning with no source: public rules let attackers test against them; a paid host of a rule-based scorer has no moat because a Redis store is easy to write; a student with no track record has a trust problem selling a fraud tool.

## Where the red team is probably right
1. **The paid cloud has no stated reason to exist.** The code is MIT and a shared store is a small adapter. A paid tier needs something a self-hoster cannot do. The only idea on the table is opt-in, anonymised cross-install reuse sharing, which is a privacy and trust problem too. Do not build any cloud until someone asks for it.
2. **The easy abuse is the commodity.** Aliases and disposable domains are free in the auth tool many AI apps already use (V1).
3. **"Huge company" has no path from here** without a data network or enterprise sales, and neither fits "no money, no contacting people". A useful free library with a clear honest niche is still a reasonable outcome, and a reasonable start.

## Where it is genuinely strong
- It states its 0% class and the 3.7% review cost in plain numbers, and now the mix sensitivity.
- No dependencies, no network calls, no personal data leaving the app, explainable reasons, custom signals as plain functions. Kaidn and the vendors cannot offer "your data never leaves your server".
- Cost to the founder if it fails is time, not money.

## What would change the verdict (pre-registered)
- Someone who is not you farms against the shipped rules in a sandbox and recall including a residential class is 60% or more. (None done.)
- At least 5 non-author GitHub issues from real production use, and 3 unprompted requests for a hosted version, within 60 days of the PyPI release. If not, keep it a free portfolio library and do not build the cloud.
- A paid feature a self-hoster cannot copy that 10 installs accept.

## Changes made from this test
- README: one paragraph on mix sensitivity and the device-signal result; command to reproduce.
- `benchmarks/sensitivity.py` (new).
- Backlog B25 to B28 (below). B28 (the kill / pivot rule) is in `docs/launch.md`.
