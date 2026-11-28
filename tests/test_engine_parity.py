"""The dashboard demo runs a browser copy of the engine. It must agree with this library on the shared scenarios,
or the demo would show behaviour the product does not have."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tenkyu import ALLOW, BLOCK, Guard, Signup

FIX = Path(__file__).parent / "fixtures"
SCEN = json.loads((FIX / "shared_scenarios.json").read_text())
T0 = 1_700_000_000.0
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/120.0 Safari/537.36"


def _ok(action, expect):
    flagged = action != ALLOW
    return {"allow": not flagged, "flagged": flagged, "not block": action != BLOCK}[expect]


@pytest.mark.parametrize("sc", SCEN, ids=[s["name"] for s in SCEN])
def test_python_engine(sc):
    g = Guard(clock=lambda: T0)
    for i, step in enumerate(sc["steps"]):
        d = g.check(Signup(email=step[0], ip=step[1], device_id=step[2], user_agent=UA, ts=T0 + i * 3600, links=step[3] if len(step) > 3 else None))
    assert _ok(d.action, sc["expect_last"]), d.reasons


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_browser_engine_agrees():
    js = (FIX / "browser_engine.js").read_text() + """
const T = window.__t, sc = %s, out = [];
for (const s of sc) {
  const t = Object.create(null);
}
""" % json.dumps(SCEN)
    script = "const window = {};\n" + (FIX / "browser_engine.js").read_text() + """
const S = %s; const out = [];
for (const s of S) {
  const w = {}; const src = require('fs').readFileSync(%s, 'utf8'); new Function('window', src)(w); const T = w.__t;
  let a; s.steps.forEach((st, i) => { T.clock(T.clock() + 3600); a = T.check('u' + i, st[0], st[1], st[2], st[3]); });
  out.push(a.action === 'challenge' ? 'review' : a.action);
}
console.log(JSON.stringify(out));
""" % (json.dumps(SCEN), json.dumps(str(FIX / "browser_engine.js")))
    res = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)
    bad = [(s["name"], a) for s, a in zip(SCEN, res) if not _ok(a, s["expect_last"])]
    assert not bad, bad
