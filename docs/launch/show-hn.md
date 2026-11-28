# Show HN draft (WRITTEN, NOT POSTED)

Notes for Or (not part of the post):
- TODO(Or): post only after the next release is on PyPI. The numbers below are for the night-2 rules (the 0.3.0 changelog entry; `pyproject.toml` still says 0.2.2 and has not been bumped). `pip install tenkyu` gives 0.2.1 until you release. Check the PyPI page yourself first.
- TODO(Or): the hosted API, the dashboard and the in-browser audit page are built but NOT deployed, and the JS client (`clients/js`) is NOT published to npm. The Supabase and Vercel recipes need both, and have not run on a real project. The post says so. Do not edit those lines out.
- Where each number comes from:
  - 250 of 939 flagged, $741.75 at $3: output of `python -m tenkyu audit --sample --grant 3` (checked on night 2).
  - Blind v2 numbers: `benchmarks/FROZEN-v2.md`, same table in README "Blind v2 test" and `docs/heldout-benchmark.md`. Seeds 101-120, run once, after the rules were frozen.
  - 34%, 32%, 46.5% (proxies, shared VPN, disposable) and 99.1% (Unicode tricks): the same file. 0.5% for a patient farmer: `docs/heldout-benchmark.md` (the night-1 set, now a tuning set).
  - Prices: `hosted/BILLING.md` (`CREDIT_PACKS` default `10000:10`, `FREE_CHECKS` 1,000 a month). Defaults, not a promise. `docs/pricing.md` matches them.
  - 2 to 3 reporters: `hosted/DESIGN.md`, "Hardened after the first red team" (phone or card 2, email or device 3).
- Removed from the old draft because the new rules changed it: the night-1 held-out figures (70% flagged, 83% precision, 4.6% flagged and 1.4% blocked), which are now a tuning set, and the 38% Unicode figure.
- Reply to every comment yourself. Do not ask for upvotes or stars.

## Title (66 characters)

Show HN: Tenkyu, audit your signups for free-credit farmers (Python, MIT)

## Post (about 300 words)

I'm Or, 18, building this alone.

Run this on your signup export and see what farmers took:

    pip install tenkyu
    python -m tenkyu audit signups.csv --grant 3 --out flagged.csv

It replays your CSV offline, in time order. Nothing leaves your machine. `python -m tenkyu audit --sample` runs it on bundled synthetic signups: 250 of 939 flagged, $741.75 of free credit withheld at $3 a grant.

It is a Python library (MIT, no dependencies, no network calls, fixed rules, no ML). It scores each signup allow, review or block, says how much free credit to release, and gives a reason for every decision. It covers email alias families, look-alike and disguised emails, disposable domains, IP bursts, shared devices, and shared cards or phones. Shadow mode scores everyone and refuses nobody, so you can check it on your users first.

How well does it work? One AI agent wrote a synthetic data generator without seeing my rules. I tuned the new rules on an older set, then ran this one once at the end and tuned nothing after. Farmer accounts flagged: 65.0% (52.7 to 79.3 across 20 seeds). Blocked: 43.6%. Honest users blocked: 1.6%, but 20.4% are flagged for review (partial credit). A school behind one IP is 97% reviewed. This is one AI's guess at farmers and honest users, not real traffic.

Limits: rotating proxies (34%), farmers on a shared VPN (32%) and unlisted disposable domains (46.5%) mostly get through. A patient farmer with a fresh inbox, IP and device for every account gets through (0.5% flagged on my older set).

Free: the library and the audit. Not deployed yet: a hosted API with a shared farmer network (a block needs 2 to 3 independent reporting customers). Planned: 1,000 checks a month free, then prepaid at $10 per 10,000. Supabase and Vercel recipes exist, but have not run on a real project, and the npm client is unpublished.

Question: when you run the audit on your own signups, what share did it flag, and which flagged rows were wrong?

https://github.com/tenkyuai/tenkyu
