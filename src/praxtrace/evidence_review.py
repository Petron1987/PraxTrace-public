"""Check explicit claim evidence and deterministic calculations; not a truth oracle."""
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path


def review(root, spec):
    from .case_records import digest, local_path, timestamp
    errors, results, unresolved = [], [], []
    if not isinstance(spec, dict) or spec.get("schema_version") != 1 or not isinstance(spec.get("claims"), list) or not spec["claims"]:
        return {"ok": False, "errors": ["invalid claim review"], "results": [], "unresolved": []}
    identifiers = set()
    for claim in spec["claims"]:
        try:
            cid = claim["id"]
            if cid in identifiers or not isinstance(cid, str):
                raise ValueError("duplicate/invalid claim id")
            identifiers.add(cid)
            for field in ("statement", "scope", "as_of", "review_note"):
                if not isinstance(claim.get(field), str) or not claim[field].strip():
                    raise ValueError(f"missing {field}")
            if not timestamp(claim["checked_at"]):
                raise ValueError("checked_at needs timezone")
            status = claim["status"]
            if status not in {"supported", "partial", "conflict", "unknown"}:
                raise ValueError("invalid claim status")
            if status != "supported":
                unresolved.append(cid)
            sources = claim.get("sources", [])
            if status == "supported" and not sources:
                raise ValueError("supported claim has no sources")
            for source in sources:
                path = local_path(root, source["path"])
                if digest(path) != source["sha256"]:
                    raise ValueError("source changed; recheck claim")
            allowed = {s["path"] for s in sources}
            def value(locator):
                if locator["path"] not in allowed:
                    raise ValueError("calculation source absent from claim sources")
                data = json.loads(local_path(root, locator["path"]).read_text(encoding="utf-8-sig"))
                pointer = locator["pointer"]
                if pointer and not pointer.startswith("/"):
                    raise ValueError("invalid JSON pointer")
                for key in pointer.split("/")[1:]:
                    key = key.replace("~1", "/").replace("~0", "~")
                    data = data[int(key)] if isinstance(data, list) else data[key]
                return data
            def number(raw):
                if isinstance(raw, bool):
                    raise ValueError("boolean is not numeric evidence")
                n = Decimal(str(raw))
                if not n.is_finite():
                    raise ValueError("non-finite evidence")
                return n
            for check in claim.get("checks", []):
                kind = check["kind"]
                if kind == "equals":
                    passed = value(check["value"]) == check["expected"]
                else:
                    values = [number(value(v)) for v in check["values"]]
                    if kind == "sum":
                        actual = sum(values)
                    elif kind == "ratio" and len(values) == 2 and values[1] != 0:
                        actual = values[0] / values[1]
                    else:
                        raise ValueError("invalid calculation or zero denominator")
                    tolerance = number(check.get("tolerance", "0"))
                    if tolerance < 0:
                        raise ValueError("negative tolerance")
                    passed = abs(actual - number(check["expected"])) <= tolerance
                results.append({"claim": cid, "kind": kind, "passed": passed})
                if not passed:
                    errors.append(f"{cid}: {kind} disagrees with saved source")
        except (KeyError, TypeError, ValueError, OSError, InvalidOperation, IndexError) as exc:
            errors.append(f"claim {claim.get('id', '?') if isinstance(claim, dict) else '?'}: {exc}")
    return {"ok": not errors, "errors": errors, "results": results, "unresolved": unresolved,
            "meaning": "Checks saved evidence, arithmetic and recorded review only; does not prove source truth or causality."}
