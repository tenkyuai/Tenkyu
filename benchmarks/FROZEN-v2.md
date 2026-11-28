# Frozen detector for the v2 held-out test

- Detector frozen at commit 01e2f0c (branch night-2; 1b00174 is the commit that records this freeze and has the same detector code), 2026-11-27 21:07 Israel time, before any Phase 9 detection change.
- Night-1 held-out results at this commit: see README "Held-out test" and docs/heldout-benchmark.md (seeds 1-20 of benchmarks/heldout_gen.py).
- Rules for Phase 9: new detection work is tuned ONLY on heldout_gen.py seeds 1-20 plus unit cases. benchmarks/heldout_gen_v2.py seeds 101-120 are run once, after the rules are final, and never used to pick rules. If v2 disagrees, it is reported as is.

## Result (run once, 2026-11-27 21:55 Israel time)

Seeds 101-120 of heldout_gen_v2.py, Guard() defaults. Before = frozen commit 1b00174, after = 9d7ba5b. Mean (2.5th to 97.5th percentile across seeds). Group rows: farmer tactics = share flagged; honest groups = share flagged.

| Metric / group | Before (1b00174) | After (9d7ba5b) |
|---|---|---|
| farmers_flagged | 52.8% (40.1 to 67.2) | 65.0% (52.7 to 79.3) 
| farmers_blocked | 24.6% (13.2 to 36.4) | 43.6% (27.7 to 59.9) 
| honest_flagged | 20.4% (19.0 to 22.7) | 20.4% (19.0 to 22.7) 
| honest_blocked | 12.5% (11.6 to 13.2) | 1.6% (1.0 to 2.1) 
| burst | 59.2% (0.0 to 93.6) | 76.5% (32.7 to 100.0) 
| cgnat | 59.9% (46.2 to 72.6) | 59.9% (46.2 to 72.6) 
| cgnat_v6 | 1.5% (0.0 to 7.7) | 1.5% (0.0 to 7.7) 
| company | 3.9% (0.0 to 8.3) | 3.9% (0.0 to 8.3) 
| device_churn | 0.1% (0.0 to 1.5) | 0.1% (0.0 to 1.5) 
| device_reuse | 80.8% (61.7 to 91.5) | 80.8% (61.7 to 91.5) 
| disposable | 45.8% (0.0 to 95.2) | 46.5% (0.0 to 95.2) 
| family | 66.8% (62.6 to 71.2) | 66.8% (62.6 to 71.2) 
| intl | 0.0% (0.0 to 0.0) | 0.0% (0.0 to 0.0) 
| launch_spike | 0.1% (0.0 to 1.0) | 0.1% (0.0 to 1.0) 
| lookalike_domain | 38.6% (0.0 to 79.3) | 87.0% (57.3 to 100.0) 
| organic | 0.1% (0.0 to 1.1) | 0.1% (0.0 to 1.1) 
| phone_card_reuse | 69.0% (43.5 to 89.5) | 69.4% (47.6 to 89.5) 
| plus_dot | 80.9% (60.5 to 91.7) | 80.9% (60.5 to 91.7) 
| proxy_rotate | 34.1% (0.0 to 92.1) | 34.1% (0.0 to 92.1) 
| school | 96.7% (96.7 to 96.7) | 96.7% (96.7 to 96.7) 
| slow_drip | 68.9% (0.0 to 91.7) | 68.9% (0.0 to 91.7) 
| unicode_local | 51.8% (0.0 to 77.5) | 99.1% (95.0 to 100.0) 
| university | 49.9% (35.4 to 63.8) | 49.9% (35.4 to 63.8) 
| vpn_shared | 31.9% (5.9 to 81.7) | 31.9% (5.9 to 81.7) 
| vpn_user | 0.2% (0.0 to 1.8) | 0.2% (0.0 to 1.8) 
| work_personal | 33.8% (26.7 to 40.0) | 33.8% (26.7 to 40.0) 

Read honestly: honest users blocked fell from 12.5% to 1.6% (schools, carrier NAT, universities now get partial credit instead of a block), but honest users flagged (review = partial credit) is unchanged at 20.4%: a school behind one IP is 97% reviewed. Proxy rotation (34%), shared-VPN farmers (32%) and unlisted disposable domains (46%) did not improve. Nothing was tuned after this run.
