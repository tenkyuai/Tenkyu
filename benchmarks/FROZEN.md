# Frozen detector for the held-out benchmark

Recorded on 2026-11-26 at 23:07 Israel time, **before** any held-out data existed.

- Code: commit `cbefd2f` on branch `hosted-v1` (Tenkyu 0.2.2). Nothing under `src/` changes during this evaluation.
- Configurations evaluated, unchanged from the library defaults:
  - `Guard()`: allow below 0.35, review 0.35 to 0.7, block at 0.7 and above; credit_fraction 1 / 0.25 / 0.
  - `Guard.for_ai_apps()`: same thresholds, starter_fraction 0.5, surge_threshold 25.
- A signup counts as **flagged** when the action is review or block.
- Dollars use an example $3 grant per signup times credit_fraction.
- The held-out data generator (`heldout_gen.py`) was written by a separate AI agent, not a separate human author. It was
  not shown `src/tenkyu/`, the old benchmark, or the bundled disposable-domain list; it got only a plain description
  of farming tactics and honest-user behaviour, and it uses its own disposable domains. 18 of its 35 disposable
  domains (the well-known ones) also appear in the bundled list. On the 17 that do not, `Guard()` disposable recall is
  65% (46 to 81); on the 18 that do, 100%.
- Results are reported as the mean over 20 seeds. The bracketed range is the 2.5th to 97.5th percentile across 20
  simulated datasets from one generator. It is not a confidence interval for real traffic.
- The test passes phone and card as links on only part of the rows. The `card_or_phone_reuse` tactic relies on them, and
  a default `Guard()` without links will not catch it.
- Farmers are about 25% of accounts, which affects precision.

During the evaluation the only change under `src/` was the new `tenkyu evaluate` command
(`src/tenkyu/evaluate.py` and its CLI wiring), which replays signups and does not change any detection rule.
Any future tuning must not use these seeds to pick rules: write a new held-out generator or use real labelled data.
