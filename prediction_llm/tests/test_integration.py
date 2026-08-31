import os

from pymongo import MongoClient

from analytics import ClaimsAnalytics


def engine():
    client = MongoClient(os.environ.get("MONGODB_URI", "mongodb://127.0.0.1:27018/"), serverSelectionTimeoutMS=3000)
    client.admin.command("ping")
    return ClaimsAnalytics(client["payer_payee"])


def test_real_database_member_summary_has_evidence_and_limits():
    service = engine()
    member = service.list_members()[0]["Member_ID"]
    result = service.member_summary(member)
    assert result["facts"]["claim_count"] > 0
    assert result["evidence_records"]
    assert result["insufficient_evidence"]
    assert "clinical advice" in result["guardrail"].lower()


def test_real_database_claim_analysis_returns_grounded_matches():
    service = engine()
    member = service.list_members()[0]["Member_ID"]
    claim_id = service.list_member_claims(member)[0]["Claim_ID"]
    result = service.claim_analysis(claim_id)
    assert result["claim_id"] == claim_id
    assert result["facts"]["target_claim"]["Claim_ID"] == claim_id
    assert all("record" in match and "match_reasons" in match for match in result["similar_historical_claims"])
