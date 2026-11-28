"""Fixes from the persona review of night-2: Postgres keys, decision-log email hash, per-network daily cap, doc consistency."""
import hashlib
import json
import re
from pathlib import Path

import pytest

from tenkyu.guard import NETWORK_DAILY_CAP
from tenkyu import ALLOW, BLOCK, REVIEW, DecisionLog, Guard, PostgresStore, Signup
from tenkyu.decision_log import read_log, summarize

ROOT = Path(__file__).resolve().parent.parent
T0 = 1_700_000_000.0
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"
DSN = "postgresql://nobody@127.0.0.1:1/none"  # nothing listens here: the refusal must come before any connection


# --- 1. PostgresStore refuses raw keys unless asked
def test_postgres_store_refuses_to_start_without_key_secret():
    with pytest.raises(ValueError) as e:
        PostgresStore(DSN)
    msg = str(e.value)
    assert "key_secret" in msg and "allow_raw_keys=True" in msg and "token_hex" in msg


def test_postgres_store_refuses_a_borrowed_connection_without_key_secret():
    class Conn:
        autocommit = True
    with pytest.raises(ValueError, match="key_secret"):
        PostgresStore(None, connection=Conn())


def test_postgres_store_allow_raw_keys_gets_past_the_check():
    psycopg = pytest.importorskip("psycopg")
    with pytest.raises(psycopg.OperationalError):  # the connection fails, so the check passed
        PostgresStore(DSN, allow_raw_keys=True, timeout=1)


def test_postgres_store_key_secret_gets_past_the_check():
    psycopg = pytest.importorskip("psycopg")
    with pytest.raises(psycopg.OperationalError):
        PostgresStore(DSN, key_secret=b"0123456789abcdef0123", timeout=1)


def test_readme_and_docs_postgres_examples_pass_key_secret():
    for name in ("README.md", "docs/shared-store.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        calls = [m.group(0) for m in re.finditer(r"= PostgresStore\((?:os\.environ|\"postgresql).*?(?:\n\n|```)", text, re.S)]
        assert calls, name
        assert all("key_secret=os.environ[" in c for c in calls), (name, calls)


# --- 2. DecisionLog: keyed email hash, or none
VICTIM = "Jane.Doe@Example.com"


def _unkeyed(email):
    return hashlib.sha256(email.lower().encode()).hexdigest()[:16]


def test_decision_log_without_secret_has_no_email_hash(tmp_path):
    path = tmp_path / "l.jsonl"
    Guard(log=DecisionLog(path)).check(Signup(email=VICTIM, ip="203.0.113.5", user_agent=UA, ts=T0))
    line = json.loads(path.read_text())
    assert "email_hash" not in line and line["decision_id"].startswith("d_")
    records, bad = read_log(path)
    assert bad == 0 and len(records) == 1
    assert summarize(records)["repeat_emails"] is None  # not tracked, rather than a misleading 0


def test_decision_log_with_secret_uses_hmac_not_plain_sha256(tmp_path):
    path = tmp_path / "l.jsonl"
    g = Guard(clock=lambda: T0, shadow=True, log=DecisionLog(path, secret=b"log-secret-0123456789"))
    g.check(Signup(email=VICTIM, ip="203.0.113.5", user_agent=UA))
    g.check(Signup(email="jane.doe+2@example.com", ip="203.0.113.6", user_agent=UA))
    recs, _ = read_log(path)
    h = recs[0]["email_hash"]
    assert len(h) == 32 and h != _unkeyed("jane.doe@example.com") and not _unkeyed("jane.doe@example.com").startswith(h[:16])
    assert recs[0]["email_hash"] == recs[1]["email_hash"] and summarize(recs)["repeat_emails"] == 1
    assert "jane" not in path.read_text().lower()


def test_decision_log_different_secrets_give_different_hashes(tmp_path):
    out = []
    for i, key in enumerate((b"a" * 16, b"b" * 16)):
        p = tmp_path / f"{i}.jsonl"
        Guard(log=DecisionLog(p, secret=key)).check(Signup(email=VICTIM, user_agent=UA, ts=T0))
        out.append(json.loads(p.read_text())["email_hash"])
    assert out[0] != out[1]


def test_decision_log_rejects_a_short_secret(tmp_path):
    with pytest.raises(ValueError, match="secret"):
        DecisionLog(tmp_path / "l.jsonl", secret=b"short")


def test_old_log_lines_with_unkeyed_hash_still_read_and_count(tmp_path):
    path = tmp_path / "old.jsonl"
    rec = {"ts": T0, "email_hash": "0123456789abcdef", "action": "allow", "would_action": "allow", "shadow": False,
           "score": 0.0, "credit_fraction": 1.0, "would_credit_fraction": 1.0, "limit": None, "reasons": [], "signals": []}
    path.write_text(json.dumps(rec) + "\n" + json.dumps({**rec, "ts": T0 + 1}) + "\n")
    records, bad = read_log(path)
    assert bad == 0 and summarize(records)["repeat_emails"] == 1


# --- 3. per-network daily cap on full grants
NAMES = ["ana", "ben", "carla", "dev", "elena", "farid", "gita", "hugo", "ines", "jonas", "kira", "luca", "maya", "nils",
         "olga", "pablo", "quinn", "rosa", "sven", "tara", "uri", "vera", "wes", "xena", "yara", "zane", "arjun", "bea",
         "cleo", "dario", "esme", "finn", "greta", "hana", "igor", "jade", "karl", "lena", "milo", "nora"]


def _guard(**kw):
    clock = {"t": T0}
    return Guard(clock=lambda: clock["t"], **kw), clock


def _signup(i, ip, t):
    return Signup(email=f"{NAMES[i % len(NAMES)]}{i}x@gmail.com".replace("x", "q"), ip=ip, device_id=f"dev-{i}", user_agent=UA, ts=t)


def test_small_office_under_the_cap_is_untouched():
    g, _ = _guard()
    actions = [g.check(_signup(i, f"203.0.113.{10 + i}", T0 + i * 3000)).action for i in range(NETWORK_DAILY_CAP - 1)]
    assert actions == [ALLOW] * (NETWORK_DAILY_CAP - 1)


def test_slow_rotating_ip_farm_inside_one_slash24_is_capped():
    g, _ = _guard()
    n = NETWORK_DAILY_CAP
    ds = [g.check(_signup(i, f"203.0.113.{i + 1}", T0 + i * 1500)) for i in range(n + 10)]
    assert [d.action for d in ds[:n]] == [ALLOW] * n
    capped = ds[n:]
    assert all(d.action == REVIEW and d.credit_fraction == 0.25 for d in capped)
    assert all(any(s.name == "network_daily_cap" for s in d.signals) for d in capped)
    assert all("network_daily_cap" not in [s.name for s in d.signals] for d in ds[:n])
    assert any("daily" in r.lower() and "network" in r.lower() for r in capped[0].reasons)


def test_the_cap_never_blocks_by_itself():
    g, _ = _guard()
    ds = [g.check(_signup(i, f"203.0.113.{i % 250 + 1}", T0 + i * 1500)) for i in range(NETWORK_DAILY_CAP + 20)]
    assert all(d.action != BLOCK for d in ds)


def test_the_cap_is_per_network_and_expires_after_a_day():
    g, _ = _guard(network_daily_cap=3)
    ips = [f"203.0.113.{i + 1}" for i in range(10)]
    acts = [g.check(_signup(i, ips[i], T0 + i * 2000)).action for i in range(5)]
    assert acts == [ALLOW, ALLOW, ALLOW, REVIEW, REVIEW]
    assert g.check(_signup(5, "198.51.100.7", T0 + 11000)).action == ALLOW  # another /24
    assert g.check(_signup(6, "203.0.113.99", T0 + 86400 + 3 * 2000 + 1)).action == ALLOW  # the first three aged out


def test_ipv6_rotation_inside_one_slash48_is_capped():
    g, _ = _guard(network_daily_cap=3)
    acts = [g.check(_signup(i, f"2001:db8:abcd:{i + 1}::1", T0 + i * 2000)).action for i in range(5)]
    assert acts == [ALLOW, ALLOW, ALLOW, REVIEW, REVIEW]


def test_capped_signups_do_not_extend_the_cap_and_dry_runs_do_not_count():
    g, _ = _guard(network_daily_cap=2)
    for i in range(2):
        g.check(_signup(i, f"203.0.113.{i + 1}", T0 + i))
    for i in range(5):  # dry runs are never recorded
        assert g.check(_signup(10 + i, "203.0.113.50", T0 + 10 + i), record=False).action == REVIEW
    g2, _ = _guard(network_daily_cap=2)
    for i in range(3):
        g2.check(_signup(i, "198.51.100.1", T0 + i))
    assert g2.check(_signup(3, "198.51.100.200", T0 + 99999 + 3 * 1)).action == ALLOW  # the one review did not count


def test_the_cap_can_be_turned_off_and_is_validated():
    g, _ = _guard(network_daily_cap=None)
    assert all(g.check(_signup(i, f"203.0.113.{i + 1}", T0 + i * 1000)).action == ALLOW for i in range(NETWORK_DAILY_CAP + 10))
    for bad in (0, -1, True, 2.5, "20"):
        with pytest.raises(ValueError):
            Guard(network_daily_cap=bad)


def test_shadow_mode_still_counts_would_be_full_grants():
    g, _ = _guard(network_daily_cap=2, shadow=True)
    ds = [g.check(_signup(i, f"203.0.113.{i + 1}", T0 + i * 2000)) for i in range(4)]
    assert [d.shadow_action for d in ds] == [ALLOW, ALLOW, REVIEW, REVIEW]
    assert all(d.action == ALLOW for d in ds)


# --- 4. consistency
def test_version_is_030_everywhere():
    import tenkyu
    assert tenkyu.__version__ == "0.3.0"
    assert 'version = "0.3.0"' in (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "installs 0.3.0" in readme and "0.2.2" not in readme
    assert "## 0.3.0 (2026-11-28)" in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")


def test_landing_page_has_no_raw_template_tags():
    html = (ROOT / "docs/landing/project/Main.dc.html").read_text(encoding="utf-8")
    assert "testCount" not in html
    assert re.search(r"<li>[\d,]+ passing tests</li>", html)


def test_readme_hash_wording_mentions_guessable_values_and_secrets():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "stored only as SHA-256 hashes" not in readme
    assert "link_secret" in readme and "guess" in readme.split("`links`")[1][:700].lower()
