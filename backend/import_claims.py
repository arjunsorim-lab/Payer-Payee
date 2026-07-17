"""Import the 837 CSV into MongoDB.

Usage: python3 -m backend.import_claims /path/to/claims.csv
"""

import csv
import json
import os
import sys
from pathlib import Path

from pymongo import ReplaceOne

from .claim_mapper import build_member_documents, normalize_claim
from .db import close_mongo, connect_mongo, get_mongo_config


def read_claims(path):
    with path.open(newline="", encoding="utf-8-sig") as csv_file:
        rows = csv.DictReader(csv_file)
        return [claim for claim in (normalize_claim(row) for row in rows) if claim.get("claimId") and claim.get("memberId")]


def upsert(collection, documents, key):
    if not documents:
        return {"matched": 0, "upserted": 0}
    result = collection.bulk_write(
        [ReplaceOne({key: document[key]}, document, upsert=True) for document in documents],
        ordered=False,
    )
    return {"matched": result.matched_count, "upserted": result.upserted_count}


def main():
    source = sys.argv[1] if len(sys.argv) > 1 else os.getenv("CSV_PATH")
    if not source:
        raise SystemExit("Provide a CSV path: python3 -m backend.import_claims /path/to/claims.csv")
    csv_path = Path(source).expanduser().resolve()
    claims = read_claims(csv_path)
    members = build_member_documents(claims)
    db = connect_mongo()
    if getattr(db, "is_fallback", False):
        raise SystemExit("MongoDB is unavailable. Set MONGODB_URI before importing the CSV.")

    db.claims.create_index("claimId", unique=True)
    db.claims.create_index([("memberId", 1), ("diagnosisCode", 1), ("dos", -1)])
    db.members.create_index("memberId", unique=True)
    results = {
        "database": get_mongo_config()["dbName"],
        "csvPath": str(csv_path),
        "claims": len(claims),
        "members": len(members),
        "claimWrite": upsert(db.claims, claims, "claimId"),
        "memberWrite": upsert(db.members, members, "memberId"),
    }
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    try:
        main()
    finally:
        close_mongo()
