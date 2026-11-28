"""How sensitive are the benchmark numbers to our own choices? Same synthetic generator as farming_bench.py (so the same caveat applies:
the data is ours, not real). Variants: devices cleared on every signup, and a harder farmer mix (50% residential instead of 15%).

Run:  PYTHONPATH=src python benchmarks/sensitivity.py"""
import sys, pathlib
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(ROOT / "benchmarks"))
import farming_bench as fb

def run(label, strategies=None, clear_devices=False):
    if strategies: fb.STRATEGIES = strategies
    ev = fb.generate()
    if clear_devices:
        for i, e in enumerate(ev): e["dev"] = f"fresh{i}"      # cookie cleared on every signup
    r = fb.evaluate(ev)
    m = r["methods"]["Tenkyu (default)"]; base = r["methods"]["email dedupe + disposable list"]
    rec = r["recall_by_strategy"]["Tenkyu (default)"]
    print(f"{label:34s} F1 {m['f1']:.3f} P {m['precision']:.3f} R {m['recall']:.3f} legit-flagged {m['legit_false_positive_rate']*100:.1f}% legit-blocked {r['tenkyu_blocked_legit']} | baseline F1 {base['f1']:.3f} | per-strategy {rec}")
orig = list(fb.STRATEGIES)
run("A default", list(orig))
run("B device cleared every signup", list(orig), clear_devices=True)
run("C hard mix (50% residential)", [("alias_gmail",.15),("disposable",.10),("lookalike",.15),("residential",.50),("bot_burst",.10)])
run("D hard mix + cleared devices", [("alias_gmail",.15),("disposable",.10),("lookalike",.15),("residential",.50),("bot_burst",.10)], clear_devices=True)
