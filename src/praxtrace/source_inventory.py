"""List reviewable Java/Spring/MyBatis/DDL source assets for a coverage ledger."""

import argparse
from collections import Counter
import json
from pathlib import Path
import re


ANNOTATIONS = {
    "api": r"RequestMapping|GetMapping|PostMapping|PutMapping|PatchMapping|DeleteMapping",
    "job": r"JobHandler|XxlJob|Scheduled",
    "event": r"StreamListener|RocketMQMessageListener|KafkaListener|RabbitListener|JmsListener|EventListener",
    "table": r"TableName",
}
JAVA = {
    kind: re.compile(r"(?m)^[ \t]*@(" + names + r")\b(?:\s*\(([^)]*)\))?")
    for kind, names in ANNOTATIONS.items()
}
XML = re.compile(r"<mapper\s+namespace\s*=\s*[\"']([^\"']+)[\"']")
DDL = re.compile(
    r"(?im)^[ \t]*CREATE\s+(?:OR\s+REPLACE\s+)?(TABLE|VIEW|TRIGGER|PROCEDURE|FUNCTION)"
    r"\s+(?:IF\s+NOT\s+EXISTS\s+)?([^\s(]+)"
)
QUOTED = re.compile(r'"([^"]+)"')
EXCLUDED = {".git", ".cache", "build", "target", "node_modules", "dist", "vendor"}


def entries(root: Path) -> list[dict]:
    assets = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        suffix = path.suffix.lower()
        if suffix not in {".java", ".xml", ".sql"} or EXCLUDED.intersection(relative.parts):
            continue
        if not path.is_file() or not path.resolve().is_relative_to(root):
            continue
        if "test" in relative.parts:
            continue
        text = path.read_text(encoding="utf-8-sig", errors="replace")

        def add(kind, token, start):
            assets.append({
                "kind": kind, "token": token, "file": relative.as_posix(),
                "line": text.count("\n", 0, start) + 1,
            })

        if suffix == ".java":
            for kind, pattern in JAVA.items():
                for match in pattern.finditer(text):
                    annotation, body = match.groups()
                    argument = QUOTED.search(body or "")
                    value = argument.group(1) if argument else " ".join((body or "").split())
                    add(kind, f"@{annotation}" + (f" {value}" if value else ""), match.start())
            if path.stem.endswith("Mapper"):
                declaration = re.search(r"\binterface\s+" + re.escape(path.stem) + r"\b", text)
                if declaration:
                    add("mapper", path.stem, declaration.start())
        elif suffix == ".xml":
            for match in XML.finditer(text):
                add("mapper_xml", match.group(1), match.start())
        else:
            for match in DDL.finditer(text):
                add(match.group(1).lower(), match.group(2), match.start())
    return assets


def key(row: dict) -> tuple:
    return row["kind"], row["file"], row["token"]


def differences(assets: list[dict], baseline: list[dict]) -> list[dict]:
    known = Counter(map(key, baseline))
    unregistered = []
    for asset in assets:
        identity = key(asset)
        if known[identity]:
            known[identity] -= 1
        else:
            unregistered.append(asset)
    return unregistered


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_root", type=Path)
    parser.add_argument("--against", type=Path, help="reviewed JSON ledger with an assets list")
    args = parser.parse_args()
    try:
        root = args.source_root.resolve(strict=True)
        if not root.is_dir():
            raise ValueError("source_root must be a directory")
        assets = entries(root)
        report = {"evidence": "source candidates", "totals": dict(Counter(row["kind"] for row in assets)), "assets": assets}
        if args.against:
            baseline = json.loads(args.against.read_text(encoding="utf-8"))["assets"]
            report["unregistered"] = differences(assets, baseline)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        parser.error(str(exc))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return int(bool(report.get("unregistered")))


if __name__ == "__main__":
    raise SystemExit(main())
