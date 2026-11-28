"""Shared helpers for the attack suite. Library only: nothing here imports hosted/."""
import pytest

from tenkyu import Guard, Signup

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from attack_helpers import T0, UA, fired  # noqa: E402,F401


@pytest.fixture
def make_guard():
    """make_guard(**settings) -> a Guard with a frozen clock, so every test is deterministic."""
    def factory(**kw):
        return Guard(clock=lambda: T0, **kw)
    return factory


@pytest.fixture
def signup():
    """signup(email, ip=..., dev=..., ts=offset_seconds, ...) -> Signup at T0 + ts."""
    def factory(email, ip="10.0.0.1", dev=None, ts=0, links=None, ua=UA):
        return Signup(email=email, ip=ip, device_id=dev, user_agent=ua, ts=T0 + ts, links=links)
    return factory

