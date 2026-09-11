import os
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

from backend.avoidable_prediction import _avoidable_evidence
from backend.financial_engine import (
    _denial_prediction,
    build_financial_result,
    member_supported_summary,
)
from backend.prediction_validation import build_validation_report
from backend.workbook_enrichment import load_workbook_database
from backend.workbook_llm import generate_workbook_chat_answer


class PredictedAvoidableSpendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database = load_workbook_database()

    def test_formula_and_validated_amount_are_separate(self):
        result = build_financial_result(self.database, "CLM00001092")
        predicted = result["predicted_avoidable_spend"]
        expected = round(
            predicted["repeat_probability_90d"]
            * predicted["avoidable_given_repeat_probability"]
            * predicted["expected_extra_repeat_allowed_cost"],
            2,
        )
        self.assertEqual(predicted["value"], expected)
        self.assertGreater(predicted["value"], 0)
        self.assertEqual(result["validated_avoidable_spend"]["value"], 0)
        self.assertFalse(result["validated_avoidable_spend"]["available"])

    def test_future_denial_exposure_uses_smoothed_probability(self):
        result = build_financial_result(self.database, "CLM00000143")
        exposure = result["financial_prediction_snapshot"][
            "future_denial_exposure"
        ]
        self.assertEqual(
            exposure["value"],
            round(
                exposure["denial_probability"]
                * exposure["predicted_paid"],
                2,
            ),
        )
        self.assertGreater(exposure["peer_count"], 0)

    def test_zero_local_denials_can_use_earlier_peer_risk_and_exclude_future(self):
        def row(claim_id, service_date, member_id, status):
            return {
                "claimId": claim_id,
                "memberId": member_id,
                "dos": service_date,
                "status": status,
                "payerId": "P1",
                "billingProviderNpi": "N1",
                "cptCode": "C1",
                "placeOfServiceCode": "11",
                "workbookFields": {"ICD10_Family": "D1"},
            }

        selected = row("SELECTED", "2024-06-01", "M1", "Processed")
        database = SimpleNamespace(
            historical_claims=(
                row("LOCAL", "2024-01-01", "M1", "Processed"),
                row("PEER1", "2024-01-02", "M2", "Denied"),
                row("PEER2", "2024-01-03", "M3", "Processed"),
                row("PEER3", "2024-01-04", "M4", "Processed"),
                row("FUTURE", "2024-07-01", "M5", "Denied"),
            )
        )
        prediction = _denial_prediction(database, selected)
        self.assertEqual(prediction["local_denials"], 0)
        self.assertGreater(prediction["probability"], 0)
        self.assertEqual(prediction["peer_denials"], 1)

    def test_hierarchical_fallback_does_not_use_zero_as_missing_evidence(self):
        result = build_financial_result(self.database, "CLM00001092")
        predicted = result["predicted_avoidable_spend"]
        self.assertGreater(predicted["peer_count"], 0)
        self.assertTrue(predicted["peer_level"])
        self.assertGreater(predicted["value"], 0)
        self.assertEqual(predicted["zero_reasons"], [])

    def test_zero_local_recurrence_can_blend_to_nonzero(self):
        match = None
        for claim in self.database.selectable_claims:
            predicted = build_financial_result(
                self.database, claim["claimId"]
            )["predicted_avoidable_spend"]
            evidence = build_financial_result(
                self.database, claim["claimId"]
            )["prediction"]["avoidable_prediction_basis"][
                "repeat_probability"
            ]["evidence"]
            if (
                evidence["local_denominator"] > 0
                and evidence["local_numerator"] == 0
                and predicted["repeat_probability_90d"] > 0
            ):
                match = predicted
                break
        self.assertIsNotNone(match)

    def test_planned_follow_up_is_not_automatically_avoidable(self):
        claim = {
            "workbookFields": {
                "Repeat_Visit_Reason": "Scheduled Follow-up",
                "Condition_Resolved": "N",
                "Treatment_Outcome": "No Change",
                "Follow_Up_Completed": "N",
            }
        }
        self.assertFalse(_avoidable_evidence(claim))

    def test_unresolved_repeat_can_inform_avoidability(self):
        claim = {
            "workbookFields": {
                "Repeat_Visit_Reason": "Symptom Recurrence",
                "Condition_Resolved": "Ongoing",
                "Treatment_Outcome": "No Change",
                "Follow_Up_Completed": "N",
            }
        }
        self.assertTrue(_avoidable_evidence(claim))

    def test_all_selectable_claims_are_scored_and_validated(self):
        for claim in self.database.selectable_claims:
            result = build_financial_result(
                self.database, claim["claimId"]
            )
            predicted = result["predicted_avoidable_spend"]
            self.assertGreaterEqual(predicted["value"], 0)
            self.assertLessEqual(
                predicted["low"], predicted["value"]
            )
            self.assertLessEqual(
                predicted["value"], predicted["high"]
            )
            self.assertTrue(result["consistency_check"]["passed"])
            exposure = result["financial_prediction_snapshot"][
                "future_denial_exposure"
            ]
            self.assertEqual(
                exposure["value"],
                round(
                    exposure["denial_probability"]
                    * exposure["predicted_paid"],
                    2,
                ),
            )

    def test_every_member_gets_episode_deduplicated_prediction(self):
        for member in self.database.members:
            member_id = member["memberId"]
            summary = member_supported_summary(self.database, member_id)
            claims = self.database.member_claims(member_id)
            latest = {}
            for claim in claims:
                key = claim.get("episodeId") or claim["claimId"]
                current = latest.get(key)
                if current is None or (
                    claim.get("dos", ""),
                    claim["claimId"],
                ) > (
                    current.get("dos", ""),
                    current["claimId"],
                ):
                    latest[key] = claim
            expected = round(
                sum(
                    build_financial_result(
                        self.database, claim["claimId"]
                    )["predicted_avoidable_spend"]["value"]
                    for claim in latest.values()
                ),
                2,
            )
            self.assertEqual(
                summary["predicted_avoidable_spend_90d"], expected
            )
            self.assertEqual(
                summary["active_episode_count"], len(latest)
            )
            self.assertGreaterEqual(
                summary["future_denial_exposure"], 0
            )

    def test_chat_uses_exact_canonical_prediction(self):
        result = build_financial_result(self.database, "CLM00000143")
        with patch.dict(os.environ, {"LLM_PROVIDER": "none"}):
            chat = generate_workbook_chat_answer(
                self.database,
                result["claim_id"],
                result["episode_id"],
                "What is the predicted avoidable spend?",
                "prediction-test",
            )
        self.assertEqual(
            chat["financial_explanation"]["predicted_avoidable_spend"],
            result["predicted_avoidable_spend"],
        )
        self.assertIn(
            f"${result['predicted_avoidable_spend']['value']:,.2f}",
            chat["answer"],
        )

    def test_frontend_and_ollama_do_not_calculate_avoidable_money(self):
        root = Path(__file__).resolve().parents[2]
        frontend = (root / "frontend/src/App.jsx").read_text()
        llm = (root / "backend/workbook_llm.py").read_text()
        self.assertNotIn(
            "repeat_probability_90d * avoidable_given_repeat_probability",
            frontend,
        )
        self.assertIn("Do not perform arithmetic", llm)
        self.assertIn('"predicted_avoidable_spend"', llm)

    def test_backtest_reports_zero_reasons_and_peer_distribution(self):
        report = build_validation_report(self.database)[
            "avoidable_spend"
        ]
        self.assertGreater(report["evaluated_anchors"], 0)
        self.assertGreaterEqual(report["mean_predicted_avoidable_spend"], 0)
        self.assertGreaterEqual(report["median_predicted_avoidable_spend"], 0)
        self.assertGreaterEqual(report["mae"], 0)
        self.assertTrue(report["peer_level_distribution"])
        self.assertIsInstance(report["zero_reasons"], dict)


if __name__ == "__main__":
    unittest.main()
