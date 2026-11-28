# Community posts (WRITTEN, NOT POSTED)

Two drafts, each under 250 words, written as a builder sharing work and asking for feedback. Read each community's rules first (many limit self-promotion to a weekly thread). Post as yourself, from one account, and answer every reply. TODO(Or): post only after the next release (0.3.0 in the changelog) is on PyPI; `pyproject.toml` still reads 0.2.2.

Where the numbers come from: 250 of 939 flagged is `python -m tenkyu audit --sample --grant 3`. The v2 figures (65.0% of farmer accounts flagged, 43.6% blocked, 1.6% of honest users blocked, 20.4% flagged, school 96.7%) are `benchmarks/FROZEN-v2.md`. The 0.5% patient-farmer figure is `docs/heldout-benchmark.md` (older set, now a tuning set). The 25% grant is `clients/js` (`failOpenCreditFraction`) and the CHANGELOG. Thresholds 0.35 and 0.7 and the NFKC and look-alike text are README "What it checks" and "Honest limits". Both posts say the hosted API is not deployed; the JS client is also unpublished. Keep those lines.

## 1. Indie hackers style

**Title:** I built an audit that shows how many free credits went to farmers. What should it check?

I'm Or, 18, solo. I built Tenkyu, an open-source Python library for AI apps that give away free credits. It scores each signup allow, review or block, because some people sign up many times to farm the credits.

The part I like most is not the scoring. It is the audit: export your signups to CSV, run one command, and see how many it would have flagged and how much free credit it would have held back. It runs offline. On my bundled synthetic sample it flags 250 of 939 signups.

I have no customers yet, so every accuracy number comes from synthetic data. On a second, blind synthetic test (written by an AI that never saw my rules, run once), it flagged 65% of farmer accounts and blocked 1.6% of honest users. But it flagged 20.4% of honest users for review (partial credit): a school behind one IP is almost all reviewed. It does not catch a patient farmer who uses a new inbox, IP and device for every account.

There is a shadow mode that scores everyone and refuses nobody, so you can check it on your own users first.

I am planning a hosted version with a shared farmer network, plus recipes for Supabase and Vercel. None of that is deployed or published yet.

If you give away free credits: what would make you trust a tool like this enough to run it on your signups?

https://github.com/tenkyuai/tenkyu

## 2. Python community style

**Title:** Tenkyu: a dependency-free signup scorer (Python 3.11+), looking for API and code review

I wrote a small library to score signups for free-tier abuse. `Guard().check(Signup(...))` returns a `Decision` with an action, a score, reasons and a `credit_fraction`. It uses only the standard library.

Things I would like opinions on:

- Signals are plain functions `(signup, store, now)`. A signal that raises is ignored so it cannot break a signup flow. Is that the right default?
- Stores (memory, SQLite, Postgres) share one interface, and `check` reads history and records the signup as one transaction. Parallel aliases of one person used to all pass before I fixed it.
- Email normalization uses NFKC and removes format characters. Cross-script look-alikes (a Cyrillic "а" in `gmаil.com`) are flagged by their own signals but are not merged with the real address, because that mapping cannot be made safely. Is that the right call?
- Scores combine as a noisy-or. Thresholds are 0.35 and 0.7.
- `Guard(shadow=True)` plus a `DecisionLog`, then `tenkyu shadow-report` against a labels CSV. Does that interface make sense?

Tested on 3.11, 3.12 and 3.13. The benchmarks are synthetic, and the README says so.

What would you change about the API?

https://github.com/tenkyuai/tenkyu
