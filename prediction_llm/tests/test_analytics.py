from datetime import date

from analytics import amount_similarity, parse_claim_date, similarity_score


def test_claim_date_parsing():
    assert parse_claim_date(20260529) == date(2026, 5, 29)
    assert parse_claim_date("2026-05-29") == date(2026, 5, 29)
    assert parse_claim_date("bad") is None


def test_amount_similarity_is_bounded_and_exact():
    assert amount_similarity(100, 100) == 1.0
    assert amount_similarity(0, 100) == 0.0
    assert 0 <= amount_similarity(90, 100) <= 1


def test_exact_code_provider_match_scores_higher_than_unrelated_claim():
    target = {
        "Member_ID": "M1",
        "CPT_Code": "99214",
        "ICD10_Diagnosis_Code": "I10",
        "ICD10_Family": "I10",
        "Billing_Provider_NPI": 123,
        "Payer_ID": 1,
        "Place_of_Service_Code": 11,
        "Charge_Amount": 100,
        "Allowed_Amount": 80,
        "Paid_Amount": 70,
    }
    close = dict(target)
    unrelated = {
        "Member_ID": "M2",
        "CPT_Code": "80050",
        "ICD10_Diagnosis_Code": "Z00.00",
        "ICD10_Family": "Z00",
        "Billing_Provider_NPI": 999,
        "Payer_ID": 2,
        "Place_of_Service_Code": 22,
        "Charge_Amount": 900,
        "Allowed_Amount": 600,
        "Paid_Amount": 500,
    }
    close_score, close_reasons = similarity_score(target, close)
    unrelated_score, _ = similarity_score(target, unrelated)
    assert close_score > unrelated_score
    assert "same CPT code" in close_reasons
    assert "same ICD-10 code" in close_reasons


def test_similarity_reason_never_infers_clinical_causation():
    target = {"CPT_Code": "1", "ICD10_Diagnosis_Code": "A", "Charge_Amount": 1, "Allowed_Amount": 1, "Paid_Amount": 1}
    _, reasons = similarity_score(target, dict(target))
    text = " ".join(reasons).lower()
    for forbidden in ("did not work", "because", "needed treatment", "doctor decided"):
        assert forbidden not in text
