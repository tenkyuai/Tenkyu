"""Audit an app's own signup export offline: replay it through Tenkyu in time order and count the farming.

Nothing leaves the machine. The summary printed to the screen holds counts and reasons only; the optional
--out file lists the flagged rows from your own export so you can act on them."""
from __future__ import annotations

import csv
import math
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional

from .guard import ALLOW, BLOCK, REVIEW, Decision, Guard, Signup

MAX_ROWS = 500_000
SAMPLE_CSV = __import__("pathlib").Path(__file__).resolve().parent / "data" / "sample_signups.csv"
NEUTRAL_UA = "Mozilla/5.0 (tenkyu audit: no user-agent column)"
ALIASES = {
    "email": ("email", "email_address", "email address", "e-mail", "user_email", "primary_email_address", "mail"),
    "ip": ("ip", "ip_address", "ip address", "signup_ip", "remote_addr", "client_ip", "last_sign_in_ip"),
    "ts": ("created_at", "createdat", "created (utc)", "created_at (utc)", "inserted_at", "signup_date", "timestamp",
           "signup_time", "signed_up_at", "created", "date", "ts", "time"),
    "device": ("device_id", "visitor_id", "fingerprint", "device", "dev"),
    "ua": ("user_agent", "useragent", "user agent", "ua"),
}


def parse_ts(value) -> Optional[float]:
    """Epoch seconds or milliseconds, or ISO 8601 (with Z or an offset; naive times are UTC). None if unreadable."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        t = float(s)
        if not math.isfinite(t):
            return None
        return t / 1000.0 if t > 1e11 else t
    except ValueError:
        pass
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00").replace(" UTC", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _columns(header: List[str], links: Dict[str, str]) -> Dict[str, Optional[str]]:
    lower = {h.strip().lower(): h for h in header}
    found = {field: next((lower[a] for a in names if a in lower), None) for field, names in ALIASES.items()}
    for name, col in links.items():
        if col not in header:
            raise ValueError(f"--link {name}={col}: no column named {col!r}")
    if not found["email"] or not found["ts"]:
        raise ValueError(f"need an email column and a time column; found columns: {', '.join(header)}")
    return found


@dataclass
class AuditResult:
    rows: int
    bad_rows: int
    decisions: List[tuple]  # (original row index, Decision), in replay order
    has_ua: bool
    has_ip: bool
    has_device: bool
    unused_columns: tuple = ()

    def summary(self, grant_usd: float = 0.0) -> dict:
        acts = Counter(d.action for _, d in self.decisions)
        flagged = acts[REVIEW] + acts[BLOCK]
        import re
        reasons = Counter(re.sub(r"\d+", "N", r) for _, d in self.decisions if d.action != ALLOW for r in d.reasons[:1])
        n = len(self.decisions)
        return {"signups": n, "unreadable_rows": self.bad_rows, "allow": acts[ALLOW], "review": acts[REVIEW], "block": acts[BLOCK],
                "flagged_pct": round(100 * flagged / n, 1) if n else 0.0,
                "usd_at_stake": round(sum(1.0 - d.credit_fraction for _, d in self.decisions) * grant_usd, 2),
                "top_reasons": reasons.most_common(6),
                "unused_columns": list(self.unused_columns),
                "missing": [c for c, ok in (("user agent", self.has_ua), ("ip", self.has_ip), ("device id", self.has_device)) if not ok]}


def audit_csv(path, links: Optional[Dict[str, str]] = None, guard: Optional[Guard] = None) -> AuditResult:
    """Replay a signup CSV through Tenkyu in time order. links maps a link name to a column, e.g. {"card": "card_fp"}."""
    links = links or {}
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as fh:
        reader = csv.DictReader(fh, strict=True)
        try:
            if not reader.fieldnames:
                raise ValueError("the file has no header row")
            cols = _columns(reader.fieldnames, links)
            rows, bad = [], 0
            for i, row in enumerate(reader):
                if len(rows) >= MAX_ROWS:
                    raise ValueError(f"more than {MAX_ROWS:,} rows; split the file by date and audit each part")
                ts = parse_ts(row.get(cols["ts"]))
                if ts is None:
                    bad += 1
                    continue
                rows.append((ts, i, row))
        except csv.Error as e:
            raise ValueError(f"cannot parse CSV near row {reader.line_num}: {e}") from None
    rows.sort(key=lambda x: (x[0], x[1]))
    first = rows[0][0] if rows else 0.0
    g = guard or Guard(clock=lambda: first)
    out = []
    for ts, i, row in rows:
        lk = {name: row[col].strip() for name, col in links.items() if (row.get(col) or "").strip()}
        su = Signup(email=row.get(cols["email"]), ip=row.get(cols["ip"]) if cols["ip"] else None,
                    device_id=row.get(cols["device"]) if cols["device"] else None,
                    user_agent=row.get(cols["ua"]) if cols["ua"] else NEUTRAL_UA, ts=ts, links=lk or None)
        try:
            d: Decision = g.check(su)
        except ValueError:
            bad += 1
            continue
        out.append((i, d))
    used = {c for c in cols.values() if c} | set(links.values())
    unused = tuple(h for h in reader.fieldnames if h not in used)
    return AuditResult(len(rows), bad, out, cols["ua"] is not None, cols["ip"] is not None, cols["device"] is not None, unused)


def write_flagged(path_in, result: AuditResult, path_out) -> int:
    """Copy the flagged rows of the original CSV to path_out with tenkyu_action, tenkyu_score and tenkyu_reason added."""
    flagged = {i: d for i, d in result.decisions if d.action != ALLOW}
    with open(path_in, newline="", encoding="utf-8-sig", errors="replace") as fh, open(path_out, "w", newline="", encoding="utf-8") as fo:
        reader = csv.DictReader(fh, strict=True)
        w = csv.DictWriter(fo, fieldnames=list(reader.fieldnames) + ["tenkyu_action", "tenkyu_score", "tenkyu_reason"])
        w.writeheader()
        for i, row in enumerate(reader):
            d = flagged.get(i)
            if d:
                w.writerow({**row, "tenkyu_action": d.action, "tenkyu_score": f"{d.score:.2f}", "tenkyu_reason": d.reasons[0] if d.reasons else ""})
    return len(flagged)
