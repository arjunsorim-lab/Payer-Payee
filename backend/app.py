import os
import re
from datetime import datetime
from pathlib import Path

from bson import ObjectId
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from werkzeug.exceptions import HTTPException

try:
    from .db import connect_mongo, get_mongo_config
    from .import_claims import read_claims
    from .llm_service import generate_provider_chat_answer, generate_provider_llm_analysis
    from .prediction_service import build_prediction_scenarios, summarize_scenarios
    from .provider_prediction import build_provider_prediction_payload, find_case
    from .workbook_enrichment import read_savings_workbook
except ImportError:
    from db import connect_mongo, get_mongo_config
    from import_claims import read_claims
    from llm_service import generate_provider_chat_answer, generate_provider_llm_analysis
    from prediction_service import build_prediction_scenarios, summarize_scenarios
    from provider_prediction import build_provider_prediction_payload, find_case
    from workbook_enrichment import read_savings_workbook

FRONTEND_DIST_DIR = Path(__file__).resolve().parent.parent / "dist"

app = Flask(__name__, static_folder=None)
CORS(app, origins=os.getenv("CORS_ORIGIN", "*").split(","))


def serialize(value):
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, list):
        return [serialize(item) for item in value]
    if isinstance(value, dict):
        return {key: serialize(item) for key, item in value.items()}
    return value


def json_response(payload, status=200):
    return jsonify(serialize(payload)), status


def escape_regex(value):
    return re.escape(str(value or "").strip())


def page_options(args, default_limit=25, max_limit=2000):
    page = max(int(args.get("page", 1) or 1), 1)
    limit = min(max(int(args.get("limit", default_limit) or default_limit), 1), max_limit)
    return page, limit, (page - 1) * limit


def build_claim_query(args):
    filters = {}
    search = str(args.get("search", "") or "").strip()
    if search:
        regex = {"$regex": escape_regex(search), "$options": "i"}
        filters["$or"] = [
            {"patient": regex},
            {"memberId": regex},
            {"number": regex},
            {"claimId": regex},
        ]

    if args.get("payer") and args.get("payer") != "All Payers":
        filters["payer"] = args.get("payer")
    if args.get("plan") and args.get("plan") != "All Plans":
        filters["filingIndicator"] = args.get("plan")
    if args.get("providerGroup") and args.get("providerGroup") != "All Groups":
        filters["billingProvider"] = args.get("providerGroup")
    if args.get("status"):
        filters["status"] = {"$regex": escape_regex(args.get("status")), "$options": "i"}

    if args.get("from") or args.get("to"):
        filters["dos"] = {}
        if args.get("from"):
            filters["dos"]["$gte"] = args.get("from")
        if args.get("to"):
            filters["dos"]["$lte"] = args.get("to")

    return filters


def financial_summary(rows):
    total_charges = round(sum(row.get("totalCharge", 0) for row in rows), 2)
    total_allowed = round(sum(row.get("allowed", 0) for row in rows), 2)
    total_paid = round(sum(row.get("paid", 0) for row in rows), 2)
    total_patient_resp = round(sum(row.get("patientResp", 0) for row in rows), 2)
    total_adjustment = round(sum(row.get("adjustment", 0) for row in rows), 2)
    denied_claims = sum(1 for row in rows if row.get("status") == "Denied")

    return {
        "totalClaims": len(rows),
        "totalCharges": total_charges,
        "totalAllowed": total_allowed,
        "totalPaid": total_paid,
        "totalPatientResp": total_patient_resp,
        "totalAdjustment": total_adjustment,
        "deniedClaims": denied_claims,
    }


def risk_level(score):
    if score >= 70:
        return "High"
    if score >= 35:
        return "Medium"
    return "Low"


def build_basic_prediction(claim):
    total_charge = float(claim.get("totalCharge") or 0)
    allowed = float(claim.get("allowed") or 0)
    paid = float(claim.get("paid") or 0)
    patient_resp = float(claim.get("patientResp") or 0)
    adjustment = float(claim.get("adjustment") or max(total_charge - allowed, 0))
    allowed_rate = round((allowed / total_charge) * 100, 1) if total_charge else 0
    paid_rate = round((paid / allowed) * 100, 1) if allowed else 0
    patient_rate = round((patient_resp / allowed) * 100, 1) if allowed else 0

    denial_score = 85 if claim.get("status") == "Denied" else 22
    adjustment_score = min(95, max(0, round((adjustment / total_charge) * 100))) if total_charge else 0
    collection_score = min(95, max(10, round(patient_rate)))
    cob_score = 68 if "Forwarded" in str(claim.get("status", "")) else 18
    repeat_score = 34 if claim.get("memberId") else 15
    provider_score = 35 if claim.get("billingProvider") else 20
    overall = min(100, round((denial_score * 0.28) + (adjustment_score * 0.18) + (collection_score * 0.18) + (cob_score * 0.14) + (repeat_score * 0.12) + (provider_score * 0.10)))

    procedure = " ".join(value for value in [claim.get("cptCode"), claim.get("cptDescription")] if value)
    service = " - ".join(value for value in [claim.get("placeOfServiceCode"), claim.get("placeOfService")] if value)
    likely = "Denied" if claim.get("status") == "Denied" else ("Processed as Secondary" if "Secondary" in str(claim.get("status", "")) else "Processed as Primary")

    reasons = [
        f"{claim.get('payer', 'Payer')} adjudication shows {allowed_rate}% allowed and {paid_rate}% paid-to-allowed for {procedure or 'this procedure'}.",
        f"{claim.get('billingProvider', 'Provider')} billed {claim.get('units') or 1} unit(s) at {service or 'the recorded place of service'}, creating a {round((adjustment / total_charge) * 100, 1) if total_charge else 0}% adjustment signal.",
        f"Member responsibility is {patient_rate}% of allowed, based on deductible/copay/coinsurance or non-covered balance recorded on the claim.",
    ]
    fixes = []
    if claim.get("status") == "Denied":
        fixes.append(f"Review denial reason: {claim.get('denialReason') or 'payer denial details not provided'}.")
    if not claim.get("priorAuth"):
        fixes.append("Confirm prior authorization requirements for this payer, CPT, and place of service.")
    if not claim.get("referral"):
        fixes.append("Confirm referral requirement and payer filing rules before resubmission.")
    if patient_rate > 30:
        fixes.append("Validate member benefit and cost-share calculation before billing the balance.")
    if not fixes:
        fixes.append("Monitor remittance and compare paid amount against contract expectation.")

    return {
        "peerCount": 0,
        "confidence": "Low",
        "money": {
            "predictedAllowed": round(allowed, 2),
            "predictedPaid": round(paid, 2),
            "predictedPatientResp": round(patient_resp, 2),
            "predictedAdjustment": round(adjustment, 2),
            "paidRange": {"low": round(max(paid * 0.9, 0), 2), "high": round(paid * 1.1, 2)},
            "allowedRate": allowed_rate,
            "paidToAllowedRate": paid_rate,
            "patientToAllowedRate": patient_rate,
            "adjustmentRate": round((adjustment / total_charge) * 100, 1) if total_charge else 0,
        },
        "risks": {
            "overall": {"level": risk_level(overall), "score": overall, "reason": reasons[0]},
            "denial": {"level": risk_level(denial_score), "score": denial_score, "reason": reasons[0]},
            "adjustment": {"level": risk_level(adjustment_score), "score": adjustment_score, "reason": reasons[1]},
            "collection": {"level": risk_level(collection_score), "score": collection_score, "reason": reasons[2]},
            "cob": {"level": risk_level(cob_score), "score": cob_score, "reason": "Status includes forwarded coordination signal." if cob_score >= 35 else "No forwarded coordination signal is present."},
            "repeat": {"level": risk_level(repeat_score), "score": repeat_score, "reason": "Member has historical claim context in the database."},
            "provider": {"level": risk_level(provider_score), "score": provider_score, "reason": "Provider risk is estimated from current claim metadata."},
        },
        "outcome": {
            "likely": likely,
            "explanation": f"{likely} is inferred from payer status, allowed rate, paid rate, and member responsibility.",
        },
        "riskDrivers": [
            {"label": "Denial", "score": denial_score, "reason": reasons[0]},
            {"label": "Adjustment", "score": adjustment_score, "reason": reasons[1]},
            {"label": "Collection", "score": collection_score, "reason": reasons[2]},
        ],
        "reasons": reasons,
        "fixes": fixes,
    }


def stored_or_basic_prediction(prediction_doc, claim):
    if prediction_doc and prediction_doc.get("prediction"):
        return prediction_doc["prediction"]
    return build_basic_prediction(claim)


@app.get("/health")
def health():
    db = connect_mongo()
    db.command("ping")
    return json_response({"ok": True, "mongo": get_mongo_config()})


@app.get("/api/claims")
def get_claims():
    db = connect_mongo()
    query = build_claim_query(request.args)
    page, limit, skip = page_options(request.args)
    collection = db.claims
    items = list(collection.find(query).sort([("dos", -1), ("claimId", 1)]).skip(skip).limit(limit))
    total = collection.count_documents(query)
    return json_response({"page": page, "limit": limit, "total": total, "items": items})


@app.get("/api/claims/<claim_number>")
def get_claim(claim_number):
    db = connect_mongo()
    claim = db.claims.find_one({"$or": [{"number": claim_number}, {"claimId": claim_number}]})
    if not claim:
        return json_response({"message": "Claim not found"}, 404)
    return json_response(claim)


@app.get("/api/members")
def get_members():
    db = connect_mongo()
    page, limit, skip = page_options(request.args)
    query = {}
    search = str(request.args.get("search", "") or "").strip()
    if search:
        regex = {"$regex": escape_regex(search), "$options": "i"}
        query["$or"] = [{"patient": regex}, {"memberId": regex}]

    items = list(db.members.find(query).sort([("latestServiceDate", -1), ("memberId", 1)]).skip(skip).limit(limit))
    total = db.members.count_documents(query)
    return json_response({"page": page, "limit": limit, "total": total, "items": items})


@app.get("/api/members/<member_id>")
def get_member(member_id):
    db = connect_mongo()
    member = db.members.find_one({"memberId": member_id})
    if not member:
        return json_response({"message": "Member not found"}, 404)
    return json_response(member)


@app.get("/api/members/<member_id>/claims")
def get_member_claims(member_id):
    db = connect_mongo()
    items = list(db.claims.find({"memberId": member_id}).sort([("dos", -1), ("claimId", 1)]))
    return json_response({"total": len(items), "items": items})


@app.get("/api/dashboard")
def get_dashboard():
    db = connect_mongo()
    query = build_claim_query(request.args)
    rows = list(db.claims.find(query).sort([("dos", -1), ("claimId", 1)]))
    return json_response({
        "summary": financial_summary(rows),
        "recentClaims": rows[:10],
        "filters": {
            "payers": sorted(value for value in db.claims.distinct("payer") if value),
            "plans": sorted(value for value in db.claims.distinct("filingIndicator") if value),
            "providerGroups": sorted(value for value in db.claims.distinct("billingProvider") if value),
        },
    })


@app.get("/api/predictions/dashboard")
def get_prediction_dashboard():
    db = connect_mongo()
    query = build_claim_query(request.args)
    claims = list(db.claims.find(query).sort([("dos", -1), ("claimId", 1)]))
    predictions_by_claim = {
        doc["claimId"]: stored_or_basic_prediction(doc, {})
        for doc in db.claim_predictions.find({"claimId": {"$in": [claim["claimId"] for claim in claims]}})
    }
    predictions = [predictions_by_claim.get(claim["claimId"]) or build_basic_prediction(claim) for claim in claims]
    total_predicted_paid = round(sum(prediction["money"]["predictedPaid"] for prediction in predictions), 2)
    total_predicted_adjustment = round(sum(prediction["money"]["predictedAdjustment"] for prediction in predictions), 2)
    at_risk_count = sum(1 for prediction in predictions if prediction["risks"]["overall"]["level"] != "Low")
    high_risk_count = sum(1 for prediction in predictions if prediction["risks"]["overall"]["level"] == "High")
    average_risk = round(sum(prediction["risks"]["overall"]["score"] for prediction in predictions) / len(predictions)) if predictions else 0

    return json_response({
        "summary": {
            "totalPredictedPaid": total_predicted_paid,
            "totalPredictedAdjustment": total_predicted_adjustment,
            "atRiskCount": at_risk_count,
            "highRiskCount": high_risk_count,
            "denialQueueCount": high_risk_count,
            "averageOverallRisk": average_risk,
        },
        "riskQueue": sorted(
            [{"claim": claim, "prediction": predictions[index]} for index, claim in enumerate(claims)],
            key=lambda item: item["prediction"]["risks"]["overall"]["score"],
            reverse=True,
        )[: int(request.args.get("limit", 10) or 10)],
    })


@app.get("/api/predictions/scenarios")
def get_prediction_scenarios():
    """Build provider-facing episode scenarios from the current database rows."""
    db = connect_mongo()
    query = build_claim_query(request.args)
    claims = list(db.claims.find(query).sort([("dos", 1), ("claimId", 1)]))
    scenarios = build_prediction_scenarios(claims)
    return json_response({
        "summary": summarize_scenarios(scenarios),
        "totalClaims": len(claims),
        "items": scenarios,
        "model": {
            "name": "Explainable episode forecast v1",
            "backend": "Python",
            "source": get_mongo_config()["dataSource"],
        },
    })


def build_provider_case(db, claim_number):
    """Build one exact provider case from the current database records."""
    workbook_path = Path(os.getenv("SAVINGS_WORKBOOK_PATH", "")).expanduser()
    csv_path = Path(os.getenv("CSV_PATH", "")).expanduser()
    if workbook_path.is_file():
        all_claims, workbook_report = read_savings_workbook(workbook_path)
        selected_claim = next((claim for claim in all_claims if claim_number in {claim.get("number"), claim.get("claimId")}), None)
    elif csv_path.is_file():
        all_claims = read_claims(csv_path)
        workbook_report = None
        selected_claim = next((claim for claim in all_claims if claim_number in {claim.get("number"), claim.get("claimId")}), None)
    else:
        workbook_report = None
        selected_claim = db.claims.find_one({"$or": [{"number": claim_number}, {"claimId": claim_number}]})
        all_claims = list(db.claims.find({}).sort([("dos", 1), ("claimId", 1)]))
    if not selected_claim:
        return None, "Claim not found"
    scenario, report = find_case(
        all_claims,
        claim_number,
        window_days=int(os.getenv("PROVIDER_EPISODE_WINDOW_DAYS", "90")),
        min_peers=int(os.getenv("PROVIDER_MIN_PEERS", "5")),
    )
    if not scenario:
        return None, "Unable to build provider case prediction"
    anchor = scenario["anchor"]
    scenario.update({
        "id": scenario["episodeId"],
        "patient": selected_claim.get("patient") or "Unknown patient",
        "memberId": selected_claim.get("memberId"),
        "payer": selected_claim.get("payer") or "Unknown payer",
        "provider": selected_claim.get("billingProvider") or "Unknown provider",
        "diagnosisCode": selected_claim.get("diagnosisCode"),
        "condition": selected_claim.get("diagnosisDescription") or selected_claim.get("diagnosisCode") or "Unspecified condition",
        "category": "provider",
        "pathway": {"label": "Provider episode"},
        "totalVisitCount": scenario["features"]["claimCount"],
        "risk": scenario["repeatRisk"],
        "likelyOutcome": f"{scenario['repeatRisk']['level']} repeat-service risk; {scenario['denialRisk']['level']} denial risk",
        "riskReasons": scenario["riskDrivers"],
        "savingsActions": scenario["recommendedActions"],
        "anchor": anchor,
        "selectedClaim": selected_claim,
        "validation": report["validation"],
        "quality": report["quality"],
        "workbookValidation": workbook_report,
    })
    return scenario, None


@app.get("/api/predictions/provider-case/<claim_number>")
def get_provider_case_prediction(claim_number):
    """Return the provider-focused episode prediction for one exact claim."""
    db = connect_mongo()
    scenario, error = build_provider_case(db, claim_number)
    if error:
        return json_response({"message": error}, 404 if error == "Claim not found" else 422)
    structured = build_provider_prediction_payload(scenario)
    return json_response({
        **structured,
        "episode_id": scenario["episodeId"],
        "member_reference": scenario["memberReference"],
        "llm_analysis": None,
        "metadata": {
            "modelVersion": scenario["method"],
            "peerCount": scenario["peerCount"],
            "confidence": scenario["confidence"],
            "priorityScore": scenario["priorityScore"],
            "workbookValidation": scenario.get("workbookValidation"),
        },
        "item": {**scenario, "selectedClaim": scenario.get("anchor")},
        "model": {
            "name": "Explainable provider case forecast v1",
            "backend": "Python",
            "source": get_mongo_config()["dataSource"],
        },
    })


@app.post("/api/predictions/provider-case/<claim_number>/llm")
def get_provider_case_llm_analysis(claim_number):
    """Generate a de-identified, provider-side LLM analysis for one claim episode."""
    db = connect_mongo()
    scenario, error = build_provider_case(db, claim_number)
    if error:
        return json_response({"message": error}, 404 if error == "Claim not found" else 422)
    try:
        result = generate_provider_llm_analysis(scenario)
        structured = build_provider_prediction_payload(scenario)
        return json_response({
            **structured,
            **result,
            "episode_id": scenario["episodeId"],
            "member_reference": scenario["memberReference"],
            "llm_analysis": result.get("analysis"),
            "metadata": {
                "modelVersion": scenario["method"],
                "promptVersion": result.get("promptVersion"),
                "confidence": scenario["confidence"],
                "peerCount": scenario["peerCount"],
                "priorityScore": scenario["priorityScore"],
                "workbookValidation": scenario.get("workbookValidation"),
            },
        })
    except RuntimeError as exc:
        return json_response({"configured": True, "message": str(exc)}, 502)


@app.post("/api/provider-llm/chat")
def provider_llm_chat():
    """Answer a claim-scoped follow-up without accepting raw claim data from the browser."""
    body = request.get_json(silent=True) or {}
    claim_id = str(body.get("claim_id") or "").strip()
    episode_id = str(body.get("episode_id") or "").strip()
    message = str(body.get("message") or "").strip()
    conversation_id = str(body.get("conversation_id") or "").strip()
    if not claim_id or not episode_id or not message or not conversation_id:
        return json_response({"message": "claim_id, episode_id, message and conversation_id are required"}, 400)
    if len(message) > 1000 or len(conversation_id) > 120:
        return json_response({"message": "Chat input exceeds the allowed length"}, 400)
    db = connect_mongo()
    scenario, error = build_provider_case(db, claim_id)
    if error:
        return json_response({"message": error}, 404 if error == "Claim not found" else 422)
    if scenario.get("episodeId") != episode_id:
        return json_response({"message": "The episode does not match the selected claim"}, 409)
    return json_response(generate_provider_chat_answer(scenario, message, conversation_id))


@app.get("/api/predictions/risk-queue")
def get_prediction_risk_queue():
    db = connect_mongo()
    query = build_claim_query(request.args)
    page, limit, skip = page_options(request.args, default_limit=25, max_limit=200)
    claims = list(db.claims.find(query).sort([("dos", -1), ("claimId", 1)]))
    prediction_docs = {
        doc["claimId"]: doc
        for doc in db.claim_predictions.find({"claimId": {"$in": [claim["claimId"] for claim in claims]}})
    }
    items = [
        {"claim": claim, "prediction": stored_or_basic_prediction(prediction_docs.get(claim["claimId"]), claim)}
        for claim in claims
    ]
    items.sort(key=lambda item: item["prediction"]["risks"]["overall"]["score"], reverse=True)
    return json_response({"page": page, "limit": limit, "total": len(items), "items": items[skip: skip + limit]})


@app.get("/api/predictions/claims/<claim_number>")
def get_claim_prediction(claim_number):
    db = connect_mongo()
    claim = db.claims.find_one({"$or": [{"number": claim_number}, {"claimId": claim_number}]})
    if not claim:
        return json_response({"message": "Claim not found"}, 404)

    prediction_doc = db.claim_predictions.find_one({"claimId": claim["claimId"]})
    return json_response({
        "claim": claim,
        "prediction": stored_or_basic_prediction(prediction_doc, claim),
    })


@app.get("/")
def serve_frontend_index():
    index_file = FRONTEND_DIST_DIR / "index.html"
    if not index_file.is_file():
        return json_response({"message": "Frontend build not found. Run npm run build before starting the server."}, 404)
    return send_from_directory(FRONTEND_DIST_DIR, "index.html")


@app.get("/<path:asset_path>")
def serve_frontend_asset(asset_path):
    if asset_path.startswith("api/"):
        return json_response({"message": "Not found"}, 404)

    requested_file = FRONTEND_DIST_DIR / asset_path
    if requested_file.is_file():
        return send_from_directory(FRONTEND_DIST_DIR, asset_path)

    index_file = FRONTEND_DIST_DIR / "index.html"
    if index_file.is_file():
        return send_from_directory(FRONTEND_DIST_DIR, "index.html")

    return json_response({"message": "Frontend build not found. Run npm run build before starting the server."}, 404)


@app.errorhandler(Exception)
def handle_error(error):
    if isinstance(error, HTTPException):
        return json_response({"message": error.description}, error.code)
    app.logger.exception(error)
    return json_response({"message": "Internal server error"}, 500)


if __name__ == "__main__":
    port = int(os.getenv("PORT", "4000"))
    app.run(host="0.0.0.0", port=port)
