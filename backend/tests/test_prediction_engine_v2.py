"""Focused unit tests for the Phase 3-9 / Phase 11 payer prediction engine.

Uses a lightweight in-memory cohort (no Excel workbook) so the suite stays fast.
"""

import unittest

from backend import prediction_engine_v2 as engine
from backend.prediction_engine_v2 import (
    build_member_payer_prediction,
    build_payer_prediction_for_claim_v2,
    run_payer_temporal_backtest,
    similarity_score,
)


def mk(claim_id, member_id, service_date, allowed, paid=None, *, icd="E11.9", payer="P1",
       provider="NPI1", pos="11", cpt="99213", filing="F", description="Type 2 Diabetes"):
    return {
        "workbookFields": {
            "Claim_ID": claim_id,
            "Member_ID": member_id,
            "Service_Date_From": service_date,
            "Allowed_Amount": allowed,
            "Paid_Amount": paid if paid is not None else allowed,
            "ICD10_Diagnosis_Code": icd,
            "ICD10_Diagnosis_Description": description,
            "Payer_Name": payer,
            "Billing_Provider_NPI": provider,
            "Place_of_Service_Code": pos,
            "CPT_Code": cpt,
            "CPT_Description": "service",
            "Claim_Filing_Indicator": filing,
            "Units": 1,
        }
    }


class CohortDatabase:
    _counter = 0

    def __init__(self, claims):
        type(self)._counter += 1
        self.workbook_hash = f"focused-v2-engine-{type(self)._counter}"
        self.claims = tuple(claims)
        self.selectable_claims = tuple(claims)

    def find_claim(self, claim_id, selectable_only=True):
        normalized = str(claim_id).replace("-", "").upper()
        source = self.selectable_claims if selectable_only else self.claims
        for claim in source:
            key = str(claim["workbookFields"]["Claim_ID"]).replace("-", "").upper()
            if key == normalized:
                return claim
        return None


def focused_database(*, peers=True):
    target_claims = [
        mk("TR01", "TARGET", "2026-01-01", 300, cpt="99213"),
        mk("TR02", "TARGET", "2026-01-10", 160, cpt="80053"),
        mk("TR03", "TARGET", "2026-02-15", 180, cpt="80053"),
    ]
    historical = []
    if peers:
        for index in range(5):
            historical.extend([
                mk(f"PH{index}1", f"PEER{index}", f"2025-{str(index + 1).zfill(2)}-01", 100 + 10 * index, cpt="99213"),
                mk(f"PH{index}2", f"PEER{index}", f"2025-{str(index + 1).zfill(2)}-15", 60 + 10 * index, cpt="80053"),
            ])
    return CohortDatabase([*target_claims, *historical])


class PayerEngineTests(unittest.TestCase):
    def test_claim_anchored_payload_contains_plan_sections(self):
        result = build_payer_prediction_for_claim_v2(focused_database(), "TR01")
        for key in ("member", "observation", "target", "historical_cohort", "utilization",
                    "cpt_analysis", "timeline", "care_pattern", "potential_savings",
                    "evidence", "validation", "confidence"):
            self.assertIn(key, result)
        self.assertGreaterEqual(result["potential_savings"]["median_opportunity"], 0.0)
        self.assertIn(result["confidence"]["level"], ("HIGH", "MEDIUM", "LOW"))

    def test_observation_window_is_configurable(self):
        for days in (7, 30, 90, 180, 365):
            result = build_payer_prediction_for_claim_v2(focused_database(), "TR01", observation_days=days)
            self.assertEqual(result["observation"]["days"], days)

    def test_similarity_weights_default_and_configurable(self):
        weights = engine._similarity_weights()
        self.assertAlmostEqual(sum(weights.values()), 1.0, places=6)
        self.assertIn("icd10_weight", weights)
        database = focused_database()
        target = database.claims[:3]
        member = database.claims[3:]
        score, reasons = similarity_score(target, member, weights)
        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 100.0)
        self.assertIsInstance(reasons, list)

    def test_savings_guardrail_clamps_to_zero(self):
        claims = [mk("LOW-1", "TARGET", "2026-01-01", 40, cpt="99213")]
        peers = [mk(f"P{i}-1", f"H{i}", f"2025-{str(i).zfill(2)}-01", 200, cpt="99213") for i in range(1, 4)]
        database = CohortDatabase([*claims, *peers])
        result = build_payer_prediction_for_claim_v2(database, "LOW-1")
        self.assertEqual(result["potential_savings"]["median_opportunity"], 0.0)
        self.assertEqual(result["utilization"]["excess_claims"], 0)

    def test_member_anchored_entry_point(self):
        result = build_member_payer_prediction(focused_database(), "TARGET")
        self.assertEqual(result["member"]["member_id"], "TARGET")
        self.assertEqual(result["member"]["condition"]["icd10"], "E11.9")

    def test_level_three_group_fallback_when_no_exact_peers(self):
        target = [mk("GRP-1", "TARGET", "2026-01-01", 300, icd="E11.9", cpt="99213")]
        peers = []
        for index in range(11):
            peers.extend([
                mk(f"G{index}-1", f"HM{index}", f"2025-{str(index + 1).zfill(2)}-01", 100, icd="E11.0", cpt="99213"),
                mk(f"G{index}-2", f"HM{index}", f"2025-{str(index + 1).zfill(2)}-20", 80, icd="E11.0", cpt="80053"),
            ])
        database = CohortDatabase([*target, *peers])
        result = build_payer_prediction_for_claim_v2(database, "GRP-1")
        self.assertEqual(result["historical_cohort"]["selection_level"], "ICD10_FAMILY")
        self.assertGreaterEqual(result["historical_cohort"]["members"], 1)
        self.assertEqual(result["member"]["condition"]["icd10_family"], "E11")

    def test_group_level_when_family_sparse(self):
        target = [mk("G2-1", "TARGET", "2026-01-01", 300, icd="E11.9", cpt="99213")]
        peers = [
            mk(f"A{i}-1", f"HA{i}", f"2025-{str(i).zfill(2)}-01", 100, icd="E10.9", cpt="99213") for i in range(1, 12)
        ]
        database = CohortDatabase([*target, *peers])
        result = build_payer_prediction_for_claim_v2(database, "G2-1")
        self.assertIn(result["historical_cohort"]["selection_level"],
                      ("EXACT_ICD10", "ICD10_FAMILY", "CONDITION_GROUP"))
        self.assertEqual(result["member"]["condition"]["condition_group"], "DIABETES")

    def test_no_historical_peer_raises_value_error(self):
        database = focused_database(peers=False)
        with self.assertRaises(ValueError):
            build_payer_prediction_for_claim_v2(database, "TR01")

    def test_timeline_and_care_pattern_are_built(self):
        result = build_payer_prediction_for_claim_v2(focused_database(), "TR01")
        self.assertGreaterEqual(len(result["timeline"]), 1)
        self.assertIn("journey", result["care_pattern"])
        self.assertIn("days_since_prior", result["timeline"][0])
        self.assertTrue(result["care_pattern"]["steps"])

    def test_claim_level_attribution_is_sorted_descending(self):
        result = build_payer_prediction_for_claim_v2(focused_database(), "TR01")
        attributions = result["potential_savings"]["claim_level_attribution"]
        self.assertTrue(attributions)
        contributions = [item["contribution"] for item in attributions]
        self.assertEqual(contributions, sorted(contributions, reverse=True))
        self.assertIn("claim_id", attributions[0])

    def test_temporal_backtest_reports_metrics(self):
        result = run_payer_temporal_backtest(focused_database())
        self.assertEqual(result["method"], "TEMPORAL_HOLDOUT")
        self.assertIn("sample_members", result)
        self.assertIn("savings_prediction_accuracy", result)


if __name__ == "__main__":
    unittest.main()