"""Decision log: one JSON line per signup decision, for reviewing a shadow-mode run.

The log never holds a raw email, IP or device id. Pass `DecisionLog(path, secret=<16+ secret bytes>)` and each line also
carries `email_hash`: the first 32 hex characters of HMAC-SHA256(secret, normalized email), which lets `tenkyu report`
count the same inbox signing up again but cannot be checked against a guessed address without the secret. Without a
secret there is no `email_hash` at all (an unkeyed hash of an email can be confirmed by anyone who guesses the address),
and the report says the repeat count is not tracked. Lines written by older versions (an unkeyed 16-hex hash) still read."""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import re
import threading
from collections import Counter
from typing import Iterable, List, Tuple

from .signals import normalize_email

ACTIONS = ("allow", "review", "block")


def _email_hash(email, secret: bytes) -> str | None:
    """Keyed hash of the normalized email (None for an invalid one). Needs the log's secret."""
    norm = normalize_email(email)
    return hmac.new(secret, b"email_hash:" + norm.encode("utf-8", "surrogatepass"), hashlib.sha256).hexdigest()[:32] if norm else None


def decision_id(rec: dict) -> str:
    """Id for a line written by an older version, which had no decision_id field: a short hash of its time, email hash,
    actions and score, so the template and the report agree. New lines carry a random id instead, because this hash
    lets someone holding the template confirm a guessed email for an old line."""
    basis = json.dumps([rec.get("ts"), rec.get("email_hash"), rec.get("action"), rec.get("would_action"), rec.get("score")])
    return "d_" + hashlib.sha256(basis.encode("utf-8", "surrogatepass")).hexdigest()[:12]


class DecisionLog:
    """Append decisions to a JSONL file. Pass it as Guard(log=DecisionLog("decisions.jsonl"))."""

    def __init__(self, path, secret: bytes | None = None):
        """`secret`: at least 16 secret bytes, kept out of the log file. With it every line carries a keyed `email_hash`;
        without it the lines have none. Keep it stable: a new secret starts new hashes."""
        if secret is not None and (not isinstance(secret, (bytes, bytearray)) or len(secret) < 16):
            raise ValueError("DecisionLog secret must be bytes, at least 16 bytes long")
        self.path = path
        self._secret = None if secret is None else bytes(secret)
        self._lock = threading.Lock()

    def write(self, signup, decision, ts: float) -> None:
        would_action = decision.shadow_action or decision.action
        would_credit = decision.would_credit_fraction if decision.would_credit_fraction is not None else decision.credit_fraction
        line = {"ts": ts, "action": decision.action, "would_action": would_action,
                "shadow": decision.shadow_action is not None, "score": round(decision.score, 3),
                "credit_fraction": decision.credit_fraction, "would_credit_fraction": would_credit, "limit": decision.would_limit or decision.limit,
                "reasons": list(decision.reasons), "signals": [s.name for s in decision.signals]}
        if self._secret is not None:
            email_hash = _email_hash(signup.email, self._secret)
            if email_hash is not None:
                line["email_hash"] = email_hash
        line["decision_id"] = "d_" + secrets.token_hex(6)  # random: a hash of the line would confirm a guessed email
        with self._lock, open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, sort_keys=True) + "\n")


def read_log(path) -> Tuple[List[dict], int]:
    """Return (records, number of unreadable lines)."""
    records, bad = [], 0
    with open(path, encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            if not raw.strip():
                continue
            try:
                rec = json.loads(raw)
                if not isinstance(rec, dict) or rec.get("would_action") not in ACTIONS:
                    raise ValueError
                if not isinstance(rec.get("reasons", []), list) or not isinstance(rec.get("signals", []), list):
                    raise ValueError
                float(rec.get("would_credit_fraction", 1.0))
            except (ValueError, TypeError):
                bad += 1
                continue
            records.append(rec)
    return records, bad


def summarize(records: Iterable[dict], grant_usd: float = 0.0) -> dict:
    """Totals by would-be action, top reasons (numbers folded to N), the free credits Tenkyu would withhold from
    flagged signups, and, separately, credits held back from allowed users until they verify (not counted as abuse)."""
    records = list(records)
    by_action = Counter(r["would_action"] for r in records)
    reasons = Counter(re.sub(r"\d+", "N", reason) for r in records for reason in r.get("reasons") or [])
    hashes = [r["email_hash"] for r in records if r.get("email_hash")]
    gap = lambda r: 1.0 - min(1.0, max(0.0, float(r.get("would_credit_fraction", 1.0))))
    withheld = sum(gap(r) for r in records if r["would_action"] != "allow")  # from flagged signups: the abuse figure
    held = sum(gap(r) for r in records if r["would_action"] == "allow")  # starter budget or surge guard on allowed users
    return {"decisions": len(records), "by_action": {a: by_action.get(a, 0) for a in ACTIONS},
            "repeat_emails": (sum(c - 1 for c in Counter(hashes).values() if c > 1) if hashes else None),  # None: no hashes in the log
            "top_reasons": reasons.most_common(5), "grants_withheld": round(withheld, 2),
            "usd_withheld": round(withheld * grant_usd, 2), "grants_held_until_verified": round(held, 2),
            "usd_held_until_verified": round(held * grant_usd, 2), "shadow": any(r.get("shadow") for r in records)}
