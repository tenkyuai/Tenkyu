# Attack suite (library level)

Each file turns one family of attack drills from the 2026-11-26 and 2026-11-27 red-team runs into plain assertions.
They import only `tenkyu`, need no database and no network, and run in under a second. CI and
`scripts/release_check.sh` collect them with the rest of `tests/`, so every release runs them.

```text
python -m pytest -q -W ignore::UserWarning tests/attacks
```

| File | Attack it encodes |
|---|---|
| `test_attack_alias_families.py` | one inbox under many addresses: Gmail, Yahoo, Fastmail, iCloud, Proton; Microsoft stays unmerged |
| `test_attack_unicode.py` | fullwidth letters, soft hyphen, zero-width and bidi characters; the Cyrillic limit pinned |
| `test_attack_link_variants.py` | one phone or card written many ways; HMAC links; the national-number limit pinned |
| `test_attack_ip_evasion.py` | ports, IPv6 /64 and /48 rotation, mapped IPv4, spoofed `X-Forwarded-For` |
| `test_attack_false_positives.py` | honest users: campus, office, busy-network crowds, a family on one device, CUBOT phones |
| `test_attack_owner_after_alias.py` | a squatter's alias must not lock out the real owner, nor give one inbox two full grants |
| `test_attack_email_validity.py` | malformed email blocks; a missing email (phone sign-in) does not |
| `test_attack_hostile_input.py` | huge ids, lone surrogates with SQLite, raising or junk custom signals, dry runs |
| `test_attack_known_limits.py` | the honest limits, pinned: a patient farmer with fresh everything still gets through |

Two kinds of test live here. Most assert that a defence holds. The ones named `documented_limit`, and everything in
`test_attack_known_limits.py`, pass when Tenkyu fails, on purpose, so the README's "Honest limits" cannot drift from the
code. If one of those starts failing, the defence got better: update the README and
`docs/threat-model-and-attacks.md`, then the test.

Shared helpers are in `conftest.py` (a frozen clock and a `signup(...)` factory). Keep new files small and library-only.

## Not duplicated here

The hosted service has its own attack tests. They need Postgres (`TENKYU_TEST_PG_DSN`, see `hosted/README.md`) and run in
the CI Postgres job, so they are not repeated in this folder:

- `hosted/tests/test_api_redteam.py`: network poisoning, rate limits and unknown-key floods, backdated `ts`, `request_id` reuse, malformed input.
- `hosted/tests/test_api_review.py`: lock contention, pool starvation, fail-open rules, network disputes and opt-out.
- `hosted/tests/test_billing.py`: ledger races, replayed and forged webhooks, refunds, test-mode keys.
- `hosted/tests/test_watcher.py`: prompt injection, secret leakage, escalation rules, read-only database role.

The dashboard and audit page drills are in `hosted/tests/test_dashboard.py` and `hosted/tests/test_audit_page.py`.
