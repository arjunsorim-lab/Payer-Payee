"""Value-based case fixtures exercise evidence, never production constants."""
from copy import deepcopy
from datetime import date, timedelta
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from backend.value_based_case import build_value_based_case_for_claim, discover_value_based_cases
from backend.workbook_enrichment import load_workbook_database, _table_header

FIXTURE = Path(__file__).parent / "fixtures" / "value_based_synthetic.xlsx"


def claim(claim_id, member_id, service_date, paid, *, family="E11", diagnosis=None,
          cpt="99213", procedure="Office visit", historical=False, **extra):
    return {"workbookFields": {
        "Claim_ID": claim_id, "Member_ID": member_id, "Service_Date_From": service_date,
        "ICD10_Family": family, "ICD10_Diagnosis_Code": diagnosis or f"{family}.9",
        "CPT_Code": cpt, "CPT_Description": procedure, "Paid_Amount": paid,
        "Is_Historical_Reference_Record": "Y" if historical else "N", **extra,
    }}


class Database:
    def __init__(self, selectable, historical=(), tables=None, config=None):
        self.selectable_claims = tuple(selectable)
        self.claims = (*self.selectable_claims, *historical)
        self.evidence_tables = tables or {}
        self.value_based_config = config or {}
        self.report = {}

    def find_claim(self, claim_id, selectable_only=True):
        rows = self.selectable_claims if selectable_only else self.claims
        return next((row for row in rows if row["workbookFields"]["Claim_ID"] == claim_id), None)


def simple_case():
    return [
        claim("REFERENCE", "MEMBER", "2026-01-01", 40, Reference_Claim_Flag="Y",
              Episode_ID="EPISODE", Reference_Service_Complete="Y"),
        claim("OUTCOME", "MEMBER", "2026-01-05", 120, cpt="TEST-A", procedure="Laboratory test",
              Episode_ID="EPISODE", Outcome_Claim_Flag="Y", Treatment_Outcome="Worsened",
              Condition_Resolved="N"),
        claim("LATER", "MEMBER", "2026-01-08", 35, cpt="FOLLOW-B", procedure="Follow-up office visit",
              Episode_ID="EPISODE", Repeat_Visit_Reason="persistent symptoms"),
    ]


def exposure(result):
    return result["calculation"]["potentially_avoidable_repeat_spend_exposure"]


class ValueBasedCaseTests(unittest.TestCase):
    def setUp(self):
        self.rows = simple_case()

    def build(self, rows=None, tables=None, config=None, selected="REFERENCE"):
        return build_value_based_case_for_claim(Database(rows if rows is not None else self.rows, tables=tables, config=config), selected)

    def test_positive_case_all_requirements_supported(self):
        result = self.build()
        self.assertTrue(result["available"])
        self.assertTrue(all(item["status"] == "YES" for item in result["requirements_verification"].values()))
        self.assertEqual(result["reference_claim"]["claim_id"], "REFERENCE")
        self.assertEqual(result["prediction_claim"]["claim_id"], "OUTCOME")
        self.assertEqual(result["days_after_reference"], 4)
        self.assertEqual(exposure(result), 155)
        self.assertIsNone(result["calculation"]["predicted_payer_avoidable_spend"])
        self.assertEqual(result["calculation"]["confirmed_savings"], 0)

    def test_backward_selection_retains_same_member_reference(self):
        result = self.build(selected="OUTCOME")
        self.assertEqual(result["selection_mode"], "backward_compatible")
        self.assertEqual(result["reference_claim"]["claim_id"], "REFERENCE")
        self.assertEqual(exposure(result), 155)

    def test_deterministic_reference_when_no_explicit_flag(self):
        del self.rows[0]["workbookFields"]["Reference_Claim_Flag"]
        self.assertEqual(self.build()["reference_claim"]["claim_id"], "REFERENCE")

    def test_different_member_never_becomes_reference(self):
        rows = [claim("SELECTED", "M1", "2026-05-10", 100, cpt="TEST", procedure="Laboratory test"),
                claim("PEER_REFERENCE", "M2", "2026-05-01", 50)]
        result = self.build(rows, selected="SELECTED")
        self.assertFalse(result["available"])
        self.assertIsNone(result["reference_claim"])
        self.assertEqual(exposure(result), 0)

    def test_prior_history_conflict_is_separate_from_first_episode_visit(self):
        self.rows.append(claim("OLDER", "MEMBER", "2024-01-01", 60, Episode_ID="OLD"))
        result = self.build()
        self.assertEqual(result["requirements_verification"]["first_documented_relevant_visit"]["status"], "NO")
        self.assertTrue(result["reference_evaluation"]["first_relevant_visit_in_episode"])
        self.assertEqual(result["reference_evaluation"]["prior_related_claims"][0]["claim_id"], "OLDER")

    def test_prior_outcome_diagnosis_does_not_disappear(self):
        self.rows[1]["workbookFields"].update(ICD10_Diagnosis_Code="J44.1", ICD10_Family="J44")
        self.rows.append(claim("OLDER", "MEMBER", "2020-01-01", 20, family="J44", diagnosis="J44.1", Episode_ID="OLD"))
        result = self.build()
        self.assertEqual(result["outcome_evaluation"]["prior_outcome_diagnosis_claim_ids"], ["OLDER"])
        self.assertFalse(result["outcome_evaluation"]["new_diagnosis_supported"])

    def test_auxiliary_prior_conditions_are_considered(self):
        conditions = [{"Claim_ID": "OLD", "Member_ID": "MEMBER", "Date": "2020-01-01", "ICD10": "E11.9"}]
        result = self.build(tables={"conditions": conditions})
        self.assertFalse(result["reference_evaluation"]["first_documented_relevant_visit"])

    def test_no_outcome_claim(self):
        result = self.build(self.rows[:1])
        self.assertFalse(result["available"])
        self.assertEqual(result["requirements_verification"]["outcome_claim_identified"]["status"], "NO")
        self.assertIsNone(result["prediction_claim"])
        self.assertEqual(exposure(result), 0)

    def test_no_episode_evidence_and_icd_chapter_alone(self):
        for row in self.rows:
            row["workbookFields"].pop("Episode_ID")
        self.rows[0]["workbookFields"].update(ICD10_Family="N30", ICD10_Diagnosis_Code="N30.90")
        self.rows[1]["workbookFields"].update(ICD10_Family="N11", ICD10_Diagnosis_Code="N11.1")
        self.rows[2]["workbookFields"].update(ICD10_Family="N39", ICD10_Diagnosis_Code="N39.0")
        result = self.build()
        self.assertEqual(result["requirements_verification"]["same_episode_supported"]["status"], "INSUFFICIENT_EVIDENCE")
        self.assertEqual(exposure(result), 0)

    def test_configured_group_links_distinct_families(self):
        for row in self.rows:
            row["workbookFields"].pop("Episode_ID")
        self.rows[1]["workbookFields"].update(ICD10_Family="J44", ICD10_Diagnosis_Code="J44.1")
        result = self.build(config={"review_groups": [{"id": "approved", "validated": True, "code_prefixes": ["E11", "J44"]}]})
        self.assertEqual(result["prediction_claim"]["claim_id"], "OUTCOME")
        self.assertIn("validated_review_group", result["episode"]["link_method"])

    def test_unvalidated_or_broad_chapter_groups_are_rejected(self):
        for group in ({"validated": False, "code_prefixes": ["E11"]}, {"validated": True, "code_prefixes": ["E"]}):
            with self.subTest(group=group), self.assertRaises(ValueError):
                self.build(config={"review_groups": [group]})

    def test_fallback_window_is_configurable_and_not_chain_expanded(self):
        for row in self.rows:
            row["workbookFields"].pop("Episode_ID")
        result = self.build(config={"episode_window_days": 5})
        self.assertEqual(result["episode"]["claim_count"], 2)
        self.assertEqual(exposure(result), 120)

    def test_conflicting_explicit_episode_vetoes_family_match(self):
        self.rows[1]["workbookFields"]["Episode_ID"] = "OTHER"
        self.assertEqual(exposure(self.build()), 35)

    def test_explicit_episode_can_span_fallback_window(self):
        self.rows[2]["workbookFields"]["Service_Date_From"] = "2026-09-01"
        self.assertEqual(exposure(self.build()), 155)

    def test_explicit_related_claim_link(self):
        for row in self.rows:
            row["workbookFields"].pop("Episode_ID")
        self.rows[1]["workbookFields"].update(ICD10_Family="J44", ICD10_Diagnosis_Code="J44.1",
                                              Related_Claim_ID="REFERENCE", Related_Claim_Flag="Y")
        self.assertEqual(self.build()["prediction_claim"]["claim_id"], "OUTCOME")

    def test_planned_follow_up_not_avoidable_even_with_synthetic_flag(self):
        self.rows[2]["workbookFields"].update(Repeat_Visit_Reason="planned follow-up", Synthetic_Flag="Y", Avoidable_Flag="Y")
        result = self.build()
        self.assertEqual(exposure(result), 120)
        self.assertIn("Planned or routine follow-up", result["avoidable_repetitive_claims"][1]["exclusion_reasons"])

    def test_later_claims_without_review_indicators_are_not_counted(self):
        for row in self.rows[1:]:
            for key in ("Treatment_Outcome", "Condition_Resolved", "Repeat_Visit_Reason"):
                row["workbookFields"].pop(key, None)
        result = self.build()
        self.assertEqual(exposure(result), 0)
        self.assertEqual(result["requirements_verification"]["avoidable_claims_supported"]["status"], "NO")

    def test_identical_duplicate_is_counted_once(self):
        self.rows.append(deepcopy(self.rows[1]))
        result = self.build()
        self.assertEqual(exposure(result), 155)
        self.assertEqual(len(result["claims_included"]), 2)
        self.assertEqual(result["duplicate_review"][0]["duplicate_row_count"], 1)

    def test_conflicting_duplicate_is_excluded(self):
        duplicate = deepcopy(self.rows[1])
        duplicate["workbookFields"]["Paid_Amount"] = 900
        self.rows.append(duplicate)
        result = self.build()
        self.assertEqual(exposure(result), 35)
        self.assertIn("Conflicting", result["duplicate_review"][0]["reason"])

    def test_final_corrected_replacement_supersedes_original(self):
        replacement = deepcopy(self.rows[1])
        replacement["workbookFields"].update(Claim_ID="CORRECTED", Replaces_Claim_ID="OUTCOME",
                                              Corrected_Claim_Flag="Y", Final_Adjudication_Status="Final Paid", Paid_Amount=70)
        self.rows.append(replacement)
        result = self.build()
        self.assertEqual(exposure(result), 105)
        self.assertNotIn("OUTCOME", [r["claim_id"] for r in result["claims_included"]])

    def test_reversal_and_duplicate_flags_exclude_claims(self):
        for key in ("Reversal_Flag", "Duplicate_Claim_Flag", "Superseded_Flag"):
            with self.subTest(key=key):
                rows = deepcopy(self.rows)
                rows[1]["workbookFields"][key] = "Y"
                self.assertEqual(exposure(self.build(rows)), 35)

    def test_missing_payment_is_not_charge_or_normalizer_zero(self):
        self.rows[1]["workbookFields"].update(Paid_Amount=None, Charge_Amount=9999, Allowed_Amount=8888)
        self.rows[1]["paid"] = 0
        result = self.build()
        self.assertEqual(exposure(result), 35)
        self.assertIsNone(result["prediction_claim"]["paid_amount"])
        self.assertFalse(result["calculation"]["complete"])
        self.assertEqual(result["requirements_verification"]["payments_verified"]["status"], "INSUFFICIENT_EVIDENCE")

    def test_zero_paid_is_a_valid_recorded_zero(self):
        self.rows[1]["workbookFields"]["Paid_Amount"] = 0
        result = self.build()
        self.assertTrue(result["prediction_claim"]["payment_verified"])
        self.assertEqual(result["calculation"]["included_claim_count"], 2)
        self.assertEqual(exposure(result), 35)

    def test_invalid_payment_is_unverified(self):
        for paid in ("nan", "inf", "-10", "garbage"):
            with self.subTest(paid=paid):
                rows = deepcopy(self.rows)
                rows[1]["workbookFields"]["Paid_Amount"] = paid
                self.assertIsNone(self.build(rows)["prediction_claim"]["paid_amount"])

    def test_final_remittance_preferred_and_difference_reported(self):
        remits = [{"Claim_ID": "OUTCOME", "Claim_Line_ID": "L1", "Payer_ID": "PAY",
                   "Paid_Amount": 90, "Final_Adjudication_Status": "Final Paid"}]
        result = self.build(tables={"remittances": remits})
        self.assertEqual(exposure(result), 125)
        self.assertEqual(result["prediction_claim"]["payment_source"], "final_adjudicated_remittance")
        self.assertTrue(result["prediction_claim"]["payment_limitations"])

    def test_multiple_remittance_lines_add_and_exact_duplicate_does_not(self):
        line = {"Claim_ID": "OUTCOME", "Claim_Line_ID": "L1", "Payer_ID": "PAY",
                "Paid_Amount": 40, "Final_Adjudication_Status": "Final Paid"}
        second = {**line, "Claim_Line_ID": "L2", "Paid_Amount": 50}
        result = self.build(tables={"remittances": [line, deepcopy(line), second]})
        self.assertEqual(exposure(result), 125)

    def test_conflicting_or_reversed_remittance_cannot_fallback_to_claim_paid(self):
        line = {"Claim_ID": "OUTCOME", "Claim_Line_ID": "L1", "Payer_ID": "PAY",
                "Paid_Amount": 40, "Final_Adjudication_Status": "Final Paid"}
        for remits in ([line, {**line, "Paid_Amount": 70}], [{**line, "Final_Adjudication_Status": "Final Reversed"}],
                       [{**line, "Final_Adjudication_Status": "Pending"}]):
            with self.subTest(remits=remits):
                result = self.build(tables={"remittances": remits})
                self.assertEqual(exposure(result), 35)
                self.assertFalse(result["prediction_claim"]["payment_verified"])

    def test_linked_medications_and_labs_disprove_office_only(self):
        result = self.build(tables={
            "medications": [{"Claim_ID": "REFERENCE", "DESCRIPTION": "Medication from source"}],
            "labs": [{"Claim_ID": "REFERENCE", "Service": "Lab test", "CPT": "TEST-A"}],
        })
        self.assertFalse(result["reference_evaluation"]["office_evaluation_only"])
        self.assertEqual(len(result["reference_evaluation"]["medications_at_reference"]), 1)
        self.assertNotIn("TEST-A", [i["cpt"] for i in result["earlier_intervention_opportunities"]])

    def test_missing_service_coverage_does_not_prove_office_only(self):
        self.rows[0]["workbookFields"].pop("Reference_Service_Complete")
        self.assertIsNone(self.build()["reference_evaluation"]["office_evaluation_only"])

    def test_missing_service_details_are_not_verified(self):
        self.rows[0]["workbookFields"].update(CPT_Code="", CPT_Description="")
        self.assertEqual(self.build()["requirements_verification"]["reference_service_verified"]["status"], "INSUFFICIENT_EVIDENCE")

    def test_undated_relevant_condition_prevents_first_visit_yes(self):
        result = self.build(tables={"conditions": [{"Member_ID": "MEMBER", "ICD10": "E11.9"}]})
        self.assertEqual(result["requirements_verification"]["first_documented_relevant_visit"]["status"], "INSUFFICIENT_EVIDENCE")

    def test_missing_ineligible_outcome_payment_still_needs_verification(self):
        self.rows[1]["workbookFields"].update(Paid_Amount=None, Avoidable_Flag="N")
        self.assertEqual(self.build()["requirements_verification"]["payments_verified"]["status"], "INSUFFICIENT_EVIDENCE")

    def test_auxiliary_table_headers_can_use_member_date_or_encounter(self):
        class Sheet:
            def __init__(self, headers):
                self.headers = headers

            def iter_rows(self, **kwargs):
                return iter([("Medication records",), self.headers])

        for headers in (("PATIENT", "START", "DESCRIPTION"), ("ENCOUNTER", "CODE", "DESCRIPTION")):
            self.assertEqual(_table_header(Sheet(headers))[0], 2)

    def test_explicit_workbook_synthetic_label_enables_demo_rules(self):
        database = Database(self.rows)
        database.report["synthetic"] = True
        for row in self.rows[1:]:
            row["workbookFields"]["Avoidable_Flag"] = "Y"
        self.assertEqual(exposure(build_value_based_case_for_claim(database, "REFERENCE")), 155)
        self.rows[1]["workbookFields"].pop("Avoidable_Flag")
        self.assertEqual(exposure(build_value_based_case_for_claim(database, "REFERENCE")), 35)

    def test_interventions_have_source_claims_and_are_hypotheses(self):
        for intervention in self.build()["earlier_intervention_opportunities"]:
            self.assertTrue(intervention["source_claim_ids"])
            self.assertTrue(intervention["hypothesis_only"])
            self.assertTrue(intervention["proposed_earlier_action"])

    def test_different_icd_alone_does_not_prove_worsening(self):
        self.rows[1]["workbookFields"].pop("Treatment_Outcome")
        self.rows[1]["workbookFields"].update(ICD10_Family="J44", ICD10_Diagnosis_Code="J44.1")
        result = self.build()
        self.assertIsNone(result["outcome_evaluation"]["worsening_supported"])
        self.assertEqual(result["outcome_evaluation"]["description"], "Subsequent utilization")

    def test_history_only_records_not_in_spend_or_reference(self):
        historical = claim("OLD-DEMO", "MEMBER", "2025-12-31", 20, historical=True)
        result = build_value_based_case_for_claim(Database(self.rows, [historical]), "REFERENCE")
        self.assertTrue(result["reference_evaluation"]["first_documented_relevant_visit"])
        self.assertEqual(exposure(result), 155)

    def test_unknown_claim_and_invalid_date_are_explicit(self):
        with self.assertRaises(KeyError):
            self.build(selected="MISSING")
        self.rows[0]["workbookFields"]["Service_Date_From"] = None
        with self.assertRaises(ValueError):
            self.build()

    def test_decimal_round_half_up(self):
        self.rows[1]["workbookFields"]["Paid_Amount"] = "10.005"
        self.rows[2]["workbookFields"]["Paid_Amount"] = "0.005"
        self.assertEqual(exposure(self.build()), 10.02)

    def test_different_member_disease_codes_ids_and_dates_work(self):
        for index, row in enumerate(self.rows):
            row["workbookFields"].update(Member_ID="NEW-PERSON", Claim_ID=f"ABC-{index}",
                                          Service_Date_From=f"2030-04-{index + 10:02}",
                                          Episode_ID="NEW-EP", ICD10_Family="F32", ICD10_Diagnosis_Code="F32.9",
                                          CPT_Code=f"SERVICE-{index}", Paid_Amount=(index + 1) * 10)
        result = self.build(selected="ABC-0")
        self.assertEqual(result["prediction_claim"]["claim_id"], "ABC-1")
        self.assertEqual(result["days_after_reference"], 1)
        self.assertEqual(exposure(result), 50)
        self.assertEqual({r["cpt"] for r in result["earlier_intervention_opportunities"]}, {"SERVICE-1", "SERVICE-2"})

    def test_deterministic_results_ignore_input_order(self):
        self.assertEqual(self.build(), self.build(list(reversed(self.rows))))

    def test_discovery_ranks_valid_candidates_without_duplicate_anchors(self):
        result = discover_value_based_cases(Database(self.rows))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["reference_claim_id"], "REFERENCE")
        self.assertGreater(result[0]["score"], 0)

    def test_production_engine_has_no_fixture_values_or_disease_rules(self):
        text = (Path(__file__).parents[1] / "value_based_case.py").read_text()
        self.assertNotRegex(text, r"CLM\d{5,}|MBR\d{5,}|3585\.03|975\.99|N30\.90|N11\.1|99214|87086|81001")


class SyntheticWorkbookRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database = load_workbook_database(FIXTURE)

    def copy_database(self):
        return Database(deepcopy(self.database.selectable_claims),
                        tables=deepcopy(self.database.evidence_tables))

    def test_supplied_workbook_loaded_without_verification_or_savings_outputs(self):
        db = self.database
        self.assertEqual(len(db.claims), 6)
        self.assertEqual(db.report["claims_header_row"], 5)
        self.assertEqual(set(db.evidence_tables), {"conditions", "procedures", "interventions", "remittances"})
        last = db.find_claim("CLM00000453")
        self.assertEqual(last["dos"], "2026-05-01")
        self.assertTrue(last["workbookFields"]["Service_Date_From"].startswith("2026-05-01"))

    def test_synthetic_explicit_flags_all_yes_and_expected_total(self):
        result = build_value_based_case_for_claim(self.database, "CLM00000414")
        self.assertEqual(result["prediction_claim"]["claim_id"], "CLM00000462")
        self.assertEqual(result["days_after_reference"], 4)
        self.assertEqual(result["calculation"]["included_claim_count"], 5)
        self.assertEqual(exposure(result), 3585.03)
        self.assertTrue(all(r["status"] == "YES" for r in result["requirements_verification"].values()))
        self.assertEqual({i["cpt"] for i in result["earlier_intervention_opportunities"]}, {"99214", "87086", "81001"})
        self.assertEqual(result["calculation"]["confirmed_savings"], 0)
        self.assertTrue(any("Synthetic/illustrative" in s for s in result["data_limitations"]))

    def test_anti_hardcoding_mutate_ids_dates_amounts_add_and_remove_flag(self):
        db = self.copy_database()
        mapping = {r["claimId"]: f"CHANGED-{index}" for index, r in enumerate(db.claims)}
        for row in db.claims:
            fields = row["workbookFields"]
            fields["Claim_ID"] = mapping[fields["Claim_ID"]]
            fields["Member_ID"] = "DIFFERENT-MEMBER"
            fields["Episode_ID"] = "DIFFERENT-EPISODE"
            fields["Service_Date_From"] = (date.fromisoformat(fields["Service_Date_From"][:10]) + timedelta(days=500)).isoformat()
            fields["Paid_Amount"] = "11.11"
        for role, rows in db.evidence_tables.items():
            for row in rows:
                if row.get("Claim_ID") in mapping:
                    row["Claim_ID"] = mapping[row["Claim_ID"]]
                if row.get("Reference_Claim_ID") in mapping:
                    row["Reference_Claim_ID"] = mapping[row["Reference_Claim_ID"]]
                if row.get("Member_ID"):
                    row["Member_ID"] = "DIFFERENT-MEMBER"
                if row.get("Service_Date_From"):
                    row["Service_Date_From"] = (date.fromisoformat(row["Service_Date_From"][:10]) + timedelta(days=500)).isoformat()
                if role == "remittances":
                    row["Paid_Amount"] = "11.11"
        result = build_value_based_case_for_claim(db, mapping["CLM00000414"])
        self.assertEqual(result["days_after_reference"], 4)
        self.assertEqual(exposure(result), 55.55)
        extra = deepcopy(db.claims[-1])
        extra["workbookFields"].update(Claim_ID="EXTRA", Paid_Amount="22.22")
        larger = Database([*db.claims, extra], tables=db.evidence_tables)
        result = build_value_based_case_for_claim(larger, mapping["CLM00000414"])
        self.assertEqual(exposure(result), 77.77)
        self.assertEqual(result["calculation"]["included_claim_count"], 6)
        extra["workbookFields"].pop("Avoidable_Flag")
        result = build_value_based_case_for_claim(larger, mapping["CLM00000414"])
        self.assertEqual(exposure(result), 55.55)
        self.assertEqual(result["calculation"]["included_claim_count"], 5)

    def test_existing_route_uses_selected_configured_workbook(self):
        from backend.app import app
        with patch.dict(os.environ, {"SAVINGS_WORKBOOK_PATH": str(FIXTURE)}):
            response = app.test_client().get("/api/predictions/value-based-case/CLM00000414")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(exposure(response.get_json()), 3585.03)

    def test_negative_case_through_existing_route(self):
        from backend.app import app
        db = self.copy_database()
        db.selectable_claims = db.claims = (db.claims[0],)
        with patch("backend.app.configured_workbook_database", return_value=db):
            response = app.test_client().get("/api/predictions/value-based-case/CLM00000414")
            self.assertEqual(response.status_code, 200)
            result = response.get_json()
            self.assertFalse(result["available"])
            self.assertEqual(result["requirements_verification"]["outcome_claim_identified"]["status"], "NO")
            self.assertEqual(exposure(result), 0)

    def test_ui_consumes_dynamic_fields_without_fixture_identity(self):
        source = (Path(__file__).parents[2] / "frontend/src/App.jsx").read_text()
        section = source[source.index("function PeerComparisonPredictionSummary"):source.index("function ValueBasedRectificationCase")]
        for field in ("billed_comparison", "source_rows", "match_checks", "selection_audit"):
            self.assertIn(field, section)
        view = source[source.index("function PredictionScenarioMap"):source.index("function filterClaimsByTime")]
        self.assertIn("<PeerComparisonPredictionSummary", view)
        self.assertNotIn("<ValueBasedRectificationCase", view)
        self.assertNotRegex(section, r"CLM\d{5,}|MBR\d{5,}|3585\.03|975\.99")


if __name__ == "__main__":
    unittest.main()
