"""Focused checks for claims-only value-based rectification scenarios."""

import unittest

from backend.value_based_case import build_value_based_case_for_claim


def claim(claim_id, member_id, service_date, paid, *, family="E11", diagnosis=None,
          cpt="99213", procedure="Office visit", historical=False):
    diagnosis = diagnosis or f"{family}.9"
    return {
        "workbookFields": {
            "Claim_ID": claim_id,
            "Member_ID": member_id,
            "Service_Date_From": service_date,
            "ICD10_Family": family,
            "ICD10_Diagnosis_Code": diagnosis,
            "ICD10_Diagnosis_Description": f"Condition {family}",
            "CPT_Code": cpt,
            "CPT_Description": procedure,
            "Paid_Amount": paid,
            "Is_Historical_Reference_Record": "Y" if historical else "N",
        }
    }


class Database:
    def __init__(self, selectable, historical=(), notes=()):
        self.selectable_claims = tuple(selectable)
        self.claims = (*self.selectable_claims, *historical)
        self.data_notes_rows = tuple(notes)

    def find_claim(self, claim_id, selectable_only=True):
        rows = self.selectable_claims if selectable_only else self.claims
        return next((row for row in rows if row["workbookFields"]["Claim_ID"] == claim_id), None)


class ValueBasedCaseTests(unittest.TestCase):
    def test_same_member_exact_diagnosis_reference_and_nonduplicated_spend(self):
        database = Database([
            claim("REFERENCE", "M1", "2026-05-01", 50),
            claim("PREDICTION", "M1", "2026-05-10", 100),
            claim("REPEAT", "M1", "2026-06-01", 200),
        ])

        result = build_value_based_case_for_claim(database, "PREDICTION")

        self.assertTrue(result["available"])
        self.assertEqual(result["status"], "Rectification candidate")
        self.assertEqual(result["reference_claim"]["claim_id"], "REFERENCE")
        self.assertEqual(result["reference_selection"]["source"], "same patient")
        self.assertEqual(result["reference_selection"]["relationship"], "same ICD-10 family")
        self.assertTrue(result["calculation"]["available"])
        self.assertEqual(result["calculation"]["present_claim_paid"], 100.0)
        self.assertEqual(result["calculation"]["later_related_paid"], 200.0)
        self.assertEqual(result["calculation"]["potential_payer_spend_for_review"], 300.0)
        self.assertEqual([item["claim_id"] for item in result["avoidable_repetitive_claims"]], ["REPEAT"])
        self.assertEqual([item["claim_id"] for item in result["claims_included"]], ["PREDICTION", "REPEAT"])
        self.assertIn("same kind of problem", result["claims_included"][1]["inclusion_reason"])

    def test_different_member_reference_is_allowed_after_same_member_candidates(self):
        database = Database([
            claim("PREDICTION", "M1", "2026-05-10", 100),
            claim("REPEAT", "M1", "2026-06-01", 200),
            claim("PEER_REFERENCE", "M2", "2026-05-01", 50),
        ])

        result = build_value_based_case_for_claim(database, "PREDICTION")

        self.assertEqual(result["reference_claim"]["claim_id"], "PEER_REFERENCE")
        self.assertEqual(result["reference_selection"]["source"], "different patient")
        self.assertTrue(result["calculation"]["available"])

    def test_same_icd_chapter_without_a_matching_family_does_not_create_a_scenario(self):
        database = Database([
            claim("REFERENCE", "M1", "2026-05-01", 50, family="N30", diagnosis="N30.90"),
            claim("PREDICTION", "M1", "2026-05-05", 100, family="N11", diagnosis="N11.1", cpt="87086", procedure="Urine culture"),
        ])

        result = build_value_based_case_for_claim(database, "PREDICTION")

        self.assertFalse(result["available"])
        self.assertEqual(result["status"], "No claims-based rectification scenario")
        self.assertEqual(result["calculation"]["potential_payer_spend_for_review"], 0.0)

    def test_no_prior_matching_reference_returns_no_scenario(self):
        result = build_value_based_case_for_claim(
            Database([claim("PREDICTION", "M1", "2026-05-10", 100)]),
            "PREDICTION",
        )

        self.assertFalse(result["available"])
        self.assertEqual(result["status"], "No claims-based rectification scenario")
        self.assertEqual(result["calculation"]["potential_payer_spend_for_review"], 0.0)

    def test_synthetic_outcome_note_is_exposed_as_a_data_limitation(self):
        database = Database(
            [
                claim("REFERENCE", "M1", "2026-05-01", 50),
                claim("PREDICTION", "M1", "2026-05-10", 100),
                claim("REPEAT", "M1", "2026-06-01", 200),
            ],
            notes=({"note": "SYNTHETIC/ILLUSTRATIVE placeholder values"},),
        )

        result = build_value_based_case_for_claim(database, "PREDICTION")

        self.assertTrue(any("synthetic/illustrative" in item.lower() for item in result["data_limitations"]))


if __name__ == "__main__":
    unittest.main()
