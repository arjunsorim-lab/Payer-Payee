import unittest
from pathlib import Path

from backend.app import app, configured_workbook_database
from backend.payer_prediction import (
    _claim_scenario_match,
    _rolling_episodes,
    build_member_payer_cohort_summary,
    build_payer_cohort_portfolio_summary,
    build_payer_prediction_for_claim,
)


ROOT = Path(__file__).resolve().parents[2]


def cohort_claim(claim_id, member_id, service_date, paid, *, family="E11", payer="P1", provider="NPI1", pos="11", cpt="99214", units=1, historical=False):
    return {
        "workbookFields": {
            "Claim_ID": claim_id,
            "Member_ID": member_id,
            "Service_Date_From": service_date,
            "Paid_Amount": paid,
            "ICD10_Family": family,
            "ICD10_Diagnosis_Code": f"{family}.9",
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


class CohortDatabase:
    _counter = 0

    def __init__(self, selectable, historical):
        type(self)._counter += 1
        self.workbook_hash = f"focused-payer-cohort-test-{type(self)._counter}"
        self.selectable_claims = tuple(selectable)
        self.historical_claims = tuple(historical)
        self.claims = (*self.selectable_claims, *self.historical_claims)

    def find_claim(self, claim_id, selectable_only=True):
        normalized = str(claim_id).replace("-", "").upper()
        source = self.selectable_claims if selectable_only else self.claims
        return next((row for row in source if row["workbookFields"]["Claim_ID"].replace("-", "").upper() == normalized), None)


def scenario_database(*, peer_payer="P1", peer_provider="NPI1", peer_pos="11", peer_cpt="99214", peer_units=1, target_claims=3, peer_claims=2):
    selectable = [
        cohort_claim(f"TARGET{index}", "TARGET_MEMBER", f"20260{5 + index}01", 300)
        for index in range(target_claims)
    ]
    historical = [
        cohort_claim(f"PEER{index}", "PEER_MEMBER", f"20260{2 + index}01", 100 + 100 * index, payer=peer_payer, provider=peer_provider, pos=peer_pos, cpt=peer_cpt, units=peer_units, historical=True)
        for index in range(peer_claims)
    ]
    return CohortDatabase(selectable, historical)


class PayerPredictionModalApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = app.test_client()

    def _first_generatable_option(self):
        options = self.client.get("/api/predictions/payer/options")
        self.assertEqual(options.status_code, 200)
        for member in options.get_json()["members"]:
            for disease in member["diseases"]:
                for episode in disease["episodes"]:
                    payload = {
                        "member_id": member["member_id"],
                        "diagnosis_family": disease["family"],
                        "comparison_episode_id": episode["episode_id"],
                    }
                    response = self.client.post("/api/predictions/payer/generate", json=payload)
                    if response.status_code == 200:
                        return payload, response.get_json()
        self.fail("No payer prediction option could produce a result")

    def test_options_are_member_specific_and_episode_scoped(self):
        response = self.client.get("/api/predictions/payer/options")
        self.assertEqual(response.status_code, 200)
        members = response.get_json()["members"]
        self.assertTrue(members)
        for member in members:
            self.assertTrue(member["member_id"])
            self.assertTrue(member["diseases"])
            for disease in member["diseases"]:
                self.assertTrue(disease["family"])
                for episode in disease["episodes"]:
                    self.assertIn("-90D", episode["episode_id"])

    def test_result_excludes_target_from_peers_and_obeys_formula(self):
        payload, result = self._first_generatable_option()
        peers = result["peer_members_used"]
        self.assertTrue(peers)
        self.assertNotIn(payload["member_id"], {peer["member_id"] for peer in peers})
        self.assertEqual(len(peers), len({peer["member_id"] for peer in peers}))
        calculation = result["calculation_summary"]
        self.assertEqual(
            calculation["predicted_payer_avoidable_spend"],
            min(
                result["benchmark_summary"]["target_payer_spend"],
                max(
                    calculation["utilisation_reduction_opportunity"],
                    calculation["payer_spend_reduction_opportunity"],
                ),
            ),
        )
        self.assertTrue({
            "target", "scenario_selection", "benchmark_summary", "peer_members_used",
            "calculation_summary", "supporting_evidence", "calculation_trace",
        }.issubset(result))

    def test_missing_inputs_are_not_reported_as_zero(self):
        response = self.client.post("/api/predictions/payer/generate", json={})
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("calculation_summary", response.get_json())


class ClaimAnchoredPayerPopupTests(unittest.TestCase):
    def setUp(self):
        self.strict_result = build_payer_prediction_for_claim(scenario_database(), "TARGET0")

    def test_01_selected_claim_determines_target_member(self):
        self.assertEqual(self.strict_result["target"]["member_id"], "TARGET_MEMBER")

    def test_02_selected_claim_determines_icd10_family(self):
        self.assertEqual(self.strict_result["target"]["diagnosis_family"], "E11")

    def test_03_comparison_episode_contains_selected_claim(self):
        target_ids = {row["claim_id"] for row in self.strict_result["supporting_evidence"] if row["evidence_role"] == "Target Episode"}
        self.assertIn("TARGET0", target_ids)
        self.assertEqual(self.strict_result["target"]["claim_id"], "TARGET0")

    def test_04_target_member_never_appears_as_external_peer(self):
        self.assertNotIn("TARGET_MEMBER", {peer["member_id"] for peer in self.strict_result["peer_members_used"]})

    def test_05_scenario_1_requires_strict_matching(self):
        self.assertEqual(self.strict_result["scenario_selection"]["selected"]["number"], 1)
        target = {"member_id": "A", "diagnosis_family": "E11", "selected_identity": {"payer_id": "P1", "provider": "N1", "pos": "11", "cpt": "99214", "procedure_family": "992", "units": 1}}
        peer = {"member_id": "B", "diagnosis_family": "E11", "anchor_identities": [{"payer_id": "P1", "provider": "DIFFERENT", "pos": "11", "cpt": "99214", "procedure_family": "992", "units": 1}]}
        self.assertFalse(_claim_scenario_match(target, peer, 1))

    def test_06_scenario_2_requires_same_disease_and_payer(self):
        result = build_payer_prediction_for_claim(scenario_database(peer_provider="OTHER"), "TARGET0")
        selected = result["scenario_selection"]["selected"]
        self.assertEqual((selected["number"], selected["name"]), (2, "Same ICD-10 Family + Same Payer"))

    def test_07_scenario_3_requires_same_disease(self):
        result = build_payer_prediction_for_claim(scenario_database(peer_payer="OTHER", peer_provider="OTHER"), "TARGET0")
        selected = result["scenario_selection"]["selected"]
        self.assertEqual((selected["number"], selected["name"]), (3, "Same ICD-10 Family Only"))

    def test_08_strongest_valid_scenario_is_selected(self):
        self.assertEqual(self.strict_result["scenario_selection"]["selected"]["number"], 1)
        self.assertIn("Scenario 1 selected", self.strict_result["scenario_selection"]["selected"]["reason"])
        self.assertEqual(self.strict_result["calculation_summary"]["confidence"]["level"], "Low")

    def test_08a_scenario_reasons_explain_required_and_non_qualifying_fields(self):
        scenario_two = build_payer_prediction_for_claim(scenario_database(peer_provider="OTHER"), "TARGET0")
        self.assertIn("provider, CPT or procedure family, POS, and units may differ", scenario_two["scenario_selection"]["selected"]["reason"])

        scenario_three = build_payer_prediction_for_claim(
            scenario_database(peer_payer="OTHER", peer_provider="OTHER"),
            "TARGET0",
        )
        self.assertIn("payer, provider, CPT or procedure family, POS, and units may differ", scenario_three["scenario_selection"]["selected"]["reason"])

    def test_09_paid_amount_is_used_as_payer_spend(self):
        self.assertEqual(self.strict_result["target"]["payer_spend"], 900.0)

    def test_10_benchmark_uses_lower_utilisation_peers(self):
        extra = [
            cohort_claim(f"P3{index}", "PEER_THREE", f"20260{2 + index}02", 250, historical=True)
            for index in range(3)
        ]
        database = scenario_database(target_claims=5)
        database = CohortDatabase(database.selectable_claims, (*database.historical_claims, *extra))
        result = build_payer_prediction_for_claim(database, "TARGET0")
        benchmark_roles = {peer["member_id"] for peer in result["peer_members_used"] if "Lower-Utilisation" in peer["benchmark_role"]}
        self.assertIn("PEER_MEMBER", benchmark_roles)
        self.assertLessEqual(result["benchmark_summary"]["utilisation_benchmark_claim_count"], 3)

    def test_11_count_based_estimate_is_correct(self):
        calculation = self.strict_result["calculation_summary"]
        self.assertEqual(calculation["utilisation_reduction_opportunity"], calculation["excess_claim_count"] * calculation["median_peer_paid_per_claim"])

    def test_12_cost_based_estimate_is_correct(self):
        calculation = self.strict_result["calculation_summary"]
        expected = max(self.strict_result["target"]["payer_spend"] - calculation["lower_spend_benchmark"], 0)
        self.assertEqual(calculation["payer_spend_reduction_opportunity"], expected)

    def test_13_final_prediction_uses_stronger_opportunity_with_target_spend_cap(self):
        calculation = self.strict_result["calculation_summary"]
        self.assertEqual(
            calculation["predicted_payer_avoidable_spend"],
            min(
                self.strict_result["target"]["payer_spend"],
                max(
                    calculation["utilisation_reduction_opportunity"],
                    calculation["payer_spend_reduction_opportunity"],
                ),
            ),
        )

    def test_14_range_uses_peer_q25_and_q75_and_contains_prediction(self):
        calculation = self.strict_result["calculation_summary"]
        self.assertEqual((calculation["q25_peer_paid_per_claim"], calculation["q75_peer_paid_per_claim"]), (125.0, 175.0))
        self.assertLessEqual(calculation["range"]["low"], calculation["predicted_payer_avoidable_spend"])
        self.assertLessEqual(calculation["predicted_payer_avoidable_spend"], calculation["range"]["high"])

    def test_15_equal_claim_counts_do_not_erase_lower_spend_opportunity(self):
        result = build_payer_prediction_for_claim(scenario_database(target_claims=2, peer_claims=2), "TARGET0")
        calculation = result["calculation_summary"]
        self.assertEqual(calculation["excess_claim_count"], 0.0)
        self.assertEqual(calculation["utilisation_reduction_opportunity"], 0.0)
        self.assertGreater(calculation["payer_spend_reduction_opportunity"], 0.0)
        self.assertGreater(calculation["predicted_payer_avoidable_spend"], 0.0)
        self.assertFalse(calculation["zero_reason"])

    def test_genuine_zero_requires_both_opportunities_to_be_zero(self):
        database = CohortDatabase(
            [cohort_claim("TARGET_ZERO", "TARGET_MEMBER", "20260501", 100)],
            [cohort_claim("PEER_HIGHER", "PEER_MEMBER", "20260401", 200, historical=True)],
        )
        calculation = build_payer_prediction_for_claim(database, "TARGET_ZERO")["calculation_summary"]
        self.assertEqual(calculation["utilisation_reduction_opportunity"], 0.0)
        self.assertEqual(calculation["payer_spend_reduction_opportunity"], 0.0)
        self.assertEqual(calculation["predicted_payer_avoidable_spend"], 0.0)
        self.assertTrue(calculation["zero_reason"])

    def test_same_family_broad_peer_is_low_confidence(self):
        result = build_payer_prediction_for_claim(
            scenario_database(peer_payer="OTHER", peer_provider="OTHER", peer_pos="22", peer_cpt="80050"),
            "TARGET0",
        )
        confidence = result["calculation_summary"]["confidence"]
        self.assertEqual(result["scenario_selection"]["selected"]["number"], 3)
        self.assertEqual(confidence["level"], "Low")
        self.assertTrue(any("differed" in reason for reason in confidence["penalties"]))

    def test_final_prediction_never_exceeds_actual_target_payer_spend(self):
        database = CohortDatabase(
            [
                cohort_claim("TARGET_CAP_1", "TARGET_MEMBER", "20260501", 10),
                cohort_claim("TARGET_CAP_2", "TARGET_MEMBER", "20260502", 10),
                cohort_claim("TARGET_CAP_3", "TARGET_MEMBER", "20260503", 10),
            ],
            [cohort_claim("PEER_CAP", "PEER_MEMBER", "20260401", 100, historical=True)],
        )
        result = build_payer_prediction_for_claim(database, "TARGET_CAP_1")
        self.assertLessEqual(
            result["calculation_summary"]["predicted_payer_avoidable_spend"],
            result["target"]["payer_spend"],
        )

    def test_popup_displays_utilisation_and_payer_spend_opportunities_separately(self):
        source = (ROOT / "frontend/src/App.jsx").read_text()
        popup = source[source.index("function CanonicalClaimPayerPredictionResult"):source.index("function LegacyClaimPayerPredictionResult")]
        self.assertIn("calculation.utilisation_reduction_opportunity", popup)
        self.assertIn("calculation.payer_spend_reduction_opportunity", popup)
        self.assertIn("calculation.lower_spend_benchmark", popup)

    def test_current_claim_regression_uses_dynamic_lower_spend_peer_episodes(self):
        result = build_payer_prediction_for_claim(configured_workbook_database(), "CLM00001092")
        calculation = result["calculation_summary"]
        self.assertEqual(result["scenario_selection"]["selected"]["number"], 3)
        self.assertEqual(calculation["excess_claim_count"], 0.0)
        self.assertEqual(calculation["utilisation_reduction_opportunity"], 0.0)
        self.assertEqual(calculation["lower_spend_benchmark"], 136.35)
        self.assertEqual(calculation["payer_spend_reduction_opportunity"], 159.42)
        self.assertEqual(calculation["predicted_payer_avoidable_spend"], 159.42)
        self.assertEqual(calculation["confidence"]["level"], "Low")

    def test_lower_spend_benchmark_claims_are_traceable_to_workbook_rows(self):
        database = configured_workbook_database()
        result = build_payer_prediction_for_claim(database, "CLM00001092")
        workbook_claim_ids = {
            row["workbookFields"]["Claim_ID"]
            for row in database.claims
        }
        benchmark_evidence = [
            row for row in result["supporting_evidence"]
            if row["evidence_role"] == "Lower-Spend Benchmark Evidence"
        ]
        self.assertTrue(benchmark_evidence)
        self.assertTrue(all(row["claim_id"] in workbook_claim_ids for row in benchmark_evidence))

    def test_16_supporting_evidence_matches_calculation_peers(self):
        peer_ids = {peer["member_id"] for peer in self.strict_result["peer_members_used"]}
        evidence_ids = {row["member_id"] for row in self.strict_result["supporting_evidence"] if row["evidence_role"] != "Target Episode"}
        self.assertEqual(peer_ids, evidence_ids)

    def test_17_historical_rows_are_not_current_target_claims(self):
        target_claim_ids = {row["claim_id"] for row in self.strict_result["supporting_evidence"] if row["evidence_role"] == "Target Episode"}
        self.assertFalse(any(claim_id.startswith("PEER") for claim_id in target_claim_ids))

    def test_18_react_performs_no_financial_calculation(self):
        source = (ROOT / "frontend/src/App.jsx").read_text()
        popup = source[source.index("function CanonicalClaimPayerPredictionResult"):source.index("function LegacyClaimPayerPredictionResult")]
        self.assertNotIn("benchmark.target_payer_spend -", popup)
        self.assertNotIn("count_based_excess_spend =", popup)
        self.assertIn("calculation.predicted_payer_avoidable_spend", popup)

    def test_19_ollama_performs_no_popup_calculation(self):
        source = (ROOT / "backend/payer_prediction.py").read_text()
        start = source.index("def build_payer_prediction_for_claim")
        claim_engine = source[start:source.index("def _episode_anchor_claim_id", start)]
        self.assertNotIn("ollama", claim_engine.lower())
        self.assertNotIn("retrieve_evidence", claim_engine.lower())

    def test_20_popup_contains_only_four_requested_sections(self):
        source = (ROOT / "frontend/src/App.jsx").read_text()
        popup = source[source.index("function CanonicalClaimPayerPredictionResult"):source.index("function LegacyClaimPayerPredictionResult")]
        self.assertEqual(popup.count('<section className="payer-result-section">'), 4)
        for title in ("Benchmark Summary", "Peer Members Used", "Prediction Range / Calculation Summary", "Supporting Evidence"):
            self.assertIn(f"<h3>{title}</h3>", popup)
        for removed in ("Claim Facts", "Financial Prediction Snapshot", "Expected Avoidable Repeat Cost", "Future Denial Exposure"):
            self.assertNotIn(removed, popup)

    def test_21_every_selectable_claim_can_open_dynamically(self):
        database = configured_workbook_database()
        for claim in database.selectable_claims:
            result = build_payer_prediction_for_claim(database, claim["claimId"])
            self.assertIn(result["available"], (True, False))
            selected = result["scenario_selection"]["selected"]
            if result["available"]:
                self.assertIn(selected["number"], (1, 2, 3))
            else:
                self.assertEqual(selected["number"], 0)
                self.assertEqual(result["peer_members_used"], [])
                self.assertNotIn("predicted_payer_avoidable_spend", result["calculation_summary"])

    def test_current_claim_without_a_peer_returns_a_precise_no_cohort_result(self):
        result = build_payer_prediction_for_claim(configured_workbook_database(), "CLM00001096")
        self.assertFalse(result["available"])
        self.assertEqual(result["target"]["claim_id"], "CLM00001096")
        self.assertEqual(result["scenario_selection"]["selected"]["number"], 0)
        self.assertEqual(result["peer_members_used"], [])
        self.assertNotIn("predicted_payer_avoidable_spend", result["calculation_summary"])

    def test_22_production_engine_has_no_hardcoded_result_identity(self):
        source = (ROOT / "backend/payer_prediction.py").read_text()
        self.assertNotRegex(source, r"CLM\d{5,}|MBR\d{5,}|\$\d")

    def test_provider_financial_prediction_click_uses_selected_claim(self):
        source = (ROOT / "frontend/src/App.jsx").read_text()
        self.assertIn("/api/predictions/payer/claim/${encodeURIComponent(claim.claimId || claim.number)}", source)

    def test_90_day_episode_splits_only_when_previous_claim_gap_exceeds_90_days(self):
        rows = [
            cohort_claim("ROLL1", "ROLL_MEMBER", "20260101", 100),
            cohort_claim("ROLL2", "ROLL_MEMBER", "20260401", 100),
            cohort_claim("ROLL3", "ROLL_MEMBER", "20260701", 100),
        ]
        episodes = _rolling_episodes(rows)
        self.assertEqual([episode["claim_count"] for episode in episodes], [2, 1])

    def test_scenario_fallback_does_not_fabricate_peers(self):
        result = build_payer_prediction_for_claim(scenario_database(peer_payer="OTHER", peer_provider="OTHER"), "TARGET0")
        self.assertEqual(result["scenario_selection"]["selected"]["number"], 3)
        self.assertEqual([peer["member_id"] for peer in result["peer_members_used"]], ["PEER_MEMBER"])
        self.assertEqual(result["calculation_summary"]["confidence"]["level"], "Low")

    def test_excess_claim_count_is_backend_calculated(self):
        calculation = self.strict_result["calculation_summary"]
        benchmark = self.strict_result["benchmark_summary"]
        self.assertEqual(calculation["excess_claim_count"], max(self.strict_result["target"]["claim_count"] - benchmark["utilisation_benchmark_claim_count"], 0))

    def test_rag_performs_no_financial_calculation_for_popup(self):
        source = (ROOT / "frontend/src/App.jsx").read_text()
        popup = source[source.index("function ClaimPayerPredictionResult"):source.index("function ProviderRenderPredictionResult")]
        self.assertNotIn("rag", popup.lower())

    def test_claim_endpoint_accepts_popup_get_and_retry_post(self):
        client = app.test_client()
        for method in (client.get, client.post):
            response = method("/api/predictions/payer/claim/CLM00001092")
            self.assertEqual(response.status_code, 200)
            self.assertIn("benchmark_summary", response.get_json())

    def test_canonical_claim_response_selects_exactly_one_scenario(self):
        result = self.strict_result
        self.assertTrue({
            "target", "scenario_selection", "benchmark_summary", "peer_members_used",
            "calculation_summary", "supporting_evidence", "calculation_trace",
        }.issubset(result))
        selected = result["scenario_selection"]["selected"]
        self.assertIn(selected["number"], (1, 2, 3))
        self.assertTrue(result["scenario_selection"][f"scenario_{selected['number']}"]["available"])
        self.assertFalse(any(
            result["scenario_selection"][f"scenario_{number}"]["available"]
            for number in range(1, selected["number"])
        ))

    def test_unselected_scenario_peers_do_not_enter_calculation_or_evidence(self):
        database = CohortDatabase(
            [cohort_claim("TARGET_SCENARIO", "TARGET_MEMBER", "20260501", 500)],
            [
                cohort_claim("STRICT_PEER", "STRICT_MEMBER", "20260401", 400, historical=True),
                cohort_claim("BROAD_PEER", "BROAD_MEMBER", "20260401", 1, payer="OTHER", provider="OTHER", pos="22", cpt="80050", historical=True),
            ],
        )
        result = build_payer_prediction_for_claim(database, "TARGET_SCENARIO")
        self.assertEqual(result["scenario_selection"]["selected"]["number"], 1)
        self.assertEqual(result["calculation_summary"]["lower_spend_benchmark"], 400.0)
        self.assertEqual({peer["member_id"] for peer in result["peer_members_used"]}, {"STRICT_MEMBER"})
        self.assertNotIn("BROAD_PEER", {row["claim_id"] for row in result["supporting_evidence"]})

    def test_one_external_member_with_four_episodes_is_one_peer_row(self):
        database = CohortDatabase(
            [cohort_claim("TARGET_FOUR", "TARGET_MEMBER", "20260501", 500)],
            [
                cohort_claim("PEER_EP_1", "PEER_MEMBER", "20230101", 100, historical=True),
                cohort_claim("PEER_EP_2", "PEER_MEMBER", "20230701", 200, historical=True),
                cohort_claim("PEER_EP_3", "PEER_MEMBER", "20240101", 300, historical=True),
                cohort_claim("PEER_EP_4", "PEER_MEMBER", "20240701", 400, historical=True),
            ],
        )
        result = build_payer_prediction_for_claim(database, "TARGET_FOUR")
        self.assertEqual(result["benchmark_summary"]["peer_member_count"], 1)
        self.assertEqual(result["benchmark_summary"]["peer_episode_count"], 4)
        self.assertEqual(len(result["peer_members_used"]), 1)
        self.assertEqual(result["peer_members_used"][0]["peer_episode_count"], 4)

    def test_member_and_portfolio_rollups_deduplicate_target_episodes(self):
        database = CohortDatabase(
            [
                cohort_claim("TARGET_ROLLUP_1", "TARGET_MEMBER", "20260501", 300),
                cohort_claim("TARGET_ROLLUP_2", "TARGET_MEMBER", "20260502", 300),
            ],
            [cohort_claim("PEER_ROLLUP", "PEER_MEMBER", "20260401", 100, historical=True)],
        )
        member = build_member_payer_cohort_summary(database, "TARGET_MEMBER")
        portfolio = build_payer_cohort_portfolio_summary(database)
        self.assertEqual(member["claims_evaluated"], 2)
        self.assertEqual(member["episodes_evaluated"], 1)
        self.assertEqual(len(member["episodes"]), 1)
        self.assertEqual(set(member["episodes"][0]["claim_ids"]), {"TARGET_ROLLUP_1", "TARGET_ROLLUP_2"})
        self.assertEqual(sum(member[f"scenario_{number}_selected_count"] for number in (1, 2, 3)), 1)
        self.assertEqual(portfolio["episodes_evaluated"], 1)
        self.assertEqual(portfolio["episodes_with_predictions"], 1)
        self.assertEqual(portfolio["total_predicted_payer_avoidable_spend"], member["member_predicted_payer_avoidable_spend"])

    def test_popup_engine_contains_no_clinical_conclusions(self):
        source = (ROOT / "backend/payer_prediction.py").read_text().lower()
        for phrase in ("unnecessary treatment", "treatment failure", "medically preventable", "unnecessary claims"):
            self.assertNotIn(phrase, source)

    def test_member_360_and_portfolio_endpoints_return_deduplicated_rollups(self):
        database = configured_workbook_database()
        member_id = database.members[0]["memberId"]
        client = app.test_client()
        member_response = client.get(f"/api/members/{member_id}")
        portfolio_response = client.get("/api/predictions/payer/portfolio")
        self.assertEqual(member_response.status_code, 200)
        self.assertIn("payerCohortSavingsSummary", member_response.get_json()["item"])
        self.assertEqual(portfolio_response.status_code, 200)
        portfolio = portfolio_response.get_json()
        self.assertEqual(portfolio["members_evaluated"], len(database.members))
        self.assertEqual(portfolio["claims_evaluated"], len(database.selectable_claims))
        self.assertEqual(
            portfolio["episodes_with_predictions"],
            sum(portfolio[f"scenario_{number}_selected_count"] for number in (1, 2, 3)),
        )


def date_from_iso(value):
    from datetime import date
    return date.fromisoformat(value)


if __name__ == "__main__":
    unittest.main()
