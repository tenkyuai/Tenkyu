"""`tenkyu evaluate`: measure Tenkyu's accuracy on your own labelled signup export, offline.

Give it a CSV with a label column (farmer or real). Rows are replayed in time order through a fresh Guard, then every
decision is compared with your label. Flagged means review or block. Nothing leaves the machine."""
from __future__ import annotations

import csv
import math
import re
from typing import Dict, Iterable, List, Optional, Tuple

from .audit import ALIASES, MAX_ROWS, NEUTRAL_UA, parse_ts
from .decision_log import ACTIONS
from .guard import ALLOW, BLOCK, Guard, Signup

PRESETS = ("default", "ai")
FARMER_LABELS = {"farmer", "1", "true", "yes", "fraud"}
REAL_LABELS = {"real", "0", "false", "no", "legit"}
COLUMN_ALIASES = {
    **ALIASES,
    "phone": ("phone", "phone_number", "phone number", "mobile"),
    "card": ("card", "card_fingerprint", "card_fp", "card_id"),
    "label": ("label", "is_farmer", "farmer", "is_fraud", "fraud", "ground_truth", "truth"),
    "tactic": ("tactic", "attack", "farm_type", "group"),
}
UNSURE_LABELS = {"not sure", "notsure", "unsure", "unknown", "unclear", "?"}
LOW_LABELS = 50  # below this many labelled decisions the percentages are too noisy to act on
# Share of the grant held back per action when only the action is known (Guard's default credit_fractions).
DEFAULT_WITHHELD = {"allow": 0.0, "review": 0.75, "block": 1.0}
TRUE_WORDS, FALSE_WORDS = {"true", "1", "yes", "y", "t"}, {"false", "0", "no", "n", "f"}
NO_LABEL_HELP = ("no label column found; add a column named label with farmer or real in each row "
                 "(1/0, true/false, yes/no, fraud/legit also work)")
_LINK_SECRET = b"tenkyu-evaluate-replay"  # fixed so the replay is repeatable and never warns about plain-hash links
NO_TACTIC = "(none)"


def parse_label(value) -> Optional[bool]:
    """True for a farmer, False for a real user, None if the value is empty or not recognised."""
    s = str(value if value is not None else "").strip().lower()
    if s in FARMER_LABELS:
        return True
    if s in REAL_LABELS:
        return False
    try:
        f = float(s)
    except ValueError:
        return None
    return True if f == 1 else False if f == 0 else None


def _no_usable_rows() -> str:
    labels = ", ".join(sorted(FARMER_LABELS) + sorted(REAL_LABELS))
    return f"no rows had a usable label and time; accepted labels: {labels}"


def _resolve(header: Iterable[str]) -> Dict[str, Optional[str]]:
    lower = {str(h).strip().lower(): h for h in header}
    cols = {f: next((lower[a] for a in names if a in lower), None) for f, names in COLUMN_ALIASES.items()}
    if not cols["label"]:
        raise ValueError(NO_LABEL_HELP)
    if not cols["email"] or not cols["ts"]:
        raise ValueError(f"need an email column and a time column; found columns: {_safe_header(header)}")
    return cols


def _make_guard(preset: str, clock) -> Guard:
    if preset == "default":
        return Guard(clock=clock, link_secret=_LINK_SECRET)
    if preset == "ai":
        return Guard.for_ai_apps(clock=clock, link_secret=_LINK_SECRET)
    raise ValueError(f"unknown preset {preset!r}; use one of: {', '.join(PRESETS)}")


def _rate(num: int, den: int) -> Optional[float]:
    return round(num / den, 4) if den else None


def _cell(row: dict, col: Optional[str]) -> str:
    v = row.get(col) if col else None
    return "" if v is None else str(v).strip()


def evaluate_rows(rows: List[dict], preset: str = "default", grant: float = 3.0,
                  columns: Optional[List[str]] = None) -> dict:
    """Score labelled rows (dicts keyed like the CSV columns) and return accuracy numbers as a plain dict.

    columns: the header, when known (a header-only file has no rows to read it from)."""
    if preset not in PRESETS:
        raise ValueError(f"unknown preset {preset!r}; use one of: {', '.join(PRESETS)}")
    if not isinstance(grant, (int, float)) or not math.isfinite(grant) or grant < 0:
        raise ValueError(f"grant must be a finite number of dollars, 0 or more; got {grant!r}")
    header: List[str] = list(columns or [])
    for r in rows:
        for k in r:
            if k not in header:
                header.append(k)
    cols = _resolve(header)
    if len(rows) > MAX_ROWS:
        raise ValueError(f"more than {MAX_ROWS:,} rows; split the file and evaluate each part")
    skipped: Dict[str, int] = {}

    def skip(reason: str) -> None:
        skipped[reason] = skipped.get(reason, 0) + 1

    usable = []
    for i, row in enumerate(rows):
        farmer = parse_label(row.get(cols["label"]))
        if farmer is None:
            skip("label empty or not recognised")
            continue
        ts = parse_ts(row.get(cols["ts"]))
        if ts is None:
            skip("no readable time")
            continue
        usable.append((ts, i, row, farmer))
    if not usable:
        raise ValueError(_no_usable_rows())
    usable.sort(key=lambda x: (x[0], x[1]))

    now = [usable[0][0]]
    guard = _make_guard(preset, lambda: now[0])
    # per group counts: farmers, real, farmers flagged, farmers blocked, real flagged, real blocked
    total = {"farmers": 0, "real": 0, "tp": 0, "tp_block": 0, "fp": 0, "fp_block": 0}
    groups: Dict[str, dict] = {}
    withheld = {"farmers": 0.0, "real": 0.0}
    used = 0
    for ts, _, row, farmer in usable:
        now[0] = ts
        links = {n: v for n, v in (("phone", _cell(row, cols["phone"])), ("card", _cell(row, cols["card"]))) if v}
        su = Signup(email=_cell(row, cols["email"]) or None, ip=_cell(row, cols["ip"]) or None,
                    device_id=_cell(row, cols["device"]) or None,
                    user_agent=_cell(row, cols["ua"]) or (None if cols["ua"] else NEUTRAL_UA), ts=ts, links=links or None)
        try:
            d = guard.check(su)
        except ValueError:
            skip("rejected by Tenkyu as invalid (bad email, ip or link value)")
            continue
        used += 1
        flagged, blocked = d.action != ALLOW, d.action == BLOCK
        who = "farmers" if farmer else "real"
        withheld[who] += (1.0 - d.credit_fraction) * grant
        g = groups.setdefault(_cell(row, cols["tactic"]) or NO_TACTIC, dict.fromkeys(total, 0))
        for bucket in (total, g):
            bucket[who] += 1
            if flagged:
                bucket["tp" if farmer else "fp"] += 1
            if blocked:
                bucket["tp_block" if farmer else "fp_block"] += 1

    t = total
    flagged_n, blocked_n = t["tp"] + t["fp"], t["tp_block"] + t["fp_block"]
    out = {
        "preset": preset, "grant_usd": grant,
        "rows_total": len(rows), "rows_used": used, "rows_skipped": len(rows) - used, "skipped_reasons": skipped,
        "farmers": t["farmers"], "real": t["real"],
        "flagged": flagged_n, "blocked": blocked_n,
        "precision": _rate(t["tp"], flagged_n), "recall": _rate(t["tp"], t["farmers"]),
        "honest_flagged": t["fp"], "honest_flagged_rate": _rate(t["fp"], t["real"]),
        "blocked_only": {"precision": _rate(t["tp_block"], blocked_n), "recall": _rate(t["tp_block"], t["farmers"]),
                         "honest_flagged": t["fp_block"], "honest_flagged_rate": _rate(t["fp_block"], t["real"])},
        "usd_withheld_from_farmers": round(withheld["farmers"], 2),
        "usd_withheld_from_real": round(withheld["real"], 2),
    }
    if cols["tactic"]:
        out["tactics"] = {name: {"farmers": g["farmers"], "real": g["real"],
                                 "recall": _rate(g["tp"], g["farmers"]), "blocked_recall": _rate(g["tp_block"], g["farmers"]),
                                 "false_positive_rate": _rate(g["fp"], g["real"]),
                                 "blocked_false_positive_rate": _rate(g["fp_block"], g["real"])}
                          for name, g in sorted(groups.items())}
    return out


def label_state(value) -> Optional[str]:
    """"farmer", "real", "unsure" (the dashboard's "not sure"), or None if empty or not recognised."""
    plain = str(value if value is not None else "").strip().lower()
    if re.sub(r"[\s_\-]+", " ", plain) in UNSURE_LABELS:
        return "unsure"
    farmer = parse_label(plain)  # the plain value: folding "-1" to " 1" would turn an "unknown" code into a farmer
    return None if farmer is None else ("farmer" if farmer else "real")


def _safe_header(header: Iterable[str]) -> str:
    """Column names for an error message: printable, short, one line (the header is untrusted input)."""
    text = ", ".join(re.sub(r"[^\x20-\x7e\u00a0-\uffff]", "?", str(h))[:40] for h in list(header)[:30])
    return text or "(none)"


def detects_decisions_export(header: Iterable[str]) -> bool:
    """A file of recorded decisions (hosted label export): an action column and no email or ip column."""
    lower = {str(h).strip().lower() for h in header}
    return "action" in lower and not lower & set(COLUMN_ALIASES["email"] + COLUMN_ALIASES["ip"])


def score_decisions(items: Iterable[Tuple[str, bool, float]], grant: float, skipped: Dict[str, int], rows_total: int,
                    basis: str) -> dict:
    """Accuracy of recorded decisions. items: (action, is_farmer, share of the grant withheld) per labelled decision.
    Flagged means review or block. Money is counted for flagged decisions only."""
    t = {"farmers": 0, "real": 0, "tp": 0, "tp_block": 0, "fp": 0, "fp_block": 0}
    money = {"farmers": 0.0, "real": 0.0}
    for action, farmer, frac in items:
        who = "farmers" if farmer else "real"
        t[who] += 1
        if action != ALLOW:
            t["tp" if farmer else "fp"] += 1
            money[who] += max(0.0, min(1.0, frac)) * grant
        if action == BLOCK:
            t["tp_block" if farmer else "fp_block"] += 1
    used = t["farmers"] + t["real"]
    flagged_n, blocked_n = t["tp"] + t["fp"], t["tp_block"] + t["fp_block"]
    return {
        "mode": "decisions", "grant_usd": grant, "withheld_basis": basis,
        "rows_total": rows_total, "rows_used": used, "rows_skipped": rows_total - used, "skipped_reasons": skipped,
        "farmers": t["farmers"], "real": t["real"], "flagged": flagged_n, "blocked": blocked_n,
        "precision": _rate(t["tp"], flagged_n), "recall": _rate(t["tp"], t["farmers"]),
        "honest_flagged": t["fp"], "honest_flagged_rate": _rate(t["fp"], t["real"]),
        "blocked_only": {"precision": _rate(t["tp_block"], blocked_n), "recall": _rate(t["tp_block"], t["farmers"]),
                         "honest_flagged": t["fp_block"], "honest_flagged_rate": _rate(t["fp_block"], t["real"])},
        "usd_withheld_from_farmers": round(money["farmers"], 2), "usd_withheld_from_real": round(money["real"], 2),
        "min_labels": LOW_LABELS, "low_labels": used < LOW_LABELS,
    }


def _bool_cell(value) -> Optional[bool]:
    s = str(value if value is not None else "").strip().lower()
    return True if s in TRUE_WORDS else False if s in FALSE_WORDS else None


def evaluate_decision_rows(rows: List[dict], grant: float = 3.0, columns: Optional[List[str]] = None) -> dict:
    """Score the decisions Tenkyu already recorded (the hosted dashboard's label export) against your labels.

    Columns: action, label, and optionally would_action and shadow. On a shadow row the decision that counts is
    would_action (what Tenkyu would have done), because action is always allow there. Rows labelled "not sure" are
    skipped and counted. Nothing is re-run through the guard."""
    if not isinstance(grant, (int, float)) or not math.isfinite(grant) or grant < 0:
        raise ValueError(f"grant must be a finite number of dollars, 0 or more; got {grant!r}")
    header: List[str] = list(columns or [])
    for r in rows:
        for k in r:
            if k not in header:
                header.append(k)
    lower = {str(h).strip().lower(): h for h in header}
    label_col = next((lower[a] for a in COLUMN_ALIASES["label"] if a in lower), None)
    if not label_col:
        raise ValueError(NO_LABEL_HELP)
    if "action" not in lower:
        raise ValueError(f"need an action column (allow, review or block); found columns: {_safe_header(header)}")
    if len(rows) > MAX_ROWS:
        raise ValueError(f"more than {MAX_ROWS:,} rows; split the file and evaluate each part")
    action_col, would_col, shadow_col = lower["action"], lower.get("would_action"), lower.get("shadow")
    skipped: Dict[str, int] = {}

    def skip(reason: str) -> None:
        skipped[reason] = skipped.get(reason, 0) + 1

    items = []
    for row in rows:
        state = label_state(row.get(label_col))
        if state == "unsure":
            skip('labelled "not sure"')
            continue
        if state is None:
            skip("label empty or not recognised")
            continue
        shadow = _bool_cell(row.get(shadow_col)) if shadow_col else False
        raw = row.get(would_col) if shadow and would_col else row.get(action_col)
        action = str(raw if raw is not None else "").strip().lower()
        if action not in ACTIONS:
            skip("no readable action")
            continue
        items.append((action, state == "farmer", DEFAULT_WITHHELD[action]))
    if not items:
        raise ValueError("no rows had a usable label and action; " + ("; ".join(f"{n:,} {r}" for r, n in skipped.items()) or "the file has no rows"))
    return score_decisions(items, grant, skipped, len(rows), "estimated from the action (review holds back 75%, block 100%)")


def evaluate_csv(path, preset: str = "default", grant: float = 3.0, decisions: bool = False) -> dict:
    """Read a labelled CSV and score it. A signup export (email and time columns) is replayed through a fresh Guard.
    A decisions export (action column, no email or ip: the hosted label export) is scored as recorded; that mode is
    picked automatically, or forced with decisions=True."""
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as fh:
        reader = csv.DictReader(fh, strict=True)
        try:
            fields = reader.fieldnames
            if not fields:
                raise ValueError("the file has no header row")
            decisions = decisions or detects_decisions_export(fields)
            if not decisions:
                _resolve(fields)  # fail on a missing label before reading the rows
            rows = []
            for row in reader:
                if len(rows) >= MAX_ROWS:
                    raise ValueError(f"more than {MAX_ROWS:,} rows; split the file and evaluate each part")
                rows.append(row)
        except csv.Error as e:
            raise ValueError(f"cannot parse CSV near row {reader.line_num}: {e}") from None
    if decisions:
        return evaluate_decision_rows(rows, grant=grant, columns=list(fields))
    return evaluate_rows(rows, preset=preset, grant=grant, columns=list(fields))


def _pct(x: Optional[float]) -> str:
    return "n/a" if x is None else f"{100 * x:.1f}%"


def format_report(r: dict) -> str:
    """Plain-text version of an evaluate result."""
    b = r["blocked_only"]

    def row(label: str, value: str, note: str = "") -> str:
        return f"  {label:<30}{value:>10}" + (f"   {note}" if note else "")

    decisions = r.get("mode") == "decisions"
    if decisions:
        lines = [f"Scored {r['rows_used']:,} recorded decisions against your labels ({r['farmers']:,} farmers, "
                 f"{r['real']:,} real users); not re-run through the guard; nothing left this machine"]
        if r.get("low_labels"):
            lines.append(f"WARNING: only {r['rows_used']:,} labelled decisions (fewer than {r['min_labels']}). Too few to trust "
                         "these percentages; label more before deciding whether to enforce.")
        if not r["farmers"] or not r["real"]:
            lines.append("WARNING: labels cover only one kind of user, so precision, recall or the false-positive rate cannot be read.")
    else:
        lines = [f"Evaluated {r['rows_used']:,} labelled signups ({r['farmers']:,} farmers, {r['real']:,} real users), "
                 f"replayed in time order, preset {r['preset']}; nothing left this machine"]
    if r["rows_skipped"]:
        why = "; ".join(f"{n:,} {reason}" for reason, n in r["skipped_reasons"].items())
        lines.append(f"Skipped {r['rows_skipped']:,} of {r['rows_total']:,} rows: {why}")
    lines += [
        "Flagged = review or block:",
        row("precision", _pct(r["precision"]), "of flagged accounts, the share that were farmers"),
        row("recall", _pct(r["recall"]), "of farmers, the share flagged"),
        row("honest users wrongly flagged", _pct(r["honest_flagged_rate"]), f"{r['honest_flagged']:,} of {r['real']:,} real users"),
        "Blocked only:",
        row("precision", _pct(b["precision"])),
        row("recall", _pct(b["recall"])),
        row("honest users wrongly blocked", _pct(b["honest_flagged_rate"]), f"{b['honest_flagged']:,} of {r['real']:,} real users"),
        f"Free credits at ${r['grant_usd']:g} per grant" + (f" ({r['withheld_basis']}):" if decisions else ":"),
        row("withheld from farmers", f"${r['usd_withheld_from_farmers']:,.2f}"),
        row("withheld from real users", f"${r['usd_withheld_from_real']:,.2f}"),
    ]
    if "tactics" in r:
        lines.append("By tactic (recall = farmers flagged; false positives = real users flagged):")
        for name, g in r["tactics"].items():
            lines.append(f"  {name:<20} farmers {g['farmers']:>6,}  recall {_pct(g['recall']):>6}   "
                         f"real {g['real']:>6,}  false positives {_pct(g['false_positive_rate']):>6}")
    return "\n".join(lines)
