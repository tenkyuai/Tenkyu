"""Red-team regression tests for the shadow-report library commands. Each test FAILS today (a working attack)."""
import csv
import json
from datetime import datetime, timezone

from tenkyu import Guard, Signup
from tenkyu.cli import main
from tenkyu.decision_log import DecisionLog, _email_hash, decision_id
from tenkyu.shadow_report import write_template

VICTIM = "Jane.Doe@Example.com"
SECRET = b"log-secret-0123456789"


def test_template_decision_id_does_not_confirm_a_guessed_email(tmp_path):
    """The labels template is meant to be handed to whoever labels. Its decision_id is an unsalted hash of (time, email_hash,
    action, would_action, score); time, action and score are printed beside it, so anyone can recompute the id for a guessed
    email and confirm that person signed up (the signup time is the app's own, in whole seconds)."""
    log, tpl = tmp_path / "log.jsonl", tmp_path / "tpl.csv"
    Guard(log=DecisionLog(str(log), secret=SECRET)).check(Signup(email=VICTIM, ip="203.0.113.5", ts=1_700_000_123))
    write_template(str(log), str(tpl))
    row = next(csv.DictReader(open(tpl, newline="")))
    when = datetime.strptime(row["time"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    guess = decision_id({"ts": when, "email_hash": _email_hash(VICTIM, SECRET), "action": row["action"],
                         "would_action": row["would_action"], "score": float(row["score"])})
    assert guess != row["decision_id"]


def _one_line_log(path, **override):
    rec = {"ts": 1.7e9, "email_hash": "ab", "action": "allow", "would_action": "block", "shadow": True, "score": 0.5,
           "credit_fraction": 1.0, "would_credit_fraction": 0.0, "limit": None, "reasons": ["r"], "signals": []}
    rec.update(override)
    path.write_text(json.dumps(rec) + "\n")


def test_a_log_line_with_a_wrong_typed_field_is_skipped_not_a_traceback(tmp_path):
    log = tmp_path / "log.jsonl"
    _one_line_log(log, reasons=5)  # valid JSON, wrong type: read_log() keeps it, write_template()/summarize() raise TypeError
    try:
        code = main(["shadow-report", str(log), "--template", str(tmp_path / "t.csv")])
    except TypeError as e:
        raise AssertionError(f"traceback instead of a clean exit: {e}") from None
    assert code in (0, 2)


def test_template_cells_from_the_log_are_formula_safe(tmp_path):
    log, tpl = tmp_path / "log.jsonl", tmp_path / "t.csv"
    _one_line_log(log, score="=HYPERLINK(\"http://evil\")", decision_id="=1+1")
    write_template(str(log), str(tpl))
    for cell in [c for row in list(csv.reader(open(tpl, newline="")))[1:] for c in row]:
        assert cell[:1] not in ("=", "+", "-", "@", "\t", "\r"), cell
