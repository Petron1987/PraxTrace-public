"""Record and review local PraxTrace upstream trials without storing question text."""

from __future__ import annotations

import argparse
import os
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path, PurePosixPath
import re
import sqlite3
from statistics import median

from .product_upstream import load_pins


ROOT = Path(__file__).resolve().parent
DEFAULT_DB = Path(os.environ.get("PRAXTRACE_STATE_DIR", str(Path.cwd() / ".praxtrace"))) / "upstream-usage.sqlite3"
STAGES = ("none", "fetch", "method", "source", "run")
COMPARISON_STAGES = STAGES[2:]
REVIEW_AFTER = 5
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,79}")
SCHEMA = """
CREATE TABLE IF NOT EXISTS usage (
    id INTEGER PRIMARY KEY,
    recorded_at TEXT NOT NULL,
    tool TEXT NOT NULL,
    case_id TEXT NOT NULL,
    sample TEXT NOT NULL,
    trigger TEXT NOT NULL,
    stage TEXT NOT NULL,
    status TEXT NOT NULL,
    evidence TEXT NOT NULL,
    tool_version TEXT,
    source_version TEXT,
    ref TEXT,
    paired INTEGER NOT NULL,
    baseline_ms INTEGER,
    assisted_ms INTEGER
)
"""
RECORD_FIELDS = (
    "tool", "case_id", "sample", "trigger", "stage", "status", "evidence", "tool_version",
    "source_version", "ref", "paired", "baseline_ms", "assisted_ms",
)


def append_record(path: Path, args: argparse.Namespace) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    values = [datetime.now(timezone.utc).isoformat(timespec="seconds"), *(getattr(args, key) for key in RECORD_FIELDS)]
    fields = ", ".join(("recorded_at", *RECORD_FIELDS))
    marks = ", ".join("?" for _ in values)
    with closing(sqlite3.connect(path, timeout=10)) as conn, conn:
        conn.execute(SCHEMA)
        cursor = conn.execute(f"INSERT INTO usage ({fields}) VALUES ({marks})", values)
        return cursor.lastrowid


def read_records(path: Path, tool: str | None, limit: int | None = None) -> list[dict]:
    if not path.exists():
        return []
    query = "SELECT * FROM usage"
    parameters = []
    if tool:
        query += " WHERE tool = ?"
        parameters.append(tool)
    query += " ORDER BY id DESC"
    if limit is not None:
        query += " LIMIT ?"
        parameters.append(limit)
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(query, parameters)]


def summarize(records: list[dict], projects: list[str], review_after: int) -> dict:
    tools = {}
    for project in projects:
        rows = [row for row in records if row["tool"] == project]
        comparisons = {}
        for stage in COMPARISON_STAGES:
            used_cases = {
                row["case_id"] for row in rows
                if row["stage"] == stage and row["sample"] == "work" and row["status"] == "completed"
            }
            latest = {}
            for row in rows:
                if (row["stage"] == stage and row["sample"] == "work"
                        and row["status"] == "completed" and row["paired"]):
                    latest.setdefault(row["case_id"], row)
            eligible = list(latest.values())
            timings = [
                row["assisted_ms"] - row["baseline_ms"]
                for row in eligible if row["baseline_ms"] is not None
            ]
            comparisons[stage] = {
                "work_cases": len(eligible),
                "used_work_cases": len(used_cases),
                "unpaired_work_cases": len(used_cases - latest.keys()),
                "comparison_needed": len(used_cases) >= review_after and len(eligible) < review_after,
                "review_due": len(eligible) >= review_after,
                "evidence": dict(Counter(row["evidence"] for row in eligible)),
                "timed_cases": len(timings),
                "median_assisted_minus_baseline_ms": median(timings) if timings else None,
            }
        tools[project] = {
            "records": len(rows),
            "stages": dict(Counter(row["stage"] for row in rows)),
            "samples": dict(Counter(row["sample"] for row in rows)),
            "statuses": dict(Counter(row["status"] for row in rows)),
            "comparisons": comparisons,
        }
    return {"review_after": review_after, "tools": tools}


def validate_record(args: argparse.Namespace) -> None:
    for field in ("case_id", "tool_version", "source_version"):
        value = getattr(args, field)
        if value is not None and not IDENTIFIER.fullmatch(value):
            raise ValueError(f"{field} must be a short identifier, not prose")
    if args.ref is not None:
        path = PurePosixPath(args.ref.split(":", 1)[0])
        valid_format = re.fullmatch(r"[A-Za-z0-9._/-]{1,160}(?::[1-9][0-9]*)?", args.ref)
        if not valid_format or path.is_absolute() or ".." in path.parts:
            raise ValueError("--ref must be a workspace-relative evidence path, optionally with a line number")
    if args.stage in ("fetch", "source", "run") and args.status == "completed" and not args.tool_version:
        raise ValueError("completed fetch, source and run records need --tool-version")
    if args.stage == "none" and args.status == "completed":
        raise ValueError("stage none requires status skipped or blocked")
    if args.paired and (args.stage not in COMPARISON_STAGES or args.status != "completed" or not args.source_version):
        raise ValueError("--paired requires completed method, source or run and --source-version")
    if (args.baseline_ms is None) != (args.assisted_ms is None):
        raise ValueError("provide both timings or neither")
    if args.baseline_ms is not None and (not args.paired or min(args.baseline_ms, args.assisted_ms) < 0):
        raise ValueError("timings require --paired and nonnegative milliseconds")


def main() -> int:
    projects = list(load_pins())
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    commands = parser.add_subparsers(dest="command", required=True)
    record = commands.add_parser("record", help="append one attempt")
    record.add_argument("tool", choices=projects)
    record.add_argument("--case", dest="case_id", required=True)
    record.add_argument("--sample", choices=("work", "trial"), default="work")
    record.add_argument("--trigger", choices=("explicit", "match", "gap"), required=True)
    record.add_argument("--stage", choices=STAGES, required=True)
    record.add_argument("--status", choices=("completed", "blocked", "skipped"), required=True)
    record.add_argument("--evidence", choices=("added", "same", "wrong", "unknown"), default="unknown")
    record.add_argument("--tool-version")
    record.add_argument("--source-version")
    record.add_argument("--ref")
    record.add_argument("--paired", action="store_true", help="same task and source compared with the local baseline")
    record.add_argument("--baseline-ms", type=int)
    record.add_argument("--assisted-ms", type=int)
    history = commands.add_parser("history", help="show recent records as JSON")
    history.add_argument("--tool", choices=projects)
    history.add_argument("--limit", type=int, default=50)
    summary = commands.add_parser("summary", help="show adoption evidence and review thresholds")
    summary.add_argument("--tool", choices=projects)
    summary.add_argument("--review-after", type=int, default=REVIEW_AFTER)
    args = parser.parse_args()

    try:
        if args.command == "record":
            validate_record(args)
            output = {"record_id": append_record(args.db, args)}
        elif args.command == "history":
            if args.limit < 1:
                raise ValueError("--limit must be positive")
            output = read_records(args.db, args.tool, args.limit)
        else:
            if args.review_after < 1:
                raise ValueError("--review-after must be positive")
            output = summarize(read_records(args.db, args.tool), [args.tool] if args.tool else projects, args.review_after)
    except (OSError, ValueError, sqlite3.Error) as exc:
        parser.error(str(exc))
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
