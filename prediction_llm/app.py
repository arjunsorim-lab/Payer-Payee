"""Local web service for evidence-grounded 837 claims analytics."""

from __future__ import annotations

import os
from typing import Any

from flask import Flask, jsonify, request, send_from_directory
from pymongo import MongoClient
from pymongo.errors import PyMongoError
from werkzeug.exceptions import HTTPException

from analytics import ClaimsAnalytics
from llm import GroundedNarrator


MONGODB_URI = os.environ.get("MONGODB_URI", "mongodb://127.0.0.1:27018/")
MONGODB_DATABASE = os.environ.get("MONGODB_DATABASE", "payer_payee")
MONGODB_COLLECTION = os.environ.get("MONGODB_COLLECTION", "837_claims")

app = Flask(__name__, static_folder="static", static_url_path="/static")
mongo = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000)
engine = ClaimsAnalytics(mongo[MONGODB_DATABASE], MONGODB_COLLECTION)
narrator = GroundedNarrator()


def wants_llm() -> bool:
    value = request.args.get("use_llm", "false").lower()
    return value in {"1", "true", "yes"}


def with_narrative(analysis: dict[str, Any], question: str | None = None) -> dict[str, Any]:
    analysis["narrative"] = narrator.explain(analysis, question) if wants_llm() else narrator.fallback(analysis)
    return analysis


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/favicon.ico")
def favicon():
    return "", 204


@app.get("/health")
def health():
    try:
        mongo.admin.command("ping")
        database_status = "ok"
        claim_count = engine.claims.count_documents({})
    except PyMongoError as error:
        database_status = f"error: {error}"
        claim_count = None
    return jsonify(
        {
            "status": "ok" if database_status == "ok" else "degraded",
            "database": database_status,
            "database_name": MONGODB_DATABASE,
            "collection": MONGODB_COLLECTION,
            "claim_count": claim_count,
            "local_llm": narrator.status(),
            "guardrail": "Claims-data analysis only; no clinical advice or inferred clinical rationale.",
        }
    )


@app.get("/api/members")
def members():
    return jsonify({"members": engine.list_members()})


@app.get("/api/members/<member_id>/claims")
def member_claims(member_id: str):
    return jsonify({"member_id": member_id, "claims": engine.list_member_claims(member_id)})


@app.get("/api/members/<member_id>/summary")
def member_summary(member_id: str):
    return jsonify(with_narrative(engine.member_summary(member_id)))


@app.get("/api/claims/<claim_id>/analysis")
def claim_analysis(claim_id: str):
    return jsonify(with_narrative(engine.claim_analysis(claim_id)))


@app.post("/api/ask")
def ask():
    payload = request.get_json(silent=True) or {}
    member_id = str(payload.get("member_id") or "").strip()
    claim_id = str(payload.get("claim_id") or "").strip()
    question = str(payload.get("question") or "").strip()
    if not question:
        return jsonify({"error": "question is required"}), 400
    if claim_id:
        analysis = engine.claim_analysis(claim_id)
    elif member_id:
        analysis = engine.member_summary(member_id)
    else:
        return jsonify({"error": "member_id or claim_id is required"}), 400
    analysis["narrative"] = narrator.explain(analysis, question)
    return jsonify(analysis)


@app.errorhandler(LookupError)
def handle_not_found(error):
    return jsonify({"error": str(error)}), 404


@app.errorhandler(PyMongoError)
def handle_mongo_error(error):
    return jsonify({"error": "MongoDB operation failed", "detail": str(error)}), 503


@app.errorhandler(Exception)
def handle_unexpected(error):
    if isinstance(error, HTTPException):
        return jsonify({"error": error.description}), error.code
    app.logger.exception("Unhandled application error")
    return jsonify({"error": "Unexpected server error", "detail": str(error)}), 500


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "8765")), debug=False)
