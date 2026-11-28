import pathlib
import py_compile

import pytest

from tenkyu import BLOCK, Guard, signup_from_request
from tenkyu.http import client_ip, header

ROOT = pathlib.Path(__file__).resolve().parent.parent
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/120.0 Safari/537.36"


def test_header_lookup_is_case_insensitive():
    assert header({"user-agent": "x"}, "User-Agent") == "x"
    assert header({"USER-AGENT": "x"}, "User-Agent") == "x"


@pytest.mark.parametrize("h", [None, {}, {"User-Agent": ""}, {"User-Agent": "   "}, {"User-Agent": 5}, 42, "str"])
def test_header_junk_is_none(h):
    assert header(h, "User-Agent") is None


def test_default_ignores_xff_so_spoofing_fails():
    assert client_ip({"X-Forwarded-For": "6.6.6.6"}, "203.0.113.9") == "203.0.113.9"


def test_one_proxy_takes_last_entry_and_spoofed_prefix_is_ignored():
    # attacker sent "1.1.1.1"; our proxy appended the real address
    assert client_ip({"X-Forwarded-For": "1.1.1.1, 198.51.100.7"}, "10.0.0.1", trusted_proxies=1) == "198.51.100.7"


def test_two_proxies_takes_second_from_right():
    assert client_ip({"X-Forwarded-For": "9.9.9.9, 198.51.100.7, 10.1.1.1"}, "10.0.0.1", trusted_proxies=2) == "198.51.100.7"


def test_short_header_falls_back_to_socket_address():
    assert client_ip({"X-Forwarded-For": "198.51.100.7"}, "10.0.0.1", trusted_proxies=2) == "10.0.0.1"


def test_missing_header_falls_back():
    assert client_ip({}, "10.0.0.1", trusted_proxies=1) == "10.0.0.1"


def test_garbage_in_xff_falls_back():
    assert client_ip({"X-Forwarded-For": "not-an-ip"}, "10.0.0.1", trusted_proxies=1) == "10.0.0.1"


def test_ipv6_is_normalized_and_brackets_stripped():
    assert client_ip({"X-Forwarded-For": "[2001:DB8:0:0:0:0:0:1]"}, "::1", trusted_proxies=1) == "2001:db8::1"


def test_whitespace_and_empty_entries():
    assert client_ip({"X-Forwarded-For": " 1.1.1.1 ,, 198.51.100.7 "}, None, trusted_proxies=1) == "198.51.100.7"


def test_no_addresses_at_all_is_none():
    assert client_ip(None, None) is None and client_ip(None, "  ") is None


@pytest.mark.parametrize("bad", [-1, 1.5, "1", True, None])
def test_bad_trusted_proxies_is_config_error(bad):
    with pytest.raises(ValueError):
        client_ip({}, "1.1.1.1", trusted_proxies=bad)


def test_signup_from_request_builds_all_fields():
    s = signup_from_request("a@acme.io", {"user-agent": UA, "X-Forwarded-For": "5.5.5.5"}, "10.0.0.1", trusted_proxies=1, device_id="vis_123")
    assert (s.email, s.ip, s.device_id, s.user_agent) == ("a@acme.io", "5.5.5.5", "vis_123", UA)


def test_end_to_end_with_guard_detects_alias_farming_behind_proxy():
    g = Guard(clock=lambda: 1_700_000_000.0)
    h = {"User-Agent": UA, "X-Forwarded-For": "7.7.7.7"}
    assert g.check(signup_from_request("farm@gmail.com", h, "10.0.0.1", trusted_proxies=1, device_id="v1")).allowed
    assert g.check(signup_from_request("f.a.r.m+2@gmail.com", h, "10.0.0.1", trusted_proxies=1, device_id="v1")).action == BLOCK


@pytest.mark.parametrize("name", ["fastapi_app.py", "flask_app.py", "fingerprint_device_id.py"])
def test_examples_compile(name):
    py_compile.compile(str(ROOT / "examples" / name), doraise=True)


def test_fingerprint_example_runs_without_frameworks():
    import runpy
    ns = runpy.run_path(str(ROOT / "examples" / "fingerprint_device_id.py"))
    assert ns["decision"].action in ("allow", "review", "block")


@pytest.mark.parametrize("entry, expected", [
    ("198.51.100.7:51234", "198.51.100.7"),          # Azure Application Gateway / IIS append the client port
    ("[2001:db8::7]:443", "2001:db8::7"),
    ("[2001:db8::7]", "2001:db8::7"),
    ("2001:db8::7", "2001:db8::7"),                   # a bare IPv6 address has many colons: not a port
    ("198.51.100.7", "198.51.100.7"),
])
def test_client_ip_accepts_the_proxy_appended_port(entry, expected):
    # before: "ip:port" was not an IP, so the proxy's own address was used and every visitor looked like one
    assert client_ip({"X-Forwarded-For": f"1.1.1.1, {entry}"}, "10.0.0.1", trusted_proxies=1) == expected


@pytest.mark.parametrize("entry", ["198.51.100.7:", "198.51.100.7:http", "198.51.100.7:99999999999:1", "not-an-ip:80", ":80", "[::1"])
def test_client_ip_still_falls_back_for_garbage_with_colons(entry):
    assert client_ip({"X-Forwarded-For": f"1.1.1.1, {entry}"}, "10.0.0.1", trusted_proxies=1) == "10.0.0.1"


# --- RFC 7239 Forwarded header (used only when trusted_proxies > 0 and X-Forwarded-For is absent)
@pytest.mark.parametrize("value,expected", [
    ("for=198.51.100.7", "198.51.100.7"),
    ("For=198.51.100.7", "198.51.100.7"),                               # parameter names are case-insensitive
    ('for="198.51.100.7:4711"', "198.51.100.7"),                        # port, quoted
    ('for="[2001:db8:cafe::17]"', "2001:db8:cafe::17"),                 # IPv6 must be quoted and bracketed
    ('for="[2001:db8:cafe::17]:4711"', "2001:db8:cafe::17"),
    ("for=198.51.100.7;proto=https;by=203.0.113.43", "198.51.100.7"),
    ("by=203.0.113.43;for=198.51.100.7;proto=https", "198.51.100.7"),
    ("for=1.1.1.1, for=198.51.100.7", "198.51.100.7"),                  # last element = the proxy's own sighting
])
def test_forwarded_header_is_read_when_proxies_trusted(value, expected):
    assert client_ip({"Forwarded": value}, "10.0.0.1", trusted_proxies=1) == expected


def test_forwarded_header_is_ignored_by_default():
    assert client_ip({"Forwarded": "for=6.6.6.6"}, "203.0.113.9") == "203.0.113.9"


def test_x_forwarded_for_wins_when_both_headers_exist():
    h = {"X-Forwarded-For": "1.1.1.1, 198.51.100.7", "Forwarded": "for=6.6.6.6"}
    assert client_ip(h, "10.0.0.1", trusted_proxies=1) == "198.51.100.7"


@pytest.mark.parametrize("value", [
    "for=unknown", "for=_hidden", "for=_gazonk", "for=", "by=203.0.113.43", "for=not-an-ip",
    '"for=198.51.100.7', 'for="[::1', "garbage", ";;;", ",,,", "for=1.1.1.1, for=unknown",
])
def test_forwarded_tokens_that_are_not_addresses_fall_back_to_the_socket(value):
    assert client_ip({"Forwarded": value}, "10.0.0.1", trusted_proxies=1) == "10.0.0.1"


def test_forwarded_counts_from_the_right_like_x_forwarded_for():
    value = "for=9.9.9.9, for=198.51.100.7, for=10.1.1.1"
    assert client_ip({"Forwarded": value}, "10.0.0.1", trusted_proxies=2) == "198.51.100.7"
    assert client_ip({"Forwarded": "for=198.51.100.7"}, "10.0.0.1", trusted_proxies=2) == "10.0.0.1"


def test_forwarded_quoted_commas_do_not_shift_the_elements():
    value = 'for=1.1.1.1;by="a,b", for=198.51.100.7'
    assert client_ip({"Forwarded": value}, "10.0.0.1", trusted_proxies=1) == "198.51.100.7"


def test_forwarded_huge_value_is_fast_and_safe():
    # Measures how time grows with size, not a fixed stopwatch limit: a fixed 2 s limit was flaky on slow CI
    # machines (2026-11-26). Linear parsing makes 10x the input take ~10x the time; quadratic would be ~100x.
    import time

    def run(n):
        best = float("inf")
        for _ in range(3):
            t = time.perf_counter()
            assert client_ip({"Forwarded": "for=1.1.1.1," * n + "for=198.51.100.7"}, "10.0.0.1", trusted_proxies=1) == "198.51.100.7"
            best = min(best, time.perf_counter() - t)
        return best

    small, big = run(20_000), run(200_000)
    assert big / max(small, 1e-4) < 40, (small, big)
    assert big < 20
