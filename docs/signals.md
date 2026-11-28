# Writing your own signal

A signal is a plain function that looks at one signup and says "this looks risky, by this much, for this reason", or says nothing. Tenkyu already ships a set of built-in signals (listed below). Add your own for rules that only your product has.

```text
Signal(name, score, reason)   # score is 0.0 to 1.0
def my_signal(signup, store, now): ...   # returns a Signal or None
```

- `signup` is the `Signup` being checked.
- `store` is the history Tenkyu keeps. You can read it with `count`, `distinct` and `values`.
- `now` is the timestamp of the signup in seconds.

## Built-in signals

One line each. Scores are 0 to 1. The README table has the thresholds.

- `alias_reuse`: another spelling of an inbox that already signed up (Gmail dots and plus, Yahoo, Fastmail, iCloud, Proton).
- `disposable_email`: a throwaway domain on the bundled list.
- `pattern_emails`: `farmer1@`, `farmer2@` from one IP.
- `ip_burst`, `ip_velocity`: many signups from one IP in a minute or an hour.
- `device_reuse`: several accounts on one device id. Soft: never blocks alone.
- `device_farm`: 6+ accounts on one device in 7 days from 3+ /24 networks (0.75). One network stays `device_reuse`.
- `automation_agent`, `no_user_agent`: scripted or missing user agent.
- `invalid_email`: a malformed address (blocked).
- `network_burst`, `network_day`: volume from one /24 (IPv6 /48). Soft; counted mostly next to another signal.
- `lookalike_domain` (0.75): a domain that imitates a big provider (Cyrillic or Greek letters, `0` for `o`, one typo, `gmail.con`, `gmailc.om`). Flagged, not merged with the real address.
- `mixed_script_email` (0.50): Latin letters mixed with Cyrillic or Greek in the name.
- `obfuscated_email`: invisible characters between Latin letters (0.75), fullwidth letters (0.6), other invisible or fullwidth characters (0.3), made-up subdomain of a known provider (0.6).
- `wide_spread`, `regular_timing`: rotating residential proxy signs (one ISP range, many /24s, one user agent; or signups on a timer). Soft; count only next to another real signal.
- `odd_domain` (0.3), `domain_burst` (0.3 to 0.5): a domain that looks auto-generated, or many addresses from one small unknown domain in a day. Soft; `domain_burst` alone never costs credit.

Clean, distinct identities behind one IP (a plain address at a big provider or school, a normal browser, no other signal) are handled before scoring is finalised: IP volume alone gives partial credit, never a block, up to 60 an hour and 200 a day per IP.

## A signal with no history

```python
from tenkyu import Guard, Signal, Signup

def support_lookalike(signup, store, now):
    if signup.email and signup.email.lower().endswith("@acme-support.io"):
        return Signal("support_lookalike", 0.8, "email domain imitates our support team")

guard = Guard(extra_signals=[support_lookalike])
d = guard.check(Signup(email="help@acme-support.io", ip="203.0.113.5", user_agent="Mozilla/5.0 Chrome/120.0"))
print(d.action, d.reasons)  # block ['email domain imitates our support team']
assert d.action == "block" and d.reasons == ["email domain imitates our support team"]
```

## A signal that reads the history

This one is stricter than the built-in IP check: it flags a second signup from one address within ten minutes. It reads the `ip:` key that Tenkyu fills itself.

```python
from tenkyu import Guard, Signal, Signup, ip_key

def quick_repeat(signup, store, now):
    if signup.ip and store.count(f"ip:{ip_key(signup.ip)}", 600, now) >= 1:
        return Signal("quick_repeat", 0.6, "second signup from this IP within 10 minutes")

guard = Guard(extra_signals=[quick_repeat])
UA = "Mozilla/5.0 Chrome/120.0"
guard.check(Signup(email="one@acme.io", ip="198.51.100.4", user_agent=UA, ts=1_700_000_000))
d = guard.check(Signup(email="two@acme.io", ip="198.51.100.4", user_agent=UA, ts=1_700_000_120))
print(d.action, d.reasons)  # review ['second signup from this IP within 10 minutes']
assert d.action == "review"
```

The key names (`email:`, `ip:`, `ipstem:`, `devemail:`) are internal and may change before version 1.0. If you need your own history, keep it in your own store or table.

## Rules your function can rely on

- Return `None` when there is nothing to say.
- If your function raises, or returns anything other than a `Signal` with a number for its score, Tenkyu ignores it. A bug in a custom signal cannot break your signup flow.
- A score outside 0 to 1 is clamped, and `nan` counts as 0. A missing reason is replaced by the signal's name.
- Scores combine with the built-in ones, so several weak signals add up and one strong signal is enough alone. Under 0.35 allows, 0.35 to 0.7 reviews, 0.7 and up blocks.
- Names do not matter: only Tenkyu's own soft signals (`device_reuse`, `network_*`, `wide_spread`, `regular_timing`, `odd_domain`, `domain_burst`) never block alone. A custom signal called `device_reuse` or `network_proxy` blocks like any other.
- A signal can only raise risk. To let some users skip checks (staff, a trusted partner), decide that before you call `check`.
- Signals run on every `check`, including dry runs (`record=False`). Keep them quick and offline. If a signal writes to the store itself, a dry run writes too.
