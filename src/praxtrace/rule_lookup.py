"""Look up a reviewed rule with citations in an explicitly supplied local catalog."""

import argparse
import json
from pathlib import Path
import re
import unicodedata


def normalized(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def matches(text: str, phrase: str) -> bool:
    term = normalized(phrase)
    if not term:
        return False
    left = r"(?<![a-z0-9_-])" if term[0].isascii() and term[0].isalnum() else ""
    right = r"(?![a-z0-9_-])" if term[-1].isascii() and term[-1].isalnum() else ""
    return re.search(left + re.escape(term) + right, text) is not None


def lookup(catalog: list[dict], country: str, project: str, version: str, question: str) -> dict:
    expected = (normalized(country), normalized(project), version.strip())
    query = normalized(question)
    candidates = []
    for rule in catalog:
        if rule.get("review_status") != "approved":
            continue
        scope = (normalized(rule["country"]), normalized(rule["project"]), rule["version"])
        if scope != expected:
            continue
        aliases = rule["aliases"]
        if not isinstance(aliases, list) or not all(isinstance(alias, str) for alias in aliases):
            raise ValueError(f"{rule['id']}: expected a list of aliases")
        if not any(matches(query, label) for label in [rule["id"], *aliases]):
            continue
        refs = rule["sources"]
        if not isinstance(refs, list) or not refs or not all(isinstance(ref, str) and ref.strip() for ref in refs):
            continue
        if rule["evidence"] not in {"implemented", "observed", "documented"} or not rule["answer"].strip():
            continue
        candidates.append({key: rule[key] for key in ("id", "answer", "evidence", "sources")})

    if len(candidates) == 1:
        return {
            "status": "found", "scope": {"country": country, "project": project, "version": version},
            **candidates[0],
        }
    if candidates:
        return {"status": "ambiguous", "rule_ids": sorted({rule["id"] for rule in candidates})}
    return {"status": "unknown", "reason": "No matching approved rule with evidence in this exact scope."}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("catalog", type=Path, help="authorized, sanitized JSON rule catalog")
    parser.add_argument("--country", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--version", required=True, help="exact catalog version")
    parser.add_argument("--question", required=True)
    args = parser.parse_args()
    if not all(value.strip() for value in (args.country, args.project, args.version, args.question)):
        parser.error("scope and question must be nonempty")
    try:
        catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
        if not isinstance(catalog, list):
            raise ValueError("expected a list of rules")
        result = lookup(catalog, args.country, args.project, args.version, args.question)
    except (OSError, ValueError, TypeError, AttributeError, KeyError) as exc:
        parser.error(f"invalid rule catalog: {exc}")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
