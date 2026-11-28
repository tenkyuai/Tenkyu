# Proposed top of README (WRITTEN, NOT APPLIED)

The README top has already been updated on night 2 (a "New here? Three ways in" list after the status line). This file is the shorter version Or may prefer. It replaces the title, the one-line promise, the intro paragraph and the status line (README lines 1 to 7). Keep "New here?" and the "First, find out what you already lost" section that follow. TODO(Or): update the version in the status line when the next release is out (`pyproject.toml` still reads 0.2.2; the changelog entry is 0.3.0).

Sources for each claim: the audit and sample command are in README "First, find out what you already lost" and `python -m tenkyu audit --sample`. "Fixed rules, no dependencies, no network calls" is README "Status". The limit sentence is README "Honest limits" and `docs/threat-model-and-attacks.md`, section 3. The hosted API is not deployed and the npm package is unpublished (`docs/threat-model-and-attacks.md`, section 3, item 7; `clients/js/package.json` is version 0.1.0 and no release is recorded).

---

# Tenkyu

**Find out how many of your free AI credits went to farmers, then stop the next ones.**

Tenkyu is a small Python library that scores each signup `allow`, `review` or `block`, says how much of the free credit to give, and tells you the reason for every decision. It runs inside your app, with no dependencies and no network calls, and its audit replays your own signup export offline.

```text
pip install tenkyu
python -m tenkyu audit --sample --grant 3
python -m tenkyu audit signups.csv --grant 3 --out flagged.csv
```

> Status: Early Access (v0.2). Python 3.11 or newer. Fixed rules, not machine learning. It slows lazy farmers and does not stop patient ones. All accuracy figures are on synthetic data. The hosted API and the JS client are not released yet. Read [Honest limits](#honest-limits) and [what was attacked](https://github.com/tenkyuai/tenkyu/blob/main/docs/threat-model-and-attacks.md) before you rely on it.
