"""Check a PraxTrace analysis plan or delivery record using local files only."""

import argparse
import json
from pathlib import Path
import re
import sqlite3

from .product_upstream import load_pins
from .upstream_usage import DEFAULT_DB, read_records


INTENTS = {"describe", "explain", "effect", "design", "inventory", "change"}
STAGES = {"none", "fetch", "source", "method", "run"}
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,79}")


def check_record(record, root, phase="delivery", usage_records=None, require_brief=False):
    """Validate completeness and references. Semantic accuracy requires source review."""
    errors = []
    root = Path(root).resolve()

    def require(condition, message):
        if not condition:
            errors.append(message)

    def filled(value):
        return isinstance(value, str) and bool(value.strip()) and value.strip() not in {"TODO", "\u5f85\u586b"}

    def references(values, label, required=False):
        if not isinstance(values, list):
            errors.append(f"{label} must be a list")
            return
        require(not required or bool(values), f"{label} needs evidence")
        for value in values:
            if not filled(value):
                errors.append(f"{label} contains an empty path")
                continue
            path = Path(value)
            resolved = (root / path).resolve()
            if path.is_absolute() or not resolved.is_relative_to(root) or not resolved.is_file():
                errors.append(f"{label} missing or outside root: {value}")

    def objects(key):
        values = record.get(key)
        if not isinstance(values, list) or any(not isinstance(value, dict) for value in values):
            errors.append(f"{key} must be a list of objects")
            return []
        return values

    if not isinstance(record, dict):
        return {"ok": False, "errors": ["record must be an object"]}
    require(phase in {"plan", "delivery"}, "unknown phase")
    require(type(record.get("schema_version")) is int and record["schema_version"] == 1, "schema_version must be 1")
    require(isinstance(record.get("case_id"), str) and bool(IDENTIFIER.fullmatch(record["case_id"])), "invalid case_id")
    for key in ("request", "scope", "scope_basis", "reuse"):
        require(filled(record.get(key)), f"{key} is required")

    brief = record.get("analysis_brief")
    require(not require_brief or isinstance(brief, dict), "analysis_brief required for new complex work")
    if "analysis_brief" in record:
        require(isinstance(brief, dict), "analysis_brief must be an object")
        if isinstance(brief, dict):
            require(type(brief.get("version")) is int and brief["version"] == 1, "analysis_brief version must be 1")
            require(type(brief.get("revision")) is int and brief["revision"] > 0, "analysis_brief revision must be positive")
            for key in ("decision", "risk", "evidence_plan", "stop_condition", "revision_reason"):
                require(filled(brief.get(key)), f"analysis_brief {key} is required")
            sources = brief.get("sources")
            require(isinstance(sources, list) and bool(sources), "analysis_brief sources must contain scoped preflight results")
            seen_sources = set()
            for source in sources if isinstance(sources, list) else []:
                if not isinstance(source, dict):
                    errors.append("analysis_brief source must be an object")
                    continue
                sid = source.get("source_id")
                require(filled(sid), "source_id is required")
                if isinstance(sid, str):
                    require(sid not in seen_sources, "duplicate source_id")
                    seen_sources.add(sid)
                for key in ("kind", "note"):
                    require(filled(source.get(key)), f"source {key} is required")
                for key, choices in (
                    ("availability", ("available", "not_configured", "inaccessible", "unknown")),
                    ("applicability", ("matched", "mismatch", "unknown")),
                    ("freshness", ("current", "stale", "unknown")),
                ):
                    require(isinstance(source.get(key), str) and source[key] in choices, f"source {key} is invalid")

    tasks = objects("tasks")
    require(bool(tasks), "tasks must cover the original question")
    task_ids = set()
    counts = {"pending": 0, "answered": 0, "partial": 0, "blocked": 0}
    for task in tasks:
        identifier = task.get("id")
        if not isinstance(identifier, str) or not IDENTIFIER.fullmatch(identifier):
            errors.append("invalid task id")
            continue
        require(identifier not in task_ids, f"duplicate task id: {identifier}")
        task_ids.add(identifier)
        require(isinstance(task.get("intent"), str) and task["intent"] in INTENTS, f"{identifier}: invalid intent")
        for key in ("question", "acceptance"):
            require(filled(task.get(key)), f"{identifier}: {key} is required")
        status = task.get("status")
        if not isinstance(status, str) or status not in counts:
            errors.append(f"{identifier}: invalid status")
            continue
        counts[status] += 1
        references(task.get("evidence"), identifier, phase == "delivery" and status in {"answered", "partial"})
        if phase == "delivery":
            require(status != "pending", f"{identifier}: unresolved task")
            require(filled(task.get("result")), f"{identifier}: result is required")
            if status in {"partial", "blocked"}:
                for key in ("limitation", "next_check"):
                    require(filled(task.get(key)), f"{identifier}: {key} is required")

    def linked_tasks(item, label):
        ids = item.get("task_ids")
        valid = isinstance(ids, list) and bool(ids) and all(isinstance(i, str) and i in task_ids for i in ids)
        require(valid, f"{label}: task_ids must refer to existing tasks")
        return ids if valid else []

    routes = objects("routes")
    selected_tasks = set()
    providers = {"local", *load_pins()}
    if phase == "delivery" and any(route.get("provider") != "local" for route in routes):
        if usage_records is None:
            usage_records = read_records(DEFAULT_DB, None)
    usage_by_id = {row["id"]: row for row in usage_records or []}
    for index, route in enumerate(routes):
        label = f"route {index + 1}"
        ids = linked_tasks(route, label)
        provider = route.get("provider")
        require(isinstance(provider, str) and provider in providers, f"{label}: unknown provider")
        require(filled(route.get("capability")), f"{label}: capability is required")
        require(filled(route.get("reason")), f"{label}: reason is required")
        decision = route.get("decision")
        status = route.get("status")
        stage = route.get("stage")
        require(decision in ("selected", "skipped"), f"{label}: invalid decision")
        require(status in ("planned", "completed", "blocked", "skipped"), f"{label}: invalid status")
        require(isinstance(stage, str) and stage in STAGES, f"{label}: invalid stage")
        if decision == "selected":
            selected_tasks.update(ids)
            require(status != "skipped", f"{label}: selected route cannot be skipped")
        if decision == "skipped":
            require(status == "skipped" and stage == "none", f"{label}: skipped route must have stage none")
        references(route.get("evidence"), label, status == "completed")
        if status == "completed":
            require(stage != "none", f"{label}: completed route needs an actual stage")
            if provider != "local":
                require(filled(route.get("version")), f"{label}: upstream version is required")
        if phase == "delivery":
            require(status != "planned", f"{label}: planned route was not closed")
            if provider != "local":
                require(type(route.get("usage_id")) is int and route["usage_id"] > 0, f"{label}: upstream usage_id is required")
                usage_id = route.get("usage_id")
                usage = usage_by_id.get(usage_id) if type(usage_id) is int else None
                matches = usage is not None and all(
                    usage.get(key) == value for key, value in (
                        ("case_id", record.get("case_id")), ("tool", provider),
                        ("stage", stage), ("status", status),
                    )
                )
                require(matches, f"{label}: usage record must match case, provider, stage and status")
                if usage is not None and route.get("version"):
                    require(usage.get("tool_version") == route["version"], f"{label}: upstream version differs from usage record")
    require(selected_tasks == task_ids, "each task needs a selected route, including local work")

    checks = objects("checks")
    check_ids = [check.get("id") for check in checks]
    require(all(isinstance(i, str) for i in check_ids), "check ids must be strings")
    for key in ("scope", "evidence", "decision"):
        require(check_ids.count(key) == 1, f"exactly one {key} check is required")
    for check in checks:
        require(check.get("status") in ("pending", "pass", "gap"), "invalid check status")
        if phase == "delivery":
            require(check.get("status") != "pending", f"{check.get('id')}: check is pending")
            require(filled(check.get("detail")), f"{check.get('id')}: check detail is required")

    actions = objects("actions")
    action_tasks = set()
    for action in actions:
        action_tasks.update(linked_tasks(action, "action"))
        for key in ("action", "metric", "condition"):
            require(filled(action.get(key)), f"action: {key} is required")
    if phase == "delivery":
        for task in tasks:
            if task.get("intent") in ("explain", "effect", "design") and task.get("status") in ("answered", "partial"):
                require(task.get("id") in action_tasks, f"{task.get('id')}: decision-oriented task needs an actionable recommendation")
        references([record.get("report")], "report", True)
        elapsed = record.get("elapsed_seconds")
        require(type(elapsed) in (int, float) and 0 <= elapsed < float("inf"), "elapsed_seconds needs a measured nonnegative value")
    limited = counts["partial"] > 0 or counts["blocked"] > 0 or any(c.get("status") == "gap" for c in checks)
    return {
        "ok": not errors,
        "phase": phase,
        "coverage": "limited" if limited else "complete" if phase == "delivery" and not errors else "planned",
        "tasks": counts,
        "errors": errors,
        "semantic_review": "Agent must verify the original question, evidence meaning and recommendations.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("record", type=Path)
    parser.add_argument("--phase", choices=("plan", "delivery"), default="delivery")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--require-case", action="store_true", help="Reject missing case registration for new work")
    parser.add_argument("--require-brief", action="store_true", help="Require analysis brief for new complex work; legacy records remain readable")
    args = parser.parse_args()
    try:
        record = json.loads(args.record.read_text(encoding="utf-8-sig"))
        result = check_record(record, args.root, args.phase, require_brief=args.require_brief)
        case_path = args.record.parent / "case.json"
        if case_path.is_file():
            from .case_records import read_json, validate_case
            case = read_json(case_path)
            link_check = validate_case(case, args.root, args.phase)
            if case.get("case_id") != record.get("case_id"):
                link_check["errors"].append("case_id differs from execution record")
                link_check["ok"] = False
            result["case_links"] = link_check
            result["ok"] = result["ok"] and link_check["ok"]
        else:
            result["case_links"] = {"status": "legacy_unregistered", "warning": "No case.json; five links have not been checked."}
            if args.require_case:
                result["errors"].append("case.json required for new work")
                result["ok"] = False
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
