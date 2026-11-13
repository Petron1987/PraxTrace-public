"""Compare verified aggregate metrics across two complete, equal-length periods.

No database access or business-rule inference. See ../growth-analysis.md.
"""

import argparse
from datetime import datetime, time
from decimal import Decimal, InvalidOperation, localcontext
import json
from pathlib import Path


VERSION = "1"


def number(value, label, integer=False):
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError(f"{label}: expected a nonnegative number")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"{label}: invalid number") from exc
    if not result.is_finite() or result < 0 or (integer and result != result.to_integral_value()):
        raise ValueError(f"{label}: expected a finite nonnegative {'integer' if integer else 'number'}")
    if result and abs(result.adjusted()) > 24:
        raise ValueError(f"{label}: magnitude outside supported range")
    return result


def formatted(value):
    if value is None:
        return None
    rounded = value.quantize(Decimal("0.00000001"))
    return "0" if rounded == 0 else format(rounded, "f").rstrip("0").rstrip(".")


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError("timestamps must be ISO strings")
    result = datetime.fromisoformat(value)
    if result.utcoffset() is None:
        raise ValueError("timestamps require an explicit UTC offset")
    return result


def text_field(item, key):
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} is required")
    return value


def compare(data):
    if not isinstance(data, dict) or type(data.get("schema_version")) is not int or data["schema_version"] != 1:
        raise ValueError("schema_version must be 1")
    for key in ("scope", "source_ref"):
        text_field(data, key)
    as_of = timestamp(data.get("as_of"))
    windows = []
    for name in ("previous", "current"):
        window = data.get(name)
        if not isinstance(window, dict):
            raise ValueError(f"{name}: window is required")
        start, end = timestamp(window.get("start")), timestamp(window.get("end"))
        if any(t.utcoffset() != as_of.utcoffset() or t.time() != time() for t in (start, end)):
            raise ValueError("windows require local midnight boundaries and the same fixed UTC offset")
        if start >= end or end > as_of:
            raise ValueError("windows must be positive and end no later than data as_of")
        windows.append((start, end))
    (p0, p1), (c0, c1) = windows
    if p1 > c0 or p1 - p0 != c1 - c0:
        raise ValueError("windows must not overlap and must have equal duration")
    metrics = data.get("metrics")
    if not isinstance(metrics, list) or not metrics:
        raise ValueError("metrics must be a nonempty list")
    results, values = [], {}
    with localcontext() as ctx:
        ctx.prec = 64
        for metric in metrics:
            if not isinstance(metric, dict):
                raise ValueError("metric must be an object")
            identifier = text_field(metric, "id")
            if identifier in values:
                raise ValueError(f"duplicate metric id: {identifier}")
            kind = metric.get("kind")
            if kind not in ("count", "amount", "rate"):
                raise ValueError(f"{identifier}: kind must be count, amount or rate")
            unit, basis = text_field(metric, "unit"), text_field(metric, "basis")
            raw, pair = {}, []
            for period in ("previous", "current"):
                val = metric.get(period)
                if kind == "rate":
                    if not isinstance(val, dict):
                        raise ValueError(f"{identifier}: rate needs numerator and denominator")
                    n = number(val.get("numerator"), "numerator", integer=True)
                    d = number(val.get("denominator"), "denominator", integer=True)
                    if n > d:
                        raise ValueError(f"{identifier}: numerator exceeds denominator")
                    raw[period] = {"numerator": formatted(n), "denominator": formatted(d)}
                    val = n / d * 100 if d else None
                else:
                    val = number(val, identifier, integer=kind == "count")
                pair.append(val)
            previous, current = pair
            values[identifier] = (kind, unit, previous, current)
            delta = current - previous if current is not None and previous is not None else None
            change_pct = delta / previous * 100 if delta is not None and previous else None
            results.append({
                "id": identifier, "kind": kind, "unit": unit, "basis": basis,
                "previous": formatted(previous), "current": formatted(current),
                "change_pp" if kind == "rate" else "change": formatted(delta),
                "change_pct": formatted(change_pct),
                "status": "missing_denominator" if delta is None else "zero_baseline" if not previous else "comparable",
                **({"counts": raw, "value_unit": "percent"} if kind == "rate" else {}),
            })
        decompositions = data.get("decompositions", [])
        if not isinstance(decompositions, list):
            raise ValueError("decompositions must be a list")
        parts = []
        for spec in decompositions:
            if not isinstance(spec, dict):
                raise ValueError("decomposition must be an object")
            total_id, count_id = text_field(spec, "amount"), text_field(spec, "count")
            if total_id not in values or count_id not in values:
                raise ValueError("decomposition references an unknown metric")
            kind, unit, a0, a1 = values[total_id]
            count_kind, _, n0, n1 = values[count_id]
            if kind != "amount" or count_kind != "count":
                raise ValueError("decomposition requires an amount and a count of the same population")
            if (not n0 and a0) or (not n1 and a1):
                raise ValueError("positive amount with zero count is inconsistent")
            if not n0 or not n1:
                parts.append({**spec, "status": "zero_count", "reason": "Average-based decomposition is undefined"})
                continue
            avg0, avg1 = a0 / n0, a1 / n1
            volume = (n1 - n0) * avg0
            average = (a1 - a0) - volume
            parts.append({
                "amount": total_id, "count": count_id, "unit": unit, "status": "computed",
                "average_previous": formatted(avg0), "average_current": formatted(avg1),
                "average_change_pct": formatted((avg1 / avg0 - 1) * 100) if avg0 else None,
                "amount_change": formatted(a1 - a0),
                "volume_component": formatted(volume), "average_component": formatted(average),
                "method": "volume-first: (n1-n0)*avg0 + n1*(avg1-avg0); arithmetic, not causality",
            })
    return {
        "schema_version": 1, "calculator_version": VERSION, "scope": data["scope"],
        "source_ref": data["source_ref"], "as_of": data["as_of"],
        "previous": data["previous"], "current": data["current"], "days": (p1-p0).days,
        "metrics": results, "decompositions": parts,
        "limits": "Caller must verify source, population, units, completeness and definitions. Two windows do not establish sustained growth or causal effects.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        result = compare(json.loads(args.input.read_text(encoding="utf-8-sig"), parse_float=Decimal))
        encoded = json.dumps(result, ensure_ascii=False, indent=2)
        if args.output:
            if args.output.resolve() == args.input.resolve():
                raise ValueError("output must differ from input")
            args.output.write_text(encoded + "\n", encoding="utf-8")
        print(encoded)
    except (OSError, ValueError, InvalidOperation) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
