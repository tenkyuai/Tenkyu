"""Plain helpers for the attack suite (kept out of conftest.py so they don't shadow hosted/tests/conftest.py)."""
T0 = 1_700_000_000.0
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"


def fired(decision):
    """Names of the signals that actually scored above zero."""
    return {s.name for s in decision.signals if s.score > 0}
