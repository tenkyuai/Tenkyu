"""`tenkyu evaluate`: labelled replay gives honest accuracy numbers, and the API matches the CSV path."""
import csv
import json

import pytest

from tenkyu.cli import main
from tenkyu.evaluate import evaluate_csv, evaluate_rows, parse_label

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/120.0 Safari/537.36"
T0 = 1_700_000_000
HEADER = ["email", "ip", "device_id", "user_agent", "ts", "label", "tactic"]


def _rows():
    """Two honest users, then an alias farmer (3 signups of one inbox) and a bot burst; deliberately out of time order."""
    rows = [
        dict(email="ana@acme.io", ip="203.0.113.10", device_id="d-ana", user_agent=UA, ts=T0, label="real", tactic=""),
        dict(email="bo@acme.io", ip="203.0.113.11", device_id="d-bo", user_agent=UA, ts=T0 + 50, label="real", tactic=""),
    ]
    for i, e in enumerate(["john@gmail.com", "j.o.h.n+free1@gmail.com", "john+free2@googlemail.com"]):
        rows.append(dict(email=e, ip="198.51.100.20", device_id="d-john", user_agent=UA, ts=T0 + 100 + i * 90,
                         label="farmer", tactic="alias"))
    for i in range(4):
        rows.append(dict(email=f"bot{i}@acme.io", ip="192.0.2.77", device_id=f"d-bot{i}", user_agent="python-requests/2.31",
                         ts=T0 + 600 + i * 2, label="farmer", tactic="bot"))
    return rows


def _write(path, rows, header=HEADER):
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=header, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return path


@pytest.fixture
def csv_path(tmp_path):
    rows = _rows()
    return _write(tmp_path / "s.csv", rows[::-1])  # reversed: replay must sort by ts


def test_label_spellings():
    for v in ("farmer", "FARMER", " 1 ", "True", "YES", "Fraud", "1.0"):
        assert parse_label(v) is True, v
    for v in ("real", "Real", "0", "FALSE", "no", "Legit", "0.0"):
        assert parse_label(v) is False, v
    for v in ("", None, "maybe", "2"):
        assert parse_label(v) is None, v


def test_counts_and_metrics(csv_path):
    r = evaluate_csv(csv_path)
    assert r["rows_used"] == 9 and r["rows_skipped"] == 0
    assert (r["farmers"], r["real"]) == (7, 2)
    assert r["honest_flagged"] == 0 and r["honest_flagged_rate"] == 0.0
    assert r["precision"] == 1.0
    assert 0 < r["recall"] <= 1
    assert r["flagged"] >= r["blocked"] > 0
    assert r["usd_withheld_from_real"] == 0.0
    assert 0 < r["usd_withheld_from_farmers"] <= 7 * 3


def test_label_spellings_in_csv_match(tmp_path):
    base = evaluate_rows(_rows())
    alt = []
    for row in _rows():
        row = dict(row)
        row["label"] = {"farmer": "1", "real": "0"}[row["label"]]
        alt.append(row)
    assert evaluate_csv(_write(tmp_path / "a.csv", alt)) == base
    for row in alt:
        row["label"] = {"1": "Fraud", "0": "LEGIT"}[row["label"]]
    assert evaluate_rows(alt) == base


def test_unreadable_rows_are_skipped_with_reasons(tmp_path):
    rows = _rows() + [dict(email="x@acme.io", ts=T0, label="maybe"), dict(email="y@acme.io", ts="not a time", label="real"),
                      dict(email="z@acme.io", ts=T0, label="")]
    r = evaluate_csv(_write(tmp_path / "s.csv", rows))
    assert r["rows_total"] == 12 and r["rows_used"] == 9 and r["rows_skipped"] == 3
    assert r["skipped_reasons"] == {"label empty or not recognised": 2, "no readable time": 1}


def test_missing_label_column_is_a_clear_error(tmp_path, capsys):
    p = _write(tmp_path / "s.csv", _rows(), header=[h for h in HEADER if h != "label"])
    with pytest.raises(ValueError, match="no label column"):
        evaluate_csv(p)
    assert main(["evaluate", str(p)]) == 2
    assert "no label column" in capsys.readouterr().err


def test_missing_email_or_time_column(tmp_path):
    p = _write(tmp_path / "s.csv", _rows(), header=["email", "label"])
    with pytest.raises(ValueError, match="time column"):
        evaluate_csv(p)


def test_optional_columns_may_be_absent(tmp_path):
    r = evaluate_csv(_write(tmp_path / "s.csv", _rows(), header=["email", "ts", "label"]))
    assert r["rows_used"] == 9 and "tactics" not in r


def test_column_aliases(tmp_path):
    rows = [{"Email Address": e["email"], "signup_ip": e["ip"], "visitor_id": e["device_id"], "UA": e["user_agent"],
             "created_at": e["ts"], "Is_Farmer": e["label"]} for e in _rows()]
    p = _write(tmp_path / "s.csv", rows, header=list(rows[0]))
    assert evaluate_csv(p)["rows_used"] == 9


def test_replay_is_in_timestamp_order(tmp_path):
    forward = evaluate_rows(_rows())
    assert evaluate_rows(_rows()[::-1]) == forward
    # a later signup of the same inbox is the one flagged, never the earlier one: swap the labels to prove it
    two = [dict(email="a.b@gmail.com", ts=T0 + 10, label="farmer", ip="1.2.3.4"),
           dict(email="ab@gmail.com", ts=T0, label="real", ip="1.2.3.5")]
    r = evaluate_rows(two[::-1])
    assert r["honest_flagged"] == 0 and r["recall"] == 1.0
    r = evaluate_rows([dict(two[0], label="real"), dict(two[1], label="farmer")])
    assert r["honest_flagged"] == 1 and r["recall"] == 0.0


def test_per_tactic_breakdown(csv_path):
    r = evaluate_csv(csv_path)
    t = r["tactics"]
    assert set(t) == {"alias", "bot", "(none)"}
    assert t["alias"]["farmers"] == 3 and t["bot"]["farmers"] == 4 and t["(none)"]["real"] == 2
    assert t["alias"]["recall"] == pytest.approx(2 / 3, abs=1e-4)  # the first signup of an inbox is not flaggable
    assert t["bot"]["recall"] is not None and t["bot"]["false_positive_rate"] is None
    assert t["(none)"]["recall"] is None and t["(none)"]["false_positive_rate"] == 0.0


def test_ai_preset_withholds_from_unverified_real_users(tmp_path):
    p = _write(tmp_path / "s.csv", _rows())
    default, ai = evaluate_csv(p), evaluate_csv(p, preset="ai")
    assert ai["usd_withheld_from_real"] == pytest.approx(2 * 0.5 * 3)  # starter budget: half grant until verified
    assert default["usd_withheld_from_real"] == 0.0
    assert evaluate_csv(p, preset="ai", grant=10)["usd_withheld_from_real"] == pytest.approx(10.0)
    with pytest.raises(ValueError, match="preset"):
        evaluate_csv(p, preset="nope")


def test_phone_and_card_links_are_used(tmp_path):
    rows = [dict(email="a@acme.io", ip="1.1.1.1", ts=T0, label="real", phone="+15550001111"),
            dict(email="b@acme.io", ip="1.1.1.2", ts=T0 + 5, label="real", card="card_1")]
    r = evaluate_csv(_write(tmp_path / "s.csv", rows, header=["email", "ip", "ts", "label", "phone", "card"]), preset="ai")
    assert r["rows_used"] == 2 and r["usd_withheld_from_real"] == 0.0  # verified users get the full grant


def test_evaluate_rows_equals_evaluate_csv(csv_path):
    with open(csv_path, newline="") as fh:
        rows = list(csv.DictReader(fh))
    for preset in ("default", "ai"):
        assert evaluate_rows(rows, preset=preset, grant=5.0) == evaluate_csv(csv_path, preset=preset, grant=5.0)
    # real python types (float ts, int labels) work too
    typed = [dict(r, ts=float(r["ts"]), label=1 if r["label"] == "farmer" else 0) for r in rows]
    assert evaluate_rows(typed) == evaluate_csv(csv_path)


def test_cli_text_output(csv_path, capsys):
    assert main(["evaluate", str(csv_path), "--grant", "3"]) == 0
    out = capsys.readouterr().out
    for want in ("precision", "recall", "wrongly flagged", "Blocked only", "withheld from farmers", "withheld from real users",
                 "By tactic", "alias", "bot"):
        assert want in out
    assert "ana@acme.io" not in out


def test_cli_json_output(csv_path, capsys):
    assert main(["evaluate", str(csv_path), "--json", "--preset", "ai", "--grant", "4"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data == evaluate_csv(csv_path, preset="ai", grant=4.0)
    assert data["preset"] == "ai" and data["grant_usd"] == 4.0


def test_cli_unreadable_file(tmp_path, capsys):
    assert main(["evaluate", str(tmp_path / "missing.csv")]) != 0
    assert "cannot read" in capsys.readouterr().err


def test_header_only_file_says_no_labelled_rows(tmp_path, capsys):
    p = _write(tmp_path / "h.csv", [])
    with pytest.raises(ValueError, match="no rows had a usable label and time; accepted labels"):
        evaluate_csv(p)
    assert main(["evaluate", str(p)]) == 2
    err = capsys.readouterr().err
    assert "no rows had a usable label" in err and "no label column" not in err


def test_no_usable_rows_is_an_error(tmp_path):
    rows = [dict(email="a@acme.io", ts=T0, label="maybe"), dict(email="b@acme.io", ts="soon", label="real")]
    with pytest.raises(ValueError, match="accepted labels: .*farmer"):
        evaluate_csv(_write(tmp_path / "s.csv", rows))


def test_huge_field_and_unclosed_quote_exit_2(tmp_path, capsys):
    from tenkyu.audit import audit_csv
    big = tmp_path / "big.csv"
    big.write_text("email,ts,label\n" + "a" * 200_000 + f"@acme.io,{T0},real\n")
    unclosed = tmp_path / "q.csv"
    unclosed.write_text(f'email,ts,label\n"ana@acme.io,{T0},real\nbo@acme.io,{T0 + 1},real\n')
    for p in (big, unclosed):
        with pytest.raises(ValueError, match="cannot parse CSV near row"):
            evaluate_csv(p)
        with pytest.raises(ValueError, match="cannot parse CSV near row"):
            audit_csv(p)
        assert main(["evaluate", str(p)]) == 2
        assert main(["audit", str(p)]) == 2
        err = capsys.readouterr().err.strip().splitlines()
        assert len(err) == 2 and all(line.startswith("tenkyu: cannot parse CSV") for line in err)


def test_row_cap_is_500k(tmp_path, monkeypatch):
    from tenkyu import audit, evaluate
    assert audit.MAX_ROWS == 500_000
    monkeypatch.setattr(evaluate, "MAX_ROWS", 2)
    with pytest.raises(ValueError, match="more than 2 rows"):
        evaluate_csv(_write(tmp_path / "s.csv", _rows()))


@pytest.mark.parametrize("bad", ["-1", "nan", "inf", "-inf", "abc"])
def test_grant_must_be_finite_and_non_negative(csv_path, bad, capsys):
    for cmd in ("evaluate", "audit"):
        with pytest.raises(SystemExit) as e:
            main([cmd, str(csv_path), "--grant", bad])
        assert e.value.code == 2
        assert "--grant" in capsys.readouterr().err
    with pytest.raises(ValueError, match="grant"):
        evaluate_rows(_rows(), grant=float(bad) if bad != "abc" else -1)
