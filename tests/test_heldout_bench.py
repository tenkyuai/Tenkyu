import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "benchmarks"))

from heldout_gen import generate  # noqa: E402
from tenkyu.evaluate import evaluate_rows  # noqa: E402


def test_generator_is_deterministic_and_labelled():
    a, b = generate(5), generate(5)
    assert a == b
    assert {r["label"] for r in a} == {"real", "farmer"}
    assert [r["ts"] for r in a] == sorted(r["ts"] for r in a)


def test_evaluator_runs_on_heldout_seed():
    r = evaluate_rows(generate(1))
    assert r["rows_used"] == r["rows_total"] > 0
    assert 0 <= r["honest_flagged_rate"] <= 1 and 0 <= r["recall"] <= 1
