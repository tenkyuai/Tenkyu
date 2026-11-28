"""Attack: change the address a little so each signup looks like a new IP."""
from tenkyu import ALLOW, BLOCK, REVIEW, Guard, signup_from_request
from tenkyu.guard import ip_key

from attack_helpers import T0, fired


def test_port_suffix_is_dropped_before_keying():
    assert ip_key("203.0.113.7:50000") == ip_key("203.0.113.7") == "203.0.113.7"
    assert ip_key("[2001:db8:abcd:1::5]:50000") == ip_key("2001:db8:abcd:1::9") == "2001:db8:abcd:1::/64"


def test_ipv4_mapped_ipv6_is_the_ipv4_address():
    assert ip_key("::ffff:203.0.113.7") == ip_key("203.0.113.7")


def test_rotating_source_ports_do_not_dodge_the_ip_burst(make_guard, signup):
    g = make_guard()
    out = [g.check(signup(f"v{i}x{i * 5}@outlook.com", f"203.0.113.7:{50000 + i}", f"d{i}", ts=i * 5)) for i in range(4)]
    # Distinct clean identities behind one IP look like a school or carrier NAT: partial credit, never full.
    assert out[-1].action == REVIEW and out[-1].credit_fraction <= 0.25 and "ip_burst" in fired(out[-1])


def test_rotating_source_ports_with_a_second_signal_still_block(make_guard, signup):
    g = make_guard()
    out = [g.check(signup(f"v{i}x{i * 5}@outlook.com", f"203.0.113.7:{50000 + i}", "same-device", ts=i * 5)) for i in range(4)]
    assert out[-1].action == BLOCK and "ip_burst" in fired(out[-1])


def test_rotating_ipv6_64s_inside_one_64_is_one_ip(make_guard, signup):
    g = make_guard()
    out = [g.check(signup(f"v{i}x{i * 5}@outlook.com", f"2001:db8:abcd:1::{i + 1:x}", f"d{i}", ts=i * 5)) for i in range(4)]
    # Distinct clean identities behind one IP look like a school or carrier NAT: partial credit, never full.
    assert out[-1].action == REVIEW and out[-1].credit_fraction <= 0.25 and "ip_burst" in fired(out[-1])


def test_rotating_source_ports_with_a_second_signal_still_block(make_guard, signup):
    g = make_guard()
    out = [g.check(signup(f"v{i}x{i * 5}@outlook.com", f"203.0.113.7:{50000 + i}", "same-device", ts=i * 5)) for i in range(4)]
    assert out[-1].action == BLOCK and "ip_burst" in fired(out[-1])


def test_rotating_64s_inside_one_48_still_count_as_one_network(make_guard, signup):
    def run(addr):
        g = make_guard()
        return [g.check(signup(f"x{i}@mailinator.com", addr(i), f"d{i}", ts=i * 30)) for i in range(6)]
    same_48 = run(lambda i: f"2001:db8:abcd:{i:x}::1")
    other_48s = run(lambda i: f"2001:db8:{i:x}:1::1")
    assert "network_burst" in fired(same_48[4])
    assert all("network_burst" not in fired(d) for d in other_48s)


def test_a_spoofed_forwarded_for_entry_is_ignored(make_guard):
    """One trusted proxy: Tenkyu takes the entry that proxy appended, not the one the client typed."""
    headers = {"User-Agent": "Mozilla/5.0 Chrome/120.0", "X-Forwarded-For": "1.1.1.1, 198.51.100.7"}
    assert signup_from_request("new@acme.io", headers, remote_addr="10.0.0.1", trusted_proxies=1).ip == "198.51.100.7"


def test_forwarded_for_is_ignored_when_no_proxy_is_trusted():
    headers = {"User-Agent": "Mozilla/5.0 Chrome/120.0", "X-Forwarded-For": "1.1.1.1"}
    assert signup_from_request("new@acme.io", headers, remote_addr="10.0.0.1").ip == "10.0.0.1"
