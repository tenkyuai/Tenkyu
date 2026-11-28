# Held-out benchmark

This is now a TUNING set, not a fair test. The generator (benchmarks/heldout_gen.py, seeds 1-20) was written without access to the detector and the detector was frozen at commit 01e2f0c (see benchmarks/FROZEN.md). The first version of this table, from that frozen detector, was the fair result. Detection work on night 2 was then tuned on these same seeds, so the numbers below are no longer an unseen-data estimate. All data is synthetic. For an unseen-data result, see "Fair test: blind v2 set" below and benchmarks/FROZEN-v2.md.

Frozen detector (01e2f0c), before night-2 tuning, for comparison: farmer accounts flagged 70.2%, blocked 38.5%; honest flagged 4.6%, blocked 1.4%; unicode_trick flagged 37.8%.

Frozen detector, 20 seeds of held-out data, mean with 95% interval. Flagged = review or block. Dollars at an example $3 grant, per 1,000 signups.

| Metric | Guard() | Guard.for_ai_apps() |
|---|---|---|
| Precision (flagged) | 84.8% (82.1 to 87.1) | 84.8% (82.1 to 87.1) |
| Farmer accounts flagged | 77.7% (68.9 to 85.1) | 77.7% (68.9 to 85.1) |
| Honest users flagged | 4.6% (3.9 to 5.4) | 4.6% (3.9 to 5.4) |
| Farmer accounts blocked | 54.9% (42.5 to 66.2) | 54.9% (42.5 to 66.2) |
| Honest users blocked | 1.2% (0.7 to 2.0) | 1.2% (0.7 to 2.0) |
| Withheld from farmers | $541 ($477 to $598) | $605 ($567 to $644) |
| Withheld from honest users | $85 ($71 to $98) | $755 ($724 to $773) |

By group (Guard()): farmers flagged for each tactic, honest users flagged for each honest group.

| Group | Rate | Rows per seed |
|---|---|---|
| card_or_phone_reuse | 83.3% (60.3 to 93.1) | 26 |
| datacenter_bot | 90.2% (46.3 to 100.0) | 27 |
| device_reuse | 90.2% (82.5 to 94.5) | 36 |
| disposable | 83.1% (65.2 to 92.0) | 46 |
| honest_campus | 1.1% (0.0 to 6.4) | 80 |
| honest_cgnat | 11.2% (6.7 to 16.2) | 96 |
| honest_family | 59.7% (55.0 to 64.7) | 25 |
| honest_intl | 0.0% (0.0 to 0.0) | 9 |
| honest_ipv6 | 0.3% (0.0 to 2.6) | 34 |
| honest_launch | 0.1% (0.0 to 0.7) | 140 |
| honest_plus | 3.2% (0.0 to 11.5) | 19 |
| honest_regular | 0.5% (0.0 to 0.9) | 358 |
| honest_vpn | 1.0% (0.0 to 4.2) | 24 |
| honest_work_personal | 50.0% (50.0 to 50.0) | 16 |
| lazy_alias | 91.7% (83.7 to 97.1) | 42 |
| patient_residential | 0.5% (0.0 to 5.2) | 16 |
| rotating_proxy | 46.2% (18.2 to 72.4) | 41 |
| unicode_trick | 95.7% (84.0 to 100.0) | 34 |

Disposable farmers split by whether the bundled disposable list knows the domain (Guard()). 18 of the generator's 35 disposable domains are in the bundled list.

| Disposable farmer rows | Recall | Rows per seed |
|---|---|---|
| Domain in bundled list | 100.0% (100.0 to 100.0) | 23 |
| Domain NOT in bundled list | 65.5% (46.3 to 81.0) | 22 |


## Fair test: blind v2 set

Generator: benchmarks/heldout_gen_v2.py, written by a separate agent that never saw the detection rules. Seeds 101-120 were run once, after the rules were frozen, and were not used to pick rules. All data is synthetic. Reproduce: `PYTHONPATH=src python benchmarks/heldout_eval_v2.py`. Raw numbers: docs/heldout-v2.json and benchmarks/FROZEN-v2.md.

Guard() defaults, mean (2.5th to 97.5th percentile across seeds). Before = commit 1b00174, after = 9d7ba5b.

| Metric / group | Before | After |
|---|---|---|
| Farmer accounts flagged | 52.8% (40.1 to 67.2) | 65.0% (52.7 to 79.3) |
| Farmer accounts blocked | 24.6% (13.2 to 36.4) | 43.6% (27.7 to 59.9) |
| Honest users flagged | 20.4% (19.0 to 22.7) | 20.4% (19.0 to 22.7) |
| Honest users blocked | 12.5% (11.6 to 13.2) | 1.6% (1.0 to 2.1) |
| lookalike_domain (farmers) | 38.6% (0.0 to 79.3) | 87.0% (57.3 to 100.0) |
| unicode_local (farmers) | 51.8% (0.0 to 77.5) | 99.1% (95.0 to 100.0) |
| burst (farmers) | 59.2% (0.0 to 93.6) | 76.5% (32.7 to 100.0) |
| proxy_rotate (farmers) | 34.1% (0.0 to 92.1) | 34.1% (0.0 to 92.1) |
| vpn_shared (farmers) | 31.9% (5.9 to 81.7) | 31.9% (5.9 to 81.7) |
| disposable (farmers) | 45.8% (0.0 to 95.2) | 46.5% (0.0 to 95.2) |
| school (honest) | 96.7% | 96.7% |
| cgnat (honest) | 59.9% (46.2 to 72.6) | 59.9% (46.2 to 72.6) |
| family (honest) | 66.8% (62.6 to 71.2) | 66.8% (62.6 to 71.2) |

Read honestly: honest users blocked fell from 12.5% to 1.6%, because schools, carrier NAT and universities now get partial credit (review) instead of a block. Honest users flagged is unchanged at 20.4%: that is partial credit, not a block, and a school behind one IP is 97% reviewed. Farmers blocked rose from 24.6% to 43.6%, mostly from look-alike and disguised emails. Proxy rotation (34%), shared-VPN farmers (32%) and unlisted disposable domains (46%) did not improve. Nothing was tuned after this run.
