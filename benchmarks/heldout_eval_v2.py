"""Frozen v2 held-out test: seeds 101-120 of heldout_gen_v2.py (written blind, never used to choose rules).

Run once per detector version:  PYTHONPATH=src python benchmarks/heldout_eval_v2.py [--json]
Reports Guard() defaults: mean and 2.5th-97.5th percentile across the 20 seeds (spread of the generator, not a
confidence interval for real traffic)."""
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from heldout_gen_v2 import generate  # noqa: E402
from tenkyu.evaluate import evaluate_rows  # noqa: E402

SEEDS = range(101, 121)


def _pct(xs, q):
    xs = sorted(xs)
    k = (len(xs) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def summ(xs):
    xs = [x for x in xs if x is not None]
    return {"mean": statistics.fmean(xs), "lo": _pct(xs, 0.025), "hi": _pct(xs, 0.975)} if xs else None


def run():
    per, tactics = [], {}
    for seed in SEEDS:
        r = evaluate_rows(generate(seed), preset="default", grant=3.0)
        per.append({"farmers_flagged": r["recall"], "farmers_blocked": r["blocked_only"]["recall"],
                    "honest_flagged": r["honest_flagged_rate"], "honest_blocked": r["blocked_only"]["honest_flagged_rate"]})
        for name, g in (r.get("tactics") or {}).items():
            rate = g.get("recall") if g.get("farmers") else g.get("false_positive_rate")
            tactics.setdefault(name, []).append(rate)
    out = {k: summ([p[k] for p in per]) for k in per[0]}
    out["groups"] = {k: summ(v) for k, v in sorted(tactics.items())}
    return out


def fmt(s):
    return "n/a" if s is None else f"{100 * s['mean']:.1f}% ({100 * s['lo']:.1f} to {100 * s['hi']:.1f})"


if __name__ == "__main__":
    res = run()
    if "--json" in sys.argv:
        print(json.dumps(res, indent=2, sort_keys=True))
    else:
        for k in ("farmers_flagged", "farmers_blocked", "honest_flagged", "honest_blocked"):
            print(f"| {k} | {fmt(res[k])} |")
        for k, v in res["groups"].items():
            print(f"| {k} | {fmt(v)} |")
