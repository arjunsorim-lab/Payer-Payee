"""Tests for data-driven preventive-visit outcome linking."""

import unittest

from backend.outcome_evidence import build_outcome_evidence


class Database:
    def __init__(self, claims):
        self.claims = tuple(claims)


def claim(claim_id, date, *, member="MEMBER-X", episode="EPISODE-X", diagnosis="Z01.419", **fields):
    return {
        "claimId": claim_id,
        "memberId": member,
        "episodeId": episode,
        "dos": date,
        "diagnosisCode": diagnosis,
        "cptCode": fields.pop("CPT_Code", ""),
        "cptDescription": fields.pop("CPT_Description", ""),
        "workbookFields": fields,
    }


class TestOutcomeEvidence(unittest.TestCase):
    def setUp(self):
        self.preventive = claim(
            "VISIT-ALPHA",
            "2025-01-10",
            CPT_Code="99395",
            CPT_Description="Preventive visit",
            Intervention_Performed="Y",
        )
        self.outcome = claim(
            "RESULT-BETA",
            "2025-02-14",
            CPT_Description="Documented outcome assessment",
            Outcome_Claim_Flag="Y",
            Reference_Claim_Flag="Y",
            Related_Claim_Flag="Y",
            Condition_Resolved="Y",
            Treatment_Outcome="Improved",
            Follow_Up_Completed="Y",
        )

    def test_links_by_member_episode_diagnosis_and_dates_not_claim_id(self):
        evidence = build_outcome_evidence(Database([self.preventive, self.outcome]), self.preventive)

        self.assertEqual(evidence["source_claim_id"], "RESULT-BETA")
        self.assertEqual(evidence["status"], "Recorded outcome evidence")
        self.assertEqual(evidence["reference_claim_flag"], "Y")
        self.assertTrue(evidence["preventive_visit_identified"])
        self.assertTrue(evidence["no_later_related_claims"])

    def test_reports_a_later_same_disease_recurrence(self):
        recurrence = claim(
            "RECURRENCE-GAMMA",
            "2025-03-20",
            Related_Claim_Flag="Y",
        )
        evidence = build_outcome_evidence(
            Database([self.preventive, self.outcome, recurrence]),
            self.preventive,
        )

        self.assertFalse(evidence["no_later_related_claims"])
        self.assertEqual(evidence["later_related_claim_ids"], ["RECURRENCE-GAMMA"])

    def test_outcome_claim_links_back_to_its_preventive_visit(self):
        evidence = build_outcome_evidence(Database([self.preventive, self.outcome]), self.outcome)

        self.assertEqual(evidence["preventive_claim_id"], "VISIT-ALPHA")
        self.assertEqual(evidence["source_claim_id"], "RESULT-BETA")
        self.assertIn("VISIT-ALPHA", evidence["conclusion"])
        self.assertIn("RESULT-BETA", evidence["conclusion"])

    def test_preventive_reference_without_recurrence_recommends_earlier_care(self):
        reference = claim(
            "PREVENTIVE-REFERENCE",
            "2025-01-01",
            diagnosis="Z01.419",
            CPT_Code="99395",
            CPT_Description="Preventive Visit 18-39 Yrs",
            Intervention_Performed="Y",
            Outcome_Claim_Flag="Y",
            Reference_Claim_Flag="Y",
            Condition_Resolved="Y",
            Treatment_Outcome="Improved",
            Follow_Up_Completed="Y",
            Episode_Duration_Days=200,
        )

        evidence = build_outcome_evidence(Database([reference]), reference)

        self.assertEqual(evidence["status"], "Recorded outcome evidence")
        self.assertTrue(evidence["no_later_related_claims"])
        self.assertIn("200-day follow-up period", evidence["conclusion"])
        self.assertIn("immediately after the first episode", evidence["conclusion"])
        self.assertEqual(evidence["preventive_claim_id"], "PREVENTIVE-REFERENCE")
        self.assertEqual(evidence["journey_claim_id"], "PREVENTIVE-REFERENCE")

    def test_selected_first_visit_uses_its_linked_preventive_sequence(self):
        initial = claim(
            "INITIAL-ONE",
            "2025-01-01",
            diagnosis="E11.9",
            Intervention_Performed="N",
        )
        preventive = claim(
            "SEQP-INITIAL-ONE",
            "2025-02-02",
            diagnosis="E11.9",
            CPT_Code="99401",
            CPT_Description="Preventive counseling and care-management follow-up",
            Reference_Claim_ID="INITIAL-ONE",
            Reason_Code="SYNTHETIC_SEQUENCE_PREVENTIVE",
            Related_Claim_Flag="Y",
            Intervention_Performed="Y",
            Outcome_Claim_Flag="Y",
            Reference_Claim_Flag="Y",
            Condition_Resolved="Y",
            Treatment_Outcome="Improved; no linked readmission during 200-day follow-up",
            Follow_Up_Completed="Y",
            Episode_Duration_Days=200,
        )

        evidence = build_outcome_evidence(Database([initial, preventive]), initial)

        self.assertTrue(evidence["reference_outcome_supported"])
        self.assertEqual(evidence["reference_claim_id"], "SEQP-INITIAL-ONE")
        self.assertEqual(evidence["historical_no_readmission_days"], 200)
        self.assertTrue(evidence["no_later_related_claims"])

    def test_does_not_borrow_another_members_outcome(self):
        other_member_outcome = claim(
            "OTHER-OUTCOME",
            "2025-02-14",
            member="MEMBER-Y",
            Outcome_Claim_Flag="Y",
            Condition_Resolved="Y",
            Treatment_Outcome="Improved",
        )
        evidence = build_outcome_evidence(
            Database([self.preventive, other_member_outcome]),
            self.preventive,
        )

        self.assertEqual(evidence["source_claim_id"], "VISIT-ALPHA")
        self.assertEqual(evidence["status"], "Not established")

    def test_cross_patient_reference_drives_recommendation_and_billed_savings(self):
        reference = claim(
            "REFERENCE-ONE",
            "2025-01-01",
            member="REFERENCE-MEMBER",
            episode="REFERENCE-EPISODE",
            diagnosis="R73.03",
            CPT_Code="99395",
            CPT_Description="Preventive Visit + Care Management",
            Intervention_Performed="Y",
            Outcome_Claim_Flag="Y",
            Reference_Claim_Flag="Y",
            Condition_Resolved="Y",
            Treatment_Outcome="Improved",
            Follow_Up_Completed="Y",
            Episode_Duration_Days=200,
        )
        prediction = claim(
            "PREDICTION-TWO",
            "2026-01-15",
            member="PREDICTION-MEMBER",
            episode="PREDICTION-EPISODE",
            diagnosis="R73.03",
            Reference_Claim_ID="REFERENCE-ONE",
            Intervention_Performed="N",
        )
        readmission = claim(
            "READMISSION-THREE",
            "2026-03-01",
            member="PREDICTION-MEMBER",
            episode="PREDICTION-EPISODE",
            diagnosis="R73.03",
            CPT_Description="Inpatient hospital care",
            Reference_Claim_ID="REFERENCE-ONE",
            Related_Claim_Flag="Y",
            Reason_Code="PREDICTED_AVOIDABLE_READMISSION",
        )
        readmission["totalCharge"] = 4800

        evidence = build_outcome_evidence(Database([reference, prediction, readmission]), prediction)

        self.assertEqual(evidence["status"], "Historical reference evidence")
        self.assertEqual(evidence["recommended_intervention"], "Preventive Visit + Care Management")
        self.assertEqual(evidence["prediction_readmission_gap_days"], 45)
        self.assertEqual(evidence["prediction_readmission_billed_amount"], 4800)
        self.assertFalse(evidence["claim_is_later_hospitalization"])
        self.assertEqual(evidence["calculation_basis"], "billed_charge_amount")

    def test_readmission_source_row_is_traceable(self):
        reference = claim(
            "REFERENCE-ONE",
            "2025-01-01",
            member="REFERENCE-MEMBER",
            episode="REFERENCE-EPISODE",
            diagnosis="R73.03",
            CPT_Code="99395",
            CPT_Description="Preventive Visit + Care Management",
            Intervention_Performed="Y",
            Outcome_Claim_Flag="Y",
            Reference_Claim_Flag="Y",
            Condition_Resolved="Y",
            Treatment_Outcome="Improved",
            Follow_Up_Completed="Y",
            Episode_Duration_Days=200,
        )
        reference["totalCharge"] = 823.9
        prediction = claim(
            "PREDICTION-TWO",
            "2026-01-15",
            member="PREDICTION-MEMBER",
            episode="PREDICTION-EPISODE",
            diagnosis="R73.03",
            Reference_Claim_ID="REFERENCE-ONE",
            Intervention_Performed="N",
        )
        readmission = claim(
            "READMISSION-THREE",
            "2026-03-01",
            member="PREDICTION-MEMBER",
            episode="PREDICTION-EPISODE",
            diagnosis="R73.03",
            CPT_Description="Inpatient hospital care",
            Reference_Claim_ID="REFERENCE-ONE",
            Related_Claim_Flag="Y",
            Reason_Code="PREDICTED_AVOIDABLE_READMISSION",
        )
        readmission["totalCharge"] = 4800

        evidence = build_outcome_evidence(Database([reference, prediction, readmission]), prediction)

        self.assertEqual(
            evidence["prediction_readmission_source"],
            {
                "claim_id": "READMISSION-THREE",
                "service_date": "2026-03-01",
                "billed_amount": 4800,
                "calculation_basis": "billed_charge_amount",
                "why_included": (
                    "Included because it is the same member's later related hospitalization for this diagnosis family "
                    "(Related_Claim_Flag = Y), and its billed (charge) amount is the potentially avoided amount."
                ),
            },
        )
        self.assertEqual(evidence["reference_source_row"]["claim_id"], "REFERENCE-ONE")
        self.assertEqual(evidence["reference_source_row"]["billed_amount"], 823.9)
        self.assertIn("Reference_Claim_ID", evidence["reference_source_row"]["why_included"])

    def test_historical_reference_reports_specific_intervention_line_when_present(self):
        reference_visit = claim(
            "REFERENCE-VISIT",
            "2025-06-14",
            member="REFERENCE-MEMBER",
            episode="REFERENCE-EPISODE",
            diagnosis="R73.03",
            CPT_Code="99395",
            CPT_Description="Preventive Visit",
            Intervention_Performed="Y",
            Outcome_Claim_Flag="Y",
            Reference_Claim_Flag="Y",
            Condition_Resolved="Y",
            Treatment_Outcome="Improved; no prediabetes-related readmission for 200 days",
            Follow_Up_Completed="Y",
            Episode_Duration_Days=200,
        )
        training = claim(
            "REFERENCE-TRAINING",
            "2025-06-14",
            member="REFERENCE-MEMBER",
            episode="REFERENCE-EPISODE",
            diagnosis="R73.03",
            CPT_Code="G0108",
            CPT_Description="Diabetes Self-Management Training, Individual, per 30 min",
            Intervention_Performed="Y",
            Outcome_Claim_Flag="Y",
            Reference_Claim_Flag="Y",
            Condition_Resolved="Y",
            Treatment_Outcome="Improved; no prediabetes-related readmission for 200 days",
            Follow_Up_Completed="Y",
            Episode_Duration_Days=200,
        )
        prediction = claim(
            "PREDICTION-TWO",
            "2026-01-15",
            member="PREDICTION-MEMBER",
            episode="PREDICTION-EPISODE",
            diagnosis="R73.03",
            Reference_Claim_ID="REFERENCE-VISIT",
            Intervention_Performed="N",
        )

        evidence = build_outcome_evidence(Database([reference_visit, training, prediction]), prediction)

        self.assertEqual(evidence["reference_claim_id"], "REFERENCE-VISIT")
        self.assertEqual(evidence["reference_intervention_claim_id"], "REFERENCE-TRAINING")
        self.assertEqual(evidence["reference_intervention"], "Diabetes Self-Management Training, Individual, per 30 min")
        self.assertEqual(evidence["recommended_intervention"], "Diabetes Self-Management Training, Individual, per 30 min")
        self.assertIn("historical outcome belongs to the reference patient", evidence["conclusion"])
        self.assertNotIn("200-day follow-up", evidence["conclusion"])

    def test_opening_the_readmission_claim_itself_stays_consistent(self):
        reference = claim(
            "REFERENCE-ONE",
            "2025-01-01",
            member="REFERENCE-MEMBER",
            episode="REFERENCE-EPISODE",
            diagnosis="R73.03",
            CPT_Code="99395",
            CPT_Description="Preventive Visit + Care Management",
            Intervention_Performed="Y",
            Outcome_Claim_Flag="Y",
            Reference_Claim_Flag="Y",
            Condition_Resolved="Y",
            Treatment_Outcome="Improved",
            Follow_Up_Completed="Y",
            Episode_Duration_Days=200,
        )
        prediction = claim(
            "PREDICTION-TWO",
            "2026-01-15",
            member="PREDICTION-MEMBER",
            episode="PREDICTION-EPISODE",
            diagnosis="R73.03",
            Reference_Claim_ID="REFERENCE-ONE",
            Intervention_Performed="N",
        )
        readmission = claim(
            "READMISSION-THREE",
            "2026-03-01",
            member="PREDICTION-MEMBER",
            episode="PREDICTION-EPISODE",
            diagnosis="R73.03",
            CPT_Description="Initial Hospital Care - Progression",
            Reference_Claim_ID="REFERENCE-ONE",
            Related_Claim_Flag="Y",
            Reason_Code="PREDICTED_AVOIDABLE_READMISSION",
        )
        readmission["totalCharge"] = 4800

        evidence = build_outcome_evidence(Database([reference, prediction, readmission]), readmission)

        self.assertEqual(evidence["status"], "Historical reference evidence")
        self.assertTrue(evidence["claim_is_later_hospitalization"])
        self.assertEqual(evidence["prediction_readmission_claim_id"], "READMISSION-THREE")
        self.assertEqual(evidence["linked_prediction_claim_id"], "PREDICTION-TWO")
        self.assertIsNone(evidence["prediction_readmission_gap_days"])
        self.assertIn("linked later hospitalization", evidence["conclusion"])
        self.assertIn("PREDICTION-TWO", evidence["conclusion"])
        self.assertEqual(evidence["prediction_readmission_source"]["billed_amount"], 4800)


if __name__ == "__main__":
    unittest.main()
