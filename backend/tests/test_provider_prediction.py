import os
import json
import unittest
from pathlib import Path
from statistics import median
from unittest.mock import patch

from backend.import_claims import read_claims
from backend.llm_service import _analysis_cache_key, _constrain_provider_summary, _remove_numeric_forecast_sentences, _validate_grounded_scope, _validate_numeric_grounding, build_llm_input, generate_provider_chat_answer, generate_provider_llm_analysis
from backend.provider_prediction import _dataset_fingerprint, build_provider_batch, build_provider_prediction_payload, find_case, validate_claims


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

    def test_financial_forecast_is_for_selected_claim_not_episode_total(self):
        case_result, _ = find_case(self.rows, "T2", min_peers=2)
        self.assertEqual(case_result["forecast"]["charge"], 120)

    def test_member_history_uses_only_claims_before_selected_claim(self):
        earlier, _ = find_case(self.rows, "T1", min_peers=2)
        later, _ = find_case(self.rows, "T2", min_peers=2)
        self.assertEqual(earlier["features"]["priorClaimCount"], 0)
        self.assertEqual(later["features"]["priorClaimCount"], 1)
        self.assertEqual(later["features"]["priorSameCptCount"], 1)
        changed = [dict(row) for row in self.rows]
        changed[-1].update({"allowed": 1, "paid": 0, "adjustment": 119, "status": "Denied"})
        earlier_after_future_change, _ = find_case(changed, "T1", min_peers=2)
        self.assertEqual(earlier["forecast"], earlier_after_future_change["forecast"])
        self.assertEqual(earlier["denialRisk"], earlier_after_future_change["denialRisk"])

    def test_longitudinal_inputs_are_exposed_as_aggregate_prediction_basis(self):
        case_result, _ = find_case(self.rows, "T2", min_peers=2)
        payload = build_provider_prediction_payload(case_result)
        basis = payload["prediction_basis"]
        self.assertEqual(basis["member_prior_claims_used"], 1)
        self.assertEqual(basis["member_prior_same_cpt_claims"], 1)
        self.assertIn("repeat_observations", basis)
        self.assertEqual(payload["exact_model_output"]["member_prior_claim_count"], 1)

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
        rows = [
            claim("P1", "P1", "2025-01-01", allowed=40),
            claim("P2", "P2", "2025-01-05", allowed=45),
            claim("M1", "M", "2025-03-01", allowed=70),
            claim("TARGET", "M", "2025-03-20", allowed=70),
        ]
        supported, _ = find_case(rows, "TARGET", min_peers=2)
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

    def test_money_metrics_reconciliation_backtest_and_scenario_map(self):
        case_result, _ = find_case(self.rows, "T2", min_peers=2)
        payload = build_provider_prediction_payload(case_result)
        money = payload["provider_financial_metrics"]
        forecast = payload["forecast"]
        self.assertEqual(money["provider_expected_reimbursement"], forecast["predicted_paid"]["value"])
        expected_exposure = round(forecast["denial_risk"]["probability"] * forecast["predicted_paid"]["value"], 2)
        self.assertEqual(money["expected_denial_exposure"], expected_exposure)
        self.assertEqual(money["expected_contractual_adjustment"], forecast["predicted_adjustment"]["value"])
        self.assertNotIn("potential_revenue_at_risk", money)
        allowed_backtest = payload["backtest_against_actual"]["allowed"]
        self.assertEqual(allowed_backtest["absolute_error"], round(abs(allowed_backtest["predicted"] - allowed_backtest["actual"]), 2))
        self.assertIn("reconciliation_difference", payload["financial_reconciliation"])
        self.assertEqual(payload["provider_money_scenario_map"]["provider_claim_payment_prediction"]["predicted_paid"]["value"], forecast["predicted_paid"]["value"])
        self.assertNotIn("provider_revenue_at_risk", payload["provider_money_scenario_map"]["provider_claim_payment_prediction"])
        self.assertIn("member_claim_history", payload["provider_money_scenario_map"])
        self.assertIn("encounter_and_coding", payload["provider_money_scenario_map"])
        self.assertIn("where_provider_money_may_be_saved", payload["provider_money_scenario_map"])
        self.assertIn("claim_workflow", payload["provider_money_scenario_map"])
        self.assertNotIn("cavity", json.dumps(payload["provider_money_scenario_map"]).lower())

    def test_savings_opportunity_separates_supported_savings_from_forecast_exposure(self):
        case_result, _ = find_case(self.rows, "T2", min_peers=2)
        payload = build_provider_prediction_payload(case_result)
        savings = payload["where_provider_money_can_be_saved"]
        future = savings["future_exposure"]
        forecast = payload["forecast"]
        self.assertEqual(
            future["expected_repeat_allowed_exposure"],
            round(forecast["repeat_service_risk"]["probability_90d"] * forecast["predicted_allowed"]["value"], 2),
        )
        self.assertEqual(
            future["expected_repeat_provider_payment_exposure"],
            round(forecast["repeat_service_risk"]["probability_90d"] * forecast["predicted_paid"]["value"], 2),
        )
        self.assertIn("not confirmed savings", future["label"].lower())
        self.assertEqual(savings["forecast_reference"]["repeat_probability_90d"], forecast["repeat_service_risk"]["probability_90d"])
        self.assertEqual(savings["forecast_reference"]["predicted_paid"], forecast["predicted_paid"]["value"])
        self.assertEqual(savings["forecast_reference"]["confidence"], forecast["confidence"]["score"])

    def test_processed_single_claim_savings_stages_exclude_denial_resubmission_and_duplicate_review(self):
        rows = self.rows[:3] + [claim("S1", "S", "2026-01-01")]
        case_result, _ = find_case(rows, "S1", min_peers=2)
        savings = build_provider_prediction_payload(case_result)["where_provider_money_can_be_saved"]
        self.assertFalse(savings["current_claim_opportunity"]["patient_balance_opportunity_available"])
        self.assertNotIn(savings["best_action"]["stage"].lower(), {"denial correction", "resubmission", "duplicate-service review", "patient-balance management"})

    def test_metric_specific_samples_and_blends_are_exposed(self):
        case_result, _ = find_case(self.rows, "T2", min_peers=2)
        payload = build_provider_prediction_payload(case_result)
        for metric in ("predicted_allowed", "predicted_paid", "predicted_patient_responsibility", "predicted_adjustment"):
            metric_basis = payload["prediction_basis"]["metric_basis"][metric]
            self.assertIn("local_sample_size", metric_basis)
            self.assertIn("external_sample_size", metric_basis)
            self.assertAlmostEqual(sum(metric_basis["blend_weights"].values()), 1.0)
        self.assertIn("blend_weights", payload["forecast"]["denial_risk"]["basis"])
        self.assertIn("blend_weights", payload["forecast"]["repeat_service_risk"]["basis"]["90"])

    def test_csv_snapshot_integration_values_are_loaded_dynamically(self):
        csv_path = Path(os.getenv("CSV_PATH", Path.home() / "Downloads" / "EDI_834_837_20 members(837_Claims).csv"))
        rows = read_claims(csv_path) if csv_path.is_file() else json.loads((Path(__file__).parents[2] / "frontend/public/data/claims-fallback.json").read_text())
        source = next(row for row in rows if row.get("claimId") == "CLM00000143")
        case_result, report = find_case(rows, source["claimId"])
        payload = build_provider_prediction_payload(case_result)
        facts = payload["actual_claim_facts"]
        self.assertEqual(report["validation"]["inputClaims"], len(rows))
        self.assertEqual(facts["charge_amount"], source["totalCharge"])
        self.assertEqual(facts["allowed_amount"], source["allowed"])
        self.assertEqual(facts["paid_amount"], source["paid"])
        self.assertEqual(facts["patient_responsibility"], source["patientResp"])
        self.assertEqual(facts["adjustment_amount"], source["adjustment"])
        self.assertEqual(facts["claim_status"], source["status"])
        self.assertEqual(source["allowed"], 668.38)
        savings = payload["where_provider_money_can_be_saved"]
        future = savings["future_exposure"]
        self.assertEqual(future["expected_repeat_allowed_exposure"], round(future["repeat_probability_90d"] * payload["forecast"]["predicted_allowed"]["value"], 2))
        self.assertNotEqual(savings["best_action"]["stage"], "Denial correction")
        self.assertFalse(savings["current_claim_opportunity"]["patient_balance_opportunity_available"])
        performance = savings["current_claim_performance"]
        self.assertEqual(performance["match_level"], "same member + same CPT")
        self.assertEqual(performance["matched_claim_count"], 14)
        self.assertEqual(performance["matched_claim_count"], payload["prediction_basis"]["member_prior_same_cpt_claims"])
        matched = [row for row in rows if row.get("claimId") in set(performance["matched_claim_ids"])]
        self.assertTrue(all(row["dos"] <= source["dos"] for row in matched))
        expected_rates = {
            "allowed": median(row["allowed"] / row["totalCharge"] for row in matched if row["totalCharge"]),
            "paid_to_allowed": median(row["paid"] / row["allowed"] for row in matched if row["allowed"]),
            "adjustment": median(row["adjustment"] / row["totalCharge"] for row in matched if row["totalCharge"]),
            "patient_share": median(row["patientResp"] / row["allowed"] for row in matched if row["allowed"]),
        }
        for metric, historical_rate in expected_rates.items():
            self.assertAlmostEqual(performance["metrics"][metric]["historical_median_rate"], historical_rate, places=7)
            self.assertEqual(performance["metrics"][metric]["variance_status"], "favourable")
        self.assertAlmostEqual(performance["metrics"]["allowed"]["actual_rate"], source["allowed"] / source["totalCharge"], places=4)
        self.assertAlmostEqual(performance["metrics"]["paid_to_allowed"]["actual_rate"], source["paid"] / source["allowed"], places=4)
        self.assertAlmostEqual(performance["metrics"]["allowed"]["related_dollar_variance"], 116.82, delta=.10)
        self.assertAlmostEqual(performance["metrics"]["paid_to_allowed"]["related_dollar_variance"], 11.08, delta=.10)
        self.assertAlmostEqual(performance["metrics"]["adjustment"]["related_dollar_variance"], 116.82, delta=.10)
        self.assertAlmostEqual(performance["metrics"]["patient_share"]["related_dollar_variance"], 48.96, delta=.10)
        basis_values = {item["metric"]: item["value"] for item in savings["current_claim_opportunity"]["calculation_basis"]}
        self.assertEqual(basis_values["potential_underpayment"], 0)
        self.assertEqual(basis_values["excessive_adjustment"], 0)
        availability = {item["key"]: item for item in savings["data_availability"]}
        self.assertEqual(availability["denial_correction_status"]["status"], "Not applicable to this claim")
        self.assertEqual(availability["collection_status"]["status"], "Missing from dataset")
        self.assertIn("1 of 4 eligible historical index claim", savings["recurrence_evidence"]["90"]["local_evidence_statement"])

    @patch.dict(os.environ, {"GROQ_API_KEY": ""}, clear=False)
    def test_chat_is_claim_scoped_and_uses_structured_backend_values(self):
        case_result, _ = find_case(self.rows, "T2", min_peers=2)
        response = generate_provider_chat_answer(case_result, "How was the predicted allowed amount calculated?", "conversation-test")
        payload = build_provider_prediction_payload(case_result)
        self.assertEqual(response["claim_id"], "T2")
        self.assertEqual(response["episode_id"], payload["episode_id"])
        self.assertIn(f"${payload['forecast']['predicted_allowed']['value']:,.2f}", response["answer"])
        self.assertNotIn("memberId", json.dumps(build_llm_input(case_result)))

    def test_cache_key_changes_when_prediction_input_changes(self):
        first, _ = find_case(self.rows, "T2", min_peers=2)
        changed_rows = [dict(row) for row in self.rows]
        changed_rows[0]["paid"] = 20
        second, _ = find_case(changed_rows, "T2", min_peers=2)
        self.assertNotEqual(
            _analysis_cache_key("model", build_llm_input(first)),
            _analysis_cache_key("model", build_llm_input(second)),
        )

    def test_dataset_cache_identity_includes_source_hash_and_versions(self):
        first = [dict(row, sourceCsvHash="source-a") for row in self.rows]
        second = [dict(row, sourceCsvHash="source-b") for row in self.rows]
        self.assertNotEqual(_dataset_fingerprint(first, 90, 2), _dataset_fingerprint(second, 90, 2))
        original = _dataset_fingerprint(first, 90, 2)
        with patch("backend.provider_prediction.MODEL_VERSION", "changed-model"):
            self.assertNotEqual(original, _dataset_fingerprint(first, 90, 2))

    def test_reconciliation_difference_is_not_savings_or_patient_balance(self):
        case_result, _ = find_case(self.rows, "T2", min_peers=2)
        savings = build_provider_prediction_payload(case_result)["where_provider_money_can_be_saved"]
        reconciliation = savings["forecast_reconciliation_difference"]
        self.assertEqual(reconciliation["label"], "Forecast reconciliation difference")
        self.assertFalse(reconciliation["is_savings"])
        self.assertFalse(savings["current_claim_opportunity"]["patient_balance_opportunity_available"])
        self.assertNotEqual(savings["best_action"]["stage"], "Patient-balance management")

    def test_denial_revenue_exposure_uses_shared_predicted_paid(self):
        case_result, _ = find_case(self.rows, "T2", min_peers=2)
        payload = build_provider_prediction_payload(case_result)
        forecast = payload["forecast"]
        exposure = payload["where_provider_money_can_be_saved"]["future_exposure"]
        self.assertEqual(exposure["expected_denial_revenue_exposure"], round(forecast["denial_risk"]["probability"] * forecast["predicted_paid"]["value"], 2))

    def test_current_recovery_uses_close_match_not_global_forecast_peers(self):
        rows = [
            claim("L1", "M", "2025-01-01", paid=35),
            claim("L2", "M", "2025-02-01", paid=42),
            claim("G1", "G1", "2025-01-01", payerId="OTHER", cptCode="93000", paid=69),
            claim("G2", "G2", "2025-01-01", payerId="OTHER", cptCode="93000", paid=69),
            claim("TARGET", "M", "2025-03-01", paid=30),
        ]
        case_result, _ = find_case(rows, "TARGET", min_peers=2)
        savings = build_provider_prediction_payload(case_result)["where_provider_money_can_be_saved"]
        self.assertEqual(savings["historical_comparison"]["match_level"], "same member + same CPT + same diagnosis family")
        self.assertEqual(savings["current_claim_opportunity"]["sample_size"], 2)
        self.assertEqual(set(savings["historical_comparison"]["affected_claim_ids"]), {"L1", "L2"})

    def test_recurrence_evidence_has_numerators_denominators_and_filters(self):
        case_result, _ = find_case(self.rows, "T2", min_peers=2)
        payload = build_provider_prediction_payload(case_result)
        recurrence = payload["where_provider_money_can_be_saved"]["recurrence_evidence"]
        for horizon in ("30", "60", "90"):
            self.assertIn("local_numerator", recurrence[horizon])
            self.assertIn("local_denominator", recurrence[horizon])
            self.assertIn("external_numerator", recurrence[horizon])
            self.assertIn("external_denominator", recurrence[horizon])
            self.assertEqual(recurrence[horizon]["eligible_prior_index_claim_count"], recurrence[horizon]["local_denominator"])
            self.assertEqual(recurrence[horizon]["recurring_prior_index_claim_count"], recurrence[horizon]["local_numerator"])
            self.assertIn("eligible historical index claim", recurrence[horizon]["local_evidence_statement"])
            self.assertNotIn("interval", recurrence[horizon]["local_evidence_statement"].lower())
            self.assertTrue(recurrence[horizon]["filters_used"])
            self.assertEqual(recurrence[horizon]["final_blended_rate"], payload["forecast"]["repeat_service_risk"][f"probability_{horizon}d"])

    def test_data_availability_is_claim_specific_and_schema_driven(self):
        rows = self.rows[:3] + [claim("S1", "S", "2026-01-01", sourceCsvColumns=["Claim_ID", "Claim_Status_Description", "Patient_Responsibility"])]
        case_result, _ = find_case(rows, "S1", min_peers=2)
        savings = build_provider_prediction_payload(case_result)["where_provider_money_can_be_saved"]
        availability = {item["key"]: item for item in savings["data_availability"]}
        self.assertEqual(availability["denial_recoverability_status"]["status"], "Not applicable to this claim")
        self.assertEqual(availability["collection_status"]["status"], "Missing from dataset")
        self.assertEqual(availability["authorization_requirement"]["status"], "Missing from dataset")
        self.assertNotEqual(savings["best_action"]["stage"], "No immediate validated savings action")

    def test_every_valid_csv_claim_can_build_the_shared_savings_forecast(self):
        csv_path = Path(os.getenv("CSV_PATH", Path.home() / "Downloads" / "EDI_834_837_20 members(837_Claims).csv"))
        if not csv_path.is_file():
            self.skipTest("Uploaded claims CSV is unavailable")
        rows = read_claims(csv_path)
        valid, validation = validate_claims(rows)
        failures = []
        for source in valid:
            try:
                case_result, _ = find_case(rows, source["claimId"])
                payload = build_provider_prediction_payload(case_result)
                savings = payload["where_provider_money_can_be_saved"]
                reference = savings["forecast_reference"]
                forecast = payload["forecast"]
                if reference["predicted_paid"] != forecast["predicted_paid"]["value"]:
                    failures.append((source["claimId"], "shared forecast mismatch"))
                if not savings["best_action"].get("action"):
                    failures.append((source["claimId"], "missing next action"))
            except Exception as error:  # pragma: no cover - failure details are asserted below
                failures.append((source.get("claimId"), type(error).__name__))
        self.assertEqual(validation["validClaims"], len(rows))
        self.assertEqual(failures, [])

    def test_shared_forecast_reference_has_no_duplicate_prediction_values(self):
        case_result, _ = find_case(self.rows, "T2", min_peers=2)
        payload = build_provider_prediction_payload(case_result)
        reference = payload["where_provider_money_can_be_saved"]["forecast_reference"]
        forecast = payload["forecast"]
        self.assertEqual(reference["repeat_probability_90d"], forecast["repeat_service_risk"]["probability_90d"])
        self.assertEqual(reference["repeat_probability_30d"], forecast["repeat_service_risk"]["probability_30d"])
        self.assertEqual(reference["repeat_probability_60d"], forecast["repeat_service_risk"]["probability_60d"])
        self.assertEqual(reference["predicted_allowed"], forecast["predicted_allowed"]["value"])
        self.assertEqual(reference["predicted_paid"], forecast["predicted_paid"]["value"])
        self.assertEqual(reference["confidence"], forecast["confidence"]["score"])
        self.assertEqual(reference["peer_sample_size"], forecast["confidence"]["peer_sample_size"])
        self.assertEqual(reference["prediction_cutoff_date"], payload["prediction_basis"]["prediction_cutoff_date"])

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
