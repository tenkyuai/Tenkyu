#!/bin/bash
# Pre-release check: tests, build, wheel contents, clean install, CLI smoke. Publishing stays a manual step.
set -euo pipefail
R="$(cd "$(dirname "$0")/.." && pwd)"; cd "$R"
W=$(mktemp -d); V=$(mktemp -d)
echo "1/5 tests";            python3 -m pytest -q -p no:cacheprovider tests | tail -1
echo "2/5 build wheel";      python3 -m pip wheel . --no-deps -q -w "$W"
WHL=$(ls "$W"/tenkyu-*.whl)
echo "3/5 wheel contents";   python3 -c "import zipfile,sys; n=zipfile.ZipFile(sys.argv[1]).namelist(); assert 'tenkyu/data/sample_signups.csv' in n, 'sample CSV missing'; assert not [x for x in n if 'tests/' in x or 'INFINITY' in x], 'stray files'; print(len(n), 'files ok')" "$WHL"
echo "4/5 clean install";    python3 -m venv "$V" && "$V/bin/pip" install -q "$WHL"
VER=$("$V/bin/python" -c "import tenkyu; print(tenkyu.__version__)")
grep -q "^version = \"$VER\"" pyproject.toml || { echo "version mismatch: $VER"; exit 1; }
echo "5/5 CLI smoke";        (cd "$V" && "$V/bin/python" -m tenkyu audit --sample --json | python3 -c "import json,sys; s=json.load(sys.stdin); assert s['signups']==939; print('audit --sample ok')")
echo "OK: tenkyu $VER is ready. Publishing is yours: python -m twine upload --repository testpypi $WHL, then PyPI."
