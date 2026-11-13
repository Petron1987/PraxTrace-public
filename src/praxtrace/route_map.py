"""Pair source-declared API paths with their aliases and cite both lines."""

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path


def read_paths(root: Path, filename: str) -> dict:
    files = sorted(root.rglob(filename))
    if len(files) != 1:
        raise ValueError(f"expected one {filename} under {root}; found {len(files)}")
    path = files[0]
    if not path.resolve().is_relative_to(root):
        raise ValueError(f"{filename} points outside the source root")
    rows = defaultdict(list)
    for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        text = line.strip()
        if not text or text.startswith(("#", "!")):
            continue
        key, separator, route = text.partition("=")
        key = key.strip()
        routes = [item.strip() for item in route.split(",") if item.strip()]
        if not separator or not key or not routes or any(not item.startswith("/") for item in routes):
            raise ValueError(f"unsupported route declaration at {path.relative_to(root)}:{number}")
        rows[key].extend(
            {"path": item, "file": path.relative_to(root).as_posix(), "line": number}
            for item in routes
        )
    return rows


def route_map(root: Path) -> list[dict]:
    original = read_paths(root, "mapping-path-ori.properties")
    aliases = read_paths(root, "mapping-path-add.properties")
    alias_handlers = defaultdict(set)
    for handler, paths in aliases.items():
        for item in paths:
            alias_handlers[item["path"]].add(handler)
    result = []
    for handler in sorted(original.keys() | aliases.keys()):
        source = original.get(handler, [])
        target = aliases.get(handler, [])
        duplicate = any(len({item["line"] for item in side}) > 1 for side in (source, target))
        shared = any(len(alias_handlers[row["path"]]) > 1 for row in target)
        if duplicate or shared:
            state = "ambiguous"
        elif source and target:
            state = "paired"
        else:
            state = "original_only" if source else "alias_only"
        result.append({"handler": handler, "original": source, "alias": target, "state": state})
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_root", type=Path, help="one country worktree or service")
    parser.add_argument("--find", default="", help="part of a handler name or API path")
    parser.add_argument("--limit", type=int, default=30)
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("--limit must be positive")
    try:
        rows = route_map(args.source_root.resolve(strict=True))
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    term = args.find.strip().casefold()
    matches = [
        row for row in rows
        if term in row["handler"].casefold()
        or any(term in item["path"].casefold() for item in row["original"] + row["alias"])
    ]
    print(json.dumps({
        "evidence": "source declarations",
        "totals": dict(Counter(row["state"] for row in rows)),
        "matches": len(matches),
        "routes": matches[:args.limit],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
