import math

import pytest

from tenkyu import ALLOW, Guard, Signal, Signup

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/120.0 Safari/537.36"


def check(*signals):
    return Guard(extra_signals=list(signals)).check(Signup(email="a@acme.io", ip="203.0.113.1", user_agent=UA))


@pytest.mark.parametrize("junk", ["oops", ("x", 0.9, "r"), {"score": 0.9}, [], 7, True, object()])
def test_non_signal_results_are_ignored(junk):
    d = check(lambda s, st, n: junk)
    assert d.action == ALLOW and d.signals == []


@pytest.mark.parametrize("score", ["0.9", None, [0.9], True])
def test_non_numeric_scores_are_ignored(score):
    d = check(lambda s, st, n: Signal("x", score, "r"))
    assert d.action == ALLOW and d.signals == []


def test_nan_score_counts_as_zero_and_inf_is_clamped():
    nan = check(lambda s, st, n: Signal("x", float("nan"), "r"))
    assert nan.score == 0.0 and math.isfinite(nan.score)
    inf = check(lambda s, st, n: Signal("x", float("inf"), "r"))
    assert inf.score == 1.0


def test_missing_reason_falls_back_to_the_signal_name():
    d = check(lambda s, st, n: Signal("odd_one", 0.5, None))
    assert d.reasons == ["odd_one"]


def test_good_signal_still_works_next_to_a_bad_one():
    def bad(s, st, n):
        raise RuntimeError("boom")

    d = check(bad, lambda s, st, n: "junk", lambda s, st, n: Signal("ok", 0.8, "custom reason"))
    assert d.reasons == ["custom reason"] and d.action == "block"
