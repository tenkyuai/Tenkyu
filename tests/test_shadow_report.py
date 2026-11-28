"""Shadow report and decisions-mode evaluate: scoring recorded decisions against labels, offline."""
import csv
import json

import pytest

from tenkyu.cli import main
from tenkyu.decision_log import decision_id, read_log
from tenkyu.evaluate import evaluate_csv, evaluate_decision_rows, label_state
from tenkyu.shadow_report import load_labels, shadow_report, write_template

T0 = 1_700_000_000.0
EXPORT_HEADER = ["decision_id", "time", "action", "would_action", "shadow", "score", "reasons", "label"]
PII = ["ana@acme.io", "203.0.113.10", "bo@acme.io", "john@gmail.com", "198.51.100.20"]


def write_csv(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)
    return str(path)


def export_row(i, action, would, shadow, label, reasons="x"):
    return [f"id{i}", "2026-11-20T10:00:00Z", action, would, "true" if shadow else "false", "0.5", reasons, label]


def make_log(path, specs):
    """specs: (would_action, would_credit_fraction, email_hash). Returns the decision ids in order."""
    ids = []
    with open(path, "w", encoding="utf-8") as fh:
        for i, (would, frac, eh) in enumerate(specs):
            rec = {"ts": T0 + i, "email_hash": eh, "action": "allow", "would_action": would, "shadow": True, "score": 0.5,
                   "credit_fraction": 1.0, "would_credit_fraction": frac, "limit": None, "reasons": ["r"], "signals": []}
            rec["decision_id"] = decision_id(rec)
            ids.append(rec["decision_id"])
            fh.write(json.dumps(rec) + "\n")
    return ids


def run(capsys, *argv):
    code = main(list(argv))
    cap = capsys.readouterr()
    return code, cap.out, cap.err


# ---- decisions-mode evaluate ----

def test_autodetects_decisions_export_and_uses_would_action_for_shadow_rows(tmp_path, capsys):
    rows = [export_row(1, "allow", "block", True, "farmer"),    # shadow: would_action counts, so a caught farmer
            export_row(2, "allow", "review", True, "real"),     # shadow: honest user flagged
            export_row(3, "allow", "allow", True, "real"),
            export_row(4, "block", "block", False, "farmer"),   # enforced: action counts
            export_row(5, "allow", "block", False, "farmer")]   # enforced allow: would_action ignored when shadow is false
    path = write_csv(tmp_path / "export.csv", EXPORT_HEADER, rows)
    res = evaluate_csv(path)
    assert res["mode"] == "decisions"
    assert (res["farmers"], res["real"], res["flagged"], res["blocked"]) == (3, 2, 3, 2)
    assert res["honest_flagged"] == 1 and res["blocked_only"]["honest_flagged"] == 0
    assert res["precision"] == round(2 / 3, 4) and res["recall"] == round(2 / 3, 4)
    code, out, _ = run(capsys, "evaluate", path, "--grant", "4")
    assert code == 0 and "recorded decisions" in out and "not re-run" in out
    res = evaluate_csv(path, grant=4)  # block holds back 100%, review 75% of the grant
    assert res["usd_withheld_from_farmers"] == 8.0 and res["usd_withheld_from_real"] == 3.0


def test_not_sure_rows_are_skipped_and_counted(tmp_path):
    rows = [export_row(1, "allow", "block", True, "farmer"), export_row(2, "allow", "allow", True, "not sure"),
            export_row(3, "allow", "allow", True, "Not_Sure"), export_row(4, "allow", "allow", True, "real"),
            export_row(5, "allow", "allow", True, ""), export_row(6, "allow", "allow", True, "banana")]
    res = evaluate_csv(write_csv(tmp_path / "e.csv", EXPORT_HEADER, rows))
    assert res["rows_used"] == 2 and res["rows_skipped"] == 4
    assert res["skipped_reasons"]['labelled "not sure"'] == 2
    assert res["skipped_reasons"]["label empty or not recognised"] == 2


def test_decisions_flag_forces_mode_even_with_email_column(tmp_path):
    header = ["email", "action", "label"]
    path = write_csv(tmp_path / "f.csv", header, [["a@b.co", "block", "farmer"], ["c@d.co", "allow", "real"]])
    assert evaluate_csv(path, decisions=True)["mode"] == "decisions"
    assert "mode" not in evaluate_csv(write_csv(tmp_path / "g.csv", ["email", "ts", "label"],
                                                [["a@b.co", T0, "real"]]))  # replay mode unchanged


def test_decisions_flag_without_action_column_is_an_error(tmp_path, capsys):
    path = write_csv(tmp_path / "h.csv", ["email", "ts", "label"], [["a@b.co", T0, "real"]])
    code, _, err = run(capsys, "evaluate", path, "--decisions")
    assert code == 2 and "action column" in err


def test_low_label_warning_under_50_and_not_at_50(tmp_path, capsys):
    few = write_csv(tmp_path / "few.csv", EXPORT_HEADER, [export_row(i, "allow", "block", True, "farmer" if i % 2 else "real") for i in range(49)])
    code, out, _ = run(capsys, "evaluate", few)
    assert code == 0 and "WARNING: only 49 labelled decisions" in out
    enough = write_csv(tmp_path / "enough.csv", EXPORT_HEADER, [export_row(i, "allow", "block", True, "farmer" if i % 2 else "real") for i in range(50)])
    code, out, _ = run(capsys, "evaluate", enough)
    assert code == 0 and "WARNING" not in out
    assert evaluate_csv(few)["low_labels"] is True and evaluate_csv(enough)["low_labels"] is False


def test_one_sided_labels_warn(tmp_path, capsys):
    path = write_csv(tmp_path / "one.csv", EXPORT_HEADER, [export_row(i, "allow", "allow", True, "real") for i in range(60)])
    code, out, _ = run(capsys, "evaluate", path)
    assert code == 0 and "only one kind of user" in out and "n/a" in out


def test_json_output(tmp_path, capsys):
    path = write_csv(tmp_path / "j.csv", EXPORT_HEADER, [export_row(1, "allow", "block", True, "farmer")])
    code, out, _ = run(capsys, "evaluate", path, "--json")
    assert code == 0 and json.loads(out)["mode"] == "decisions"


@pytest.mark.parametrize("body,needle", [
    ("", "no header"),
    ("decision_id,action\nx,block\n", "label"),
    ("decision_id,action,label\n", "no rows"),
    ("decision_id,action,label\nx,allow,not sure\n", "no rows"),
    ("decision_id,action,label\nx,maybe,farmer\n", "no rows"),
])
def test_malformed_decisions_exports_exit_2_with_one_line(tmp_path, capsys, body, needle):
    p = tmp_path / "bad.csv"
    p.write_text(body)
    code, out, err = run(capsys, "evaluate", str(p), "--decisions")
    assert code == 2 and needle in err and "Traceback" not in err and err.count("\n") == 1 and out == ""


def test_nul_bytes_and_huge_fields_do_not_crash(tmp_path, capsys):
    p = tmp_path / "nul.csv"
    p.write_bytes(b"decision_id,action,label\nx\x00y,block,farmer\n")
    code, out, err = run(capsys, "evaluate", str(p), "--decisions")
    assert code in (0, 2) and "Traceback" not in err
    p.write_text("decision_id,action,label\n" + "a" * 200_000 + ",block,farmer\n")
    code, _, err = run(capsys, "evaluate", str(p), "--decisions")
    assert code == 2 and "cannot parse CSV" in err


def test_bom_unicode_and_odd_case_labels(tmp_path):
    p = tmp_path / "u.csv"
    p.write_bytes("﻿decision_id,Action,Would_Action,Shadow,Label\nדוגמה,ALLOW,Block,TRUE,Farmer\nñandú,allow,allow,true,REAL\n".encode("utf-8"))
    res = evaluate_csv(str(p))
    assert (res["farmers"], res["real"], res["flagged"], res["blocked"]) == (1, 1, 1, 1)


def test_invalid_utf8_is_replaced_not_fatal(tmp_path):
    p = tmp_path / "bin.csv"
    p.write_bytes(b"decision_id,action,label\n\xff\xfe,block,farmer\n")
    assert evaluate_csv(str(p))["farmers"] == 1


def test_csv_injection_cells_are_inert(tmp_path, capsys):
    evil = ['=HYPERLINK("http://evil","x")', "+cmd|' /C calc'!A0", "-2+3", "@SUM(1+1)", "\t=1+1"]
    rows = [[e, e, "block", "block", "true", e, e, "farmer"] for e in evil] + [["ok", "t", "allow", "allow", "false", "0", "", "real"]]
    path = write_csv(tmp_path / "inj.csv", EXPORT_HEADER, rows)
    code, out, err = run(capsys, "evaluate", path)
    assert code == 0 and err == ""
    for e in evil:
        assert e not in out
    assert evaluate_csv(path)["farmers"] == 5


def test_evil_header_names_are_sanitised_in_errors(tmp_path, capsys):
    p = tmp_path / "hdr.csv"
    p.write_text("a\x1b[31mred,=cmd,label\n1,2,farmer\n")
    code, _, err = run(capsys, "evaluate", str(p))
    assert code == 2 and "\x1b" not in err


def test_replay_mode_still_chosen_for_signup_exports(tmp_path):
    path = write_csv(tmp_path / "s.csv", ["email", "ip", "ts", "label"],
                     [["ana@acme.io", "203.0.113.10", T0, "real"], ["x@mailinator.com", "192.0.2.1", T0 + 5, "farmer"]])
    assert evaluate_csv(path)["preset"] == "default"


def test_label_state():
    assert [label_state(v) for v in ("farmer", " REAL ", "not sure", "not-sure", "Unsure", "", None, "wat", "1", "0", "2")] == \
        ["farmer", "real", "unsure", "unsure", "unsure", None, None, None, "farmer", "real", None]


def test_evaluate_decision_rows_api_validates_grant():
    with pytest.raises(ValueError):
        evaluate_decision_rows([{"action": "allow", "label": "real"}], grant=float("nan"))
    with pytest.raises(ValueError):
        evaluate_decision_rows([{"action": "allow", "label": "real"}], grant=-1)


# ---- shadow-report ----

@pytest.fixture
def log_and_labels(tmp_path):
    # 3 farmers (block, review, allow) and 3 honest users (allow, allow, review), one unlabelled decision
    specs = [("block", 0.0, "h1"), ("review", 0.25, "h2"), ("allow", 1.0, "h3"),
             ("allow", 1.0, "h4"), ("allow", 0.5, "h5"), ("review", 0.25, "h6"), ("block", 0.0, "h7")]
    log = tmp_path / "decisions.jsonl"
    ids = make_log(log, specs)
    labels = ["farmer", "farmer", "farmer", "real", "real", "real"]
    rows = [[ids[i], "", "", "", "", "", "", labels[i]] for i in range(6)]
    return str(log), write_csv(tmp_path / "labels.csv", EXPORT_HEADER, rows), ids


def test_shadow_report_numbers(log_and_labels, capsys):
    log, labels, _ = log_and_labels
    code, out, err = run(capsys, "shadow-report", log, labels, "--grant", "10")
    assert code == 0 and err == ""
    res = shadow_report(log, labels, 10)
    assert (res["farmers"], res["real"], res["flagged"], res["blocked"]) == (3, 3, 3, 1)
    assert res["precision"] == round(2 / 3, 4) and res["recall"] == round(2 / 3, 4)
    assert res["honest_flagged"] == 1 and res["blocked_only"]["honest_flagged"] == 0
    assert res["usd_withheld_from_farmers"] == 17.5  # block 100% + review 75% of $10; the allow row withholds nothing
    assert res["usd_withheld_from_real"] == 7.5      # the flagged honest review row; the 0.5 starter hold on an allow is not counted
    assert res["unlabelled_decisions"] == 1 and res["decisions_in_log"] == 7
    assert "joined on decision_id" in out and "WARNING: only 6 labelled" in out and "$17.50" in out


def test_shadow_report_json(log_and_labels, capsys):
    log, labels, _ = log_and_labels
    code, out, _ = run(capsys, "shadow-report", log, labels, "--json")
    data = json.loads(out)
    assert code == 0 and data["farmers"] == 3 and data["shadow"] is True


def test_no_pii_or_ids_in_output(tmp_path, capsys):
    log = tmp_path / "d.jsonl"
    from tenkyu import DecisionLog, Guard
    g = Guard(shadow=True, log=DecisionLog(str(log)))
    from tenkyu.guard import Signup
    ua = "Mozilla/5.0 Chrome/120.0 Safari/537.36"
    g.check(Signup("ana@acme.io", "203.0.113.10", "dev-ana", ua))
    g.check(Signup("john@gmail.com", "198.51.100.20", "dev-j", ua))
    recs, _ = read_log(str(log))
    tpl = tmp_path / "t.csv"
    write_template(str(log), str(tpl))
    rows = list(csv.DictReader(open(tpl)))
    for r, lab in zip(rows, ["real", "farmer"]):
        r["label"] = lab
    labels = write_csv(tmp_path / "l.csv", EXPORT_HEADER, [[r[h] for h in EXPORT_HEADER] for r in rows])
    code, out, err = run(capsys, "shadow-report", str(log), labels)
    code2, out2, _ = run(capsys, "shadow-report", str(log), labels, "--json")
    blob = out + err + out2
    assert code == 0 and code2 == 0
    for needle in PII + [r["decision_id"] for r in recs] + [r["email_hash"] for r in recs if "email_hash" in r]:
        assert needle not in blob
    assert "ana@" not in tpl.read_text() and "203.0.113" not in tpl.read_text()


def test_new_log_lines_get_random_ids_and_old_lines_derive_a_stable_one(tmp_path):
    from tenkyu import DecisionLog, Guard
    from tenkyu.guard import Signup
    log = tmp_path / "d.jsonl"
    Guard(shadow=True, log=DecisionLog(str(log))).check(Signup("ana@acme.io", "203.0.113.10", "d", "Mozilla/5.0 Chrome/120"))
    rec = json.loads(log.read_text())
    old = {k: v for k, v in rec.items() if k != "decision_id"}
    assert rec["decision_id"].startswith("d_") and rec["decision_id"] != decision_id(old)  # not a hash of the line
    assert decision_id(old) == decision_id(dict(old))  # an old line without an id still gets a stable one


def test_old_log_without_decision_id_still_joins(tmp_path, capsys):
    log = tmp_path / "old.jsonl"
    recs = [{"ts": T0 + i, "email_hash": f"h{i}", "action": "allow", "would_action": w, "shadow": True, "score": 0.1,
             "would_credit_fraction": f} for i, (w, f) in enumerate([("block", 0.0), ("allow", 1.0)])]
    log.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
    labels = write_csv(tmp_path / "l.csv", EXPORT_HEADER, [[decision_id(recs[0]), "", "", "", "", "", "", "farmer"],
                                                           [decision_id(recs[1]), "", "", "", "", "", "", "real"]])
    code, out, _ = run(capsys, "shadow-report", str(log), labels)
    assert code == 0 and "2 of 2 logged decisions" in out


def test_dollars_use_the_logged_credit_fraction(tmp_path):
    log = tmp_path / "d.jsonl"
    ids = make_log(log, [("review", 0.4, "a"), ("block", 0.0, "b")])
    labels = write_csv(tmp_path / "l.csv", EXPORT_HEADER, [[ids[0], "", "", "", "", "", "", "real"], [ids[1], "", "", "", "", "", "", "farmer"]])
    res = shadow_report(str(log), labels, 5)
    assert res["usd_withheld_from_real"] == 3.0 and res["usd_withheld_from_farmers"] == 5.0


def test_not_sure_unmatched_and_conflicting_labels_are_set_aside(tmp_path, capsys):
    log = tmp_path / "d.jsonl"
    ids = make_log(log, [("block", 0.0, "a"), ("allow", 1.0, "b"), ("allow", 1.0, "c"), ("review", 0.25, "d")])
    rows = [[ids[0], "", "", "", "", "", "", "farmer"], [ids[0], "", "", "", "", "", "", "farmer"],  # duplicate, same label: fine
            [ids[1], "", "", "", "", "", "", "not sure"],
            [ids[2], "", "", "", "", "", "", "real"], [ids[2], "", "", "", "", "", "", "farmer"],  # conflict: dropped
            ["d_unknown", "", "", "", "", "", "", "real"],
            [ids[3], "", "", "", "", "", "", "perhaps"], ["", "", "", "", "", "", "", "real"]]
    labels = write_csv(tmp_path / "l.csv", EXPORT_HEADER, rows)
    code, out, _ = run(capsys, "shadow-report", str(log), labels)
    res = shadow_report(str(log), labels)
    assert code == 0 and res["rows_used"] == 1
    assert res["set_aside"] == {"labels set aside: not sure": 1, "labels set aside: unrecognised label": 1,
                                "labels set aside: no decision id": 1, "labels set aside: conflicting labels": 1,
                                "labels with no matching decision in the log": 1}
    assert "Set aside:" in out


def test_exit_codes_for_bad_input(tmp_path, capsys, log_and_labels):
    log, labels, _ = log_and_labels
    empty = tmp_path / "empty"
    empty.write_text("")
    garbage = tmp_path / "garbage.jsonl"
    garbage.write_text("not json\n{]\n\n[1,2]\n")
    nolabel = write_csv(tmp_path / "nl.csv", ["decision_id", "foo"], [["x", "y"]])
    noid = write_csv(tmp_path / "ni.csv", ["email", "label"], [["a@b.co", "real"]])
    nomatch = write_csv(tmp_path / "nm.csv", ["decision_id", "label"], [["zzz", "real"]])
    for args in [[str(tmp_path / "missing.jsonl"), labels], [log, str(tmp_path / "missing.csv")], [str(empty), labels],
                 [str(garbage), labels], [log, str(empty)], [log, nolabel], [log, noid], [log, nomatch], [log]]:
        code, out, err = run(capsys, "shadow-report", *args)
        assert code == 2 and out == "" and "tenkyu:" in err and "Traceback" not in err, args
    assert run(capsys, "shadow-report", log, labels)[0] == 0


def test_labels_csv_with_email_but_no_decision_id_is_pointed_at_the_template(tmp_path, capsys, log_and_labels):
    log, _, _ = log_and_labels
    noid = write_csv(tmp_path / "ni.csv", ["email", "label"], [["a@b.co", "real"]])
    _, _, err = run(capsys, "shadow-report", log, noid)
    assert "decision_id" in err and "--template" in err


def test_malformed_label_csvs(tmp_path, capsys, log_and_labels):
    log, _, ids = log_and_labels
    for body in ['decision_id,label\n"unterminated,real\n', "decision_id,label\n\x00,real\n", "\n\n"]:
        p = tmp_path / "m.csv"
        p.write_text(body)
        code, _, err = run(capsys, "shadow-report", log, str(p))
        assert code == 2 and "Traceback" not in err


def test_ragged_label_rows_do_not_crash(tmp_path, capsys, log_and_labels):
    log, _, ids = log_and_labels
    p = tmp_path / "r.csv"
    p.write_text(f"decision_id,label\n{ids[0]}\n{ids[1]},farmer,extra,more\n{ids[3]},real\n")
    code, out, _ = run(capsys, "shadow-report", log, str(p))
    assert code == 0 and "2 of 7" in out


def test_garbled_log_lines_are_skipped_and_counted(tmp_path, capsys):
    log = tmp_path / "d.jsonl"
    ids = make_log(log, [("block", 0.0, "a"), ("allow", 1.0, "b")])
    with open(log, "a") as fh:
        fh.write("{broken\n" + json.dumps({"would_action": "explode"}) + "\n")
    labels = write_csv(tmp_path / "l.csv", ["decision_id", "label"], [[ids[0], "farmer"], [ids[1], "real"]])
    code, out, _ = run(capsys, "shadow-report", str(log), labels)
    assert code == 0 and "2 unreadable log line(s)" in out


def test_unicode_ids_and_labels(tmp_path, capsys):
    log = tmp_path / "d.jsonl"
    ids = make_log(log, [("block", 0.0, "שלום"), ("allow", 1.0, "ñ")])
    labels = write_csv(tmp_path / "l.csv", ["decision_id", "label"], [[ids[0], "FARMER"], [ids[1], "Real"], ["שלום-🦊", "real"]])
    code, out, _ = run(capsys, "shadow-report", str(log), labels)
    assert code == 0 and "labels with no matching decision" in out


def test_csv_injection_in_labels_file(tmp_path, capsys, log_and_labels):
    log, _, ids = log_and_labels
    rows = [[ids[0], '=HYPERLINK("http://evil")'], ["=1+1", "farmer"], ["@x", "real"], [ids[3], "real"], [ids[1], "farmer"]]
    labels = write_csv(tmp_path / "inj.csv", ["decision_id", "label"], rows)
    code, out, err = run(capsys, "shadow-report", log, labels)
    assert code == 0 and "HYPERLINK" not in out + err and "=1+1" not in out + err


def test_template_roundtrip_and_formula_guard(tmp_path, capsys):
    log = tmp_path / "d.jsonl"
    rec = {"ts": T0, "email_hash": "a", "action": "allow", "would_action": "block", "shadow": True, "score": 0.9,
           "would_credit_fraction": 0.0, "reasons": ["=cmd|' /C calc'!A0", "plain"], "signals": []}
    log.write_text(json.dumps(rec) + "\n")
    tpl = tmp_path / "t.csv"
    code, out, _ = run(capsys, "shadow-report", str(log), "--template", str(tpl))
    assert code == 0 and "Wrote 1 decisions" in out
    rows = list(csv.DictReader(open(tpl)))
    assert list(rows[0]) == EXPORT_HEADER and rows[0]["label"] == "" and rows[0]["decision_id"] == decision_id(rec)
    assert rows[0]["reasons"].startswith("'=") and rows[0]["time"] == "2023-11-14T22:13:20Z"
    labels, counts = load_labels(str(tpl))  # blank labels: set aside, nothing crashes
    assert labels == {} and counts["unrecognised label"] == 1
    rows[0]["label"] = "farmer"
    write_csv(tmp_path / "filled.csv", EXPORT_HEADER, [[rows[0][h] for h in EXPORT_HEADER]])
    code, out, _ = run(capsys, "shadow-report", str(log), str(tmp_path / "filled.csv"))
    assert code == 0 and "1 farmers, 0 real users" in out


def test_template_errors_exit_2(tmp_path, capsys):
    code, _, err = run(capsys, "shadow-report", str(tmp_path / "nope.jsonl"), "--template", str(tmp_path / "t.csv"))
    assert code == 2 and "tenkyu:" in err
    empty = tmp_path / "e.jsonl"
    empty.write_text("")
    assert run(capsys, "shadow-report", str(empty), "--template", str(tmp_path / "t.csv"))[0] == 2


def test_bad_grant_is_a_usage_error(log_and_labels):
    log, labels, _ = log_and_labels
    for g in ("abc", "-1", "nan", "inf"):
        with pytest.raises(SystemExit) as e:
            main(["shadow-report", log, labels, "--grant", g])
        assert e.value.code == 2


def test_fifty_labels_clears_the_warning(tmp_path, capsys):
    log = tmp_path / "d.jsonl"
    ids = make_log(log, [("block" if i % 2 else "allow", 0.0 if i % 2 else 1.0, f"h{i}") for i in range(50)])
    labels = write_csv(tmp_path / "l.csv", ["decision_id", "label"], [[ids[i], "farmer" if i % 2 else "real"] for i in range(50)])
    code, out, _ = run(capsys, "shadow-report", str(log), labels)
    assert code == 0 and "WARNING" not in out
    labels49 = write_csv(tmp_path / "l49.csv", ["decision_id", "label"], [[ids[i], "farmer" if i % 2 else "real"] for i in range(49)])
    assert "WARNING: only 49 labelled decisions" in run(capsys, "shadow-report", str(log), labels49)[1]


def test_docs_link_and_commands_exist():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    readme = (root / "README.md").read_text()
    guide = (root / "docs" / "first-week.md").read_text()
    assert "docs/first-week.md" in readme
    for needle in ("shadow-report", "Guard(shadow=True", "7 days", "Your accuracy", "fewer than 50", "--template"):
        assert needle in guide


def test_minus_one_is_not_read_as_a_farmer_label():
    from tenkyu.evaluate import label_state
    assert label_state("-1") != "farmer"
    assert label_state("not-sure") == "unsure" and label_state("farmer") == "farmer"
