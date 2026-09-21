"""Scenario-specific review proposals; never rewrite recorded claims or savings.

Demo follow-up intervals are illustrative configuration, not clinical protocols.
"""

PROFILES = (
    ("urinary", ("N300", "N309", "N390"), "Urinary infection review",
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


def _demo(key, prefixes, title, action, days):
    return (key, prefixes, title, action, days,
            "Illustrative scenario timing only; confirm symptoms, treatment and urgency before scheduling.", None)


PROFILES += (
    _demo("thyroid", ("E01", "E03", "E06", "E890"), "Thyroid monitoring review", "Review thyroid results and medication use; consider whether TSH or free T4 monitoring is due.", 42),
    _demo("lipids", ("E78",), "Lipid management review", "Review lipid results, cardiovascular risk and medication tolerance.", 30),
    _demo("metabolic", ("E66", "R73"), "Metabolic risk review", "Review glucose trends, nutrition and activity support; assess whether metabolic testing is appropriate.", 21),
    _demo("respiratory", ("J00", "J02", "J03", "J06", "J09", "J10", "J11", "J18", "J22", "R05", "R070"), "Respiratory symptom reassessment", "Review persistent respiratory symptoms and prior treatment; consider targeted testing only when clinically indicated.", 3),
    _demo("airways", ("J41", "J44", "J45", "J98", "J68"), "Airway management review", "Review inhaler technique, adherence, symptom control and the existing respiratory action plan.", 5),
    _demo("sinus", ("J32",), "Sinus symptom review", "Review persistent sinus symptoms and treatment response; consider specialist assessment if appropriate.", 14),
    _demo("mental_health", ("F20", "F31", "F32", "F33", "F41", "F43", "Z630"), "Mental health follow-up", "Review symptom scores, medication effects, safety and access to psychological support.", 10),
    _demo("renal", ("N18", "I12", "I13", "Z992"), "Kidney care review", "Review renal function, medication safety and the existing nephrology or dialysis care plan.", 14),
    _demo("cardiac", ("I11", "I25", "I34", "I44", "I48", "I50", "I65", "R00", "Z950"), "Cardiac care review", "Review symptoms, blood pressure or rhythm records, medication use and the existing cardiology plan.", 10),
    _demo("anemia", ("D50", "D64"), "Anemia investigation review", "Review blood counts and previous investigations; consider whether iron studies or assessment of the underlying cause is needed.", 21),
    _demo("oncology", ("C18", "C50", "C56", "C79", "D05", "Z5111", "Z853"), "Oncology care coordination", "Review the treating team's monitoring schedule, treatment effects and outstanding results; coordinate with oncology.", 14),
    _demo("breast", ("D24", "N63"), "Breast assessment review", "Review examination findings, existing imaging and any outstanding breast clinic referral.", 10),
    _demo("reflux", ("K21", "K22", "K31", "R12"), "Digestive symptom review", "Review reflux or digestive symptoms and medication response; assess whether further investigation is indicated.", 14),
    _demo("colon", ("K57", "K63"), "Bowel care review", "Review bowel symptoms, previous findings and the documented surveillance or specialist plan.", 30),
    _demo("musculoskeletal", ("M16", "M17", "M22", "M25", "M43", "M47", "M48", "M51", "M54", "M62", "M75", "M79", "G54", "G57", "G89", "Z966"), "Musculoskeletal rehabilitation review", "Review pain and functional progress, medication safety and suitability for physiotherapy or rehabilitation.", 14),
    _demo("rheumatology", ("M05", "M06", "M08", "M35", "L405"), "Inflammatory disease review", "Review joint symptoms, treatment response and medication monitoring with the treating clinician.", 21),
    _demo("bone", ("M81",), "Bone health review", "Review fracture risk, existing bone density results and treatment adherence.", 30),
    _demo("neurology", ("G20", "G24", "G35", "G37", "G40"), "Neurological care review", "Review symptom changes, medication tolerance and the existing specialist management plan.", 14),
    _demo("headache", ("G43", "G44", "R51"), "Headache management review", "Review headache frequency, triggers and medication use; assess whether further evaluation is indicated.", 10),
    _demo("sleep", ("G47",), "Sleep care review", "Review sleep symptoms and adherence to any prescribed sleep therapy; consider sleep-service follow-up.", 21),
    _demo("skin", ("L20", "L28", "L29", "L40", "L41", "L57", "L82", "D23"), "Skin treatment review", "Review lesion or rash changes, response to treatment and whether dermatology assessment is appropriate.", 14),
    _demo("urinary_symptoms", ("N393", "R30", "R35", "N20", "N11"), "Urinary symptom assessment", "Review urinary symptoms and previous investigations; choose testing or urology review based on the clinical findings, not antibiotic use alone.", 5),
    _demo("medication", ("Z79", "T47"), "Medication safety review", "Reconcile the medication list, indication, adverse effects and required monitoring with the prescriber.", 10),
    _demo("preventive", ("Z00", "Z01", "Z02", "Z09", "Z11", "Z12", "Z13", "Z23", "Z82", "Z87"), "Preventive care planning", "Review documented screening and vaccination history and individual eligibility; no disease worsening is inferred from a preventive encounter.", 30),
)

# These diagnoses must not receive a routine example appointment interval.
ACUTE_PREFIXES = ("I26", "I82", "I2511", "G45", "J96", "R079", "R56", "E87", "E1164", "E1100", "A35")


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
    if code.startswith(ACUTE_PREFIXES):
        return {**base, "status": "clinical_review_required",
                "title": "Clinical urgency review",
                "reason": "This diagnosis may describe an acute or serious condition. Confirm the current clinical situation and urgency; no routine follow-up interval is assigned from claims alone."}
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


def build_member_intervention_plans(claims):
    """Group a member's selectable claims into review plans without scoring savings."""
    grouped = {}
    for claim in sorted(claims, key=lambda row: (str(row.get("dos") or ""), str(row.get("claimId") or "")), reverse=True):
        plan = build_intervention_plan(claim)
        key = plan.get("scenario") or plan["matched_diagnosis"] or "unknown"
        if key not in grouped:
            grouped[key] = {**plan, "anchor_claim_id": claim.get("claimId"),
                            "source_claim_ids": [], "diagnosis_codes": []}
        item = grouped[key]
        item["source_claim_ids"].append(claim.get("claimId"))
        if plan["matched_diagnosis"] not in item["diagnosis_codes"]:
            item["diagnosis_codes"].append(plan["matched_diagnosis"])
    return list(grouped.values())
