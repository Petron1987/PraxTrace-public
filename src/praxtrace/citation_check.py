"""Check citation lines and optional literal anchors at one pinned Git commit."""

import argparse
import json
from pathlib import Path, PurePosixPath
import re
import subprocess


def git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "--no-optional-locks", "-C", str(root), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
    )


def check(root: Path, commit: str, citations: list[dict]) -> list[dict]:
    blobs = {}
    result = []
    for item in citations:
        file = item["file"]
        path = PurePosixPath(file)
        start, end = item["start"], item["end"]
        anchor = item.get("anchor")
        if not path.parts or path.is_absolute() or ".." in path.parts or "\\" in file or ":" in file:
            raise ValueError(f"invalid repository-relative path: {file}")
        if type(start) is not int or type(end) is not int or not 1 <= start <= end <= start + 59:
            raise ValueError(f"invalid citation range: {start}-{end}")
        if anchor is not None and (not isinstance(anchor, str) or not anchor.strip()):
            raise ValueError("anchor must be a nonempty string")

        if file not in blobs:
            blob = git(root, "cat-file", "blob", f"{commit}:{path.as_posix()}")
            lines = blob.stdout.removesuffix("\n").split("\n") if blob.stdout else []
            blobs[file] = lines if blob.returncode == 0 else None
        lines = blobs[file]
        excerpt = [] if lines is None or end > len(lines) else lines[start - 1:end]
        if lines is None:
            status = "missing_file"
        elif end > len(lines):
            status = "out_of_range"
        elif anchor is not None and anchor not in "\n".join(excerpt):
            status = "anchor_missing"
        else:
            status = "located"
        result.append({
            "id": item["id"], "file": file, "start": start, "end": end, "status": status,
            "excerpt": excerpt,
        })
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_root", type=Path)
    parser.add_argument("citations", type=Path, help="JSON list with id, file, start, end, optional anchor")
    parser.add_argument("--commit", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", args.commit):
        parser.error("--commit must be a full 40-character Git SHA")
    try:
        citations = json.loads(args.citations.read_text(encoding="utf-8"))
        if not isinstance(citations, list) or not citations:
            raise ValueError("expected a nonempty list of citations")
        root = args.source_root.resolve(strict=True)
        top = git(root, "rev-parse", "--show-toplevel")
        if top.returncode != 0 or Path(top.stdout.strip()).resolve() != root:
            raise ValueError(top.stderr.strip() or "provide the root of one Git checkout")
        pinned = git(root, "cat-file", "-t", args.commit)
        if pinned.returncode != 0 or pinned.stdout.strip() != "commit":
            raise ValueError(pinned.stderr.strip() or "could not verify the Git commit")
        results = check(root, args.commit, citations)
    except (OSError, ValueError, TypeError, KeyError, subprocess.TimeoutExpired) as exc:
        parser.error(str(exc))
    print(json.dumps({"commit": args.commit, "citations": results}, ensure_ascii=False, indent=2))
    return int(any(item["status"] != "located" for item in results))


if __name__ == "__main__":
    raise SystemExit(main())
