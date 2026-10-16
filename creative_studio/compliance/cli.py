"""Command line entry point.

    python -m creative_studio.compliance.cli check --input script.txt --content-id V123 --db precheck.db
    python -m creative_studio.compliance.cli check --input shots.json --content-id V123
    python -m creative_studio.compliance.cli outcome --db precheck.db --content-id V123 --status rejected --reason "医疗/药品/限制行业"
    python -m creative_studio.compliance.cli report --db precheck.db
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .engine import ComplianceChecker, RuleSet
from . import store


def _load_payload(path: Path):
    raw = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        return json.loads(raw)
    return raw


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="compliance", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="pre-check a script (.txt) or shot list (.json)")
    c.add_argument("--input", required=True, type=Path)
    c.add_argument("--content-id", default="unnamed")
    c.add_argument("--rules", type=Path, default=None, help="custom rules JSON")
    c.add_argument("--db", default=None, help="sqlite file to store the run")

    o = sub.add_parser("outcome", help="record the platform's real review result")
    o.add_argument("--db", required=True)
    o.add_argument("--content-id", required=True)
    o.add_argument("--status", required=True, choices=store.PLATFORM_STATUSES)
    o.add_argument("--reason", action="append", default=[], help="repeatable; platform rejection category")

    r = sub.add_parser("report", help="agreement between pre-check and platform outcomes")
    r.add_argument("--db", required=True)
    return p


def main(argv: list[str] | None = None, out=None) -> int:
    out = out or sys.stdout
    args = build_parser().parse_args(argv)

    if args.cmd == "check":
        rules = RuleSet.load(args.rules) if args.rules else RuleSet.load()
        result = ComplianceChecker(rules).check(_load_payload(args.input), content_id=args.content_id)
        if args.db:
            conn = store.connect(args.db)
            store.save_run(conn, result)
            conn.close()
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), file=out)
        return 2 if result.verdict == "block_recommended" else 0

    conn = store.connect(args.db)
    try:
        if args.cmd == "outcome":
            store.record_outcome(conn, args.content_id, args.status, args.reason)
            print(f"recorded {args.status} for {args.content_id}", file=out)
            return 0
        if args.cmd == "report":
            print(json.dumps(store.agreement_report(conn), ensure_ascii=False, indent=2), file=out)
            return 0
    finally:
        conn.close()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
