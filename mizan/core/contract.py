"""Shared wire format and small, explicit error vocabulary."""
import hashlib
import json
import math
from datetime import datetime, timezone


class Refused(ValueError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code


def require(ok, code, detail):
    if not ok:
        raise Refused(code, detail)


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")


def digest(value):
    return hashlib.sha256(value if isinstance(value, bytes) else canonical(value)).hexdigest()


def strict_load(data):
    def unique(pairs):
        obj = {}
        for name, value in pairs:
            require(name not in obj, "DUPLICATE_KEY", "Duplicate JSON object member")
            obj[name] = value
        return obj

    def invalid(_):
        raise Refused("NONFINITE", "JSON requires finite numbers")
    def finite_float(raw):
        v=float(raw)
        require(math.isfinite(v),"NONFINITE","JSON requires finite numbers")
        return v
    return json.loads(data, object_pairs_hook=unique, parse_constant=invalid, parse_float=finite_float)


def number(x, label, low=-1e9, high=1e9):
    require(type(x) in (int, float) and math.isfinite(x) and low <= x <= high,
            "INPUT_RANGE", f"Out of range: {label}")
    return float(x)


def instant(value):
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(dt.tzinfo is not None, "TIMEZONE", "An offset-aware timestamp is required")
    return dt.astimezone(timezone.utc)
