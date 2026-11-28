"""Decision log and `tenkyu report`: what a shadow-mode trial produces and how it is read back."""
import io
import json

from tenkyu import DecisionLog, Guard, Signup
from tenkyu.cli import main
from tenkyu.decision_log import read_log, summarize

T0 = 1_700_000_000.0
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/120.0 Safari/537.36"


def _trial(path, shadow=True):
    g = Guard(clock=lambda: T0, shadow=shadow, starter_fraction=0.5, log=DecisionLog(path, secret=b"k" * 16))
    g.check(Signup("ana@outlook.com", "79.1.1.1", "dev-ana", UA, T0))
    for i, e in enumerate(["john@gmail.com", "j.o.h.n+1@gmail.com", "john+2@googlemail.com"]):
        g.check(Signup(e, "45.32.9.9", "dev-j", UA, T0 + 60 + i))
    g.check(Signup("tmp1@mailinator.com", "45.32.9.10", "dev-t", "python-requests/2.31", T0 + 90))
    return g


def test_log_writes_one_hashed_line_per_decision(tmp_path):
    path = tmp_path / "d.jsonl"
    _trial(path)
    lines = path.read_text().splitlines()
    assert len(lines) == 5
    rec = json.loads(lines[1])
    assert set(rec) >= {"ts", "email_hash", "action", "would_action", "shadow", "score", "credit_fraction", "would_credit_fraction", "limit", "reasons", "signals"}
    raw = path.read_text()
    for secret in ["ana@outlook.com", "john", "79.1.1.1", "45.32.9.9", "dev-ana", "dev-j", "mailinator.com\""]:
        assert secret not in raw.replace("disposable email domain (mailinator.com)", "")


def test_shadow_log_records_what_would_have_happened(tmp_path):
    path = tmp_path / "d.jsonl"
    _trial(path)
    recs, bad = read_log(path)
    assert bad == 0
    assert all(r["action"] == "allow" and r["credit_fraction"] == 1.0 for r in recs)
    s = summarize(recs, grant_usd=3)
    assert s["shadow"] and s["by_action"]["block"] >= 3
    assert s["repeat_emails"] == 2
    assert s["usd_withheld"] == 9.0  # 3 blocked signups x $3; the starter cap on allowed users is not counted as abuse
    assert s["grants_held_until_verified"] == 1.0  # ana and john: half a grant each until they verify


def test_report_cli_text_and_json(tmp_path, capsys):
    path = tmp_path / "d.jsonl"
    _trial(path)
    with open(path, "a") as fh:
        fh.write("not json\n{\"would_action\": \"maybe\"}\n")
    assert main(["report", str(path), "--grant", "3"]) == 0
    out = capsys.readouterr().out
    assert "5 signups (shadow mode" in out and "Skipped 2 unreadable line(s)." in out and "Top reasons:" in out
    assert main(["report", str(path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["unreadable_lines"] == 2


def test_report_missing_file_exit_code(tmp_path, capsys):
    assert main(["report", str(tmp_path / "nope.jsonl")]) == 3


def test_broken_log_never_breaks_signup():
    class Broken:
        def write(self, *a):
            raise OSError("disk full")

    d = Guard(clock=lambda: T0, log=Broken()).check(Signup("ana@outlook.com", "1.2.3.4", None, UA, T0))
    assert d.action == "allow"


def test_dry_run_is_not_logged(tmp_path):
    path = tmp_path / "d.jsonl"
    Guard(clock=lambda: T0, log=DecisionLog(path)).check(Signup("ana@outlook.com", "1.2.3.4", None, UA, T0), record=False)
    assert not path.exists()
