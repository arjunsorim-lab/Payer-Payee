"""Tests for the cross-patient episode comparison engine."""
import unittest
from backend.workbook_enrichment import load_workbook_database
from backend.patient_comparison import (
    build_same_patient_billed_intervention_savings,
    compare_patients,
    discover_comparable_pairs,
    organ_for_family,
)


class SyntheticDatabase:
    def __init__(self, claims):
        self.selectable_claims = tuple(claims)


def billed_claim(claim_id, service_date, charge, cpt, description):
    return {"workbookFields": {
        "Claim_ID": claim_id,
        "Member_ID": "PATIENT",
        "Service_Date_From": service_date,
        "ICD10_Family": "N30",
        "ICD10_Diagnosis_Code": "N30.00",
        "ICD10_Diagnosis_Description": "Acute infective cystitis",
        "CPT_Code": cpt,
        "CPT_Description": description,
        "Charge_Amount": charge,
        "Paid_Amount": 0,
        "Is_Historical_Reference_Record": "N",
    }}


class TestOrganMapping(unittest.TestCase):
    def test_urinary_family(self):
        label, emoji = organ_for_family("N30")
        self.assertEqual(label, "Kidneys & Urinary Tract")
        self.assertEqual(emoji, "💧")

    def test_cardiac_family(self):
        label, _ = organ_for_family("I25")
        self.assertEqual(label, "Heart & Blood Vessels")

    def test_respiratory_family(self):
        label, _ = organ_for_family("J44")
        self.assertEqual(label, "Lungs & Airways")

    def test_cancer_family(self):
        label, _ = organ_for_family("C50")
        self.assertEqual(label, "Cancer / Neoplasms")

    def test_mental_health(self):
        label, _ = organ_for_family("F32")
        self.assertEqual(label, "Mental Health")

    def test_empty_returns_unknown(self):
        label, emoji = organ_for_family("")
        self.assertEqual(label, "Unknown")
        self.assertEqual(emoji, "❓")


class TestDiscoverComparablePairs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = load_workbook_database("data/claims-demo.xlsx")
        cls.result = discover_comparable_pairs(cls.db)

    def test_returns_organ_groups(self):
        self.assertIn("organ_groups", self.result)
        self.assertGreater(len(self.result["organ_groups"]), 0)

    def test_each_group_has_required_fields(self):
        for group in self.result["organ_groups"]:
            self.assertIn("organ_system", group)
            self.assertIn("organ_emoji", group)
            self.assertIn("disease_families", group)
            self.assertIn("total_diseases", group)
            self.assertIn("total_members", group)

    def test_disease_families_have_multiple_members(self):
        for group in self.result["organ_groups"]:
            for disease in group["disease_families"]:
                self.assertGreaterEqual(disease["member_count"], 2)

    def test_urinary_tract_present(self):
        organs = [g["organ_system"] for g in self.result["organ_groups"]]
        self.assertIn("Kidneys & Urinary Tract", organs)


class TestComparePatients(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = load_workbook_database("data/claims-demo.xlsx")

    def test_same_member_rejected(self):
        with self.assertRaises(ValueError):
            compare_patients(self.db, "MBR00006", "MBR00006", "N39")

    def test_missing_member_rejected(self):
        with self.assertRaises(ValueError):
            compare_patients(self.db, "MBR00006", "NONEXISTENT", "N39")

    def test_missing_family_rejected(self):
        with self.assertRaises(ValueError):
            compare_patients(self.db, "MBR00006", "MBR00017", "ZZZ")

    def test_comparison_returns_required_fields(self):
        result = compare_patients(self.db, "MBR00006", "MBR00017", "N39")
        self.assertTrue(result["available"])
        self.assertIn("savings_summary", result)
        self.assertIn("higher_cost_patient", result)
        self.assertIn("lower_cost_patient", result)
        self.assertIn("service_divergence", result)
        self.assertTrue(result["clinical_review_required"])

    def test_savings_are_nonnegative(self):
        result = compare_patients(self.db, "MBR00006", "MBR00017", "N39")
        self.assertGreaterEqual(result["savings_summary"]["total_savings"], 0)

    def test_higher_cost_is_actually_higher(self):
        result = compare_patients(self.db, "MBR00006", "MBR00017", "N39")
        self.assertGreaterEqual(
            result["higher_cost_patient"]["episode"]["total_billed"],
            result["lower_cost_patient"]["episode"]["total_billed"],
        )
        self.assertEqual(result["savings_summary"]["metric"], "billed_amount")

    def test_different_members_in_result(self):
        result = compare_patients(self.db, "MBR00006", "MBR00017", "N39")
        self.assertNotEqual(
            result["higher_cost_patient"]["member_id"],
            result["lower_cost_patient"]["member_id"],
        )

    def test_service_divergence_has_categories(self):
        result = compare_patients(self.db, "MBR00006", "MBR00017", "N39")
        for item in result["service_divergence"]:
            self.assertIn(item["category"], {"additional", "excess", "price_variation"})


class TestSamePatientBilledInterventionSavings(unittest.TestCase):
    def setUp(self):
        self.db = SyntheticDatabase([
            billed_claim("E1-ENCOUNTER", "2023-06-28", 87.71, "ER", "Emergency encounter"),
            billed_claim("E1-PROCEDURES", "2023-06-28", 3019.80, "E1", "Episode 1 procedures"),
            billed_claim("E1-MEDICATION", "2023-06-28", 129.94, "RX1", "Ciprofloxacin"),
            billed_claim("E2-ENCOUNTER", "2023-10-28", 85.55, "AMB", "Ambulatory encounter"),
            billed_claim("E2-OTHER", "2023-10-28", 4418.69, "E2", "Other Episode 2 procedures including pregnancy test"),
            billed_claim("E2-CULTURE", "2023-10-28", 431.40, "87086", "Urine culture"),
            billed_claim("E2-SPECIMEN", "2023-10-28", 431.40, "99000", "Urine specimen collection"),
            billed_claim("E2-MEDICATION", "2023-10-28", 129.94, "RX2", "Nitrofurantoin"),
        ])

    def test_matches_supplied_workbook_billed_calculation(self):
        result = build_same_patient_billed_intervention_savings(self.db, "PATIENT", "N30")
        calculation = result["calculation"]
        self.assertTrue(result["available"])
        self.assertEqual(result["days_between_episodes"], 122)
        self.assertEqual(calculation["earlier_episode_actual_billed"], 3237.45)
        self.assertEqual(calculation["later_episode_actual_billed"], 5496.98)
        self.assertEqual(calculation["culture_and_specimen_add_on_billed"], 862.80)
        self.assertEqual(calculation["actual_two_episode_billed"], 8734.43)
        self.assertEqual(calculation["proposed_earlier_episode_with_add_on_billed"], 4100.25)
        self.assertEqual(calculation["potential_billed_difference"], 4634.18)

    def test_later_episode_keeps_unrelated_billed_lines(self):
        result = build_same_patient_billed_intervention_savings(self.db, "PATIENT", "N30")
        later_ids = {claim["claim_id"] for claim in result["later_episode"]["claims"]}
        self.assertIn("E2-OTHER", later_ids)
        self.assertTrue(result["includes_all_later_episode_lines"])

    def test_non_uti_family_is_not_substituted(self):
        result = build_same_patient_billed_intervention_savings(self.db, "PATIENT", "F41")
        self.assertFalse(result["available"])

    def test_more_units_alone_are_not_called_an_intervention(self):
        earlier = billed_claim("PREVENTIVE-EARLIER", "2024-01-01", 800.00, "99395", "Preventive Visit")
        later = billed_claim("PREVENTIVE-LATER", "2024-06-01", 1600.00, "99395", "Preventive Visit")
        for claim, units in ((earlier, 1), (later, 2)):
            fields = claim["workbookFields"]
            fields["Member_ID"] = "GENERAL-PATIENT"
            fields["ICD10_Family"] = "Z01"
            fields["ICD10_Diagnosis_Code"] = "Z01.419"
            fields["Units"] = units
        result = build_same_patient_billed_intervention_savings(
            SyntheticDatabase([earlier, later]), "GENERAL-PATIENT", "Z01", "PREVENTIVE-LATER"
        )
        self.assertFalse(result["available"])

    def test_explicit_distinct_intervention_can_use_general_template(self):
        earlier = billed_claim("BASE-EARLIER", "2024-01-01", 800.00, "99395", "Preventive Visit")
        later_base = billed_claim("BASE-LATER", "2024-06-01", 800.00, "99395", "Preventive Visit")
        intervention = billed_claim("INTERVENTION", "2024-06-01", 200.00, "99401", "Preventive counseling")
        for claim in (earlier, later_base, intervention):
            fields = claim["workbookFields"]
            fields["Member_ID"] = "GENERAL-PATIENT"
            fields["ICD10_Family"] = "Z01"
            fields["ICD10_Diagnosis_Code"] = "Z01.419"
            fields["Units"] = 1
        intervention["workbookFields"]["Intervention_Performed"] = "Y"
        result = build_same_patient_billed_intervention_savings(
            SyntheticDatabase([earlier, later_base, intervention]), "GENERAL-PATIENT", "Z01", "BASE-LATER"
        )
        self.assertTrue(result["available"])
        self.assertEqual(result["calculation"]["culture_and_specimen_add_on_billed"], 200.00)
        self.assertEqual(result["calculation"]["actual_two_episode_billed"], 1800.00)
        self.assertEqual(result["calculation"]["proposed_earlier_episode_with_add_on_billed"], 1000.00)
        self.assertEqual(result["calculation"]["potential_billed_difference"], 800.00)

    def test_bundled_synthetic_presentation_case_uses_billed_amounts(self):
        database = load_workbook_database("data/claims-demo.xlsx")
        result = build_same_patient_billed_intervention_savings(
            database, "MBRDEMO01", "N39", "CLM00990003"
        )
        calculation = result["calculation"]
        self.assertTrue(result["available"])
        self.assertTrue(result["synthetic_demo"])
        self.assertEqual(result["calculation_basis"], "billed_amount")
        self.assertEqual(result["days_to_first_intervening_episode"], 25)
        self.assertEqual(result["days_between_episodes"], 32)
        self.assertGreater(calculation["potential_billed_difference"], 0)

    def test_gynecological_journey_starts_at_first_symptomatic_visit(self):
        database = load_workbook_database("data/claims-demo.xlsx")
        result = build_same_patient_billed_intervention_savings(
            database, "MBR00015", "N92", "CLM09921096"
        )
        calculation = result["calculation"]

        self.assertTrue(result["available"])
        self.assertEqual(result["earlier_episode"]["claims"][0]["claim_id"], "CLM09921096")
        self.assertEqual(result["intervening_episodes"][0]["claims"][0]["claim_id"], "SEQW-CLM09921096")
        self.assertEqual(result["later_episode"]["claims"][0]["claim_id"], "SEQP-CLM09921096")
        self.assertEqual(result["days_between_episodes"], 32)
        self.assertEqual(result["days_to_first_intervening_episode"], 25)
        self.assertGreater(calculation["potential_billed_difference"], 0)

    def test_explicit_linked_sequence_calculates_from_selected_first_visit(self):
        first = billed_claim("BASE-CLAIM", "2025-01-01", 500.00, "99213", "Initial symptomatic visit")
        worsening = billed_claim("SEQW-BASE-CLAIM", "2025-01-26", 800.00, "99215", "Worsening follow-up")
        preventive = billed_claim("SEQP-BASE-CLAIM", "2025-02-02", 250.00, "99401", "Preventive intervention follow-up")
        for claim in (first, worsening, preventive):
            fields = claim["workbookFields"]
            fields["Member_ID"] = "SEQUENCE-PATIENT"
            fields["ICD10_Family"] = "E11"
            fields["ICD10_Diagnosis_Code"] = "E11.9"
        worsening["workbookFields"].update({
            "Reference_Claim_ID": "BASE-CLAIM",
            "Reason_Code": "SYNTHETIC_SEQUENCE_WORSENING",
            "Intervention_Performed": "N",
        })
        preventive["workbookFields"].update({
            "Reference_Claim_ID": "BASE-CLAIM",
            "Reason_Code": "SYNTHETIC_SEQUENCE_PREVENTIVE",
            "Intervention_Performed": "Y",
            "Episode_Duration_Days": 200,
        })

        result = build_same_patient_billed_intervention_savings(
            SyntheticDatabase([first, worsening, preventive]),
            "SEQUENCE-PATIENT",
            "E11",
            "BASE-CLAIM",
        )

        self.assertTrue(result["available"])
        self.assertTrue(result["synthetic_demo"])
        self.assertEqual(result["days_to_first_intervening_episode"], 25)
        self.assertEqual(result["days_between_episodes"], 32)
        self.assertEqual(result["calculation"]["actual_two_episode_billed"], 1550.00)
        self.assertEqual(result["calculation"]["proposed_earlier_episode_with_add_on_billed"], 750.00)
        self.assertEqual(result["calculation"]["potential_billed_difference"], 800.00)

    def test_bundled_dataset_has_complete_sequence_for_every_selectable_claim(self):
        database = load_workbook_database("data/claims-demo.xlsx")
        linked_rows = {}
        for claim in database.claims:
            fields = claim["workbookFields"]
            if fields.get("Reason_Code") not in {"SYNTHETIC_SEQUENCE_WORSENING", "SYNTHETIC_SEQUENCE_PREVENTIVE"}:
                continue
            linked_rows.setdefault(str(fields["Reference_Claim_ID"]), []).append(fields)

        self.assertEqual(set(linked_rows), {claim["claimId"] for claim in database.selectable_claims})
        for claim_id, rows in linked_rows.items():
            self.assertEqual({row["Reason_Code"] for row in rows}, {
                "SYNTHETIC_SEQUENCE_WORSENING",
                "SYNTHETIC_SEQUENCE_PREVENTIVE",
            }, claim_id)
            preventive = next(row for row in rows if row["Reason_Code"] == "SYNTHETIC_SEQUENCE_PREVENTIVE")
            self.assertEqual(float(preventive["Episode_Duration_Days"]), 200)
            self.assertEqual(str(preventive["Follow_Up_Completed"]).upper(), "Y")


if __name__ == "__main__":
    unittest.main()
