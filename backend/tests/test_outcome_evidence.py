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


if __name__ == "__main__":
    unittest.main()
