import importlib.util
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("farming_bench", ROOT / "benchmarks" / "farming_bench.py")
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)

RES = bench.evaluate()
TENKYU = RES["methods"]["Tenkyu (default)"]


def test_generation_is_deterministic():
    assert bench.generate() == bench.generate()


def test_dataset_shape():
    ev = bench.generate()
    assert RES["events"] == len(ev) == RES["legit_users"] + RES["extra_accounts"] + RES["farm_operators"]
    assert RES["legit_users"] == 2000 and RES["farm_operators"] == 200
    assert [e["ts"] for e in ev] == sorted(e["ts"] for e in ev)


def test_tenkyu_beats_both_naive_baselines_on_f1():
    others = [m["f1"] for n, m in RES["methods"].items() if n != "Tenkyu (default)"]
    assert TENKYU["f1"] > max(others) + 0.2


def test_honesty_hard_strategy_is_mostly_undetected():
    assert RES["recall_by_strategy"]["Tenkyu (default)"]["residential"] < 0.2


def test_easy_strategies_are_caught():
    r = RES["recall_by_strategy"]["Tenkyu (default)"]
    assert r["alias_gmail"] > 0.9 and r["bot_burst"] > 0.9 and r["disposable"] > 0.9


def test_real_users_rarely_flagged_and_never_blocked():
    assert TENKYU["legit_false_positive_rate"] < 0.05
    assert RES["tenkyu_blocked_legit"] == 0


def test_committed_json_matches_fresh_run():
    committed = json.loads((ROOT / "docs" / "benchmark.json").read_text())
    assert committed == json.loads(json.dumps(RES))
    assert committed["f1"] == TENKYU["f1"]


def test_readme_table_matches_fresh_run():
    assert bench.markdown_table(RES) in (ROOT / "README.md").read_text()


def test_readme_states_synthetic_caveat():
    text = (ROOT / "README.md").read_text()
    assert "synthetic" in text and "not** a measurement of real-world accuracy" in text


ECON = bench.economics()


def test_economics_default_matches_flags_story():
    d = ECON["Tenkyu (default)"]
    assert d["farmer_grants_by_strategy_pct"]["residential"] == 100.0  # honest: default does nothing for residential
    assert d["honest_avg_grant_pct"] > 95


def test_ai_app_preset_halves_residential_and_says_what_it_costs():
    p = ECON["Guard.for_ai_apps()"]
    assert p["farmer_grants_by_strategy_pct"]["residential"] <= 50
    assert p["farmer_grants_pct"] < ECON["Tenkyu (default)"]["farmer_grants_pct"]
    assert p["honest_full_grant_pct"] == 0  # every unverified honest user starts at half: the documented trade-off


def test_card_link_closes_residential_under_its_assumption():
    c = ECON["Guard.for_ai_apps() + card link"]
    assert c["farmer_grants_by_strategy_pct"]["residential"] == 0 and c["honest_avg_grant_pct"] > 95


def test_committed_economics_json_matches_fresh_run():
    assert json.loads((ROOT / "docs" / "benchmark-economics.json").read_text()) == json.loads(json.dumps(ECON))


def test_readme_economics_table_matches_fresh_run():
    assert bench.economics_table(ECON) in (ROOT / "README.md").read_text()
