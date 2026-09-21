"""Scenario-specific review proposals; never rewrite recorded claims or savings.

Demo follow-up intervals are illustrative configuration, not clinical protocols.
"""

PROFILES = (
    ("urinary", ("N30", "N390"), "Urinary infection review",
     "Consider urine culture and susceptibility testing if symptoms persist or worsen after antibiotics, and review treatment against the results.",
     2, "Reassess if symptoms do not improve within 48 hours of antibiotics, or sooner if worsening.",
     "https://www.nice.org.uk/guidance/ng109/chapter/Recommendations"),
    ("diabetes", ("E10", "E11", "E13"), "Diabetes management review",
     "Review glucose results, medication adherence and whether diabetes education or further monitoring is appropriate.",
     14, "Illustrative review interval; clinician to determine timing from glucose results and symptoms.", None),
    ("hypertension", ("I10",), "Blood pressure review",
     "Review home blood pressure readings, medication adherence and the need for treatment assessment.",
     30, "Illustrative review interval; clinician to determine timing from blood pressure and symptoms.", None),
    ("gynecological", ("N91", "N92", "N93", "N94"), "Gynecological symptom review",
     "Review the menstrual or pelvic symptom history and consider targeted investigation or gynecology referral when indicated.",
     10, "Illustrative review interval; clinician to determine timing and investigations from symptoms.", None),
)


def build_intervention_plan(claim):
    fields = claim.get("workbookFields", claim)
    code = str(claim.get("diagnosisCode") or fields.get("ICD10_Diagnosis_Code") or "").upper().replace(".", "").strip()
    base = {
        "status": "insufficient_evidence", "available": False,
        "recorded": False, "requires_clinical_review": True,
        "included_in_savings": False, "matched_diagnosis": code,
        "follow_up_days": None,
        "reason": "No specific intervention profile matches the recorded diagnosis. Review the clinical record before choosing an intervention or timing.",
    }
    for key, prefixes, title, action, days, timing, source in PROFILES:
        if not code.startswith(prefixes):
            continue
        return {
            **base, "available": True, "status": "proposed_for_review",
            "scenario": key, "title": title, "action": action,
            "follow_up_days": days, "timing": timing,
            "timing_basis": "conditional_guidance" if source else "illustrative_demo",
            "source_url": source,
            "reason": "Matched to the claim diagnosis; symptoms, treatment history and suitability must be confirmed.",
        }
    return base
