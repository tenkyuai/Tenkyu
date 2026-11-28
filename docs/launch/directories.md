# Directory listings (WRITTEN, NOT SUBMITTED)

Three short versions for developer tool directories. Each is under 90 words. Check each directory's rules and category names yourself before you submit. Submitting is TODO(Or). Sources: the signal list is README "What it checks"; "fixed rules, no dependencies, no network calls" is README "Status"; the limit sentence is README "Honest limits". The hosted API is not deployed and the JS client is not on npm, so no entry below lists them as available. Submit only after the release that contains the signals named here (0.3.0 in the changelog) is on PyPI.

## 1. The library

**Tenkyu**: signup scoring for free-credit abuse.
A Python library that scores each signup allow, review or block, says how much free credit to release, and explains every decision. It catches email alias families, look-alike and disguised emails, disposable domains, IP bursts, shared devices and reused cards. MIT, no dependencies, no network calls, fixed rules. Early Access. It slows lazy farmers and does not stop patient ones.
Tags: python, abuse prevention, free tier, open source.

## 2. The audit

**Tenkyu audit**: see what farmers took from your free tier.
Export your signups to CSV and run one command. It replays them offline in time order and reports how many it would have flagged and how much free credit it would have withheld. A bundled sample shows the output first. Nothing leaves your machine. Free and open source (MIT, Python 3.11+).
Tags: python, cli, fraud, free tier.

## 3. Measure and shadow mode

**Tenkyu evaluate and shadow mode**: test the rules on your own users before you enforce them.
Add a label column to a past export and `tenkyu evaluate` prints precision, recall and honest users wrongly flagged. Shadow mode logs what Tenkyu would have done while everyone still gets the full grant, and `tenkyu shadow-report` scores that log against your labels. Open source, offline, Python 3.11+. Benchmarks are synthetic until you run it on your data.
Tags: python, evaluation, abuse prevention.
