"""`tenkyu audit`: replaying a signup export must reproduce the benchmark and never print raw emails."""
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tenkyu.audit import audit_csv, parse_ts
from tenkyu.cli import main

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "benchmarks"))
import farming_bench as fb  # noqa: E402


def _iso(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")


@pytest.fixture(scope="module")
def bench_csv(tmp_path_factory):
    events = fb.generate()
    order = list(range(len(events)))
    import random
    random.Random(3).shuffle(order)  # exports are rarely sorted; audit must sort them
    path = tmp_path_factory.mktemp("a") / "signups.csv"
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["user_id", "Email", "signup_ip", "created_at", "device_id", "user_agent", "plan"])
        for k in order:
            e = events[k]
            w.writerow([f"u{k}", e["email"], e["ip"], _iso(e["ts"]), e["dev"], e["ua"], "free"])
    return path, events, order


def test_audit_reproduces_the_benchmark(bench_csv):
    path, events, order = bench_csv
    res = audit_csv(path)
    by_row = {i: d for i, d in res.decisions}
    tp = fn = legit_blocked = 0
    for row_index, k in enumerate(order):
        e, d = events[k], by_row[row_index]
        if e["label"] == "extra":
            tp += d.action != "allow"
            fn += d.action == "allow"
        if e["label"] == "legit":
            legit_blocked += d.action == "block"
    bench = fb.evaluate()["methods"]["Tenkyu (default)"]["recall"]
    assert abs(tp / (tp + fn) - bench) <= 0.02
    assert legit_blocked == 0


def test_cli_prints_counts_not_emails(bench_csv, capsys, tmp_path):
    path, events, _ = bench_csv
    out_csv = tmp_path / "flagged.csv"
    assert main(["audit", str(path), "--grant", "3", "--out", str(out_csv)]) == 0
    text = capsys.readouterr().out
    assert "Audited" in text and "Flagged for review or block" in text and "would have withheld: $" in text
    assert "@gmail.com" not in text and "@outlook.com" not in text
    rows = list(csv.DictReader(open(out_csv)))
    assert rows and {"tenkyu_action", "tenkyu_score", "tenkyu_reason", "user_id"} <= set(rows[0])
    assert all(r["tenkyu_action"] in ("review", "block") for r in rows)


def test_json_and_missing_columns(tmp_path, capsys):
    p = tmp_path / "s.csv"
    p.write_text("email,timestamp\njohn@gmail.com,1700000000\nj.o.h.n+2@gmail.com,1700000060\nbad@x.com,\nann@outlook.com,2023-11-14T22:15:00Z\n")
    assert main(["audit", str(p), "--json"]) == 0
    s = json.loads(capsys.readouterr().out)
    assert (s["signups"], s["block"], s["unreadable_rows"]) == (3, 1, 1)
    assert set(s["missing"]) == {"user agent", "ip", "device id"}


def test_link_column_joins_accounts(tmp_path, capsys):
    p = tmp_path / "s.csv"
    p.write_text("email,created_at,card_fp\nann@outlook.com,1700000000,fp1\nbob@proton.me,1700009000,fp1\ncat@fastmail.com,1700019000,\n")
    assert main(["audit", str(p), "--link", "card=card_fp", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["block"] == 1


@pytest.mark.parametrize("content,msg", [("name,ip\nx,1.2.3.4\n", "need an email column"), ("", "no header")])
def test_bad_files_exit_2(tmp_path, capsys, content, msg):
    p = tmp_path / "s.csv"
    p.write_text(content)
    assert main(["audit", str(p)]) == 2
    assert msg in capsys.readouterr().err


def test_bad_link_spec_and_missing_file(tmp_path, capsys):
    p = tmp_path / "s.csv"
    p.write_text("email,created_at\na@b.com,1700000000\n")
    assert main(["audit", str(p), "--link", "card"]) == 2
    assert main(["audit", str(p), "--link", "card=nope"]) == 2
    assert main(["audit", str(tmp_path / "missing.csv")]) == 3


@pytest.mark.parametrize("raw,expected", [("1700000000", 1700000000.0), ("1700000000000", 1700000000.0), ("2023-11-14T22:13:20Z", 1700000000.0),
                                          ("2023-11-14 22:13:20", 1700000000.0), ("2023-11-15T00:13:20+02:00", 1700000000.0), ("", None), ("soon", None), ("nan", None)])
def test_parse_ts(raw, expected):
    assert parse_ts(raw) == expected


def test_sample_runs_and_readme_shows_its_real_output(capsys):
    assert main(["audit", "--sample", "--grant", "3"]) == 0
    out = capsys.readouterr().out
    assert "bundled sample" in out and "Audited 939 signups" in out
    readme = (Path(__file__).resolve().parent.parent / "README.md").read_text()
    assert "$ python -m tenkyu audit --sample --grant 3\n" + out in readme


def test_no_csv_and_no_sample_exits_2(capsys):
    assert main(["audit"]) == 2


def test_short_dev_ua_columns_and_unused_warning(tmp_path, capsys):
    p = tmp_path / "s.csv"
    p.write_text("email,ts,dev,ua,plan\na@b.com,1700000000,d1,curl/8,free\n")
    assert main(["audit", str(p), "--json"]) == 0
    s = json.loads(capsys.readouterr().out)
    assert s["missing"] == ["ip"] and s["unused_columns"] == ["plan"]
    assert main(["audit", str(p)]) == 0
    assert "Columns not used: plan" in capsys.readouterr().out


def test_sample_json_is_pure_json(capsys):
    # Regression (2026-11-26): the sample notice was printed before the JSON and broke piping.
    assert main(["audit", "--sample", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["signups"] == 939


def test_sample_shows_money_by_default_but_real_audit_does_not(capsys, tmp_path):
    assert main(["audit", "--sample"]) == 0
    assert "would have withheld: $" in capsys.readouterr().out
    p = tmp_path / "s.csv"
    p.write_text("email,created_at\na@b.com,1700000000\n")
    assert main(["audit", str(p)]) == 0
    assert "$" not in capsys.readouterr().out
