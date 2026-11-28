"""Held-out benchmark: run the frozen detector (see FROZEN.md) on data from heldout_gen.py, which was written
without access to the detector. Reports mean and a 95% interval (2.5th to 97.5th percentile) over seeds.

Run:  PYTHONPATH=src python benchmarks/heldout_eval.py [--seeds 20] [--write]
"""
import json
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))
sys.path.insert(0, HERE)

from heldout_gen import DISPOSABLE as GEN_DISPOSABLE, generate  # noqa: E402
from tenkyu.signals import DISPOSABLE_DOMAINS, _domain_listed  # noqa: E402
from tenkyu.evaluate import evaluate_rows  # noqa: E402

PRESETS = {"default": "Guard()", "ai": "Guard.for_ai_apps()"}


def _pct(xs, q):
    xs = sorted(xs)
    k = (len(xs) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def summarize(xs):
    xs = [x for x in xs if x is not None]
    if not xs:
        return None
    return {"mean": statistics.fmean(xs), "lo": _pct(xs, 0.025), "hi": _pct(xs, 0.975)}


def _listed(email):
    return _domain_listed(email.rsplit("@", 1)[-1].strip().lower(), DISPOSABLE_DOMAINS)


def disposable_split(seeds):
    """Guard() recall on the generator's `disposable` farmers, split by whether the bundled list knows the domain.
    Only the tactic label is changed (the detector never sees it), so the replay is identical to the main run."""
    gen_domains = sorted(set(GEN_DISPOSABLE))
    overlap = [d for d in gen_domains if _domain_listed(d, DISPOSABLE_DOMAINS)]
    per = {"listed": [], "unlisted": []}
    counts = {"listed": [], "unlisted": []}
    for seed in range(1, seeds + 1):
        rows = [dict(r) for r in generate(seed)]
        for r in rows:
            if r["tactic"] == "disposable":
                r["tactic"] = "disposable_listed" if _listed(r["email"]) else "disposable_unlisted"
        t = evaluate_rows(rows, preset="default", grant=3.0)["tactics"]
        for k in per:
            g = t.get(f"disposable_{k}")
            if g and g["farmers"]:
                per[k].append(g["recall"])
                counts[k].append(g["farmers"])
    return {"generator_domains": len(gen_domains), "overlapping_domains": len(overlap),
            "overlap_list": overlap,
            **{f"recall_{k}": summarize(v) for k, v in per.items()},
            **{f"rows_per_seed_{k}": statistics.fmean(v) if v else 0 for k, v in counts.items()}}


def run(seeds=20):
    out = {}
    for preset in PRESETS:
        per = []
        for seed in range(1, seeds + 1):
            rows = generate(seed)
            r = evaluate_rows(rows, preset=preset, grant=3.0)
            n = r["rows_used"]
            per.append({
                "precision": r["precision"], "recall": r["recall"], "honest_flagged_rate": r["honest_flagged_rate"],
                "blocked_precision": r["blocked_only"]["precision"], "blocked_recall": r["blocked_only"]["recall"],
                "honest_blocked_rate": r["blocked_only"]["honest_flagged_rate"],
                "usd_farmers_per_1000": 1000 * r["usd_withheld_from_farmers"] / n,
                "usd_real_per_1000": 1000 * r["usd_withheld_from_real"] / n,
                "tactics": r.get("tactics", {}),
            })
        agg = {k: summarize([p[k] for p in per]) for k in per[0] if k != "tactics"}
        tactics = sorted({t for p in per for t in p["tactics"]})
        agg["tactics"] = {}
        for t in tactics:
            vals = [p["tactics"].get(t) for p in per if t in p["tactics"]]
            key = "recall" if t.split("_")[0] != "honest" else "false_positive_rate"
            agg["tactics"][t] = {key: summarize([v.get(key) for v in vals]),
                                 "rows_per_seed": statistics.fmean((v["farmers"] or 0) + (v["real"] or 0) for v in vals)}
        out[preset] = agg
    return {"seeds": seeds, "results": out, "disposable_split": disposable_split(seeds)}


def _f(s, pct=True):
    if s is None:
        return "n/a"
    if pct:
        return f"{100 * s['mean']:.1f}% ({100 * s['lo']:.1f} to {100 * s['hi']:.1f})"
    return f"${s['mean']:.0f} (${s['lo']:.0f} to ${s['hi']:.0f})"


def markdown(res):
    lines = [f"Frozen detector, {res['seeds']} seeds of held-out data, mean with 95% interval. "
             "Flagged = review or block. Dollars at an example $3 grant, per 1,000 signups.", "",
             "| Metric | Guard() | Guard.for_ai_apps() |", "|---|---|---|"]
    names = [("precision", "Precision (flagged)", True), ("recall", "Farmer accounts flagged", True),
             ("honest_flagged_rate", "Honest users flagged", True), ("blocked_recall", "Farmer accounts blocked", True),
             ("honest_blocked_rate", "Honest users blocked", True),
             ("usd_farmers_per_1000", "Withheld from farmers", False), ("usd_real_per_1000", "Withheld from honest users", False)]
    for k, label, pct in names:
        lines.append(f"| {label} | {_f(res['results']['default'][k], pct)} | {_f(res['results']['ai'][k], pct)} |")
    lines += ["", "By group (Guard()): farmers flagged for each tactic, honest users flagged for each honest group.", "",
              "| Group | Rate | Rows per seed |", "|---|---|---|"]
    for t, v in res["results"]["default"]["tactics"].items():
        s = v.get("recall") or v.get("false_positive_rate")
        lines.append(f"| {t} | {_f(s)} | {v['rows_per_seed']:.0f} |")
    d = res["disposable_split"]
    lines += ["", f"Disposable farmers split by whether the bundled disposable list knows the domain (Guard()). "
              f"{d['overlapping_domains']} of the generator's {d['generator_domains']} disposable domains are in the bundled list.", "",
              "| Disposable farmer rows | Recall | Rows per seed |", "|---|---|---|",
              f"| Domain in bundled list | {_f(d['recall_listed'])} | {d['rows_per_seed_listed']:.0f} |",
              f"| Domain NOT in bundled list | {_f(d['recall_unlisted'])} | {d['rows_per_seed_unlisted']:.0f} |"]
    return "\n".join(lines)


if __name__ == "__main__":
    seeds = int(sys.argv[sys.argv.index("--seeds") + 1]) if "--seeds" in sys.argv else 20
    res = run(seeds)
    md = markdown(res)
    print(md)
    if "--write" in sys.argv:
        with open(os.path.join(HERE, "..", "docs", "heldout-benchmark.json"), "w") as f:
            json.dump(res, f, indent=1)
        with open(os.path.join(HERE, "..", "docs", "heldout-benchmark.md"), "w") as f:
            f.write("# Held-out benchmark\n\nSee benchmarks/FROZEN.md for how this was set up.\n\n" + md + "\n")
