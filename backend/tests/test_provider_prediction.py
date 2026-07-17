import os
import unittest
from unittest.mock import patch

from backend.llm_service import _analysis_cache_key, _constrain_provider_summary, _remove_numeric_forecast_sentences, _validate_grounded_scope, _validate_numeric_grounding, build_llm_input, generate_provider_llm_analysis
from backend.provider_prediction import build_provider_batch, build_provider_prediction_payload, find_case, validate_claims


def claim(claim_id, member, dos, **overrides):
    row = {
        "claimId": claim_id,
        "number": claim_id,
        "memberId": member,
        "dos": dos,
        "diagnosisCode": "I10",
        "cptCode": "99214",
        "payerId": "P1",
        "payer": "Payer One",
        "billingProviderNpi": "1234567890",
        "placeOfServiceCode": "11",
        "totalCharge": 100,
        "allowed": 70,
        "paid": 55,
        "patientResp": 15,
        "adjustment": 30,
        "status": "Processed as Primary",
        "submissionDate": dos,
    }
    row.update(overrides)
    return row


class ProviderPredictionTests(unittest.TestCase):
    def setUp(self):
        self.rows = [
            claim("P1", "M1", "2025-01-01"),
            claim("P2", "M2", "2025-01-03", allowed=80, paid=64, patientResp=16, adjustment=20),
            claim("P3", "M3", "2025-02-01", allowed=60, paid=42, patientResp=18, adjustment=40),
            claim("T1", "MT", "2026-01-01", totalCharge=200, allowed=199, paid=198, patientResp=1, adjustment=1),
            claim("T2", "MT", "2026-01-20", totalCharge=120, allowed=119, paid=118, patientResp=1, adjustment=1),
        ]

    def test_validation_reports_duplicate_and_missing_identifiers(self):
        valid, report = validate_claims([claim("A", "M", "2025-01-01"), claim("A", "M", "2025-01-02"), claim("", "", "bad")])
        self.assertEqual(len(valid), 1)
        self.assertEqual(report["issues"]["duplicate_claim_id"], 1)
        self.assertEqual(report["issues"]["missing_member_id"], 1)
        self.assertEqual(report["issues"]["invalid_service_date"], 1)

    def test_every_valid_claim_is_assigned_once_and_gap_splits_episode(self):
        rows = [claim("A", "M", "2025-01-01"), claim("B", "M", "2025-03-15"), claim("C", "M", "2025-07-01")]
        episodes, report = build_provider_batch(rows, window_days=90, min_peers=1, use_cache=False)
        self.assertEqual(len(episodes), 2)
        self.assertTrue(report["quality"]["allValidClaimsAssignedOnce"])

    def test_single_claim_episode_is_supported_with_low_confidence(self):
        episodes, _ = build_provider_batch([claim("A", "M", "2025-01-01")], use_cache=False)
        self.assertEqual(episodes[0]["features"]["claimCount"], 1)
        self.assertEqual(episodes[0]["confidence"], "Low")
        self.assertFalse(episodes[0]["avoidableSpendSupported"])

    def test_financial_forecast_does_not_use_target_allowed_or_paid(self):
        first, _ = find_case(self.rows, "T2", min_peers=2)
        changed = [dict(row) for row in self.rows]
        changed[-1].update({"allowed": 1, "paid": 0, "patientResp": 0, "adjustment": 119})
        second, _ = find_case(changed, "T2", min_peers=2)
        self.assertEqual(first["forecast"], second["forecast"])

    def test_peer_hierarchy_and_ranges_are_reported(self):
        case_result, _ = find_case(self.rows, "T1", min_peers=2)
        self.assertGreaterEqual(case_result["peerCount"], 2)
        self.assertLessEqual(case_result["forecast"]["paidRange"]["low"], case_result["forecast"]["paidRange"]["high"])
        self.assertTrue(case_result["forecast"]["peerHierarchy"])

    def test_exact_claim_evidence_and_no_direct_phi_in_llm_input(self):
        case_result, _ = find_case(self.rows, "T2", min_peers=2)
        case_result["selectedClaim"] = next(row for row in case_result["claims"] if row["claimId"] == "T2")
        payload = build_llm_input(case_result)
        serialized = str(payload).lower()
        self.assertIn("t2", serialized)
        self.assertNotIn("patient_name", serialized)
        self.assertNotIn("member_id", serialized)
        self.assertNotIn("date_of_birth", serialized)

    def test_avoidable_spend_requires_repeat_and_minimum_peers(self):
        no_peers, _ = find_case(self.rows[-2:], "T2", min_peers=5)
        self.assertEqual(no_peers["avoidableSpend"], 0)
        self.assertFalse(no_peers["avoidableSpendSupported"])
        supported, _ = find_case(self.rows, "T2", min_peers=2)
        self.assertTrue(supported["avoidableSpendSupported"])

    def test_structured_payload_separates_actual_and_predicted_allowed(self):
        case_result, _ = find_case(self.rows, "T2", min_peers=2)
        payload = build_provider_prediction_payload(case_result)
        self.assertEqual(payload["actual_claim_facts"]["allowed_amount"], 119)
        self.assertEqual(payload["forecast"]["predicted_allowed"]["value"], case_result["forecast"]["allowed"])
        self.assertNotEqual(payload["actual_claim_facts"]["allowed_amount"], payload["forecast"]["predicted_allowed"]["value"])

    def test_single_claim_without_repeats_has_no_repeat_or_denial_action(self):
        rows = self.rows[:3] + [claim("S1", "S", "2026-01-01", priorAuth="", referral="")]
        case_result, _ = find_case(rows, "S1", min_peers=2)
        payload = build_provider_prediction_payload(case_result)
        codes = {action["code"] for action in payload["recommended_actions"]}
        self.assertNotIn("review_repeat", codes)
        self.assertNotIn("review_denial", codes)
        self.assertIn("verify_authorization", codes)
        auth_action = next(action for action in payload["recommended_actions"] if action["code"] == "verify_authorization")
        self.assertIn("does not establish whether authorization was required", auth_action["reason"])

    def test_unsupported_avoidable_spend_is_null_with_reason(self):
        rows = self.rows[:3] + [claim("S1", "S", "2026-01-01")]
        case_result, _ = find_case(rows, "S1", min_peers=2)
        avoidable = build_provider_prediction_payload(case_result)["forecast"]["potentially_avoidable_spend"]
        self.assertFalse(avoidable["available"])
        self.assertIsNone(avoidable["value"])
        self.assertTrue(avoidable["reason"])

    def test_exact_model_output_matches_backend_forecast(self):
        case_result, _ = find_case(self.rows, "T2", min_peers=2)
        payload = build_provider_prediction_payload(case_result)
        exact = payload["exact_model_output"]
        self.assertEqual(exact["denial_probability"], payload["forecast"]["denial_risk"]["probability"])
        self.assertEqual(exact["repeat_probability_90d"], payload["forecast"]["repeat_service_risk"]["probability_90d"])
        self.assertEqual(exact["predicted_paid"], payload["forecast"]["predicted_paid"]["value"])
        self.assertEqual(exact["peer_sample_size"], payload["prediction_basis"]["peer_claims_used"])

    def test_cache_key_changes_when_prediction_input_changes(self):
        first, _ = find_case(self.rows, "T2", min_peers=2)
        changed_rows = [dict(row) for row in self.rows]
        changed_rows[0]["paid"] = 20
        second, _ = find_case(changed_rows, "T2", min_peers=2)
        self.assertNotEqual(
            _analysis_cache_key("model", build_llm_input(first)),
            _analysis_cache_key("model", build_llm_input(second)),
        )

    def test_llm_scope_guard_rejects_clinical_or_unsupported_setting_advice(self):
        analysis = {
            "provider_summary": "A physician office claim was reviewed.",
            "financial_forecast_summary": "Payment is estimated from peers.",
            "risk_drivers": [],
            "recommended_actions": ["Add clinical justification to the chart."],
            "evidence_used": [],
            "limitations": "Limited evidence.",
            "unsupported_assumptions": [],
        }
        with self.assertRaises(ValueError):
            _validate_grounded_scope(analysis, {"selected_claim": {"place_of_service_code": "41"}})

    def test_llm_cannot_invent_currency_or_percentage_values(self):
        analysis = {
            "provider_summary": "Predicted denial probability is 99.9%.",
            "financial_forecast_summary": "Predicted paid is $999.99.",
            "risk_drivers": [], "recommended_actions": [], "evidence_used": [],
            "limitations": "Limited evidence.", "unsupported_assumptions": [],
        }
        with self.assertRaises(ValueError):
            _validate_numeric_grounding(analysis, {"deterministic_forecast": {"denial": .18, "paid": 55.0}})

    def test_provider_summary_is_limited_to_five_sentences(self):
        summary = "One. Two. Three. Four. Five. Six."
        self.assertEqual(_constrain_provider_summary(summary), "One. Two. Three. Four. Five.")

    def test_llm_numeric_sentences_are_removed_before_display(self):
        text = "The actual claim was processed. Predicted paid is $99.99. Denial risk is 12.3%."
        self.assertEqual(_remove_numeric_forecast_sentences(text, "Fallback."), "The actual claim was processed.")
        self.assertEqual(_remove_numeric_forecast_sentences("Predictions are close to actual values.", "Fallback."), "Fallback.")
        self.assertEqual(_remove_numeric_forecast_sentences("Predictions fall within a narrow range.", "Fallback."), "Fallback.")
        self.assertEqual(_remove_numeric_forecast_sentences("Predicted paid is 471. 20.", "Fallback."), "Fallback.")
        self.assertEqual(_remove_numeric_forecast_sentences("No potential savings were identified.", "Fallback."), "Fallback.")
        self.assertEqual(_remove_numeric_forecast_sentences("Predicted paid is slightly lower than actual paid.", "Fallback."), "Fallback.")

    @patch.dict(os.environ, {"GROQ_API_KEY": "test-key", "GROQ_MODEL": "test-model"})
    @patch("backend.llm_service._request_groq", side_effect=ValueError("invalid schema"))
    def test_invalid_llm_schema_repairs_once_then_falls_back(self, request_mock):
        case_result, _ = find_case(self.rows, "T2", min_peers=2)
        result = generate_provider_llm_analysis(case_result)
        self.assertEqual(request_mock.call_count, 2)
        self.assertTrue(result["fallback"])
        cached = generate_provider_llm_analysis(case_result)
        self.assertEqual(request_mock.call_count, 2)
        self.assertTrue(cached["cached"])
        self.assertEqual(set(result["analysis"]), {
            "provider_summary", "financial_forecast_summary", "risk_drivers",
            "recommended_actions", "evidence_used", "limitations", "unsupported_assumptions",
        })


if __name__ == "__main__":
    unittest.main()
