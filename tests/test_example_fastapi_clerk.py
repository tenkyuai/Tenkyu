"""examples/fastapi-clerk/app.py: imported (and exercised) when FastAPI is installed, else only compiled."""
import base64
import hashlib
import hmac
import importlib.util
import json
import pathlib
import time

import pytest

APP = pathlib.Path(__file__).resolve().parent.parent / "examples" / "fastapi-clerk" / "app.py"
SECRET = "whsec_" + base64.b64encode(b"tenkyu-test-secret-32-bytes-long").decode()


def test_example_compiles():
    compile(APP.read_text(), str(APP), "exec")


@pytest.fixture
def client(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    testclient = pytest.importorskip("fastapi.testclient")  # needs httpx
    monkeypatch.setenv("CLERK_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("TENKYU_DB", str(tmp_path / "tenkyu.db"))
    monkeypatch.setenv("APP_DB", str(tmp_path / "app.db"))
    spec = importlib.util.spec_from_file_location("clerk_example_app", APP)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return testclient.TestClient(mod.app), mod


def _post(c, event, msg_id, secret=SECRET):
    body = json.dumps(event).encode()
    ts = str(int(time.time()))
    key = base64.b64decode(secret.split("_", 1)[1])
    sig = base64.b64encode(hmac.new(key, f"{msg_id}.{ts}.".encode() + body, hashlib.sha256).digest()).decode()
    return c.post("/webhooks/clerk", content=body,
                  headers={"svix-id": msg_id, "svix-timestamp": ts, "svix-signature": f"v1,{sig}"})


def _user(uid, email):
    return {"type": "user.created", "data": {"id": uid, "primary_email_address_id": "e1",
            "email_addresses": [{"id": "e1", "email_address": email}], "created_at": int(time.time() * 1000)}}


def _credits(mod):
    return dict(mod._db.execute("SELECT user_id, credits FROM grants"))


def test_grants_then_withholds_for_an_alias_and_ignores_repeats_and_other_events(client):
    c, mod = client
    assert _post(c, _user("u1", "john@gmail.com"), "msg_1").status_code == 200
    assert _post(c, _user("u1", "john@gmail.com"), "msg_1").status_code == 200  # same delivery again
    assert _post(c, _user("u2", "j.o.h.n+free@gmail.com"), "msg_2").status_code == 200
    assert _post(c, {"type": "user.deleted", "data": {}}, "msg_3").status_code == 200
    assert _credits(mod) == {"u1": mod.GRANT, "u2": 0}


def test_bad_signature_and_bad_body_are_400_and_grant_nothing(client):
    c, mod = client
    other = "whsec_" + base64.b64encode(b"another-secret-32-bytes-long!!!!").decode()
    assert _post(c, _user("u1", "a@acme.io"), "msg_1", secret=other).status_code == 400
    assert c.post("/webhooks/clerk", content=b"{}").status_code == 400
    assert _credits(mod) == {}
