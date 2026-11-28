import json
import os
import pathlib
import re
import subprocess
import sys

import pytest

from tenkyu import __version__
from tenkyu.cli import main

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENV = {**os.environ, "PYTHONPATH": str(ROOT / "src")}


def run_cli(*args):
    return subprocess.run([sys.executable, "-m", "tenkyu", *args], capture_output=True, text=True, env=ENV)


def test_version(capsys):
    assert main(["version"]) == 0
    assert capsys.readouterr().out.strip() == __version__


def test_no_args_prints_help(capsys):
    assert main([]) == 0
    assert "usage: tenkyu" in capsys.readouterr().out


def test_demo_runs_and_has_all_three_scenes(capsys):
    assert main(["demo"]) == 0
    out = capsys.readouterr().out
    assert "honest user" in out and "alias farmer" in out and "bot burst" in out
    assert "ALLOW" in out and "BLOCK" in out and "REVIEW" in out


def test_demo_is_deterministic():
    a, b = run_cli("demo"), run_cli("demo")
    assert a.returncode == 0 and a.stdout == b.stdout and a.stderr == ""


def test_demo_has_no_color_codes():
    assert "\x1b[" not in run_cli("demo").stdout


def test_demo_honest_user_allowed_farmer_blocked():
    lines = run_cli("demo").stdout.splitlines()
    assert "ALLOW" in [l for l in lines if "ana@acme.io" in l][0]
    assert "BLOCK" in [l for l in lines if "j.o.h.n+free1" in l][0]


def test_readme_demo_output_matches_real_output():
    readme = (ROOT / "README.md").read_text()
    block = re.search(r"```text\n(scene .*?)```", readme, re.S).group(1)
    assert block == run_cli("demo").stdout


@pytest.mark.parametrize("email,code", [("ok.person@acme.io", 0), ("x@mailinator.com", 20)])
def test_score_exit_codes(email, code):
    ua = ["--ua", "Mozilla/5.0 Chrome/120.0"]
    assert run_cli("score", "--email", email, *ua).returncode == code


def test_score_review_exit_code():
    assert run_cli("score", "--email", "a@acme.io", "--ua", "curl/8.0").returncode == 10


def test_score_json_is_valid():
    r = run_cli("score", "--email", "x@mailinator.com", "--json")
    d = json.loads(r.stdout)
    assert d["action"] == "block" and d["signals"]


def test_score_db_remembers_between_runs(tmp_path):
    db = str(tmp_path / "t.db")
    ua = ["--ua", "Mozilla/5.0 Chrome/120.0"]
    assert run_cli("score", "--email", "john@gmail.com", "--db", db, *ua).returncode == 0
    assert run_cli("score", "--email", "j.o.h.n+2@gmail.com", "--db", db, *ua).returncode == 20


def test_score_dry_run_does_not_remember(tmp_path):
    db = str(tmp_path / "t.db")
    ua = ["--ua", "Mozilla/5.0 Chrome/120.0"]
    run_cli("score", "--email", "john@gmail.com", "--db", db, "--dry-run", *ua)
    assert run_cli("score", "--email", "john@gmail.com", "--db", db, *ua).returncode == 0


def test_score_requires_email():
    r = run_cli("score")
    assert r.returncode == 2 and "--email" in r.stderr


def test_score_junk_email_does_not_crash():
    r = run_cli("score", "--email", "\u202enot-an-email", "--ua", "Mozilla Chrome")
    assert r.returncode == 20 and "email address is not valid" in r.stdout


def test_unknown_command_is_usage_error():
    assert run_cli("frobnicate").returncode == 2


def test_pyproject_declares_console_script():
    import tomllib
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert data["project"]["scripts"]["tenkyu"] == "tenkyu.cli:main"


@pytest.mark.parametrize("kind", ["missing_dir", "is_a_directory", "not_a_database"])
def test_unusable_db_gives_one_line_error_not_a_traceback(kind, tmp_path):
    if kind == "missing_dir":
        bad = tmp_path / "no_such_dir" / "t.db"
    elif kind == "is_a_directory":
        bad = tmp_path
    else:
        bad = tmp_path / "junk.db"
        bad.write_text("this is not a sqlite file")
    r = run_cli("score", "--email", "a@b.c", "--db", str(bad))
    assert r.returncode == 3, r.stderr
    assert "Traceback" not in r.stderr
    assert r.stderr.startswith("tenkyu: cannot use database") and len(r.stderr.strip().splitlines()) == 1
    assert r.stdout == ""
