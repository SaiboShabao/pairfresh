"""PairFresh: check paired market snapshots before downstream research."""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

__version__ = "0.1.0"


def parse_timestamp(value):
    """Parse ISO 8601 timestamps; require an explicit UTC offset."""
    if not isinstance(value, str):
        raise ValueError("timestamp must be an ISO 8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("timestamp must be an ISO 8601 string") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include Z or a UTC offset")
    return parsed.astimezone(timezone.utc)


def valid_cycle(value):
    return (isinstance(value, str) and bool(value.strip())) or type(value) is int


def check_pair(record, *, as_of=None, max_age_ms=5000, max_skew_ms=1000):
    """Return structured checks; acceptance says nothing about profitability.

    Omit as_of to evaluate against the current UTC clock. Pass an aware
    datetime only for an explicitly historical replay.
    """
    if type(max_age_ms) is not int or max_age_ms <= 0:
        raise ValueError("max_age_ms must be a positive integer")
    if type(max_skew_ms) is not int or max_skew_ms < 0:
        raise ValueError("max_skew_ms must be a nonnegative integer")
    now = as_of if as_of is not None else datetime.now(timezone.utc)
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("as_of must be a timezone-aware datetime")
    now = now.astimezone(timezone.utc)
    result = {
        "accepted": False,
        "pair": None,
        "research_cycle": None,
        "evaluated_at": now.isoformat(),
        "mode": "replay" if as_of is not None else "live",
        "reasons": [],
        "legs": [],
        "skew_ms": None,
    }
    reasons = result["reasons"]
    if not isinstance(record, dict):
        reasons.append("record:invalid_object")
        return result
    pair = record.get("pair")
    cycle = record.get("research_cycle")
    if not isinstance(pair, str) or not pair.strip():
        reasons.append("pair:missing_or_invalid")
    else:
        result["pair"] = pair
    if not valid_cycle(cycle):
        reasons.append("research_cycle:missing_or_invalid")
    else:
        result["research_cycle"] = cycle
    legs = record.get("legs")
    if not isinstance(legs, list) or len(legs) != 2:
        reasons.append("legs:expected_exactly_two")
        return result

    names = set()
    timestamps = []
    for index, leg in enumerate(legs):
        prefix = f"legs[{index}]"
        if not isinstance(leg, dict):
            reasons.append(f"{prefix}:invalid_object")
            continue
        name = leg.get("name")
        if not isinstance(name, str) or not name.strip():
            reasons.append(f"{prefix}:missing_name")
        elif name in names:
            reasons.append(f"{prefix}:duplicate_name")
        else:
            names.add(name)
        # A recently cached timestamp does not prove a successful current fetch.
        if leg.get("fetch_ok") is not True:
            reasons.append(f"{prefix}:fetch_not_successful")
        leg_cycle = leg.get("research_cycle")
        if not valid_cycle(leg_cycle):
            reasons.append(f"{prefix}:missing_or_invalid_cycle")
        elif type(leg_cycle) is not type(cycle) or leg_cycle != cycle:
            reasons.append(f"{prefix}:cycle_mismatch")
        try:
            timestamp = parse_timestamp(leg.get("timestamp"))
        except (ValueError, OverflowError):
            reasons.append(f"{prefix}:invalid_timestamp")
            continue
        age_ms = (now - timestamp).total_seconds() * 1000
        timestamps.append(timestamp)
        result["legs"].append({"index": index, "name": name, "age_ms": age_ms})
        if age_ms < 0:
            reasons.append(f"{prefix}:future_timestamp")
        elif age_ms >= max_age_ms:
            reasons.append(f"{prefix}:stale")
    if len(timestamps) == 2:
        skew_ms = abs((timestamps[0] - timestamps[1]).total_seconds() * 1000)
        result["skew_ms"] = skew_ms
        if skew_ms > max_skew_ms:
            reasons.append("legs:skew_exceeded")
    result["accepted"] = not reasons
    return result


def positive_int(value):
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def nonnegative_int(value):
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be zero or greater")
    return parsed


def reject_constant(value):
    raise ValueError(f"nonstandard JSON constant: {value}")


def unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="JSONL file, or - for standard input")
    parser.add_argument("--max-age-ms", type=positive_int, default=5000,
                        help="require 0 <= age < this many ms (default: 5000)")
    parser.add_argument("--max-skew-ms", type=nonnegative_int, default=1000,
                        help="maximum difference between leg timestamps (default: 1000)")
    parser.add_argument("--as-of", help="historical replay only: an ISO 8601 evaluation time")
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args(argv)
    as_of = None
    if args.as_of is not None:
        try:
            as_of = parse_timestamp(args.as_of)
        except (ValueError, OverflowError) as exc:
            parser.error(str(exc))
    mode = "replay" if as_of is not None else "live"
    total = accepted = 0
    stream = None
    try:
        stream = sys.stdin if args.input == "-" else Path(args.input).open(encoding="utf-8-sig")
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            total += 1
            try:
                record = json.loads(line, parse_constant=reject_constant,
                                    object_pairs_hook=unique_fields)
            except (ValueError, RecursionError):
                result = {"accepted": False, "mode": mode, "reasons": ["record:invalid_json"]}
            else:
                result = check_pair(record, as_of=as_of, max_age_ms=args.max_age_ms,
                                    max_skew_ms=args.max_skew_ms)
            result["line"] = line_number
            accepted += int(result["accepted"])
            print(json.dumps(result, ensure_ascii=True, allow_nan=False))
    except (OSError, UnicodeError) as exc:
        print(json.dumps({"error": str(exc), "mode": mode, "incomplete": True}), file=sys.stderr)
        return 2
    finally:
        if stream is not None and stream is not sys.stdin:
            stream.close()
    summary = {"mode": mode, "total": total, "accepted": accepted, "rejected": total - accepted}
    if total == 0:
        summary["error"] = "no_records"
    print(json.dumps(summary), file=sys.stderr)
    return 0 if total > 0 and accepted == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
