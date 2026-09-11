import unittest

from backend.claim_patterns import (
    SIMILARITY_WEIGHTS,
    historical_patterns,
    select_peers,
    short_timeframe_patterns,
    similarity,
)
from backend.financial_engine import build_financial_result
from backend.workbook_enrichment import load_workbook_database


class PredictionSnapshotDesignTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database = load_workbook_database()
        cls.claim = cls.database.selectable_claims[0]
        cls.result = build_financial_result(cls.database, cls.claim["claimId"])

    def test_canonical_response_sections_exist(self):
        required = {
            "claim_facts", "historical_patterns", "financial_prediction",
            "similar_historical_claims", "short_timeframe_patterns",
            "supported_financial_opportunities", "historical_prediction_basis",
            "rag_evidence", "prediction_explanation", "limitations",
        }
        self.assertTrue(required.issubset(self.result))

    def test_peer_selection_uses_icd_and_cpt_and_cutoff(self):
        peers, basis = select_peers(self.database, self.claim)
        self.assertIn("ICD family", basis["matching_dimensions"])
        self.assertTrue(basis["peer_level"] == 9 or "CPT" in basis["matching_dimensions"])
        self.assertTrue(all(row["dos"] < self.claim["dos"] for row in peers))
        self.assertEqual(basis["peer_count"], len(basis["claim_ids_used"]))

    def test_similarity_is_dynamic_and_explained(self):
        earlier = [row for row in self.database.claims if row["dos"] < self.claim["dos"]][:2]
        scores = [similarity(row, self.claim) for row in earlier]
        self.assertEqual(sum(SIMILARITY_WEIGHTS.values()), 100)
        self.assertTrue(all(item["match_reason"] for item in scores))
        if len(scores) == 2 and earlier[0] != earlier[1]:
            self.assertIsInstance(scores[0]["similarity_score"], float)

    def test_historical_time_horizons_are_exact(self):
        patterns = historical_patterns(self.database, self.claim)
        pairs = short_timeframe_patterns(self.database, self.claim)
        for horizon in (3, 7, 30, 60, 90):
            self.assertEqual(patterns[f"within_{horizon}_days"], sum(pair["days_apart"] <= horizon for pair in pairs))

    def test_financial_ranges_expose_rates_and_claim_ids(self):
        snapshot = self.result["financial_prediction_snapshot"]
        for key in ("predicted_allowed", "predicted_provider_payment", "predicted_patient_responsibility", "predicted_contractual_adjustment"):
            value = snapshot[key]
            self.assertIn("historical_rate", value)
            self.assertEqual(value["peer_count"], len(value["claim_ids_used"]))
            self.assertLessEqual(value["low"], value["value"])
            self.assertLessEqual(value["value"], value["high"])

    def test_denial_and_repeat_evidence_is_transparent(self):
        snapshot = self.result["financial_prediction_snapshot"]
        denial = snapshot["denial_prediction_basis"]
        for key in ("local_numerator", "local_denominator", "local_rate", "external_numerator", "external_denominator", "external_rate", "prior_strength", "final_blended_probability", "evidence_claims"):
            self.assertIn(key, denial)
        for horizon in ("30d", "60d", "90d"):
            evidence = snapshot["repeat_probability_evidence"][horizon]
            self.assertIn("blend_weights", evidence)
            self.assertIn("final_probability", evidence)

    def test_avoidable_and_exposure_formulas_remain_separate(self):
        snapshot = self.result["financial_prediction_snapshot"]
        predicted = snapshot["predicted_avoidable_spend"]
        self.assertEqual(predicted["value"], round(predicted["repeat_probability_90d"] * predicted["avoidable_given_repeat_probability"] * predicted["expected_extra_repeat_allowed_cost"], 2))
        self.assertEqual(snapshot["future_denial_exposure"]["value"], round(snapshot["denial_probability"] * snapshot["predicted_provider_payment"]["value"], 2))
        self.assertEqual(snapshot["predicted_repeat_payment_exposure"], round(snapshot["repeat_probability_90d"] * snapshot["predicted_provider_payment"]["value"], 2))
        self.assertIsNot(self.result["predicted_avoidable_spend"], self.result["validated_avoidable_spend"])

    def test_scenario_map_has_required_nonclinical_path(self):
        titles = [section["title"] for section in self.result["scenario_map"]["sections"]]
        self.assertEqual(titles, ["Member History", "Current Claim", "ICD + CPT Relationship", "Similar Historical Claims", "Short-Timeframe / Repeat Pattern", "Financial Prediction", "Financial Opportunity", "Supporting Evidence", "Best Provider Action"])
        self.assertNotIn("treatment failed", str(self.result).lower())


if __name__ == "__main__":
    unittest.main()
