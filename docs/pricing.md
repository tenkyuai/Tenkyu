# Pricing

## The model

| | Price | What you get | Status |
|---|---|---|---|
| Library | free forever, MIT | the Python library you run inside your own app: no key, no network calls | available now (`pip install tenkyu`) |
| Hosted API | 1,000 free checks every month | a shared store across servers, a dashboard, and the same scoring as the library | **launching soon** |
| Hosted API, extra checks | $10 for a pack of 10,000 checks | prepaid; one check is spent per signup you score | **launching soon** |

- No subscription. You never get a monthly bill.
- The 1,000 free checks renew each month and do not roll over. Purchased checks never expire. Free checks are spent first.
- When you run out, the API still answers (from your own history, with `credits_exhausted: true`) and never charges you. It does not break your signup flow.
- The library stays free and is not a trial. The hosted API is for people who want one shared history across servers without running a database.

The billing rules live with the hosted service, which is private.

The rest of this file is the market research behind the price. Prices there are other vendors' and may be out of date.

## What the market charges (entry paid plans, as reported)
| Tool | Free tier | Entry paid plan | Source |
|---|---|---|---|
| TrueGuard | 1k events/mo | $49.99/mo for 10k | https://shieldlabs.ai/blog/best-free-trial-abuse-prevention-tools (vendor-published; page says updated 2026-10-21; opened 2026-11-18) |
| check-mail.org (email only) | 1k/mo | $50/mo for 200k | https://check-mail.org/pricing |
| Fingerprint | 1k API calls/mo | $99/mo for 20k calls, then $4 per 1k | https://fingerprint.com/pricing/ (opened 2026-11-18) |
| Castle | 1k calls/mo | Pro $200/mo for 100k calls per the roundup; other sources have said about $99 (not re-checked, UNVERIFIED) | roundup above |
| IPQualityScore | 1k lookups/mo | from $99/mo (volume not stated) | roundup above |
| Verisoul | dashboard-only starter, no API | Professional from $249/mo | roundup above |
| Rupt | 500 tracked users/mo | Premium $299/mo | roundup above |
| SEON | none listed | $699/mo for 2,500 checks | roundup above |
| WorkOS Radar | first 1,000 checks free | $100/mo per 50k checks; includes device fingerprinting (20+ signals, per the page) | https://workos.com/radar (opened 2026-11-18) |
| Kaidn | 10,000 events/mo free, no card | paid plans above that; price not seen | https://pypi.org/project/kaidn/ (opened 2026-11-18; hosted API, needs a key; MIT client) |

Re-checked on 2026-11-18 (INTEL c24): rows with an "opened" date above were read that day; rows that say "roundup above" were read from the vendor-published roundup, not from each vendor. Not re-opened, so UNVERIFIED this run: check-mail.org ($50/mo for 200k) and the Fingerprint 14-day trial.

Reading: the cheapest paid plans cluster at $50 to $100 a month, and the roundup (published by one of the vendors in it) lists none of its eight tools as open source or self-hosted. Free tiers are not small: Kaidn gives 10,000 events a month, WorkOS 1,000 checks, Fingerprint 1,000 calls. A solo builder protecting a free tier is covered for free at low volume, which is why the hosted API has a free monthly allowance and no subscription (see stress-test.md).

## Why $10 per 10,000 checks
That is $1 per 1,000 checks, well under the $50 to $100 a month that the cheapest paid plans above ask, and there is no monthly commitment. It is reasoning from list prices only. No one has been asked.

## What would change the numbers
- If friends say they would run the free library forever and never pay for hosting, the paid plan needs a different reason to exist (a shared store is the obvious one).
- If friends already pay a fingerprint vendor, Tenkyu's job is "the rules on top of the visitor id", and pricing should sit beside that bill, not replace it.
- If nobody has a farming problem yet, the product is early and pricing is premature.

## Friends-only feedback template
Send this yourself, to people you already know who run an app with free credits. Tenkyu's builder contacts nobody. Do not post it publicly or send it to strangers.

> I built a small open-source Python library that scores signups to catch people farming free credits. Could you spend 10 minutes trying it on your signup flow and answer three things?
> 1. Do you see fake or repeat signups on your free tier today? Roughly how many a week?
> 2. What did it flag that was right, and what did it flag that was a real user?
> 3. If a hosted version existed with a shared store and a review queue, what would you pay a month, if anything? (Be blunt. "Nothing" is a useful answer.)
> Quickstart is in the README: it takes about ten lines.

Record answers in a plain list with the person's first name and the date. Treat any figure as one data point, not a market.
