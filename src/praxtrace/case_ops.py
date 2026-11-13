"""Discover unregistered outputs, import reviewed history, reconcile reply links and finish."""
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path


def discover_outputs(root, scans, registered):
    from .case_records import local_path, SKIP
    root = Path(root).resolve()
    known = {local_path(root, x, exists=False) for x in registered}
    candidates = set()
    for scan in scans:
        start = local_path(root, scan, exists=False)
        if not start.is_dir():
            raise ValueError(f"scan directory missing: {scan}")
        for directory, dirs, names in os.walk(start, followlinks=False):
            dirs[:] = [d for d in dirs if d not in SKIP | {"scripts", "fixtures", "history", "snapshots"} and not (Path(directory) / d).is_symlink()]
            parent = Path(directory)
            for name in names:
                p = parent / name
                if p.suffix.lower() not in {".md", ".html", ".xlsx", ".pdf", ".docx"} or p.is_symlink():
                    continue
                if name.startswith("deliverable-") or p.stem.lower() in {"readme", "index", "evidence", "attribution"} or "template" in p.stem.lower():
                    continue
                if not any(x in p.parts for x in ("outputs", "out")):
                    continue
                local_path(root, p.relative_to(root).as_posix())
                if p.resolve() not in known:
                    candidates.add(p.relative_to(root).as_posix())
    return [{"path": p, "status": "unregistered_output", "note": "Candidate artifact; independent question and delivery not verified"} for p in sorted(candidates)]


def import_report(root, source, title, question, scope, tags):
    from .case_records import local_path, now, write_json, read_json, capture
    root = Path(root).resolve()
    src = local_path(root, source)
    cid = "history-" + hashlib.sha256(src.relative_to(root).as_posix().encode()).hexdigest()[:16]
    folder = root / "analysis/workspace/outputs" / cid
    record = folder / "case.json"
    if record.exists():
        return {"case": record.relative_to(root).as_posix(), "created": False}
    data = {"schema_version": 1, "case_id": cid, "title": title, "question": question, "scope": scope,
            "tags": tags, "registered_at": now(), "retention": "retain", "relations": [],
            "runs": [{"id": "R1", "origin": "legacy", "recorded_at": now(), "status": "partial",
                      "summary": "Imported historical report; execution, approval and completeness not revalidated.", "execution": None}],
            "artifacts": [], "reuse": [], "learning": [{"id": "L1", "run_id": "R1", "decision": "legacy_unknown",
                      "layer": "case", "status": "unknown", "reason": "Import does not infer historical learning decisions.",
                      "validation": "Report existence and topic checked; business queries not rerun.", "evidence": [source]}]}
    write_json(record, data, new=True)
    capture(root, record.relative_to(root).as_posix(), "R1", source, "A1", "report", "historical-draft")
    data = read_json(record)
    data["artifacts"][0].update(delivery_status="unknown", approval_status="unknown")
    write_json(record, data)
    return {"case": record.relative_to(root).as_posix(), "created": True}


def receipt_check(receipt, artifact, root):
    from .case_records import timestamp, local_path
    required = ("thread_id", "turn_id", "message_id")
    if receipt.get("phase") != "final_answer" or not all(isinstance(receipt.get(k), str) and receipt[k] for k in required):
        raise ValueError("receipt needs completed final-answer identity")
    if not timestamp(receipt.get("completed_at")):
        raise ValueError("receipt missing completion time")
    links = receipt.get("links", [])
    if not isinstance(links, list):
        raise ValueError("receipt links must be a list")
    normalized = {local_path(root, p, exists=False) for p in links}
    exact = local_path(root, artifact["path"]) in normalized
    source = artifact.get("source") and local_path(root, artifact["source"], exists=False) in normalized
    if not exact and not source:
        raise ValueError("reply does not reference this artifact or its source")
    captured = artifact.get("captured_at")
    version_known = exact and timestamp(captured) and datetime.fromisoformat(captured.replace("Z", "+00:00")) <= datetime.fromisoformat(receipt["completed_at"].replace("Z", "+00:00"))
    return "delivered" if version_known else "referenced"


def reconcile(root, case_path, bundle_path):
    from .case_records import local_path, read_json, write_json, digest
    root = Path(root).resolve()
    path, bundle_file = local_path(root, case_path), local_path(root, bundle_path)
    data, bundle = read_json(path), read_json(bundle_file)
    if bundle.get("schema_version") != 1 or bundle.get("source") != "codex.read_thread":
        raise ValueError("unsupported receipt bundle")
    changes = []
    for artifact in data["artifacts"]:
        for i, receipt in enumerate(bundle.get("receipts", [])):
            try:
                status = receipt_check(receipt, artifact, root)
            except (ValueError, KeyError):
                continue
            if artifact.get("delivery_status") == "delivered":
                continue
            artifact.update(delivery_status=status, delivery_receipt={"path": bundle_path, "sha256": digest(bundle_file), "item": i},
                            delivery_basis=f"Final response reference; {receipt['thread_id']}/{receipt['message_id']}. Timestamp is the completion time of that turn.",
                            delivered_at=receipt["completed_at"] if status == "delivered" else None)
            changes.append({"artifact": artifact["id"], "status": status})
            break
    if changes:
        write_json(path, data)
    return {"case": case_path, "changes": changes}


def finish(root, case_path, scans, output):
    from .case_records import validate_case, read_json, local_path, write_json, build_index, render_index
    from .execution_record import check_record
    root = Path(root).resolve()
    data = read_json(local_path(root, case_path))
    result = validate_case(data, root)
    for run in data["runs"]:
        if run.get("execution"):
            checked = check_record(read_json(local_path(root, run["execution"])), root)
            result["errors"].extend(checked["errors"])
    if not data.get("claim_review") and not data.get("review_not_applicable"):
        result["errors"].append("record claim_review or justified review_not_applicable before finish")
    result["ok"] = not result["errors"]
    if not result["ok"]:
        return result
    index = build_index(root, scans)
    if index["issues"]:
        return {"ok": False, "errors": index["issues"]}
    destination = local_path(root, output, exists=False)
    if ".local" not in destination.relative_to(root).parts or destination.suffix != ".json":
        raise ValueError("index must be .local JSON")
    write_json(destination, index)
    destination.with_suffix(".md").write_text(render_index(index, root, destination.with_suffix(".md")), encoding="utf-8")
    return {"ok": True, "case": data["case_id"], "unregistered_outputs": len(index["unregistered_outputs"]),
            "notice": "ready is not automatically delivered or approved"}


def main():
    from .case_records import build_index
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    sub = parser.add_subparsers(dest="command", required=True)
    audit = sub.add_parser("audit")
    audit.add_argument("--scan", action="append", required=True)
    importer = sub.add_parser("import-report")
    for key in ("source", "title", "question", "scope"):
        importer.add_argument("--" + key, required=True)
    importer.add_argument("--tag", action="append", default=[])
    rec = sub.add_parser("reconcile")
    rec.add_argument("--case", required=True)
    rec.add_argument("--receipts", required=True)
    close = sub.add_parser("finish")
    close.add_argument("--case", required=True)
    close.add_argument("--scan", action="append", required=True)
    close.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        if args.command == "audit":
            index = build_index(args.root, args.scan)
            result = {"issues": index["issues"], "unregistered_outputs": index["unregistered_outputs"],
                      "pending_delivery": [{"case": e["case_id"], "artifact": a["id"], "status": a["delivery_status"]}
                        for e in index["entries"] if e["valid"] for a in e.get("artifacts", []) if a["delivery_status"] in {"ready", "unknown"}],
                      "claim_review_pending": [e["case_id"] for e in index["entries"] if not e.get("claim_review") and not e.get("review_not_applicable")]}
        elif args.command == "import-report":
            result = import_report(args.root, args.source, args.title, args.question, args.scope, args.tag)
        elif args.command == "reconcile":
            result = reconcile(args.root, args.case, args.receipts)
        else:
            result = finish(args.root, args.case, args.scan, args.output)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if result.get("ok") is False or result.get("issues") else 0
    except (OSError, ValueError, TypeError, KeyError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
