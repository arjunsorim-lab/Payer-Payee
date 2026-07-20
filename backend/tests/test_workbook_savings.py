import os
import json
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from backend.llm_service import build_llm_input, generate_provider_chat_answer
from backend.provider_prediction import build_provider_prediction_payload, find_case
from backend.workbook_enrichment import SYNTHETIC_WARNING, join_claim_enrichment, read_savings_workbook


WORKBOOK = Path(os.getenv("SAVINGS_WORKBOOK_PATH", Path.home() / "Downloads" / "claims_with_dummy_savings_fields.xlsx"))


def original_row(claim_id="C1", member_id="M1", **overrides):
    row = {
        "Claim_ID": claim_id, "Member_ID": member_id, "Service_Date_From": "20260101",
        "Claim_Status_Description": "Processed as Primary", "CPT_Code": "99214",
        "ICD10_Diagnosis_Code": "I10", "Payer_ID": "P1", "Billing_Provider_NPI": "B1",
        "Place_of_Service_Code": "11", "Units": 1, "Charge_Amount": 100,
        "Allowed_Amount": 80, "Paid_Amount": 60, "Patient_Responsibility": 20,
        "Adjustment_Amount": 20,
    }
    row.update(overrides)
    return row


def enrichment_row(claim_id="C1", member_id="M1", **overrides):
    row = {
        "Claim_ID": claim_id, "Member_ID": member_id, "Dummy_Data_Flag": "YES",
        "Expected_Reimbursement": 90, "Contract_Allowed_Amount": 95, "Payment_Tolerance": 5,
        "Recovered_Amount": 0, "Outstanding_Patient_Balance": 0, "Balance_Status": "Outstanding",
        "Aging_Bucket": "0-30", "Collection_Status": "Not in Collections",
        "Prior_Auth_Required": "No", "Prior_Auth_Status": "Not Required",
        "Referral_Required": "No", "Referral_Status": "Not Required",
        "Duplicate_Claim_Flag": "No", "Claim_Frequency_Code": "1", "Corrected_Claim_Indicator": "No",
        "Episode_ID": "EP1", "Related_Claim_Flag": "No", "Condition_Resolved": "Unknown",
        "Treatment_Outcome": "Unknown", "Follow_Up_Completed": "Not Documented",
        "Repeat_Visit_Reason": "Not Applicable", "Denial_Correctable_Flag": "Not Applicable",
        "Appeal_Status": "Not Applicable", "Resubmission_Status": "Not Applicable",
    }
    row.update(overrides)
    return row


class WorkbookJoinTests(unittest.TestCase):
    def test_join_requires_matching_claim_and_member(self):
        claims, report = join_claim_enrichment(
            [original_row("C1", "M1"), original_row("C2", "M2")],
            [enrichment_row("C1", "WRONG"), enrichment_row("C2", "M2")],
            "hash-1",
        )
        by_id = {claim["claimId"]: claim for claim in claims}
        self.assertIsNone(by_id["C1"]["syntheticEnrichment"])
        self.assertIsNotNone(by_id["C2"]["syntheticEnrichment"])
        self.assertEqual(report["member_mismatch_count"], 1)
        self.assertEqual(report["matched_enrichment_count"], 1)

    def test_original_financial_values_are_never_overwritten(self):
        enriched = enrichment_row(Expected_Reimbursement=9999, Contract_Allowed_Amount=8888)
        claims, _ = join_claim_enrichment([original_row(Paid_Amount=60, Allowed_Amount=80)], [enriched], "hash-2")
        self.assertEqual(claims[0]["paid"], 60)
        self.assertEqual(claims[0]["allowed"], 80)
        self.assertEqual(claims[0]["syntheticEnrichment"]["Expected_Reimbursement"], 9999)

    def test_duplicate_enrichment_is_rejected(self):
        claims, report = join_claim_enrichment([original_row()], [enrichment_row(), enrichment_row()], "hash-3")
        self.assertIsNone(claims[0]["syntheticEnrichment"])
        self.assertEqual(report["duplicate_enrichment_count"], 1)


@unittest.skipUnless(WORKBOOK.is_file(), "Uploaded savings workbook is unavailable")
class WorkbookSavingsIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.claims, cls.report = read_savings_workbook(WORKBOOK)

    def test_complete_workbook_join_and_original_source_values(self):
        self.assertEqual(self.report["source_claim_count"], 1502)
        self.assertEqual(self.report["matched_enrichment_count"], self.report["valid_source_claim_count"])
        self.assertEqual(self.report["unmatched_claim_count"], 0)
        self.assertEqual(self.report["duplicate_enrichment_count"], 0)
        sample = next(claim for claim in self.claims if claim["claimId"] == "CLM00000143")
        self.assertEqual((sample["totalCharge"], sample["allowed"], sample["paid"], sample["patientResp"], sample["adjustment"]), (791.96, 668.38, 549.53, 134.22, 123.58))

    def test_prediction_and_savings_use_the_same_forecast_and_label_synthetic_data(self):
        case, _ = find_case(self.claims, "CLM00000143", min_peers=5)
        case["selectedClaim"] = next(claim for claim in self.claims if claim["claimId"] == "CLM00000143")
        payload = build_provider_prediction_payload(case)
        savings = payload["where_provider_money_can_be_saved"]
        self.assertEqual(savings["forecast_reference"]["repeat_probability_90d"], payload["forecast"]["repeat_service_risk"]["probability_90d"])
        self.assertEqual(savings["future_exposure"]["repeat_allowed_exposure"], round(payload["forecast"]["repeat_service_risk"]["probability_90d"] * payload["forecast"]["predicted_allowed"]["value"], 2))
        self.assertEqual(savings["data_provenance"]["data_mode"], "synthetic_demo")
        self.assertEqual(savings["data_provenance"]["synthetic_warning"], SYNTHETIC_WARNING)

    def test_patient_balance_authorization_and_referral_rules_are_conditional(self):
        case, _ = find_case(self.claims, "CLM00000143", min_peers=5)
        selected = next(claim for claim in self.claims if claim["claimId"] == "CLM00000143")
        case["selectedClaim"] = selected
        opportunity_types = {item["type"] for item in build_provider_prediction_payload(case)["where_provider_money_can_be_saved"]["current_claim_opportunity"]["opportunities"]}
        enrichment = selected["syntheticEnrichment"]
        self.assertEqual("patient_balance" in opportunity_types, bool(enrichment["Outstanding_Patient_Balance"] > 0 and enrichment["Balance_Status"] == "Outstanding" and (enrichment["Collection_Status"] == "In Collections" or enrichment["Aging_Bucket"] in {"61-90", "91+"})))
        self.assertEqual("authorization" in opportunity_types, enrichment["Prior_Auth_Required"] == "Yes" and enrichment["Prior_Auth_Status"] in {"Missing", "Expired", "Denied", "Insufficient Units"})
        self.assertEqual("referral" in opportunity_types, enrichment["Referral_Required"] == "Yes" and enrichment["Referral_Status"] in {"Missing", "Invalid", "Expired"})

    def test_underpayment_tolerance_recovery_and_denial_rules(self):
        case, _ = find_case(self.claims, "CLM00000143", min_peers=5)
        selected = deepcopy(next(claim for claim in self.claims if claim["claimId"] == "CLM00000143"))
        selected["syntheticEnrichment"].update({
            "Expected_Reimbursement": selected["paid"] + 150,
            "Contract_Allowed_Amount": selected["allowed"],
            "Payment_Tolerance": 10,
            "Recovered_Amount": 50,
            "Outstanding_Patient_Balance": 0,
            "Aging_Bucket": "0-30",
            "Collection_Status": "Not in Collections",
            "Prior_Auth_Required": "No",
            "Prior_Auth_Status": "Not Required",
            "Referral_Required": "No",
            "Referral_Status": "Not Required",
            "Duplicate_Claim_Flag": "No",
            "Claim_Frequency_Code": "1",
            "Corrected_Claim_Indicator": "No",
        })
        case["selectedClaim"] = selected
        opportunities = build_provider_prediction_payload(case)["where_provider_money_can_be_saved"]["current_claim_opportunity"]["opportunities"]
        underpayment = next(item for item in opportunities if item["type"] == "underpayment")
        self.assertEqual(underpayment["amount"], 100)
        self.assertFalse(any(item["type"] == "patient_balance" for item in opportunities))

        selected["status"] = "Denied"
        selected["syntheticEnrichment"].update({
            "Denial_Correctable_Flag": "Yes", "Appeal_Status": "Appeal Submitted",
            "Resubmission_Status": "Pending Review", "Expected_Reimbursement": 600,
            "Recovered_Amount": 100,
        })
        case["selectedClaim"] = selected
        opportunities = build_provider_prediction_payload(case)["where_provider_money_can_be_saved"]["current_claim_opportunity"]["opportunities"]
        self.assertFalse(any(item["type"] == "underpayment" for item in opportunities))
        denial = next(item for item in opportunities if item["type"] == "denial")
        self.assertEqual(denial["amount"], 500)
        self.assertEqual(denial["recovered_amount"], 100)

    def test_duplicate_action_requires_duplicate_or_correction_evidence(self):
        case, _ = find_case(self.claims, "CLM00000143", min_peers=5)
        selected = deepcopy(next(claim for claim in self.claims if claim["claimId"] == "CLM00000143"))
        selected["syntheticEnrichment"].update({"Duplicate_Claim_Flag": "No", "Claim_Frequency_Code": "1", "Corrected_Claim_Indicator": "No"})
        case["selectedClaim"] = selected
        opportunities = build_provider_prediction_payload(case)["where_provider_money_can_be_saved"]["current_claim_opportunity"]["opportunities"]
        self.assertFalse(any(item["type"] == "duplicate_or_correction" for item in opportunities))
        selected["syntheticEnrichment"]["Duplicate_Claim_Flag"] = "Yes"
        opportunities = build_provider_prediction_payload(case)["where_provider_money_can_be_saved"]["current_claim_opportunity"]["opportunities"]
        self.assertTrue(any(item["type"] == "duplicate_or_correction" for item in opportunities))

    def test_groq_input_contains_derived_provenance_not_raw_enrichment_rows_or_phi(self):
        case, _ = find_case(self.claims, "CLM00000143", min_peers=5)
        case["selectedClaim"] = next(claim for claim in self.claims if claim["claimId"] == "CLM00000143")
        llm_input = build_llm_input(case)
        serialized = str(llm_input)
        self.assertIn("synthetic_demo", serialized)
        self.assertNotIn("syntheticEnrichment", serialized)
        self.assertNotIn("Patient_First_Name", serialized)
        self.assertNotIn("Patient_DOB", serialized)
        self.assertLess(len(json.dumps(llm_input)), 25000)

    @patch.dict(os.environ, {"GROQ_API_KEY": ""}, clear=False)
    def test_chat_explains_backend_values_without_recalculating_them(self):
        case, _ = find_case(self.claims, "CLM00000143", min_peers=5)
        case["selectedClaim"] = next(claim for claim in self.claims if claim["claimId"] == "CLM00000143")
        payload = build_provider_prediction_payload(case)
        response = generate_provider_chat_answer(case, "How much can be saved?", "workbook-chat-test")
        expected = payload["where_provider_money_can_be_saved"]["synthetic_demo_opportunity"]["amount"]
        self.assertIn(f"${expected:,.2f}", response["answer"])
        self.assertIn("Synthetic demonstration opportunity", response["answer"])
        explanation = response["financial_explanation"]
        self.assertEqual(explanation["synthetic_demo_opportunity"]["amount"], expected)
        self.assertEqual(explanation["future_financial_exposure"]["denial_exposure"], payload["where_provider_money_can_be_saved"]["future_exposure"]["expected_denial_revenue_exposure"])
        self.assertFalse(explanation["validated_real_savings"]["available"])

    def test_synthetic_patient_balance_is_never_presented_as_verified(self):
        case, _ = find_case(self.claims, "CLM00000143", min_peers=5)
        case["selectedClaim"] = next(claim for claim in self.claims if claim["claimId"] == "CLM00000143")
        savings = build_provider_prediction_payload(case)["where_provider_money_can_be_saved"]
        patient = next(item for item in savings["synthetic_demo_opportunity"]["breakdown"] if item["type"] == "patient_balance")
        self.assertEqual(patient["stage"], "DEMO PATIENT-BALANCE OPPORTUNITY")
        self.assertEqual(patient["data_source"], "Dummy_Enrichment")
        self.assertIn("not a verified billing recommendation", patient["warning"])
        self.assertFalse(savings["validated_real_savings"]["available"])

    def test_overlapping_synthetic_opportunities_are_not_double_counted(self):
        case, _ = find_case(self.claims, "CLM00000143", min_peers=5)
        selected = deepcopy(next(claim for claim in self.claims if claim["claimId"] == "CLM00000143"))
        selected["syntheticEnrichment"].update({
            "Expected_Reimbursement": selected["paid"] + 100,
            "Payment_Tolerance": 1,
            "Recovered_Amount": 0,
            "Outstanding_Patient_Balance": 75,
            "Balance_Status": "Outstanding",
            "Aging_Bucket": "91+",
            "Collection_Status": "In Collections",
        })
        case["selectedClaim"] = selected
        demo = build_provider_prediction_payload(case)["where_provider_money_can_be_saved"]["synthetic_demo_opportunity"]
        amounts = [item["amount"] for item in demo["breakdown"] if item.get("amount")]
        self.assertGreater(len(amounts), 1)
        self.assertIn(demo["amount"], amounts)
        self.assertNotEqual(demo["amount"], sum(amounts))


if __name__ == "__main__":
    unittest.main()
