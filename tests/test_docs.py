import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _blocks(name):
    text = (ROOT / "docs" / name).read_text(encoding="utf-8")
    return re.findall(r"```python\n(.*?)```", text, re.S)


@pytest.mark.parametrize("i", range(2))
def test_signals_doc_examples_run(i):
    blocks = _blocks("signals.md")
    assert len(blocks) == 2
    exec(compile(blocks[i], f"signals.md block {i}", "exec"), {})
