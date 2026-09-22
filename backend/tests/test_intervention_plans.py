from copy import deepcopy

from backend.intervention_plans import build_intervention_plan, build_member_intervention_plans


def test_scenarios_have_distinct_interventions_and_timing():
    plans = []
    for index, code in enumerate(("N39.0", "E11.9", "I10", "N92.0")):
        anchor = {"claimId": f"ANCHOR-{index}", "diagnosisCode": code, "dos": "2026-01-01",
                  "workbookFields": {"Treatment_History": "Follow-up care documented"}}
        followup = {"claimId": f"FOLLOW-{index}", "diagnosisCode": code, "dos": "2025-12-20",
                    "cptDescription": "Follow-up treatment visit"}
        from backend.intervention_plans import clinical_inputs
        plans.append(build_intervention_plan(anchor, clinical_inputs([anchor, followup], anchor)))
    assert all(plan["available"] for plan in plans)
    assert len({plan["action"] for plan in plans}) == 4
    assert len({plan["follow_up_days"] for plan in plans}) == 4
    assert plans[0]["test_recommendation_available"] is False
    assert "test is not recommended" in plans[0]["action"]
    assert all(not plan["recorded"] and not plan["included_in_savings"] for plan in plans)


def test_unrelated_antibiotics_and_urinary_conditions_do_not_imply_uti():
    for code in ("J18.9", "N39.3", "N30.1", "Z01.419", "", "R10.9"):
        plan = build_intervention_plan({"diagnosisCode": code, "cptDescription": "Antibiotic treatment"})
        assert plan.get("scenario") != "urinary"
        assert "urine culture" not in plan.get("action", "")
        if not code or code == "R10.9":
            assert not plan["available"]
            assert plan["follow_up_days"] is None


def test_plan_preserves_recorded_claim_and_supports_workbook_fields():
    claim = {"workbookFields": {"ICD10_Diagnosis_Code": "N30.0", "Service_Date_From": "20260101", "CPT_Code": "99395"}}
    before = deepcopy(claim)
    plan = build_intervention_plan(claim)
    assert plan["scenario"] == "urinary"
    assert not plan["available"]
    assert "treatment_history" in plan["missing_evidence"]
    assert plan["requires_clinical_review"]
    assert claim == before


def test_acute_diagnoses_override_routine_profiles():
    for code in ("E11.641", "I25.110", "I26.99", "J96.01", "R07.9"):
        plan = build_intervention_plan({"diagnosisCode": code})
        assert plan["status"] == "clinical_review_required"
        assert plan["follow_up_days"] is None


def test_member_groups_conditions_and_keeps_latest_anchor():
    claims = [
        {"claimId": "OLD", "memberId": "M1", "dos": "2026-01-01", "diagnosisCode": "E11.9", "cptDescription": "Diabetes treatment follow-up"},
        {"claimId": "NEW", "memberId": "M1", "dos": "2026-02-01", "diagnosisCode": "E11.65", "cptDescription": "Diabetes treatment follow-up"},
        {"claimId": "UTI", "memberId": "M1", "dos": "2026-01-01", "diagnosisCode": "N39.0", "cptDescription": "UTI follow-up visit"},
        {"claimId": "UNKNOWN", "dos": "2026-01-01", "diagnosisCode": ""},
    ]
    plans = build_member_intervention_plans(claims)
    assert len(plans) == 3
    diabetes = next(plan for plan in plans if plan.get("scenario") == "diabetes")
    assert diabetes["anchor_claim_id"] == "NEW"
    assert diabetes["source_claim_ids"] == ["NEW", "OLD"]
    assert len(diabetes["diagnosis_codes"]) == 2
    assert sum(len(plan["source_claim_ids"]) for plan in plans) == len(claims)


def test_every_workbook_member_and_claim_is_reviewed():
    from backend.workbook_enrichment import load_workbook_database
    database = load_workbook_database()
    reviewed = []
    for member in database.members:
        claims = database.member_claims(member["memberId"])
        plans = build_member_intervention_plans(claims)
        assert plans
        ids = [claim_id for plan in plans for claim_id in plan["source_claim_ids"]]
        assert sorted(ids) == sorted(claim["claimId"] for claim in claims)
        reviewed.extend(ids)
        for plan in plans:
            assert not plan["recorded"] and not plan["included_in_savings"]
            if not plan["available"]:
                assert plan["follow_up_days"] is None
    assert sorted(reviewed) == sorted(claim["claimId"] for claim in database.selectable_claims)


def test_member_endpoint_avoids_financial_computation():
    from unittest.mock import patch
    from types import SimpleNamespace
    from backend.app import app
    database = SimpleNamespace(member_claims=lambda member_id: [
        {"claimId": "C1", "diagnosisCode": "E03.9"}
    ] if member_id == "M1" else [])
    with patch("backend.app.configured_workbook_database", return_value=database), patch(
        "backend.app.member_supported_summary", side_effect=AssertionError("Unexpected financial computation")
    ):
        client = app.test_client()
        response = client.get("/api/members/M1/intervention-plans")
        assert response.status_code == 200
        assert response.get_json()["plans"][0]["scenario"] == "thyroid"
        assert client.get("/api/members/UNKNOWN/intervention-plans").status_code == 404
