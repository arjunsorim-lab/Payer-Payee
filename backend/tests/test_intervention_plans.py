from copy import deepcopy

from backend.intervention_plans import build_intervention_plan


def test_scenarios_have_distinct_interventions_and_timing():
    plans = [build_intervention_plan({"diagnosisCode": code}) for code in ("N39.0", "E11.9", "I10", "N92.0")]
    assert all(plan["available"] for plan in plans)
    assert len({plan["action"] for plan in plans}) == 4
    assert len({plan["follow_up_days"] for plan in plans}) == 4
    assert "urine culture" in plans[0]["action"]
    assert all(not plan["recorded"] and not plan["included_in_savings"] for plan in plans)


def test_unrelated_antibiotics_and_urinary_conditions_do_not_imply_uti():
    for code in ("J18.9", "N39.3", "Z01.419", "", "R10.9"):
        plan = build_intervention_plan({"diagnosisCode": code, "cptDescription": "Antibiotic treatment"})
        assert not plan["available"]
        assert plan["follow_up_days"] is None


def test_plan_preserves_recorded_claim_and_supports_workbook_fields():
    claim = {"workbookFields": {"ICD10_Diagnosis_Code": "N30.0", "Service_Date_From": "20260101", "CPT_Code": "99395"}}
    before = deepcopy(claim)
    plan = build_intervention_plan(claim)
    assert plan["scenario"] == "urinary"
    assert plan["requires_clinical_review"]
    assert claim == before
