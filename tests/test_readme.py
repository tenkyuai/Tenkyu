import pathlib
import re
import subprocess
import sys
import tomllib

from tenkyu import Signup

ROOT = pathlib.Path(__file__).resolve().parent.parent


def python_blocks():
    text = (ROOT / "README.md").read_text()
    return re.findall(r"```python\n(.*?)```", text, re.S)


def test_readme_has_python_blocks():
    assert len(python_blocks()) >= 5


def test_readme_quickstart_runs_and_blocks_the_alias(capsys):
    ns = {}
    exec(compile(python_blocks()[0], "README quickstart", "exec"), ns)
    out = capsys.readouterr().out
    assert out.splitlines()[0].startswith("allow")
    assert ns["again"].action == "block"


def test_readme_tuning_blocks_run():
    ns = {}
    for i in (1, 2):
        if i == 1:
            exec("from tenkyu import *\nfrom tenkyu import Guard, Signup\nsignup = Signup(email='a@b.co')\n" + python_blocks()[1], ns)
        else:
            exec(python_blocks()[2], ns)
    assert ns["guard"].check(Signup(email="x@y.co")).action in ("allow", "review", "block")


def test_example_matches_readme_and_runs():
    example = (ROOT / "examples" / "quickstart.py").read_text()
    assert python_blocks()[0].strip() == example.strip()
    r = subprocess.run([sys.executable, str(ROOT / "examples" / "quickstart.py")], capture_output=True,
                       text=True, env={"PYTHONPATH": str(ROOT / "src"), "PATH": ""})
    assert r.returncode == 0, r.stderr
    assert "block" in r.stdout


def test_pyproject_valid_and_version_in_sync():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    import tenkyu
    assert data["project"]["name"] == "tenkyu"
    assert data["project"]["version"] == tenkyu.__version__
    assert data["project"]["dependencies"] == []
    assert (ROOT / "LICENSE").read_text().startswith("MIT License")


def test_every_other_readme_block_runs():
    ran = 0
    for block in python_blocks()[3:]:
        if block.lstrip().startswith("# not run in tests"):
            continue
        ns = {}
        exec(compile(block, "README block", "exec"), ns)
        ran += 1
    assert ran >= 1


def test_credit_block_prints_review_100(capsys):
    block = [b for b in python_blocks() if "credit_fractions" in b][0]
    exec(compile(block, "credits", "exec"), {})
    assert capsys.readouterr().out.strip() == "review 100"


def test_framework_block_prints_documented_output(capsys):
    block = [b for b in python_blocks() if "signup_from_request" in b][0]
    exec(compile(block, "framework", "exec"), {})
    assert capsys.readouterr().out.strip() == "198.51.100.7 allow"


def test_sqlite_block_blocks_after_restart(capsys):
    block = [b for b in python_blocks() if "SQLiteStore" in b and "mkdtemp" in b][0]
    exec(compile(block, "sqlite", "exec"), {})
    assert capsys.readouterr().out.strip() == "block"
