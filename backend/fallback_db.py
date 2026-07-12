import json
import re
from copy import deepcopy
from pathlib import Path

from .claim_mapper import build_member_documents


FALLBACK_CLAIMS_PATH = (
    Path(__file__).resolve().parent.parent
    / "frontend"
    / "public"
    / "data"
    / "claims-fallback.json"
)


def _field_value(document, field):
    value = document
    for part in field.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _matches_value(value, expected):
    if isinstance(expected, dict):
        if "$regex" in expected:
            return re.search(expected["$regex"], str(value or ""), re.IGNORECASE if "i" in expected.get("$options", "") else 0) is not None
        if "$in" in expected and value not in expected["$in"]:
            return False
        if "$gte" in expected and (value is None or value < expected["$gte"]):
            return False
        if "$lte" in expected and (value is None or value > expected["$lte"]):
            return False
        return True
    return value == expected


def _matches(document, query):
    if not query:
        return True
    for field, expected in query.items():
        if field == "$or":
            if not any(_matches(document, condition) for condition in expected):
                return False
            continue
        if not _matches_value(_field_value(document, field), expected):
            return False
    return True


class FallbackCursor:
    def __init__(self, rows):
        self.rows = list(rows)

    def sort(self, spec):
        for field, direction in reversed(spec):
            self.rows.sort(key=lambda row: (_field_value(row, field) is not None, _field_value(row, field)), reverse=direction < 0)
        return self

    def skip(self, count):
        self.rows = self.rows[count:]
        return self

    def limit(self, count):
        self.rows = self.rows[:count]
        return self

    def __iter__(self):
        return iter(deepcopy(self.rows))


class FallbackCollection:
    def __init__(self, rows):
        self.rows = rows

    def find(self, query=None):
        return FallbackCursor(row for row in self.rows if _matches(row, query or {}))

    def find_one(self, query=None):
        return next((deepcopy(row) for row in self.rows if _matches(row, query or {})), None)

    def count_documents(self, query=None):
        return sum(1 for row in self.rows if _matches(row, query or {}))

    def distinct(self, field):
        return list({_field_value(row, field) for row in self.rows if _field_value(row, field) is not None})


class FallbackDatabase:
    is_fallback = True

    def __init__(self, claims):
        self.claims = FallbackCollection(claims)
        self.members = FallbackCollection(build_member_documents(claims))
        self.claim_predictions = FallbackCollection([])

    def command(self, command):
        if command != "ping":
            raise ValueError(f"Unsupported fallback database command: {command}")
        return {"ok": 1, "source": "bundled-snapshot"}


def load_fallback_database():
    with FALLBACK_CLAIMS_PATH.open(encoding="utf-8") as fallback_file:
        claims = json.load(fallback_file)
    return FallbackDatabase(claims)
