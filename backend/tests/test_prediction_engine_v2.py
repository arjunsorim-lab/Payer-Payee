"""Focused regressions for the one authoritative payer savings engine."""

import unittest

from backend.payer_prediction import (
    build_payer_prediction_for_claim,
    run_payer_temporal_backtest,
)
from backend.prediction_engine_v2 import build_payer_prediction_for_claim_v2


def claim(claim_id, member_id, service_date, paid, *, allowed=None, family="E11", payer="P1",
          provider="NPI1", pos="11", cpt="99214", units=1, historical=False):
    return {
        "workbookFields": {
            "Claim_ID": claim_id,
            "Member_ID": member_id,
            "Service_Date_From": service_date,
            "ICD10_Family": family,
            "ICD10_Diagnosis_Code": f"{family}.9",
            "Paid_Amount": paid,
            "Allowed_Amount": paid if allowed is None else allowed,
            "Payer_ID": payer,
            "Payer_Name": payer,
            "Billing_Provider_NPI": provider,
            "Billing_Provider_Name": provider,
            "Place_of_Service_Code": pos,
            "CPT_Code": cpt,
            "Units": units,
            "Is_Historical_Reference_Record": "Y" if historical else "N",
        }
    }


class Database:
    def __init__(self, selectable, historical=()):
        self.workbook_hash = f"authoritative-{id(self)}"
        self.selectable_claims = tuple(selectable)
        self.claims = (*self.selectable_claims, *historical)

    def find_claim(self, claim_id, selectable_only=True):
        rows = self.selectable_claims if selectable_only else self.claims
        return next((row for row in rows if row["workbookFields"]["Claim_ID"] == claim_id), None)


class AuthoritativePayerEngineTests(unittest.TestCase):
    def test_paid_amount_drives_savings_not_allowed_amount(self):
        database = Database(
            [claim("TARGET", "M1", "2026-05-01", 300, allowed=900)],
            [claim("PEER", "M2", "2026-04-01", 100, allowed=1, historical=True)],
        )
        result = build_payer_prediction_for_claim(database, "TARGET")
        self.assertEqual(result["target"]["payer_spend"], 300.0)
        self.assertEqual(result["calculation_summary"]["payer_spend_reduction_opportunity"], 200.0)

    def test_strict_cutoff_excludes_future_peer_claims(self):
        database = Database(
            [claim("TARGET", "M1", "2026-05-01", 300)],
            [
                claim("PAST", "M2", "2026-04-01", 200, historical=True),
                claim("FUTURE", "M3", "2026-06-01", 1, historical=True),
            ],
        )
        result = build_payer_prediction_for_claim(database, "TARGET")
        evidence_ids = {row["claim_id"] for row in result["supporting_evidence"]}
        self.assertIn("PAST", evidence_ids)
        self.assertNotIn("FUTURE", evidence_ids)
        self.assertTrue(all(
            row["service_date"] <= result["calculation_trace"]["prediction_cutoff"]
            for row in result["supporting_evidence"]
            if row["evidence_role"] != "Target Episode"
        ))

    def test_historical_reference_cannot_be_selected_as_current_target(self):
        database = Database(
            [claim("REFERENCE", "M1", "2026-05-01", 300, historical=True)],
            [claim("PEER", "M2", "2026-04-01", 100, historical=True)],
        )
        with self.assertRaises(KeyError):
            build_payer_prediction_for_claim(database, "REFERENCE")

    def test_no_cohort_returns_an_unavailable_result_not_a_zero_prediction(self):
        database = Database([claim("TARGET", "M1", "2026-05-01", 300)])
        result = build_payer_prediction_for_claim(database, "TARGET")
        self.assertFalse(result["available"])
        self.assertEqual(result["scenario_selection"]["selected"]["number"], 0)
        self.assertEqual(result["peer_members_used"], [])
        self.assertNotIn("predicted_payer_avoidable_spend", result["calculation_summary"])
        self.assertFalse(result["procedure_comparison"]["available"])
        self.assertIn("different-member", result["procedure_comparison"]["reason"])

    def test_procedure_comparison_uses_one_different_member_and_prior_claim_history(self):
        database = Database(
            [
                claim("TARGET_PRIOR", "M1", "2026-04-15", 100),
                claim("TARGET", "M1", "2026-05-01", 300),
                claim("PEER_PRIOR", "M2", "2026-03-15", 90),
            ],
            [
                claim("PEER_VISIT", "M2", "2026-04-01", 100, historical=True),
            ],
        )
        comparison = build_payer_prediction_for_claim(database, "TARGET")["procedure_comparison"]

        self.assertTrue(comparison["available"])
        self.assertEqual(comparison["history_window_days"], 90)
        self.assertEqual(comparison["target"]["claim"]["claim_id"], "TARGET")
        self.assertEqual(comparison["target"]["claim"]["member_id"], "M1")
        self.assertEqual(comparison["peer"]["claim"]["member_id"], "M2")
        self.assertNotEqual(
            comparison["target"]["claim"]["member_id"],
            comparison["peer"]["claim"]["member_id"],
        )
        self.assertEqual(comparison["peer"]["claim"]["claim_id"], "PEER_VISIT")
        self.assertEqual(
            [item["claim_id"] for item in comparison["target"]["prior_services"]],
            ["TARGET_PRIOR"],
        )
        self.assertEqual(comparison["target"]["prior_services_total_paid"], 100.0)
        self.assertEqual(
            [item["claim_id"] for item in comparison["peer"]["prior_services"]],
            ["PEER_PRIOR"],
        )
        self.assertEqual(comparison["peer"]["prior_services_total_paid"], 90.0)
        self.assertEqual(
            [item["claim_id"] for item in comparison["peer"]["comparison_records"]],
            ["PEER_PRIOR"],
        )
        self.assertEqual(comparison["peer"]["claim"]["paid_amount"], 100.0)
        self.assertIn("Exact billing-code match", comparison["matches"]["procedure"])

    def test_procedure_comparison_excludes_unrelated_prior_bills(self):
        database = Database(
            [
                claim("TARGET_PRIOR", "M1", "2026-04-15", 100, family="N39"),
                claim("TARGET", "M1", "2026-05-01", 300, family="N39"),
                claim("TARGET_UNRELATED", "M1", "2026-04-20", 500, family="I10", cpt="90853"),
                claim("PEER_PRIOR", "M2", "2026-03-15", 90, family="N39"),
                claim("PEER_UNRELATED", "M2", "2026-03-20", 25, family="E11", cpt="80053"),
            ],
            [claim("PEER_VISIT", "M2", "2026-04-01", 100, family="N39", historical=True)],
        )

        comparison = build_payer_prediction_for_claim(database, "TARGET")["procedure_comparison"]

        self.assertEqual(
            [item["claim_id"] for item in comparison["target"]["prior_services"]],
            ["TARGET_PRIOR"],
        )
        self.assertEqual(
            [item["claim_id"] for item in comparison["peer"]["prior_services"]],
            ["PEER_PRIOR"],
        )

    def test_exactly_one_scenario_controls_peer_evidence(self):
        database = Database(
            [claim("TARGET", "M1", "2026-05-01", 300)],
            [
                claim("STRICT", "M2", "2026-04-01", 200, historical=True),
                claim("BROAD", "M3", "2026-04-01", 1, payer="P2", provider="NPI2", pos="22", cpt="80053", historical=True),
            ],
        )
        result = build_payer_prediction_for_claim(database, "TARGET")
        self.assertEqual(result["scenario_selection"]["selected"]["number"], 1)
        self.assertEqual({peer["member_id"] for peer in result["peer_members_used"]}, {"M2"})
        self.assertNotIn("BROAD", {row["claim_id"] for row in result["supporting_evidence"]})

    def test_equal_claim_counts_can_have_payer_spend_reduction(self):
        database = Database(
            [claim("TARGET", "M1", "2026-05-01", 300)],
            [claim("PEER", "M2", "2026-04-01", 100, historical=True)],
        )
        calculation = build_payer_prediction_for_claim(database, "TARGET")["calculation_summary"]
        self.assertEqual(calculation["excess_claim_count"], 0.0)
        self.assertEqual(calculation["utilisation_reduction_opportunity"], 0.0)
        self.assertEqual(calculation["payer_spend_reduction_opportunity"], 200.0)
        self.assertEqual(calculation["predicted_payer_avoidable_spend"], 200.0)

    def test_two_visit_comparison_amount_uses_only_displayed_paid_amounts(self):
        database = Database(
            [claim("TARGET", "M1", "2026-05-01", 300)],
            [claim("PEER", "M2", "2026-04-01", 100, historical=True)],
        )

        comparison = build_payer_prediction_for_claim(database, "TARGET")["procedure_comparison"]
        prediction = comparison["comparison_prediction"]

        self.assertEqual(prediction["basis"], "Paid_Amount")
        self.assertEqual(prediction["target_visit_paid"], 300.0)
        self.assertEqual(prediction["peer_visit_paid"], 100.0)
        self.assertEqual(prediction["possible_payer_spend_difference"], 200.0)
        self.assertEqual(prediction["confirmed_savings"], 0.0)
        self.assertEqual(prediction["formula"], "$300.00 - $100.00 = $200.00")
        self.assertEqual(
            prediction["source_rows"],
            [
                {"role": "This person's visit", "claim_id": "TARGET", "field": "Paid_Amount", "value": 300.0},
                {"role": "Other person's matching visit", "claim_id": "PEER", "field": "Paid_Amount", "value": 100.0},
            ],
        )

    def test_multi_claim_episode_uses_proportional_claim_attribution(self):
        database = Database(
            [
                claim("TARGET_A", "M1", "2026-05-01", 100),
                claim("TARGET_B", "M1", "2026-05-02", 300),
            ],
            [claim("PEER", "M2", "2026-04-01", 100, historical=True)],
        )
        result = build_payer_prediction_for_claim(database, "TARGET_A")
        self.assertEqual(result["target"]["payer_spend"], 400.0)
        self.assertEqual(result["selected_claim"]["episode_spend_share"], 0.25)
        self.assertEqual(result["selected_claim"]["attributed_payer_avoidable_spend"], 75.0)

    def test_legacy_v2_entry_point_is_only_a_canonical_wrapper(self):
        database = Database(
            [claim("TARGET", "M1", "2026-05-01", 300)],
            [claim("PEER", "M2", "2026-04-01", 100, historical=True)],
        )
        self.assertEqual(
            build_payer_prediction_for_claim_v2(database, "TARGET"),
            build_payer_prediction_for_claim(database, "TARGET"),
        )

    def test_backtest_reports_the_paid_amount_three_scenario_method(self):
        database = Database(
            [claim("TARGET", "M1", "2026-05-01", 300)],
            [claim("PEER", "M2", "2026-04-01", 100, historical=True)],
        )
        report = run_payer_temporal_backtest(database)
        self.assertEqual(report["method"], "HISTORICAL_THREE_SCENARIO_PAID_AMOUNT_BACKTEST")
        self.assertFalse(report["future_data_leakage_found"])
        self.assertNotIn("savings_prediction_accuracy", report)


if __name__ == "__main__":
    unittest.main()
