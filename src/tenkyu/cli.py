"""Command line: `python -m tenkyu demo | score | audit | evaluate | report | shadow-report | version`."""
from __future__ import annotations

import argparse
import json
import math
import sys
from typing import List, Optional

from . import __version__
from .guard import ALLOW, BLOCK, REVIEW, Guard, Signup
from .audit import SAMPLE_CSV, audit_csv, write_flagged
from .decision_log import read_log, summarize
from .evaluate import PRESETS, evaluate_csv, format_report
from .shadow_report import format_shadow_report, shadow_report, write_template

EXIT = {ALLOW: 0, REVIEW: 10, BLOCK: 20}
EXIT_DB_ERROR = 3  # the --db file could not be opened or read
BROWSER = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/120.0 Safari/537.36"
DEMO_T0 = 1_700_000_000.0


def _demo_scenes():
    """(label, Signup) pairs: an honest user, an alias farmer, and a bot burst."""
    scenes = [("honest user", Signup("ana@acme.io", "203.0.113.10", "dev-ana", BROWSER, DEMO_T0))]
    for i, e in enumerate(["john@gmail.com", "j.o.h.n+free1@gmail.com", "john+free2@googlemail.com"]):
        scenes.append(("alias farmer", Signup(e, "198.51.100.20", "dev-john", BROWSER, DEMO_T0 + 30 + i * 90)))
    for i in range(4):
        scenes.append(("bot burst", Signup(f"user{i}x@mailinator.com" if i % 2 else f"bot{i}@acme.io", "192.0.2.77",
                                           f"dev-bot{i}", "python-requests/2.31", DEMO_T0 + 600 + i * 2)))
    return scenes


def run_demo(out=None) -> int:
    out = out or sys.stdout
    guard = Guard(clock=lambda: DEMO_T0)
    rows = []
    for label, signup in _demo_scenes():
        d = guard.check(signup)
        reason = d.reasons[0] if d.reasons else "-"
        rows.append((label, signup.email, d.action.upper(), f"{d.score:.2f}", f"{int(d.credit_fraction * 100)}%", reason))
    head = ("scene", "email", "action", "score", "credits", "main reason")
    widths = [max(len(str(r[i])) for r in rows + [head]) for i in range(5)]
    fmt = lambda r: "  ".join(str(r[i]).ljust(widths[i]) for i in range(5)) + "  " + str(r[5])
    print(fmt(head), file=out)
    print("  ".join("-" * w for w in widths) + "  " + "-" * 11, file=out)
    for r in rows:
        print(fmt(r), file=out)
    return 0


def run_score(args, out=None) -> int:
    out = out or sys.stdout
    import sqlite3

    from .sqlite_store import SQLiteStore
    store = None
    try:
        store = SQLiteStore(args.db) if args.db else None
        guard = Guard(store=store) if store else Guard()
        d = guard.check(Signup(email=args.email, ip=args.ip, device_id=args.device, user_agent=args.ua), record=not args.dry_run)
    except sqlite3.Error as e:
        print(f"tenkyu: cannot use database {args.db}: {e}", file=sys.stderr)
        return EXIT_DB_ERROR
    finally:
        if store:
            store.close()
    if args.json:
        print(json.dumps(d.to_dict(), indent=2), file=out)
    else:
        print(f"{d.action.upper()}  score={d.score:.2f}  credits={int(d.credit_fraction * 100)}%", file=out)
        for r in d.reasons:
            print(f"  - {r}", file=out)
    return EXIT[d.action]


def run_report(args, out=None) -> int:
    out = out or sys.stdout
    try:
        records, bad = read_log(args.log)
    except OSError as e:
        print(f"tenkyu: cannot read {args.log}: {e.strerror or e}", file=sys.stderr)
        return EXIT_DB_ERROR
    s = summarize(records, args.grant)
    if args.json:
        print(json.dumps({**s, "unreadable_lines": bad}, indent=2), file=out)
        return 0
    mode = "shadow mode: nobody was refused; this is what Tenkyu would have done" if s["shadow"] else "enforced decisions"
    print(f"{s['decisions']} signups ({mode})", file=out)
    for a in ("allow", "review", "block"):
        n = s["by_action"][a]
        pct = 100 * n / s["decisions"] if s["decisions"] else 0
        print(f"  {a:<7}{n:>6}  {pct:5.1f}%", file=out)
    repeats = "not tracked (the log has no email hashes; pass DecisionLog(path, secret=...) to track it)" if s["repeat_emails"] is None else s["repeat_emails"]
    print(f"Same inbox signing up again: {repeats}", file=out)
    grant = f" = ${s['usd_withheld']:,.2f} at ${args.grant:g} per grant" if args.grant else ""
    print(f"Free grants withheld from flagged signups: {s['grants_withheld']:g}{grant}", file=out)
    if s["grants_held_until_verified"]:
        held = f" (${s['usd_held_until_verified']:,.2f})" if args.grant else ""
        print(f"Held from allowed users until they verify (not abuse): {s['grants_held_until_verified']:g} grants{held}", file=out)
    if s["top_reasons"]:
        print("Top reasons:", file=out)
        for reason, n in s["top_reasons"]:
            print(f"  {n:>5}  {reason}", file=out)
    if bad:
        print(f"Skipped {bad} unreadable line(s).", file=out)
    return 0


def run_audit(args, out=None) -> int:
    out = out or sys.stdout
    links = {}
    for spec in args.link or []:
        name, _, col = spec.partition("=")
        if not col:
            print(f"tenkyu: --link must look like name=column, got {spec!r}", file=sys.stderr)
            return 2
        links[name.strip()] = col.strip()
    if args.sample:
        args.csv = str(SAMPLE_CSV)
        if not args.grant:  # the demo should show the money; a real audit only prints $ when you give your own grant
            args.grant = 3.0
        if not args.json:  # --json output must stay pure JSON
            print("Using the bundled sample (synthetic signups with known farms, not real users).", file=out)
    elif not args.csv:
        print("tenkyu: give a CSV file, or --sample to try it on bundled synthetic data", file=sys.stderr)
        return 2
    try:
        res = audit_csv(args.csv, links)
    except OSError as e:
        print(f"tenkyu: cannot read {args.csv}: {e.strerror or e}", file=sys.stderr)
        return EXIT_DB_ERROR
    except ValueError as e:
        print(f"tenkyu: {e}", file=sys.stderr)
        return 2
    s = res.summary(args.grant)
    if args.out:
        s["flagged_rows_written"] = write_flagged(args.csv, res, args.out)
    if args.json:
        print(json.dumps(s, indent=2), file=out)
        return 0
    print(f"Audited {s['signups']:,} signups (replayed in time order, nothing left this machine)", file=out)
    for a in ("allow", "review", "block"):
        print(f"  {a:<7}{s[a]:>9,}", file=out)
    print(f"Flagged for review or block: {s['flagged_pct']}% (review is a softer check; some honest users land there)", file=out)
    if args.grant:
        print(f"Free credits these decisions would have withheld: ${s['usd_at_stake']:,.2f} at ${args.grant:g} per grant", file=out)
    if s["top_reasons"]:
        print("Main reason per flagged signup:", file=out)
        for reason, n in s["top_reasons"]:
            print(f"  {n:>7,}  {reason}", file=out)
    if s["unused_columns"] and not args.sample:
        print(f"Columns not used: {', '.join(s['unused_columns'])} (rename one to device_id or user_agent, or use --link)", file=out)
    if s["missing"]:
        print(f"Not in your export, so those signals were off: {', '.join(s['missing'])}", file=out)
    if s["unreadable_rows"]:
        print(f"Skipped {s['unreadable_rows']:,} row(s) with no readable time or bad link values.", file=out)
    if args.out:
        print(f"Wrote {s['flagged_rows_written']:,} flagged rows to {args.out}", file=out)
    return 0


def run_evaluate(args, out=None) -> int:
    out = out or sys.stdout
    try:
        res = evaluate_csv(args.csv, preset=args.preset, grant=args.grant, decisions=args.decisions)
    except OSError as e:
        print(f"tenkyu: cannot read {args.csv}: {e.strerror or e}", file=sys.stderr)
        return EXIT_DB_ERROR
    except ValueError as e:
        print(f"tenkyu: {e}", file=sys.stderr)
        return 2
    print(json.dumps(res, indent=2) if args.json else format_report(res), file=out)
    return 0


def run_shadow_report(args, out=None) -> int:
    """Exit codes: 0 ok, 2 bad input (unreadable or malformed file, nothing to join). Prints counts only."""
    out = out or sys.stdout
    try:
        if args.template:
            n = write_template(args.log, args.template)
            print(f"Wrote {n:,} decisions to {args.template}; fill in the label column (farmer, real or not sure), "
                  "then run: tenkyu shadow-report LOG LABELS", file=out)
            return 0
        if not args.labels:
            print("tenkyu: give a labels CSV (or --template FILE to create one from the log)", file=sys.stderr)
            return 2
        res = shadow_report(args.log, args.labels, args.grant)
    except OSError as e:
        print(f"tenkyu: cannot read or write a file: {e.strerror or e}", file=sys.stderr)
        return 2
    except ValueError as e:
        print(f"tenkyu: {e}", file=sys.stderr)
        return 2
    print(json.dumps(res, indent=2) if args.json else format_shadow_report(res), file=out)
    return 0


def _grant(text: str) -> float:
    try:
        v = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a number") from None
    if not math.isfinite(v) or v < 0:
        raise argparse.ArgumentTypeError(f"must be a finite number, 0 or more; got {text!r}")
    return v


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tenkyu", description="Stop free-tier and credit farming in AI apps.",
                                epilog="`score` exit codes: 0 allow, 10 review, 20 block, 3 the --db file could not be used.")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("demo", help="simulate an honest user, an alias farmer and a bot burst")
    sub.add_parser("version", help="print the version")
    s = sub.add_parser("score", help="score one signup")
    s.add_argument("--email", required=True)
    s.add_argument("--ip")
    s.add_argument("--device")
    s.add_argument("--ua", help="user agent string")
    s.add_argument("--db", help="SQLite file to remember signups between runs")
    s.add_argument("--dry-run", action="store_true", help="score without remembering the signup")
    s.add_argument("--json", action="store_true")
    a = sub.add_parser("audit", help="replay your own signup export (CSV) and count the farming")
    a.add_argument("--sample", action="store_true", help="audit the bundled synthetic sample to see what the output looks like (uses an example $3 grant unless --grant is given)")
    a.add_argument("csv", nargs="?", help="CSV with an email column and a time column (created_at, timestamp, ...); ip, device_id, user_agent optional")
    a.add_argument("--link", action="append", metavar="NAME=COLUMN", help="treat a column as a link, e.g. card=card_fingerprint (repeatable)")
    a.add_argument("--grant", type=_grant, default=0.0, help="dollar value of one free grant: your own number (examples use $3)")
    a.add_argument("--out", help="write the flagged rows of your CSV here, with Tenkyu's action and reason")
    a.add_argument("--json", action="store_true")
    e = sub.add_parser("evaluate", help="measure Tenkyu's accuracy on your own signup export, using a label column")
    e.add_argument("csv", help="CSV like the audit input plus a label column (farmer or real); optional phone, card and tactic columns")
    e.add_argument("--preset", choices=PRESETS, default="default", help="default Guard, or ai for Guard.for_ai_apps()")
    e.add_argument("--grant", type=_grant, default=3.0, help="dollar value of one free grant: your own number (default 3, the example value)")
    e.add_argument("--decisions", action="store_true",
                   help="score recorded decisions (columns action, would_action, shadow, label: the hosted label export) instead of replaying signups; "
                        "chosen automatically when there is an action column and no email or ip column")
    e.add_argument("--json", action="store_true")
    r = sub.add_parser("report", help="summarize a decision log (for example from a shadow-mode trial)")
    r.add_argument("log", help="JSONL file written by DecisionLog")
    r.add_argument("--grant", type=_grant, default=0.0, help="dollar value of one free grant, to show $ at stake: your own number (examples use $3)")
    r.add_argument("--json", action="store_true")
    sr = sub.add_parser("shadow-report", help="score a shadow-mode decision log against your labels (farmer, real, not sure)",
                        epilog="exit codes: 0 ok, 2 bad input")
    sr.add_argument("log", help="JSONL file written by DecisionLog")
    sr.add_argument("labels", nargs="?", help="CSV with decision_id and label columns")
    sr.add_argument("--grant", type=_grant, default=3.0, help="dollar value of one free grant: your own number (default 3, the example value)")
    sr.add_argument("--template", metavar="OUT.csv", help="instead of reporting, write a labels file from the log, with the label column blank")
    sr.add_argument("--json", action="store_true")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.cmd == "demo":
        return run_demo()
    if args.cmd == "score":
        return run_score(args)
    if args.cmd == "audit":
        return run_audit(args)
    if args.cmd == "evaluate":
        return run_evaluate(args)
    if args.cmd == "shadow-report":
        return run_shadow_report(args)
    if args.cmd == "report":
        return run_report(args)
    if args.cmd == "version":
        print(__version__)
        return 0
    parser.print_help()
    return 0
