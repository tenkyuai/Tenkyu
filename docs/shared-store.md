# Shared network store

Status: `PostgresStore` is built (`src/tenkyu/postgres_store.py`, optional extra `tenkyu[postgres]`) and tested against a real Postgres 16 in CI. A Redis store is still a design only; the Redis section below is that design. This note explains how a store plugs into Tenkyu so several servers see one signup history, and why `PostgresStore` is built the way it is.

## Using PostgresStore
```python
import os
from tenkyu import Guard, PostgresStore

# key_secret: 16+ secret bytes from your environment or secret manager, never stored in the database.
# Make one with: python -c "import secrets; print(secrets.token_hex(32))"
store = PostgresStore("postgresql://user:pass@host/db", prefix="acme", timeout=5.0,
                      key_secret=os.environ["TENKYU_KEY_SECRET"].encode())
guard = Guard(store=store)
```
- `prefix` is the namespace: one per customer when many share a database. Every row stores its prefix and every query, expiry sweep and lock is scoped to it by equality, so a customer cannot read another's events. A prefix that starts with another prefix is still a different customer.
- `timeout` is how long to wait to connect and for the prefix lock. On expiry `check` raises `psycopg.errors.LockNotAvailable` and records nothing.
- `table` (default `tenkyu_events`) renames the table. It is created idempotently on first use, under an advisory lock so 16 processes opening an empty database at once do not trip over each other. If the table already exists nothing is created, so the app role needs no DDL rights (`SELECT, INSERT, UPDATE, DELETE` plus `USAGE` on the id sequence).
- Data model: `(id bigserial, ns text, key bytea, ts double precision, value text)`, indexes on `(ns, key, ts)` and `(ns, ts)`. Keys are stored as bytes, so NUL and lone surrogates (which a Postgres `text` column rejects) round-trip. Keys over 256 bytes are stored as their SHA-256, because an attacker controls the length of an email or user-agent and a B-tree entry cannot exceed about 2.7 kB. Values are JSON text, like the SQLite store.
- `ts` comes from the `clock` you pass (default `time.time` on the app server), not from the database's `now()`, so tests can inject a clock. Keep your servers' clocks in sync.
- Retention and the 2,000-events-per-key cap run inside `add` (same as the other stores); the sweep for keys that never return runs on open and every 1,000 adds, deletes only your prefix, and uses `FOR UPDATE SKIP LOCKED` so sweeps never wait on each other.

### Keeping raw values out of the table
`key_secret` is required. Without it the table would hold raw emails, IPs and device ids as keys, so `PostgresStore` raises `ValueError` unless you pass `allow_raw_keys=True` (a throwaway database, a test).

`PostgresStore(dsn, prefix=..., key_secret=<16+ secret bytes>)` stores no raw email, IP or device id. The variable part of every key (everything after the first ":") and every value is HMAC-SHA256'd with the secret (hex, truncated to 32 characters); the kind before the first ":" (`email`, `ip`, `devemail`, ...) stays readable so you can still prune and debug by kind. `BlindStore(inner, secret)` in `tenkyu.store` does the same for any store. Equal inputs still give equal outputs, so counts, windows and the 2,000-per-key cap are unchanged, and the benchmark replay gives the same decision for every row with and without it.
- `store.blind(raw)` returns the form a raw value has in `values()` results (the identity for stores that keep raw values). `Guard` calls it before it unions the current normalized email into `values()` results (`device_reuse`, `pattern_emails`) and before it looks for the `"plain"` alias marker; a custom signal or store that compares a raw value with `values()` must do the same. A store without `blind()` is treated as keeping raw values.
- The secret is the only thing that makes the table unreadable, so keep it out of the database (the hosted service derives it from `MASTER_SECRET` and a per-customer secret). With it, someone can confirm a guessed email; without it, the table is 128-bit hashes. A new secret orphans the history.
- `PostgresStore(None, connection=conn, key_secret=...)` borrows an autocommit connection from your own pool; the table must already exist.

### Locking
`transaction()` opens a Postgres transaction (READ COMMITTED), sets `lock_timeout`, and takes `pg_advisory_xact_lock` on a 64-bit id derived from the table name and prefix as its first statements. One lock per prefix, held until COMMIT or ROLLBACK.
- `SELECT ... FOR UPDATE` cannot close this race: two requests that both see no row for an email and then both insert one have nothing to lock. The lock has to exist before the data does.
- One lock per key would need the keys up front, but `Guard.check` discovers which of its keys (email, ip, device, aliases, links) it needs while it runs, and taking locks as they are found invites deadlocks. A single lock per prefix is the only lock a transaction takes, so it cannot deadlock.
- Different prefixes never block each other. Requests inside one prefix serialize; a check is a handful of indexed reads and writes, so that is milliseconds.
- The `_xact_` variant is released automatically when the connection dies; a session-level lock held by a crashed process would not be.
- READ COMMITTED matters: the lock is the first statement, so every later statement takes a fresh snapshot that includes what the previous holder committed. Under REPEATABLE READ the snapshot would predate the wait and the check would read stale counts.

What the tests cover (`tests/test_postgres_store.py`, runs when `TENKYU_TEST_PG_DSN` is set): the shared store suite and `tenkyu.testing.StoreConformance`, namespace isolation, 16 processes and 16 threads opening an empty database at once, 8 to 16 parallel `Guard.check` calls (shared store and one connection each) letting exactly one through, a second server blocking an alias the first recorded, odd and huge keys, and reconnect after the server drops the connection.

## Why
`MemoryStore` is per process and `SQLiteStore` is per machine. A farmer whose signups land on different servers is invisible to each of them. The README lists this under "Honest limits".

## The interface a store must implement
`Guard` only uses four calls (see `src/tenkyu/store.py`):

| Call | Meaning |
|---|---|
| `add(key, value=None, ts=None)` | remember one event under `key` at time `ts` (default: now) |
| `count(key, window, now=None)` | number of events with `now - window <= ts <= now` |
| `distinct(key, window, now=None)` | number of distinct values in that window |
| `values(key, window, now=None)` | the set of values in that window |
| `blind(value)` (optional) | the form a raw value takes in `values()` results: identity unless the store hashes what it keeps |

Values must survive a round trip as hashable Python values (strings in practice: normalized emails).

## Test your own store
`tenkyu.testing.StoreConformance` is the contract as runnable tests (stdlib `unittest`, works under pytest). Mix it into a test case and say how to build your store:

```python
import unittest
from tenkyu.testing import StoreConformance

class TestMyStore(StoreConformance, unittest.TestCase):
    def make_store(self, clock, retention):
        return MyStore(clock=clock, retention=retention)  # fresh and empty
```

It checks window edges, `None`/str/int values, non-finite timestamps, retention, the 2,000-event cap per key, parallel adds, that `transaction()` really makes a read-then-write atomic across 12 threads (and stays usable after an exception), and that separate `Guard`s sharing the store let only one of 10 parallel aliases through. A store with no `transaction()` skips those tests. `tests/test_conformance.py` runs it against `MemoryStore`, `SQLiteStore` and a small dict-backed store, and also checks that it fails for a lock-less `transaction()`, a store with no cap, no expiry, or one that trusts `NaN` timestamps. What it cannot check: behavior across several machines, so run it against a real Redis or Postgres too.

## Redis mapping
- One sorted set per key. Score = timestamp. Member = `"<ts>:<random nonce>:<value>"`, because the same value can repeat and sorted-set members must be unique.
- `add`: `ZADD`, then `ZREMRANGEBYSCORE key -inf (ts - retention)`, then `ZREMRANGEBYRANK key 0 -2001` to keep the newest 2,000, then `EXPIRE key retention`.
- `count`: `ZCOUNT key (now - window) now`.
- `values` / `distinct`: `ZRANGEBYSCORE`, strip the prefix from each member, and build a set.
- Use Redis `TIME` for `now` so servers with drifting clocks agree.

## Postgres mapping (the first sketch; the built store differs as described above)
- Table `events(key text, ts double precision, value text)` with an index on `(key, ts)`.
- `count`: `SELECT count(*) ... WHERE key = $1 AND ts BETWEEN $2 AND $3`. `distinct`: `count(DISTINCT value)`. `values`: `SELECT DISTINCT value`.
- Retention and the 2,000-event cap run as a periodic delete, not on every `add`.
- Use `now()` from the database, not the app server.

## Open design questions (decisions for the owner)
1. **Race between read and write.** On one machine this is closed: `Guard.check` runs read-then-record inside `store.transaction()` (a lock for `MemoryStore`, `BEGIN IMMEDIATE` for `SQLiteStore`, so other threads and processes wait). Before that, 16 parallel aliases of one person let in 8 on average through a shared `SQLiteStore`, so "farming is rarely that tightly timed" was wrong for scripted requests. A networked store must provide the same guarantee: implement `transaction()` as one atomic step (Redis `MULTI`/Lua; `PostgresStore` uses a transaction with an advisory lock per prefix). A store without `transaction()` still works, but only one `check` runs at a time per `Guard`, and several servers can race.
2. **Store down.** Fail open (allow the signup, log loudly) or fail closed (send to review)? Failing open protects real users; failing closed protects credits. Suggested default: fail open, with a counter so you notice.
3. **Latency.** Each check makes about six reads and up to five writes. Over a network that adds up; batching into one pipeline round trip is the likely first optimization.
4. **Privacy.** Stored keys contain normalized emails, IPs and device ids. A shared store needs a retention limit, access control and a deletion path for user requests.
5. **Where it lives.** Decided for Postgres: it ships in the core package as an optional extra, with the driver imported lazily, so the core stays dependency-free. A Redis store could follow the same pattern.

## What would count as done
A store class that passes the contract tests against a real Redis or Postgres in CI, a two-process test showing one server blocks an alias the other recorded, and a README section that says what was and was not tested. Postgres meets this (the CI job `postgres` runs the tests against a `postgres:16` service container); Redis does not exist yet.
