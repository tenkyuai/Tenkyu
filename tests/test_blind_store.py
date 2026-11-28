"""BlindStore: a wrapper that keeps raw keys and values out of any store, and proof that it changes no decision."""
import os
import sys
import unittest

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "benchmarks"))

from heldout_gen import generate  # noqa: E402
from tenkyu import BlindStore, Guard, MemoryStore, Signup, SQLiteStore  # noqa: E402
from tenkyu.store import Blinder  # noqa: E402
from tenkyu.testing import StoreConformance  # noqa: E402

T0 = 1_700_000_000.0
SECRET = b"blind-test-secret-0123456789abcdef"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/120.0 Safari/537.36"


class TestBlindMemoryConformance(StoreConformance, unittest.TestCase):
    def make_store(self, clock, retention):
        return BlindStore(MemoryStore(clock=clock, retention=retention), SECRET)


class TestBlindSQLiteConformance(StoreConformance, unittest.TestCase):
    def make_store(self, clock, retention):
        s = BlindStore(SQLiteStore(":memory:", clock=clock, retention=retention), SECRET)
        self.addCleanup(s.close)
        return s


def test_stored_keys_and_values_are_hashes_but_the_kind_stays_readable():
    inner = MemoryStore(clock=lambda: T0)
    s = BlindStore(inner, SECRET)
    s.add("email:maya@gmail.com", "maya@gmail.com", ts=T0)
    s.add("ipstem:203.0.113.7:maya", None, ts=T0)
    s.add("nocolon", 5, ts=T0)
    keys = list(inner._events)
    assert sorted(k.split(":")[0] for k in keys) == ["_", "email", "ipstem"]
    for k in keys:
        assert len(k.split(":", 1)[1]) == 32 and int(k.split(":", 1)[1], 16) >= 0
    stored = {v for q in inner._events.values() for _, v in q}
    assert stored == {s.blind("maya@gmail.com"), None, s.blind(5)}
    assert "maya" not in repr(inner._events) and "203.0.113" not in repr(inner._events)
    assert s.count("email:maya@gmail.com", 10, T0) == 1


def test_blinding_is_keyed_and_typed():
    a, b = Blinder(SECRET), Blinder(b"another-secret-0123456789abcdef")
    assert a.value("x") == a.value("x") != b.value("x")
    assert a.key("email:x") != b.key("email:x")
    assert a.value(7) != a.value("7")
    assert a.value(None) is None
    assert a.key("email:x") != a.key("ip:x")
    assert a.key("_:abc") != a.key("abc")  # a key with no kind cannot collide with one that has the kind "_"
    assert a.key("k:" + "é" * 500).startswith("k:") and len(a.key("k:" + "é" * 500)) == 34
    assert a.key("\ud800:x\ud800")  # lone surrogates are fine


@pytest.mark.parametrize("bad", [b"", b"short", "string-not-bytes", None, 5, True])
def test_secret_must_be_16_bytes(bad):
    with pytest.raises(ValueError):
        BlindStore(MemoryStore(), bad)


def test_transaction_exists_only_when_the_inner_store_has_one():
    class Bare:
        def add(self, *a, **k): ...
    assert not hasattr(BlindStore(Bare(), SECRET), "transaction")
    s = BlindStore(MemoryStore(), SECRET)
    with s.transaction():
        s.add("k", 1)


def test_other_attributes_come_from_the_wrapped_store():
    inner = MemoryStore(clock=lambda: T0)
    assert BlindStore(inner, SECRET).clock is inner.clock
    with pytest.raises(AttributeError):
        BlindStore(inner, SECRET).no_such_thing


def test_guard_does_not_double_count_the_current_email():
    """device_reuse and pattern_emails union the current email into stored values: it must be blinded first, or one
    person would count as two."""
    for store in (MemoryStore(clock=lambda: T0), BlindStore(MemoryStore(clock=lambda: T0), SECRET)):
        g = Guard(store=store, clock=lambda: T0)
        g.check(Signup(email="ann@acme.io", ip="203.0.113.5", device_id="dev1", user_agent=UA, ts=T0))
        d = g.check(Signup(email="ann2@acme.io", ip="203.0.113.5", device_id="dev1", user_agent=UA, ts=T0 + 5))
        names = {s.name: s.score for s in d.signals}
        assert names.get("device_reuse") == 0.5, names  # two accounts on the device, not three
        assert "pattern_emails" not in names


def _rows():
    return generate(1)


def _signup(r):
    links = {k: r[k] for k in ("phone", "card") if r.get(k)}
    return Signup(email=r["email"] or None, ip=r["ip"] or None, device_id=r["device_id"] or None,
                  user_agent=r["user_agent"] or None, ts=r["ts"], links=links or None)


def _replay(store_factory, preset):
    now = [0.0]
    store = store_factory(lambda: now[0])
    make = Guard.for_ai_apps if preset == "ai" else Guard
    g = make(store=store, clock=lambda: now[0], link_secret=b"link-secret-0123456789abcdef")
    out = []
    for r in _rows():
        now[0] = r["ts"]
        try:
            out.append(g.check(_signup(r)).to_dict())
        except ValueError:
            out.append("invalid")
    return out


@pytest.mark.parametrize("preset", ["default", "ai"])
def test_decisions_are_identical_with_and_without_blinding_on_the_benchmark(preset):
    plain = _replay(lambda clock: MemoryStore(clock=clock), preset)
    blind = _replay(lambda clock: BlindStore(MemoryStore(clock=clock), SECRET), preset)
    assert len(plain) == len(blind) == len(_rows()) > 1000
    assert [p["action"] if p != "invalid" else p for p in plain] == [b["action"] if b != "invalid" else b for b in blind]
    assert plain == blind  # and so is everything else: score, credit fraction, signals, reasons
    assert {p["action"] for p in plain if p != "invalid"} == {"allow", "review", "block"}
