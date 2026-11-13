"""Local case links, artifact snapshots and rebuildable search; no database or network."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import unicodedata


IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,79}")
SKIP = {".git", ".cache", ".local", "node_modules", "venv", "repos", "upstream", "tmp", "archive", "legacy-sources"}
DELIVERY = {"draft", "ready", "referenced", "delivered", "unknown"}
APPROVAL = {"not_requested", "pending", "approved", "rejected", "unknown"}
DECISIONS = {"reuse", "revise", "add", "narrow", "move", "retire", "none", "defer", "legacy_unknown"}


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def local_path(root, value, exists=True):
    root = Path(root).resolve()
    if not isinstance(value, str) or not value.strip():
        raise ValueError("empty local path")
    p = Path(value)
    if p.is_absolute() or not (root / p).resolve().is_relative_to(root):
        raise ValueError(f"path must stay inside root: {value}")
    p = (root / p).resolve()
    if exists and not p.is_file():
        raise ValueError(f"missing file: {value}")
    return p


def write_json(path, data, new=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # New records never overwrite; updates replace atomically after successful serialization.
    body = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if new:
        with path.open("x", encoding="utf-8") as stream:
            stream.write(body)
    else:
        temporary = path.with_name(path.name + ".pending")
        with temporary.open("x", encoding="utf-8") as stream:
            stream.write(body)
        temporary.replace(path)


def timestamp(value):
    try:
        return isinstance(value, str) and datetime.fromisoformat(value.replace("Z", "+00:00")).utcoffset() is not None
    except ValueError:
        return False


def validate_case(data, root, phase="delivery"):
    try:
        return _validate_case(data, root, phase)
    except (TypeError, KeyError, AttributeError, OSError, ValueError) as exc:
        return {"ok": False, "errors": [f"malformed or unreadable case: {exc}"], "warnings": [], "protected_paths": []}


def _validate_case(data, root, phase="delivery"):
    errors, warnings, protected = [], [], set()
    root = Path(root).resolve()

    def need(ok, message):
        if not ok:
            errors.append(message)

    def text(value):
        return isinstance(value, str) and bool(value.strip()) and value.strip() not in {"\u5f85\u586b", "TODO"}

    def ref(value):
        try:
            p = local_path(root, value)
            protected.add(p.relative_to(root).as_posix())
            return p
        except ValueError as exc:
            errors.append(str(exc))
            return None

    def rows(key):
        result = data.get(key)
        if not isinstance(result, list) or any(not isinstance(x, dict) for x in result):
            errors.append(f"{key}: expected list of objects")
            return []
        return result

    def unique(items, label):
        ids = set()
        for x in items:
            key = x.get("id")
            if not isinstance(key, str) or not IDENTIFIER.fullmatch(key) or key in ids:
                errors.append(f"{label}: invalid or duplicate id")
            else:
                ids.add(key)
        return ids

    if not isinstance(data, dict):
        return {"ok": False, "errors": ["case must be an object"], "warnings": [], "protected_paths": []}
    need(type(data.get("schema_version")) is int and data["schema_version"] == 1, "case schema_version must be 1")
    need(isinstance(data.get("case_id"), str) and bool(IDENTIFIER.fullmatch(data["case_id"])), "invalid case_id")
    for k in ("title", "question", "scope"):
        need(text(data.get(k)), f"{k} is required (sanitized)")
    need(timestamp(data.get("registered_at")), "registered_at needs timezone")
    need(data.get("retention") == "retain", "case evidence must be retained; no automatic cleanup")
    tags = data.get("tags")
    need(isinstance(tags, list) and all(text(t) for t in tags), "tags must be strings")
    for relation in rows("relations"):
        target = relation.get("case_id")
        need(isinstance(target, str) and bool(IDENTIFIER.fullmatch(target)) and target != data.get("case_id"), "invalid relation case_id")
        need(relation.get("kind") in {"follow_up", "correction", "rerun", "related"}, "invalid relation kind")
        need(text(relation.get("basis")), "relation needs basis")

    runs = rows("runs")
    run_ids = unique(runs, "runs")
    need(bool(runs), "at least one run is required")
    tasks_by_run = {}
    for run in runs:
        rid = run.get("id")
        need(run.get("origin") in {"native", "legacy"}, "run origin must be native or legacy")
        need(run.get("status") in {"in_progress", "complete", "partial", "blocked"}, "invalid run status")
        need(text(run.get("summary")), "run needs summary")
        need(timestamp(run.get("recorded_at")), "run recorded_at needs timezone")
        if run.get("origin") == "legacy":
            warnings.append(f"{rid}: historical timestamps/decisions may be unknown")
        if run.get("execution"):
            p = ref(run["execution"])
            if p:
                try:
                    ex = read_json(p)
                    need(isinstance(ex, dict) and ex.get("case_id") == data.get("case_id"), "execution case_id mismatch")
                    tasks = ex.get("tasks", [])
                    tasks_by_run[rid] = {t.get("id") for t in tasks if isinstance(t, dict)}
                    states = {t.get("status") for t in tasks if isinstance(t, dict)}
                    if run.get("status") == "complete":
                        need(bool(states) and states == {"answered"}, "complete run has unresolved execution tasks")
                    if phase == "delivery":
                        need("pending" not in states, "execution still has pending tasks")
                    for task in tasks:
                        for path in task.get("evidence", []):
                            ref(path)
                    for route in ex.get("routes", []):
                        for path in route.get("evidence", []):
                            ref(path)
                    if ex.get("report"):
                        ref(ex["report"])
                except (ValueError, TypeError, AttributeError) as exc:
                    errors.append(f"invalid execution: {exc}")
            if run.get("execution_sha256") and p:
                need(digest(p) == run["execution_sha256"], "execution snapshot changed")
        if phase == "delivery":
            need(run.get("status") != "in_progress", "run still in progress")
            if run.get("execution") and run.get("origin") == "native":
                need(bool(run.get("execution_sha256")), "seal completed native execution before delivery")

    artifacts = rows("artifacts")
    artifact_ids = unique(artifacts, "artifacts")
    replacements = {}
    for art in artifacts:
        rid = art.get("run_id")
        need(rid in run_ids, "artifact run does not exist")
        for k in ("kind", "version"):
            need(text(art.get(k)), f"artifact {k} required")
        p = ref(art.get("path"))
        if art.get("source"):
            ref(art["source"])
        need(isinstance(art.get("sha256"), str) and bool(re.fullmatch(r"[a-f0-9]{64}", art["sha256"])), "artifact needs sha256")
        if p:
            need(digest(p) == art.get("sha256"), "artifact content changed; capture a new version")
        ids = art.get("task_ids", [])
        need(isinstance(ids, list) and all(isinstance(i, str) and i in tasks_by_run.get(rid, set()) for i in ids), "artifact links unknown tasks")
        need(art.get("delivery_status") in DELIVERY, "invalid delivery status")
        if art.get("delivery_status") == "delivered":
            need(timestamp(art.get("delivered_at")) and text(art.get("delivery_basis")), "delivered requires actual time and basis")
        if art.get("delivery_receipt") or art.get("delivery_status") == "referenced":
            receipt = art.get("delivery_receipt", {})
            p_receipt = ref(receipt.get("path"))
            if p_receipt:
                from .case_ops import receipt_check
                try:
                    need(digest(p_receipt) == receipt.get("sha256"), "delivery receipt changed")
                    item = json.loads(p_receipt.read_text(encoding="utf-8"))["receipts"][receipt["item"]]
                    need(receipt_check(item, art, root) == art.get("delivery_status"), "receipt does not support delivery status")
                except (ValueError, KeyError, IndexError) as exc:
                    errors.append(f"invalid delivery receipt: {exc}")
        need(art.get("approval_status") in APPROVAL, "invalid approval status")
        if art.get("approval_status") in {"approved", "rejected"}:
            need(text(art.get("approval_basis")), "approval/rejection requires explicit basis")
        prior = art.get("supersedes")
        if prior:
            need(prior in artifact_ids and prior != art.get("id"), "unknown superseded artifact")
            need(prior not in replacements, "ambiguous replacement: two artifacts supersede the same version")
            replacements[prior] = art.get("id")
    for start in replacements:
        seen, cur = set(), start
        while cur in replacements:
            if cur in seen:
                errors.append("artifact replacement cycle")
                break
            seen.add(cur)
            cur = replacements[cur]

    for reuse in rows("reuse"):
        need(reuse.get("run_id") in run_ids and text(reuse.get("reason")), "reuse needs run and reason")
        need(text(reuse.get("version")), "reuse needs observed version or explicit unknown")
        ref(reuse.get("path"))

    learning = rows("learning")
    unique(learning, "learning")
    for item in learning:
        need(item.get("run_id") in run_ids, "learning run does not exist")
        need(item.get("decision") in DECISIONS, "invalid learning decision")
        need(item.get("layer") in {"protocol", "method", "host", "case"}, "invalid learning layer")
        need(item.get("status") in {"proposed", "applied", "unknown"}, "invalid learning status")
        for k in ("reason", "validation"):
            need(text(item.get(k)), f"learning needs {k}")
        evidence = item.get("evidence")
        need(isinstance(evidence, list), "learning evidence must be a list")
        for path in evidence if isinstance(evidence, list) else []:
            ref(path)
        if item.get("decision") == "legacy_unknown":
            need(any(r.get("id") == item.get("run_id") and r.get("origin") == "legacy" for r in runs), "legacy_unknown only for imported history")
        elif item.get("decision") not in {"none", "defer"}:
            ref(item.get("target"))
            if item.get("status") == "applied":
                need(bool(evidence), "applied learning needs evidence")
                if item.get("layer") == "host":
                    need(text(item.get("approval_basis")), "host knowledge change requires approval basis")
    if phase == "delivery":
        for rid in run_ids:
            need(any(a.get("run_id") == rid and a.get("delivery_status") in {"ready", "referenced", "delivered", "unknown"} for a in artifacts), f"{rid}: no deliverable")
            need(any(x.get("run_id") == rid for x in learning), f"{rid}: missing learning decision (none allowed with reason)")
    if data.get("claim_review"):
        p_review = ref(data["claim_review"])
        if p_review:
            from .evidence_review import review
            review_spec = read_json(p_review)
            reviewed = review(root, review_spec)
            if isinstance(review_spec, dict):
                for claim in review_spec.get("claims", []):
                    for source in claim.get("sources", []) if isinstance(claim, dict) else []:
                        ref(source.get("path"))
            errors.extend(reviewed["errors"])
            if reviewed["unresolved"]:
                warnings.append("claims unresolved: " + ", ".join(reviewed["unresolved"]))
    if data.get("review_not_applicable") is not None:
        need(text(data["review_not_applicable"]), "review_not_applicable requires a reason")
    return {"ok": not errors, "errors": errors, "warnings": warnings, "protected_paths": sorted(protected)}


def capture(root, case_path, run_id, source, artifact_id, kind, version, supersedes=None):
    root = Path(root).resolve()
    path = local_path(root, case_path)
    data = read_json(path)
    if not IDENTIFIER.fullmatch(artifact_id) or not any(r["id"] == run_id for r in data["runs"]):
        raise ValueError("invalid artifact id or run")
    if any(a["id"] == artifact_id for a in data["artifacts"]):
        raise ValueError("artifact id already exists")
    if supersedes and not any(a["id"] == supersedes for a in data["artifacts"]):
        raise ValueError("superseded artifact does not exist")
    source_path = local_path(root, source)
    # Keep same-directory report references valid for the usual task-local source.
    destination = path.parent / ("deliverable-" + artifact_id + source_path.suffix)
    local_path(root, destination.relative_to(root).as_posix(), exists=False)
    destination.parent.mkdir(exist_ok=True)
    content = source_path.read_bytes()
    rebased = source_path.suffix.lower() == ".md" and source_path.parent != destination.parent
    if rebased:
        def rebase(match):
            target = match.group(1).strip("<>")
            if ":" in target or target.startswith("#"):
                return match.group(0)
            file, separator, anchor = target.partition("#")
            resolved = (source_path.parent / file).resolve()
            if not resolved.is_relative_to(root):
                return match.group(0)
            adjusted = os.path.relpath(resolved, destination.parent).replace("\\", "/")
            return "](<" + adjusted + ("#" + anchor if separator else "") + ">)"
        content = re.sub(r"\]\(([^)]+)\)", rebase, content.decode("utf-8-sig")).encode("utf-8")
    with destination.open("xb") as stream:
        stream.write(content)
    artifact = {"id": artifact_id, "run_id": run_id, "kind": kind, "version": version,
                "source": source, "path": destination.relative_to(root).as_posix(), "sha256": digest(destination),
                "source_sha256": digest(source_path), "links_rebased": rebased,
                "captured_at": now(), "task_ids": [], "delivery_status": "ready", "delivered_at": None,
                "delivery_basis": "", "approval_status": "not_requested", "approval_basis": "", "supersedes": supersedes}
    data["artifacts"].append(artifact)
    write_json(path, data)
    return artifact


def seal(root, case_path, run_id):
    root = Path(root).resolve()
    path = local_path(root, case_path)
    data = read_json(path)
    run = next((r for r in data["runs"] if r["id"] == run_id), None)
    if not run or not IDENTIFIER.fullmatch(run_id) or not run.get("execution") or run.get("execution_sha256"):
        raise ValueError("run missing execution or already sealed")
    source = local_path(root, run["execution"])
    dest = path.parent / "history" / (run_id + ".execution.json")
    local_path(root, dest.relative_to(root).as_posix(), exists=False)
    dest.parent.mkdir(exist_ok=True)
    with dest.open("xb") as stream:
        stream.write(source.read_bytes())
    run.update(source_execution=run["execution"], execution=dest.relative_to(root).as_posix(), execution_sha256=digest(dest))
    write_json(path, data)
    return run


def discover(root, scans):
    root = Path(root).resolve()
    files = set()
    for value in scans:
        start = local_path(root, value, exists=False)
        if not start.is_dir():
            raise ValueError(f"scan directory not found: {value}")
        for directory, dirs, names in os.walk(start, followlinks=False):
            dirs[:] = sorted(d for d in dirs if d not in SKIP and not (Path(directory) / d).is_symlink())
            for name in ("case.json", "execution.json"):
                if name in names:
                    p = Path(directory) / name
                    if name == "execution.json" and "case.json" in names:
                        continue
                    files.add(p)
    return sorted(files)


def build_index(root, scans):
    root = Path(root).resolve()
    entries, issues, protected = [], [], set()
    for path in discover(root, scans):
        rel = path.relative_to(root).as_posix()
        try:
            local_path(root, rel)
            data = read_json(path)
            if not isinstance(data, dict):
                raise ValueError("record must be object")
            if path.name == "execution.json":
                if not isinstance(data.get("case_id"), str) or not IDENTIFIER.fullmatch(data["case_id"]):
                    raise ValueError("legacy record missing case_id")
                report = data.get("report")
                if report:
                    local_path(root, report)
                    protected.add(report)
                entries.append({"case_id": data["case_id"], "title": data.get("request", ""), "question": data.get("request", ""),
                                "scope": data.get("scope", ""), "tags": [], "path": rel, "record_type": "legacy_execution",
                                "valid": True, "relations": [], "runs": [], "artifacts": [], "learning": [], "reuse": [],
                                "legacy_report": report, "gaps": ["Legacy: delivery/approval/learning links not registered"]})
                protected.add(rel)
                continue
            completed = isinstance(data.get("runs"), list) and all(isinstance(r, dict) and r.get("status") != "in_progress" for r in data["runs"])
            check = validate_case(data, root, "delivery" if completed else "plan")
            if not isinstance(data.get("case_id"), str) or not IDENTIFIER.fullmatch(data["case_id"]):
                issues.append({"path": rel, "error": "invalid case_id"})
                continue
            protected.update(check["protected_paths"])
            protected.add(rel)
            entries.append({**{k: data.get(k) for k in ("case_id", "title", "question", "scope", "tags", "relations", "runs", "artifacts", "learning", "reuse", "claim_review", "review_not_applicable")},
                            "path": rel, "record_type": "case", "valid": check["ok"], "gaps": check["errors"] + check["warnings"]})
            issues.extend({"path": rel, "error": e} for e in check["errors"])
        except (ValueError, OSError, TypeError, KeyError) as exc:
            issues.append({"path": rel, "error": str(exc)})
    by_id = {}
    for e in entries:
        by_id.setdefault(e["case_id"], []).append(e)
    for cid, group in by_id.items():
        if len(group) > 1:
            for e in group:
                e["valid"] = False
            issues.append({"case_id": cid, "error": "duplicate case_id"})
    for e in entries:
        for relation in (e.get("relations") if isinstance(e.get("relations"), list) else []) or []:
            if not isinstance(relation, dict):
                continue
            if relation.get("case_id") not in by_id:
                e["valid"] = False
                issues.append({"case_id": e["case_id"], "error": "relation target missing from scanned roots"})
    edges = {e["case_id"]: [r["case_id"] for r in (e.get("relations") if isinstance(e.get("relations"), list) else [])
                              if isinstance(r, dict) and r.get("kind") != "related" and isinstance(r.get("case_id"), str)] for e in entries}
    def has_cycle(start, cur, visited):
        for target in edges.get(cur, []):
            if target == start:
                return True
            if target not in visited and has_cycle(start, target, visited | {target}):
                return True
        return False
    for e in entries:
        if has_cycle(e["case_id"], e["case_id"], {e["case_id"]}):
            e["valid"] = False
            issues.append({"case_id": e["case_id"], "error": "follow-up/correction/rerun relation cycle"})
    from .case_ops import discover_outputs
    unregistered = discover_outputs(root, scans, protected)
    return {"schema_version": 1, "generated_at": now(), "scan_roots": scans, "entries": entries, "unregistered_outputs": unregistered,
            "issues": issues, "protected_paths": sorted(protected), "notice": "Local derived index, not approved business knowledge; no deletion is authorized."}


def search(index, query="", tag=None, case_id=None, target=None):
    from .case_search import match, equivalents
    norm = lambda s: unicodedata.normalize("NFKC", s).casefold()
    terms = norm(query).split()
    result = []
    for entry in index["entries"]:
        if not entry["valid"] or (case_id and entry["case_id"] != case_id):
            continue
        if tag and not any(norm(t) in [norm(a) for a in equivalents(tag)] for t in entry.get("tags") or []):
            continue
        if target and not any(x.get("target") == target or x.get("path") == target for x in (entry.get("learning") or []) + (entry.get("reuse") or [])):
            continue
        # Never search raw evidence bodies or credentials. Scope/tags help filter versions/countries.
        text = norm(json.dumps({k: entry.get(k) for k in ("title", "question", "scope", "tags", "learning")}, ensure_ascii=False))
        found, concepts = match(query, text)
        if found:
            result.append({**entry, "search_match": concepts})
    return result


def render_index(index, root, destination):
    root, destination = Path(root).resolve(), Path(destination).resolve()
    def safe(value):
        return str(value).replace("\n", " ").replace("\r", " ").replace("|", "\\|").replace("[", "\\[").replace("]", "\\]")
    def link(label, path):
        relative = os.path.relpath(local_path(root, path), destination.parent).replace("\\", "/")
        return f"[{safe(label)}](<{relative}>)"
    lines = ["# Local case and artifact index", "", f"Generated: {index['generated_at']}. Derived index; source records remain in task directories.", "",
             "Delivery, execution and approval are independent. Unknown means missing historical information; this index does not approve knowledge.", "",
             "| Question | Scope tags | Execution | Latest artifacts | Learning |", "| --- | --- | --- | --- | --- |"]
    for entry in index["entries"]:
        if not entry["valid"]:
            lines.append(f"| {link('[Validation failed] ' + str(entry.get('title', entry['case_id'])), entry['path'])} | \u2014 | Needs repair | Not ready for reuse | Needs repair |")
            continue
        superseded = {a.get("supersedes") for a in entry.get("artifacts") or []}
        artifacts = []
        for art in entry.get("artifacts") or []:
            if not isinstance(art, dict) or art.get("id") in superseded:
                continue
            try:
                artifacts.append(link(f"{art['version']} / {art['delivery_status']} / {art['approval_status']}", art["path"]))
            except (ValueError, KeyError):
                artifacts.append("\u4ea4\u4ed8\u7269Needs repair")
        if entry.get("legacy_report"):
            artifacts.append(link("Historical report, status unregistered", entry["legacy_report"]))
        states = ", ".join(r.get("status", "unknown") for r in entry.get("runs") or [] if isinstance(r, dict)) or "legacy"
        learning = ", ".join(x.get("decision", "unknown") for x in entry.get("learning") or [] if isinstance(x, dict)) or "Unregistered"
        label = ("[Validation failed] " if not entry["valid"] else "") + entry.get("title", entry["case_id"])
        lines.append(f"| {link(label, entry['path'])} | {safe(', '.join(entry.get('tags') or []))} | {safe(states)} | {'; '.join(artifacts) or 'Unregistered'} | {safe(learning)} |")
    lines += ["", f"Global link issues: {len(index['issues'])}. See the corresponding JSON and inspect sources before reuse.", ""]
    lines += ["## Unregistered\u4ea7\u7269\u5019\u9009", "", "Scan candidates require review before registration; they do not prove independent questions or delivery.", ""]
    lines += ["- " + link(x["path"], x["path"]) for x in index.get("unregistered_outputs", [])]
    if not index.get("unregistered_outputs"):
        lines.append("No candidates found in the scanned scope.")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    for key in ("directory", "id", "title", "question", "scope"):
        init.add_argument("--" + key, required=True)
    init.add_argument("--tag", action="append", default=[])
    init.add_argument("--execution")
    init.add_argument("--legacy", action="store_true")
    check = sub.add_parser("check")
    check.add_argument("case")
    check.add_argument("--phase", choices=["plan", "delivery"], default="delivery")
    cap = sub.add_parser("capture")
    for key in ("case", "run", "source", "id", "kind", "version"):
        cap.add_argument("--" + key, required=True)
    cap.add_argument("--supersedes")
    close = sub.add_parser("seal")
    close.add_argument("--case", required=True)
    close.add_argument("--run", required=True)
    for name in ("index", "search"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--scan", action="append", required=True)
        if name == "index":
            cmd.add_argument("--output", required=True)
        else:
            cmd.add_argument("--query", default="")
            cmd.add_argument("--tag")
            cmd.add_argument("--case-id")
            cmd.add_argument("--target")
    args = parser.parse_args()
    try:
        if args.command == "init":
            if not IDENTIFIER.fullmatch(args.id):
                raise ValueError("invalid case id")
            directory = local_path(args.root, args.directory, exists=False)
            if args.execution:
                local_path(args.root, args.execution)
            data = {"schema_version": 1, "case_id": args.id, "title": args.title, "question": args.question,
                    "scope": args.scope, "tags": args.tag, "registered_at": now(), "retention": "retain", "relations": [],
                    "runs": [{"id": "R1", "origin": "legacy" if args.legacy else "native", "recorded_at": now(),
                              "status": "in_progress", "summary": "Registered; execution or historical review pending", "execution": args.execution}],
                    "artifacts": [], "reuse": [], "learning": []}
            result = validate_case(data, args.root, "plan")
            if not result["ok"]:
                raise ValueError("; ".join(result["errors"]))
            write_json(directory / "case.json", data, new=True)
            result = {"created": (directory / "case.json").relative_to(args.root.resolve()).as_posix()}
        elif args.command == "check":
            result = validate_case(read_json(local_path(args.root, args.case)), args.root, args.phase)
        elif args.command == "capture":
            result = capture(args.root, args.case, args.run, args.source, args.id, args.kind, args.version, args.supersedes)
        elif args.command == "seal":
            result = seal(args.root, args.case, args.run)
        else:
            result = build_index(args.root, args.scan)
            if args.command == "index":
                output = local_path(args.root, args.output, exists=False)
                if ".local" not in output.relative_to(args.root.resolve()).parts or output.suffix != ".json" or output.name in {"case.json", "execution.json"}:
                    raise ValueError("derived index must be a JSON file in a .local directory")
                write_json(output, result)
                markdown = output.with_suffix(".md")
                markdown.write_text(render_index(result, args.root, markdown), encoding="utf-8")
                result = {"entries": len(result["entries"]), "issues": result["issues"], "unregistered_outputs": len(result["unregistered_outputs"]), "output": args.output,
                          "readable_index": markdown.relative_to(args.root.resolve()).as_posix()}
            else:
                result = {"matches": search(result, args.query, args.tag, args.case_id, args.target), "issues": result["issues"],
                          "unregistered_outputs": result["unregistered_outputs"]}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if result.get("ok") is False or result.get("issues") else 0
    except (OSError, ValueError, TypeError, KeyError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
