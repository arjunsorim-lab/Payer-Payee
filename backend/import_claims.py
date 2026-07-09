import csv
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from .claim_mapper import build_member_documents, normalize_claim
    from .db import close_mongo, connect_mongo, get_mongo_config
except ImportError:
    from claim_mapper import build_member_documents, normalize_claim
    from db import close_mongo, connect_mongo, get_mongo_config

DEFAULT_CSV_PATH = "/Users/user/Downloads/EDI_834_837_20 members(837_Claims).csv"


def chunk(items, size):
    for index in range(0, len(items), size):
        yield items[index:index + size]


def ensure_indexes(db):
    claims = db.claims
    members = db.members
    predictions = db.claim_predictions

    claims.create_index("claimId", unique=True)
    claims.create_index("number", unique=True)
    claims.create_index([("memberId", 1), ("dos", -1)])
    claims.create_index([("payer", 1), ("dos", -1)])
    claims.create_index([("billingProvider", 1), ("dos", -1)])

    members.create_index("memberId", unique=True)
    members.create_index([("patient", 1), ("memberId", 1)])

    predictions.create_index("claimId", unique=True)
    predictions.create_index("number", unique=True)
    predictions.create_index([("memberId", 1), ("dos", -1)])
    predictions.create_index("prediction.risks.overall.score")


def build_seed_prediction(claim):
    total_charge = claim.get("totalCharge", 0) or 0
    allowed = claim.get("allowed", 0) or 0
    paid = claim.get("paid", 0) or 0
    patient_resp = claim.get("patientResp", 0) or 0
    adjustment = claim.get("adjustment", 0) or max(total_charge - allowed, 0)
    allowed_rate = round((allowed / total_charge) * 100, 1) if total_charge else 0
    paid_rate = round((paid / allowed) * 100, 1) if allowed else 0
    patient_rate = round((patient_resp / allowed) * 100, 1) if allowed else 0
    status = claim.get("status", "")

    score = 25
    if status == "Denied":
      score += 45
    if "Forwarded" in status:
      score += 18
    if adjustment and total_charge:
      score += min(20, round((adjustment / total_charge) * 40))
    if patient_rate > 30:
      score += 10
    score = min(score, 100)
    level = "High" if score >= 70 else "Medium" if score >= 35 else "Low"
    likely = "Denied" if status == "Denied" else "Processed as Secondary" if "Secondary" in status else "Processed as Primary"

    procedure = " ".join(value for value in [claim.get("cptCode"), claim.get("cptDescription")] if value)
    service = " - ".join(value for value in [claim.get("placeOfServiceCode"), claim.get("placeOfService")] if value)
    reasons = [
        f"{claim.get('payer')} shows {allowed_rate}% allowed and {paid_rate}% paid-to-allowed for {procedure or 'this procedure'}.",
        f"{claim.get('billingProvider')} billed {claim.get('units') or 1} unit(s) at {service or 'the recorded place of service'} with {round((adjustment / total_charge) * 100, 1) if total_charge else 0}% adjustment.",
        f"Patient responsibility is {patient_rate}% of allowed for this member claim.",
    ]
    fixes = []
    if claim.get("status") == "Denied":
        fixes.append(f"Review denial reason: {claim.get('denialReason') or 'payer denial detail not provided'}.")
    if not claim.get("priorAuth"):
        fixes.append("Confirm prior authorization requirements for this CPT, payer, and place of service.")
    if not claim.get("referral"):
        fixes.append("Confirm referral requirement and payer filing rules.")
    if not fixes:
        fixes.append("Monitor remittance against the expected paid amount.")

    return {
        "peerCount": 0,
        "confidence": "Low",
        "money": {
            "predictedAllowed": allowed,
            "predictedPaid": paid,
            "predictedPatientResp": patient_resp,
            "predictedAdjustment": adjustment,
            "paidRange": {"low": round(max(paid * 0.9, 0), 2), "high": round(paid * 1.1, 2)},
            "allowedRate": allowed_rate,
            "paidToAllowedRate": paid_rate,
            "patientToAllowedRate": patient_rate,
            "adjustmentRate": round((adjustment / total_charge) * 100, 1) if total_charge else 0,
        },
        "risks": {
            "overall": {"level": level, "score": score, "reason": reasons[0]},
            "denial": {"level": level if status == "Denied" else "Low", "score": 85 if status == "Denied" else 20, "reason": reasons[0]},
            "adjustment": {"level": "Medium" if adjustment else "Low", "score": min(95, round((adjustment / total_charge) * 100)) if total_charge else 0, "reason": reasons[1]},
            "collection": {"level": "Medium" if patient_rate > 30 else "Low", "score": min(95, round(patient_rate)), "reason": reasons[2]},
            "cob": {"level": "Medium" if "Forwarded" in status else "Low", "score": 60 if "Forwarded" in status else 15, "reason": "Forwarded status is present." if "Forwarded" in status else "No forwarded status is present."},
            "repeat": {"level": "Low", "score": 20, "reason": "Repeat risk is estimated from member claim history."},
            "provider": {"level": "Low", "score": 25, "reason": "Provider risk is estimated from current claim metadata."},
        },
        "outcome": {"likely": likely, "explanation": f"{likely} is inferred from status, allowed rate, and paid-to-allowed rate."},
        "riskDrivers": [{"label": "Overall", "score": score, "reason": reason} for reason in reasons],
        "reasons": reasons,
        "fixes": fixes,
    }


def bulk_upsert(collection, documents, key_field):
    modified = 0
    upserted = 0
    for batch in chunk(documents, 500):
        if not batch:
            continue
        result = collection.bulk_write([
            pymongo_replace(document, key_field)
            for document in batch
        ], ordered=False)
        modified += result.modified_count
        upserted += result.upserted_count
    return {"modified": modified, "upserted": upserted}


def pymongo_replace(document, key_field):
    from pymongo import ReplaceOne

    return ReplaceOne({key_field: document[key_field]}, document, upsert=True)


def read_csv_rows(csv_path):
    with open(csv_path, newline="", encoding="utf-8-sig") as csv_file:
        return list(csv.DictReader(csv_file))


def main():
    csv_path = Path(sys.argv[1] if len(sys.argv) > 1 else os.getenv("CSV_PATH", DEFAULT_CSV_PATH)).expanduser().resolve()
    rows = read_csv_rows(csv_path)
    claims = sorted(
        [normalize_claim(row) for row in rows],
        key=lambda claim: (claim.get("dos", ""), claim.get("claimId", "")),
        reverse=True,
    )
    claims = [claim for claim in claims if claim.get("claimId") and claim.get("memberId")]
    members = build_member_documents(claims)
    prediction_docs = [{
        "claimId": claim["claimId"],
        "number": claim["number"],
        "memberId": claim["memberId"],
        "patient": claim["patient"],
        "payer": claim["payer"],
        "billingProvider": claim["billingProvider"],
        "dos": claim["dos"],
        "prediction": build_seed_prediction(claim),
        "predictedAt": datetime.now(timezone.utc),
    } for claim in claims]

    db = connect_mongo()
    ensure_indexes(db)
    claim_result = bulk_upsert(db.claims, claims, "claimId")
    member_result = bulk_upsert(db.members, members, "memberId")
    prediction_result = bulk_upsert(db.claim_predictions, prediction_docs, "claimId")

    if os.getenv("SYNC_DELETE") == "true":
        db.claims.delete_many({"claimId": {"$nin": [claim["claimId"] for claim in claims]}})
        db.members.delete_many({"memberId": {"$nin": [member["memberId"] for member in members]}})
        db.claim_predictions.delete_many({"claimId": {"$nin": [claim["claimId"] for claim in claims]}})

    print(json.dumps({
        "database": get_mongo_config()["dbName"],
        "csvPath": str(csv_path),
        "parsedRows": len(rows),
        "importedClaims": len(claims),
        "importedMembers": len(members),
        "claimResult": claim_result,
        "memberResult": member_result,
        "predictionResult": prediction_result,
    }, indent=2))


if __name__ == "__main__":
    try:
        main()
    finally:
        close_mongo()
