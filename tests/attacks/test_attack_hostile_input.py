"""Attack: make check() raise, hang or eat memory with crafted input. It must answer, and never silently allow a lock failure."""
import sys

import pytest

from tenkyu import ALLOW, Guard, SQLiteStore, Signal, Signup

from attack_helpers import T0, UA


def test_huge_device_ids_are_stored_as_short_hashes(make_guard, signup):
    g = make_guard()
    d1 = g.check(signup("a@outlook.com", "84.1.1.1", "x" * 100_000))
    d2 = g.check(signup("b@outlook.com", "84.2.2.2", "x" * 100_000, ts=60))
    assert d1.action == ALLOW
    assert "device_reuse" in {s.name for s in d2.signals if s.score > 0}  # equal long values still match


def test_lone_surrogates_never_raise_with_sqlite(tmp_path):
    g = Guard(store=SQLiteStore(str(tmp_path / "t.db")), clock=lambda: T0)
    d = g.check(Signup(email="a@outlook.com", ip="84.1.1.1\ud800", device_id="dev\udc00", user_agent=UA, ts=T0))
    assert d.action in ("allow", "review", "block")


def test_a_custom_signal_that_raises_is_ignored(make_guard, signup):
    def boom(signup_, store, now):
        raise RuntimeError("hostile")

    def junk(signup_, store, now):
        return "not a signal"

    d = make_guard(extra_signals=[boom, junk]).check(signup("a@outlook.com", "84.1.1.1", "d1"))
    assert d.action == ALLOW


def test_a_custom_signal_with_a_soft_looking_name_is_still_a_real_risk_signal(make_guard, signup):
    soft_name = lambda s, st, now: Signal("device_reuse", 0.9, "custom rule")  # noqa: E731
    d = make_guard(extra_signals=[soft_name]).check(signup("a@outlook.com", "84.1.1.1", "d1"))
    assert d.action == "block"


def test_record_false_is_a_dry_run(make_guard, signup):
    g = make_guard()
    g.check(signup("john@gmail.com"), record=False)
    assert g.check(signup("j.o.h.n+x@gmail.com", "84.2.2.2", "d2", ts=60)).action == ALLOW  # john was never remembered
