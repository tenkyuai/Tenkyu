"""`tenkyu shadow-report`: join a DecisionLog with a CSV of labels and show how accurate Tenkyu's decisions were.

Join key: decision_id. Every line DecisionLog writes carries a decision_id (a short hash of the decision's time, email
hash, actions and score); a log from an older version has no such field, so the same id is derived from the line. The
labels CSV needs a decision_id column and a label column (farmer, real or not sure). The log holds no raw email or IP,
and this report prints no ids, hashes, emails or IPs, only counts, rates and dollars."""
from __future__ import annotations

import csv
from datetime import datetime, timezone
from typing import Dict, List, Tuple

from .audit import MAX_ROWS
from .decision_log import decision_id, read_log
from .evaluate import COLUMN_ALIASES, NO_LABEL_HELP, _safe_header, label_state, score_decisions

MAX_REASONS_CHARS = 300


def _read_csv_rows(path) -> Tuple[List[str], List[dict]]:
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as fh:
        reader = csv.DictReader(fh, strict=True)
        try:
            fields = reader.fieldnames
            if not fields:
                raise ValueError("the labels file has no header row")
            rows = []
            for row in reader:
                if len(rows) >= MAX_ROWS:
                    raise ValueError(f"more than {MAX_ROWS:,} label rows; split the file")
                rows.append(row)
        except csv.Error as e:
            raise ValueError(f"cannot parse the labels CSV near row {reader.line_num}: {e}") from None
    return list(fields), rows


def load_labels(path) -> Tuple[Dict[str, str], Dict[str, int]]:
    """Return ({decision_id: "farmer" | "real"}, counts of what was set aside)."""
    fields, rows = _read_csv_rows(path)
    lower = {str(h).strip().lower(): h for h in fields}
    if "decision_id" not in lower:
        raise ValueError(f"the labels file needs a decision_id column; found columns: {_safe_header(fields)}. "
                         "Run `tenkyu shadow-report LOG --template labels.csv` to get a file to fill in")
    label_col = next((lower[a] for a in COLUMN_ALIASES["label"] if a in lower), None)
    if not label_col:
        raise ValueError(NO_LABEL_HELP)
    id_col = lower["decision_id"]
    labels: Dict[str, str] = {}
    conflicts = set()
    counts = {"not sure": 0, "unrecognised label": 0, "no decision id": 0}
    for row in rows:
        state = label_state(row.get(label_col))
        key = str(row.get(id_col) or "").strip()
        if state == "unsure":
            counts["not sure"] += 1
        elif state is None:
            counts["unrecognised label"] += 1
        elif not key:
            counts["no decision id"] += 1
        elif key in labels and labels[key] != state:
            conflicts.add(key)
        else:
            labels[key] = state
    for key in conflicts:
        del labels[key]
    counts["conflicting labels"] = len(conflicts)
    return labels, counts


def shadow_report(log_path, labels_path, grant: float = 3.0) -> dict:
    """Join and score. Raises ValueError (bad input) or OSError (unreadable file)."""
    records, bad_lines = read_log(log_path)
    if not records:
        raise ValueError("the log has no readable decisions")
    labels, set_aside = load_labels(labels_path)
    items, matched_ids = [], set()
    for rec in records:
        key = rec.get("decision_id") or decision_id(rec)
        state = labels.get(key)
        if state is None:
            continue
        matched_ids.add(key)
        flagged = rec["would_action"] != "allow"
        frac = 1.0 - min(1.0, max(0.0, float(rec.get("would_credit_fraction", 1.0))))
        items.append((rec["would_action"], state == "farmer", frac if flagged else 0.0))
    if not items:
        raise ValueError("no labelled decision matched the log; check that the labels file uses decision_ids from this log")
    skipped = {f"labels set aside: {k}": n for k, n in set_aside.items() if n}
    unmatched = len(labels) - len(matched_ids)
    if unmatched:
        skipped["labels with no matching decision in the log"] = unmatched
    out = score_decisions(items, grant, {}, len(items), "from the credit fraction in the log")
    out.update({"mode": "shadow-report", "decisions_in_log": len(records), "unlabelled_decisions": len(records) - len(items),
                "unreadable_log_lines": bad_lines, "set_aside": skipped,
                "shadow": any(r.get("shadow") for r in records)})
    return out


def format_shadow_report(r: dict) -> str:
    from .evaluate import format_report
    mode = "shadow mode: nobody was refused" if r["shadow"] else "enforced decisions"
    lines = [f"Shadow report: {r['rows_used']:,} of {r['decisions_in_log']:,} logged decisions have a label "
             f"({r['farmers']:,} farmers, {r['real']:,} real users; {mode}); joined on decision_id; nothing left this machine"]
    body = format_report({**r, "mode": "decisions"}).split("\n")[1:]  # reuse the accuracy view, with this header
    lines += body
    notes = [f"{n:,} {why}" for why, n in r["set_aside"].items()]
    if r["unreadable_log_lines"]:
        notes.append(f"{r['unreadable_log_lines']:,} unreadable log line(s)")
    if notes:
        lines.append("Set aside: " + "; ".join(notes))
    return "\n".join(lines)


def _ts(rec: dict) -> float:
    try:
        t = float(rec.get("ts") or 0)
    except (TypeError, ValueError):
        return 0.0
    return t if t == t else 0.0


def _csv_safe(text: str) -> str:
    """Stop a spreadsheet from running a cell that starts like a formula."""
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def write_template(log_path, out_path) -> int:
    """Write decision_id,time,action,would_action,shadow,score,reasons,label (label blank) for every logged decision."""
    records, _ = read_log(log_path)
    if not records:
        raise ValueError("the log has no readable decisions")
    records.sort(key=lambda r: _ts(r))
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["decision_id", "time", "action", "would_action", "shadow", "score", "reasons", "label"])
        for r in records:
            try:
                when = datetime.fromtimestamp(_ts(r), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            except (ValueError, OverflowError, OSError):
                when = ""
            reasons = "; ".join(str(x) for x in (r.get("reasons") or []))[:MAX_REASONS_CHARS]
            w.writerow([_csv_safe(str(r.get("decision_id") or decision_id(r))), when, _csv_safe(str(r.get("action", ""))),
                        r["would_action"], "true" if r.get("shadow") else "false", _csv_safe(str(r.get("score", ""))),
                        _csv_safe(reasons), ""])
    return len(records)
