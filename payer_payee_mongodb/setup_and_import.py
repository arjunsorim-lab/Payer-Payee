#!/usr/bin/env python3
"""Create the isolated payer_payee schema and import extracted 837 claim JSON."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from pymongo import ASCENDING, MongoClient


URI = os.environ.get("MONGODB_URI", "mongodb://127.0.0.1:27018/")
DATABASE = os.environ.get("MONGODB_DATABASE", "payer_payee")
CLAIMS_COLLECTION = os.environ.get("MONGODB_COLLECTION", "837_claims")


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: setup_and_import.py /path/to/claims.json")

    source_path = Path(sys.argv[1]).resolve()
    payload = json.loads(source_path.read_text(encoding="utf-8"))
    headers = payload["headers"]
    claims = payload["documents"]

    if len(headers) != len(set(headers)):
        raise RuntimeError("Spreadsheet headings are not unique")
    if not claims:
        raise RuntimeError("No claim rows were extracted")

    client = MongoClient(URI, serverSelectionTimeoutMS=5000)
    client.admin.command("ping")
    db = client[DATABASE]

    existing = db.list_collection_names()
    if existing:
        raise RuntimeError(
            f"Safety stop: database {DATABASE!r} already has collections {existing}; nothing was overwritten"
        )

    required_claim_fields = headers + ["Patient_ID", "_source_row"]
    db.create_collection(
        CLAIMS_COLLECTION,
        validator={
            "$jsonSchema": {
                "bsonType": "object",
                "required": required_claim_fields,
                "properties": {
                    "Patient_ID": {"bsonType": "string", "minLength": 1},
                    "Claim_ID": {"bsonType": "string", "minLength": 1},
                    "_source_row": {"bsonType": "number"},
                },
            }
        },
        validationLevel="strict",
        validationAction="error",
    )
    db.create_collection(
        "patients",
        validator={
            "$jsonSchema": {
                "bsonType": "object",
                "required": ["_id", "Patient_Account_Number", "Member_ID"],
                "properties": {"_id": {"bsonType": "string", "minLength": 1}},
            }
        },
        validationLevel="strict",
        validationAction="error",
    )
    db.create_collection(
        "claim_registry",
        validator={
            "$jsonSchema": {
                "bsonType": "object",
                "required": ["_id", "Patient_ID"],
                "properties": {
                    "_id": {"bsonType": "string", "minLength": 1},
                    "Patient_ID": {"bsonType": "string", "minLength": 1},
                },
            }
        },
        validationLevel="strict",
        validationAction="error",
    )

    patient_fields = [
        "Patient_Account_Number",
        "Member_ID",
        "Patient_First_Name",
        "Patient_Last_Name",
        "Patient_DOB",
        "Patient_Gender",
    ]
    patients_by_id = {}
    claim_registry = []
    for claim in claims:
        patient_id = claim["Patient_ID"]
        patients_by_id.setdefault(
            patient_id,
            {"_id": patient_id, **{field: claim.get(field) for field in patient_fields}},
        )
        claim_registry.append({"_id": claim["Claim_ID"], "Patient_ID": patient_id})

    db["patients"].insert_many(list(patients_by_id.values()), ordered=True)
    db["claim_registry"].insert_many(claim_registry, ordered=True)
    db[CLAIMS_COLLECTION].insert_many(claims, ordered=True)

    db[CLAIMS_COLLECTION].create_index([("Claim_ID", ASCENDING)], unique=True, name="uq_claim_id")
    db[CLAIMS_COLLECTION].create_index([("Patient_ID", ASCENDING)], name="ix_patient_id")
    db[CLAIMS_COLLECTION].create_index(
        [("Patient_ID", ASCENDING), ("Claim_ID", ASCENDING)],
        unique=True,
        name="uq_patient_claim",
    )
    db[CLAIMS_COLLECTION].create_index([("Payer_ID", ASCENDING)], name="ix_payer_id")
    db[CLAIMS_COLLECTION].create_index([("Service_Date_From", ASCENDING)], name="ix_service_date_from")
    db["claim_registry"].create_index([("Patient_ID", ASCENDING)], name="ix_registry_patient")

    print(
        json.dumps(
            {
                "database": DATABASE,
                "collection": CLAIMS_COLLECTION,
                "claim_rows": db[CLAIMS_COLLECTION].count_documents({}),
                "patients": db["patients"].count_documents({}),
                "claim_registry": db["claim_registry"].count_documents({}),
                "spreadsheet_columns": len(headers),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
