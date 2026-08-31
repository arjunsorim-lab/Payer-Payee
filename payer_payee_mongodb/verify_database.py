#!/usr/bin/env python3
"""Verify counts, indexes, exact spreadsheet fields, and logical references."""

from __future__ import annotations

import json
import os

from pymongo import MongoClient


URI = os.environ.get("MONGODB_URI", "mongodb://127.0.0.1:27018/")
DATABASE = os.environ.get("MONGODB_DATABASE", "payer_payee")
CLAIMS_COLLECTION = os.environ.get("MONGODB_COLLECTION", "837_claims")


def main() -> None:
    client = MongoClient(URI, serverSelectionTimeoutMS=5000)
    client.admin.command("ping")
    db = client[DATABASE]
    claims = db[CLAIMS_COLLECTION]

    missing_patients = list(
        claims.aggregate(
            [
                {"$lookup": {"from": "patients", "localField": "Patient_ID", "foreignField": "_id", "as": "match"}},
                {"$match": {"match": {"$size": 0}}},
                {"$count": "count"},
            ]
        )
    )
    missing_claim_registry = list(
        claims.aggregate(
            [
                {"$lookup": {"from": "claim_registry", "localField": "Claim_ID", "foreignField": "_id", "as": "match"}},
                {"$match": {"match": {"$size": 0}}},
                {"$count": "count"},
            ]
        )
    )
    sample = claims.find_one({}, {"_id": 0}) or {}

    result = {
        "ping": "ok",
        "database": DATABASE,
        "collections": sorted(db.list_collection_names()),
        "claim_rows": claims.count_documents({}),
        "patients": db["patients"].count_documents({}),
        "claim_registry": db["claim_registry"].count_documents({}),
        "claim_document_fields": len(sample),
        "missing_patient_references": missing_patients[0]["count"] if missing_patients else 0,
        "missing_claim_registry_references": missing_claim_registry[0]["count"] if missing_claim_registry else 0,
        "indexes": sorted(claims.index_information()),
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
