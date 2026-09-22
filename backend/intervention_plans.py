"""Patient-specific intervention review proposals.

Recommendations are built from the recorded clinical inputs for the member
(diagnosis, symptoms, medications, allergies, laboratory results, prior
procedures, treatment history, previous outcomes and existing tests). They are
regenerated whenever those inputs change, so two members with the same
diagnosis but different recorded care receive different recommendations.

A recommendation is never made when its required evidence is missing. Test
recommendations are withheld until symptom or laboratory evidence exists, and
the response states exactly which evidence was used and which evidence limited
the recommendation.

Nothing in this module is recorded care, and nothing here establishes savings.
"""

from __future__ import annotations

from hashlib import sha256
import json

try:
    from . import evidence
    from .savings_validation import build_evidence_quality, build_savings_validation
except ImportError:  # pragma: no cover - script execution
    import evidence
    from savings_validation import build_evidence_quality, build_savings_validation

PROFILE_VERSION = "review-profiles-3"

INPUT_KEYS = (
    "diagnosis",
    "symptoms",
    "medications",
    "allergies",
    "labs",
    "prior_procedures",
    "treatment_history",
    "previous_outcomes",
    "existing_tests",
)

# History-derived inputs that can support a disease review when no explicit
# symptom/laboratory field is recorded.
HISTORY_INPUTS = ("prior_procedures", "treatment_history", "previous_outcomes", "existing_tests")
TEST_INPUTS = ("symptoms", "labs")

TEST_DESCRIPTION_TOKENS = (
    "culture",
    "laboratory",
    "lab ",
    "panel",
    "glucose",
    "a1c",
    "haemoglobin",
    "hemoglobin",
    "lipid",
    "thyroid",
    "tsh",
    "ultrasound",
    "mammograph",
    "radiograph",
    "x-ray",
    "imaging",
    "biopsy",
    "screen",
    "urinalysis",
    "test",
)

POSITIVE_OUTCOME_TERMS = ("resolved", "improved", "recovered", "normal", "controlled")
NEGATIVE_OUTCOME_TERMS = ("worsened", "worse", "unresolved", "ongoing", "deteriorat", "not improved")


def source_type(claim, dataset_synthetic=False):
    return evidence.source_type(claim, dataset_synthetic)


def _text(value):
    return "" if value is None else str(value).strip()


def _first_field(fields, aliases):
    for name in aliases:
        value = fields.get(name)
        if value not in (None, ""):
            text = _text(value)
            if text.lower() not in {"unknown", "not recorded", "n/a", "none"}:
                return text
    return None


def _claim_family(claim):
    fields = claim.get("workbookFields", claim)
    code = _text(
        claim.get("diagnosisCode")
        or fields.get("ICD10_Diagnosis_Code")
        or fields.get("ICD10_Family")
    ).upper().replace(".", "")
    return code[:3]


def _is_test_service(claim):
    description = _text(claim.get("cptDescription") or claim.get("workbookFields", {}).get("CPT_Description")).lower()
    cpt = _text(claim.get("cptCode"))
    return any(token in description for token in TEST_DESCRIPTION_TOKENS) or cpt in {
        "80053", "80061", "83036", "84443", "85025", "87086", "87088", "81001",
    }


def clinical_inputs(claims, anchor, dataset_synthetic=False):
    """Collect the recorded clinical inputs a recommendation may rely on."""
    fields = anchor.get("workbookFields", anchor)
    cutoff = _text(anchor.get("dos"))
    prior = [
        row for row in claims
        if row.get("claimId") != anchor.get("claimId")
        and _text(row.get("dos")) and (not cutoff or _text(row.get("dos")) <= cutoff)
        and evidence.source_type(row, dataset_synthetic) == evidence.source_type(anchor, dataset_synthetic)
    ]
    family = _claim_family(anchor)
    related = [row for row in prior if family and _claim_family(row) == family]
    inputs = {}
    evidence_used = []

    def record(key, value, field_name, source_claim=None):
        inputs[key] = value
        evidence_used.append({
            "input": key,
            "field": field_name,
            "value": value,
            "evidence_type": evidence.source_type(source_claim, dataset_synthetic) if source_claim
            else evidence.RECORDED_CLAIM_FACT,
            "evidence_label": evidence.label(
                evidence.source_type(source_claim, dataset_synthetic) if source_claim
                else evidence.RECORDED_CLAIM_FACT
            ),
            "claim_id": source_claim.get("claimId") if source_claim else anchor.get("claimId"),
            "service_date": source_claim.get("dos") if source_claim else anchor.get("dos"),
        })

    diagnosis = _text(anchor.get("diagnosisDescription") or anchor.get("diagnosisCode")
                      or fields.get("ICD10_Diagnosis_Description") or fields.get("ICD10_Diagnosis_Code"))
    if diagnosis:
        record("diagnosis", diagnosis, "ICD10_Diagnosis_Code/Description")

    symptom_fields = ("Symptoms", "Presenting_Symptoms", "Reported_Symptoms")
    value = _first_field(fields, symptom_fields)
    if value:
        record("symptoms", value, next(name for name in symptom_fields if _text(fields.get(name))))
    medication_fields = ("Medication_Name", "Current_Medications", "Medication", "Medications")
    value = _first_field(fields, medication_fields)
    if value:
        record("medications", value, next(name for name in medication_fields if _text(fields.get(name))))
    allergy_fields = ("Allergies", "Drug_Allergies", "Allergy_List")
    value = _first_field(fields, allergy_fields)
    if value:
        record("allergies", value, next(name for name in allergy_fields if _text(fields.get(name))))
    lab_fields = ("Lab_Results", "Test_Result", "Laboratory_Result", "Lab_Result")
    value = _first_field(fields, lab_fields)
    if value:
        record("labs", value, next(name for name in lab_fields if _text(fields.get(name))))

    procedures = [
        {"claim_id": row.get("claimId"), "service_date": row.get("dos"), "cpt": row.get("cptCode"),
         "description": row.get("cptDescription"),
         "evidence_type": evidence.source_type(row, dataset_synthetic)}
        for row in (related or prior) if row.get("cptCode")
    ]
    if procedures:
        record("prior_procedures", procedures[:10], "CPT_Code history", related[0] if related else prior[0])
    if related:
        record("treatment_history", {
            "related_claim_count": len(related),
            "claim_ids": [row.get("claimId") for row in related][:10],
            "latest_service_date": max(_text(row.get("dos")) for row in related),
        }, "Related member claims in the same diagnosis family", related[0])
    outcomes = []
    for row in related:
        row_fields = row.get("workbookFields", {})
        outcome = _text(row_fields.get("Treatment_Outcome") or row_fields.get("Condition_Resolved"))
        if outcome:
            outcomes.append({
                "claim_id": row.get("claimId"),
                "service_date": row.get("dos"),
                "outcome": outcome,
                "outcome_date": _text(row_fields.get("Outcome_Date")),
                "evidence_type": evidence.source_type(row, dataset_synthetic),
            })
    anchor_outcome = _text(fields.get("Treatment_Outcome") or fields.get("Condition_Resolved"))
    if not outcomes and anchor_outcome:
        outcomes.append({
            "claim_id": anchor.get("claimId"),
            "service_date": anchor.get("dos"),
            "outcome": anchor_outcome,
            "outcome_date": _text(fields.get("Outcome_Date")),
            "evidence_type": evidence.source_type(anchor, dataset_synthetic),
        })
    if outcomes:
        record("previous_outcomes", outcomes[:10], "Treatment_Outcome/Condition_Resolved",
               related[0] if related else anchor)

    tests = [
        {"claim_id": row.get("claimId"), "service_date": row.get("dos"), "cpt": row.get("cptCode"),
         "description": row.get("cptDescription")}
        for row in (related or prior) if _is_test_service(row)
    ]
    if tests:
        record("existing_tests", tests[:10], "Prior test CPT history", related[0] if related else prior[0])

    return {
        "inputs": inputs,
        "evidence_used": evidence_used,
        "missing_evidence": [key for key in INPUT_KEYS if key not in inputs],
        "history": {
            "history_start": min((_text(row.get("dos")) for row in prior), default=None),
            "history_end": cutoff or None,
            "history_claim_count": len(prior),
        },
    }


def _outcome_trend(outcomes):
    if not outcomes:
        return None
    if isinstance(outcomes, list):
        text = " ".join(_text(item.get("outcome")) for item in outcomes).lower()
    else:
        text = _text(outcomes).lower()
    if any(term in text for term in NEGATIVE_OUTCOME_TERMS):
        return "worsening"
    if any(term in text for term in POSITIVE_OUTCOME_TERMS):
        return "improving"
    return None


def _compose_action(spec, inputs, outcome_trend):
    """Build the recommendation text from the exact inputs that are recorded."""
    clauses = [spec["review_action"]]
    if "existing_tests" in inputs and inputs["existing_tests"]:
        first = inputs["existing_tests"][0]
        clauses.append(
            f"A test service is already recorded ({first.get('cpt') or 'procedure'} on "
            f"{first.get('service_date') or 'an unrecorded date'}); do not repeat it without a new "
            "clinical indication."
        )
    if "labs" in inputs and inputs["labs"]:
        clauses.append(f"Recorded laboratory result: {inputs['labs']}.")
    if "symptoms" in inputs and inputs["symptoms"]:
        clauses.append(f"Recorded symptoms: {inputs['symptoms']}.")
    if "medications" in inputs and inputs["medications"]:
        clauses.append(f"Recorded medication: {inputs['medications']}.")
    if "allergies" in inputs and inputs["allergies"]:
        clauses.append(f"Recorded allergies: {inputs['allergies']}.")
    history = inputs.get("treatment_history")
    if isinstance(history, dict) and history.get("related_claim_count"):
        clauses.append(
            f"{history['related_claim_count']} related claim(s) in this diagnosis family are recorded, "
            f"most recently {history.get('latest_service_date') or 'at an unknown date'}."
        )
    if outcome_trend == "worsening":
        clauses.append("The most recent recorded outcome indicates worsening; review urgency before scheduling.")
    elif outcome_trend == "improving":
        clauses.append("The most recent recorded outcome indicates improvement; confirm before adding care.")
    return " ".join(clauses)


def _timing_for(spec, outcome_trend):
    days = spec["follow_up_days"]
    if outcome_trend == "worsening":
        return max(1, days // 2), f"Shortened because the recorded outcome is worsening. {spec['timing']}"
    if outcome_trend == "improving":
        return days * 2, f"Extended because the recorded outcome is improving. {spec['timing']}"
    return days, spec["timing"]


def _profile_specs():
    """Scenario specifications. Intervals are illustrative demo configuration."""
    return (
        dict(key="urinary", prefixes=("N300", "N309", "N390"), title="Urinary infection review",
             review_action="Review the recorded urinary symptoms, prior treatment and any existing culture result before changing treatment.",
             test_action="Consider urine culture and susceptibility testing if symptoms persist or worsen after antibiotics, and review treatment against the results.",
             follow_up_days=2, timing="Reassess if symptoms do not improve within 48 hours of antibiotics, or sooner if worsening.",
             timing_basis="conditional_guidance", source_url="https://www.nice.org.uk/guidance/ng109/chapter/Recommendations"),
        dict(key="diabetes", prefixes=("E10", "E11", "E13"), title="Diabetes management review",
             review_action="Review glucose results, medication adherence and whether diabetes education or further monitoring is appropriate.",
             test_action="Consider metabolic monitoring (HbA1c or glucose testing) when the recorded results or symptoms support it.",
             follow_up_days=14, timing="Illustrative review interval; clinician to determine timing from glucose results and symptoms.",
             timing_basis="illustrative_demo", source_url=None),
        dict(key="hypertension", prefixes=("I10",), title="Blood pressure review",
             review_action="Review home blood pressure readings, medication adherence and the need for treatment assessment.",
             test_action=None,
             follow_up_days=30, timing="Illustrative review interval; clinician to determine timing from blood pressure and symptoms.",
             timing_basis="illustrative_demo", source_url=None),
        dict(key="gynecological", prefixes=("N91", "N92", "N93", "N94"), title="Gynecological symptom review",
             review_action="Review the menstrual or pelvic symptom history and consider targeted investigation or gynecology referral when indicated.",
             test_action="Consider targeted gynecological investigation only when the recorded symptoms support it.",
             follow_up_days=10, timing="Illustrative review interval; clinician to determine timing and investigations from symptoms.",
             timing_basis="illustrative_demo", source_url=None),
        dict(key="thyroid", prefixes=("E01", "E03", "E06", "E890"), title="Thyroid monitoring review",
             review_action="Review thyroid results and medication use; consider whether TSH or free T4 monitoring is due.",
             test_action="Consider TSH or free T4 monitoring when recorded results or symptoms support it.",
             follow_up_days=42, timing="Illustrative scenario timing only; confirm symptoms, treatment and urgency before scheduling.",
             timing_basis="illustrative_demo", source_url=None),
        dict(key="lipids", prefixes=("E78",), title="Lipid management review",
             review_action="Review lipid results, cardiovascular risk and medication tolerance.",
             test_action="Consider a lipid panel when the recorded results are due or symptoms support it.",
             follow_up_days=30, timing="Illustrative scenario timing only; confirm symptoms, treatment and urgency before scheduling.",
             timing_basis="illustrative_demo", source_url=None),
        dict(key="metabolic", prefixes=("E66", "R73"), title="Metabolic risk review",
             review_action="Review glucose trends, nutrition and activity support; assess whether metabolic testing is appropriate.",
             test_action="Consider metabolic testing only when recorded results support it.",
             follow_up_days=21, timing="Illustrative scenario timing only; confirm symptoms, treatment and urgency before scheduling.",
             timing_basis="illustrative_demo", source_url=None),
        dict(key="respiratory", prefixes=("J00", "J02", "J03", "J06", "J09", "J10", "J11", "J18", "J22", "R05", "R070"),
             title="Respiratory symptom reassessment",
             review_action="Review persistent respiratory symptoms and prior treatment; consider targeted testing only when clinically indicated.",
             test_action="Consider targeted respiratory testing only when the recorded symptoms support it.",
             follow_up_days=3, timing="Illustrative scenario timing only; confirm symptoms, treatment and urgency before scheduling.",
             timing_basis="illustrative_demo", source_url=None),
        dict(key="airways", prefixes=("J41", "J44", "J45", "J98", "J68"), title="Airway management review",
             review_action="Review inhaler technique, adherence, symptom control and the existing respiratory action plan.",
             test_action=None,
             follow_up_days=5, timing="Illustrative scenario timing only; confirm symptoms, treatment and urgency before scheduling.",
             timing_basis="illustrative_demo", source_url=None),
        dict(key="sinus", prefixes=("J32",), title="Sinus symptom review",
             review_action="Review persistent sinus symptoms and treatment response; consider specialist assessment if appropriate.",
             test_action=None,
             follow_up_days=14, timing="Illustrative scenario timing only; confirm symptoms, treatment and urgency before scheduling.",
             timing_basis="illustrative_demo", source_url=None),
        dict(key="mental_health", prefixes=("F20", "F31", "F32", "F33", "F41", "F43", "Z630"), title="Mental health follow-up",
             review_action="Review symptom scores, medication effects, safety and access to psychological support.",
             test_action=None,
             follow_up_days=10, timing="Illustrative scenario timing only; confirm symptoms, treatment and urgency before scheduling.",
             timing_basis="illustrative_demo", source_url=None),
        dict(key="renal", prefixes=("N18", "I12", "I13", "Z992"), title="Kidney care review",
             review_action="Review renal function, medication safety and the existing nephrology or dialysis care plan.",
             test_action="Consider renal function monitoring when recorded results support it.",
             follow_up_days=14, timing="Illustrative scenario timing only; confirm symptoms, treatment and urgency before scheduling.",
             timing_basis="illustrative_demo", source_url=None),
        dict(key="cardiac", prefixes=("I11", "I25", "I34", "I44", "I48", "I50", "I65", "R00", "Z950"), title="Cardiac care review",
             review_action="Review symptoms, blood pressure or rhythm records, medication use and the existing cardiology plan.",
             test_action="Consider cardiac monitoring or testing when recorded symptoms or results support it.",
             follow_up_days=10, timing="Illustrative scenario timing only; confirm symptoms, treatment and urgency before scheduling.",
             timing_basis="illustrative_demo", source_url=None),
        dict(key="anemia", prefixes=("D50", "D64"), title="Anemia investigation review",
             review_action="Review blood counts and previous investigations; consider whether iron studies or assessment of the underlying cause is needed.",
             test_action="Consider iron studies or blood-count testing when recorded results support it.",
             follow_up_days=21, timing="Illustrative scenario timing only; confirm symptoms, treatment and urgency before scheduling.",
             timing_basis="illustrative_demo", source_url=None),
        dict(key="oncology", prefixes=("C18", "C50", "C56", "C79", "D05", "Z5111", "Z853"), title="Oncology care coordination",
             review_action="Review the treating team's monitoring schedule, treatment effects and outstanding results; coordinate with oncology.",
             test_action=None,
             follow_up_days=14, timing="Illustrative scenario timing only; confirm symptoms, treatment and urgency before scheduling.",
             timing_basis="illustrative_demo", source_url=None),
        dict(key="breast", prefixes=("D24", "N63"), title="Breast assessment review",
             review_action="Review examination findings, existing imaging and any outstanding breast clinic referral.",
             test_action="Consider breast imaging when recorded findings support it.",
             follow_up_days=10, timing="Illustrative scenario timing only; confirm symptoms, treatment and urgency before scheduling.",
             timing_basis="illustrative_demo", source_url=None),
        dict(key="reflux", prefixes=("K21", "K22", "K31", "R12"), title="Digestive symptom review",
             review_action="Review reflux or digestive symptoms and medication response; assess whether further investigation is indicated.",
             test_action="Consider further digestive investigation when recorded symptoms support it.",
             follow_up_days=14, timing="Illustrative scenario timing only; confirm symptoms, treatment and urgency before scheduling.",
             timing_basis="illustrative_demo", source_url=None),
        dict(key="colon", prefixes=("K57", "K63"), title="Bowel care review",
             review_action="Review bowel symptoms, previous findings and the documented surveillance or specialist plan.",
             test_action=None,
             follow_up_days=30, timing="Illustrative scenario timing only; confirm symptoms, treatment and urgency before scheduling.",
             timing_basis="illustrative_demo", source_url=None),
        dict(key="musculoskeletal", prefixes=("M16", "M17", "M22", "M25", "M43", "M47", "M48", "M51", "M54", "M62", "M75", "M79", "G54", "G57", "G89", "Z966"),
             title="Musculoskeletal rehabilitation review",
             review_action="Review pain and functional progress, medication safety and suitability for physiotherapy or rehabilitation.",
             test_action=None,
             follow_up_days=14, timing="Illustrative scenario timing only; confirm symptoms, treatment and urgency before scheduling.",
             timing_basis="illustrative_demo", source_url=None),
        dict(key="rheumatology", prefixes=("M05", "M06", "M08", "M35", "L405"), title="Inflammatory disease review",
             review_action="Review joint symptoms, treatment response and medication monitoring with the treating clinician.",
             test_action="Consider inflammatory monitoring when recorded results support it.",
             follow_up_days=21, timing="Illustrative scenario timing only; confirm symptoms, treatment and urgency before scheduling.",
             timing_basis="illustrative_demo", source_url=None),
        dict(key="bone", prefixes=("M81",), title="Bone health review",
             review_action="Review fracture risk, existing bone density results and treatment adherence.",
             test_action="Consider bone density assessment when recorded results support it.",
             follow_up_days=30, timing="Illustrative scenario timing only; confirm symptoms, treatment and urgency before scheduling.",
             timing_basis="illustrative_demo", source_url=None),
        dict(key="neurology", prefixes=("G20", "G24", "G35", "G37", "G40"), title="Neurological care review",
             review_action="Review symptom changes, medication tolerance and the existing specialist management plan.",
             test_action=None,
             follow_up_days=14, timing="Illustrative scenario timing only; confirm symptoms, treatment and urgency before scheduling.",
             timing_basis="illustrative_demo", source_url=None),
        dict(key="headache", prefixes=("G43", "G44", "R51"), title="Headache management review",
             review_action="Review headache frequency, triggers and medication use; assess whether further evaluation is indicated.",
             test_action=None,
             follow_up_days=10, timing="Illustrative scenario timing only; confirm symptoms, treatment and urgency before scheduling.",
             timing_basis="illustrative_demo", source_url=None),
        dict(key="sleep", prefixes=("G47",), title="Sleep care review",
             review_action="Review sleep symptoms and adherence to any prescribed sleep therapy; consider sleep-service follow-up.",
             test_action=None,
             follow_up_days=21, timing="Illustrative scenario timing only; confirm symptoms, treatment and urgency before scheduling.",
             timing_basis="illustrative_demo", source_url=None),
        dict(key="skin", prefixes=("L20", "L28", "L29", "L40", "L41", "L57", "L82", "D23"), title="Skin treatment review",
             review_action="Review lesion or rash changes, response to treatment and whether dermatology assessment is appropriate.",
             test_action=None,
             follow_up_days=14, timing="Illustrative scenario timing only; confirm symptoms, treatment and urgency before scheduling.",
             timing_basis="illustrative_demo", source_url=None),
        dict(key="urinary_symptoms", prefixes=("N393", "R30", "R35", "N20", "N11"), title="Urinary symptom assessment",
             review_action="Review urinary symptoms and previous investigations; choose testing or urology review based on the clinical findings, not antibiotic use alone.",
             test_action="Consider urine testing only when the recorded symptoms support it.",
             follow_up_days=5, timing="Illustrative scenario timing only; confirm symptoms, treatment and urgency before scheduling.",
             timing_basis="illustrative_demo", source_url=None),
        dict(key="medication", prefixes=("Z79", "T47"), title="Medication safety review",
             review_action="Reconcile the medication list, indication, adverse effects and required monitoring with the prescriber.",
             test_action=None,
             follow_up_days=10, timing="Illustrative scenario timing only; confirm symptoms, treatment and urgency before scheduling.",
             timing_basis="illustrative_demo", source_url=None),
        dict(key="preventive", prefixes=("Z00", "Z01", "Z02", "Z09", "Z11", "Z12", "Z13", "Z23", "Z82", "Z87"), title="Preventive care planning",
             review_action="Review documented screening and vaccination history and individual eligibility; no disease worsening is inferred from a preventive encounter.",
             test_action="Consider a screening service only when population eligibility and recorded history support it.",
             follow_up_days=30, timing="Illustrative scenario timing only; confirm symptoms, treatment and urgency before scheduling.",
             timing_basis="illustrative_demo", source_url=None),
    )


PROFILES = _profile_specs()
PROFILE_BY_KEY = {spec["key"]: spec for spec in PROFILES}

# These diagnoses must not receive a routine example appointment interval.
ACUTE_PREFIXES = ("I26", "I82", "I2511", "G45", "J96", "R079", "R56", "E87", "E1164", "E1100", "A35")


def _base_plan(code):
    return {
        "status": "insufficient_evidence",
        "available": False,
        "recommendation_available": False,
        "test_recommendation_available": False,
        "recorded": False,
        "requires_clinical_review": True,
        "included_in_savings": False,
        "matched_diagnosis": code,
        "follow_up_days": None,
        "evidence_used": [],
        "missing_evidence": list(INPUT_KEYS),
        "clinical_inputs": {},
    }


def build_intervention_plan(claim, clinical=None, dataset_synthetic=False):
    """Build one patient-specific intervention plan for a claim.

    ``clinical`` is the output of :func:`clinical_inputs`; when omitted only the
    claim itself is available, which typically means the plan is evidence-limited.
    """
    fields = claim.get("workbookFields", claim)
    code = _text(
        claim.get("diagnosisCode") or fields.get("ICD10_Diagnosis_Code") or ""
    ).upper().replace(".", "").strip()
    base = _base_plan(code)
    if clinical is None:
        clinical = clinical_inputs([claim], claim, dataset_synthetic)
    inputs = clinical["inputs"]
    history = clinical["history"]
    quality = build_evidence_quality(
        history_start=history.get("history_start"),
        history_end=history.get("history_end"),
        history_claim_count=history.get("history_claim_count", 0),
        missing_evidence=[] if inputs else ["clinical evidence"],
        recorded_inputs=sorted(inputs),
    )
    base.update(
        evidence_type=evidence.RECOMMENDATION,
        evidence_label=evidence.label(evidence.RECOMMENDATION),
        evidence_used=clinical["evidence_used"],
        missing_evidence=[key for key in INPUT_KEYS if key not in inputs],
        clinical_inputs=inputs,
        evidence_quality={
            **quality,
            "recorded_context": {key: value for key, value in inputs.items() if key in INPUT_KEYS},
            "financial_evidence": "No verified savings. Recorded charges, payments and reviewer outcomes do not establish an intervention effect.",
        },
        source_data_type=evidence.source_type(claim, dataset_synthetic),
    )
    if not code:
        base["reason"] = (
            "No diagnosis code is recorded, so no intervention can be matched or recommended."
        )
        return base
    if code.startswith(ACUTE_PREFIXES):
        base.update(
            status="clinical_review_required",
            reason=(
                "This diagnosis may describe an acute or serious condition. Confirm the current "
                "clinical situation and urgency; no routine follow-up interval is assigned from claims alone."
            ),
        )
        return base

    spec = next((item for item in PROFILES if code.startswith(item["prefixes"])), None)
    if spec is None:
        base["reason"] = (
            "No specific intervention profile matches the recorded diagnosis. Review the clinical "
            "record before choosing an intervention or timing."
        )
        return base

    supporting_history = [key for key in HISTORY_INPUTS if key in inputs]
    if not supporting_history:
        base.update(
            status="insufficient_evidence",
            scenario=spec["key"],
            title=spec["title"],
            reason=(
                "A disease-specific recommendation requires recorded treatment history or a previous "
                "outcome for this diagnosis family. None is recorded, so no intervention is recommended."
            ),
            missing_evidence=[key for key in INPUT_KEYS if key not in inputs],
            limiting_evidence=[
                "treatment history for this diagnosis family",
                "previous outcome for this diagnosis family",
                "prior procedures for this diagnosis family",
                "existing tests for this diagnosis family",
            ],
        )
        return base

    outcome_trend = _outcome_trend(inputs.get("previous_outcomes"))
    action = _compose_action(spec, inputs, outcome_trend)
    days, timing = _timing_for(spec, outcome_trend)
    test_evidence = [key for key in TEST_INPUTS if key in inputs]
    test_available = bool(spec["test_action"]) and bool(test_evidence)
    limiting_evidence = []
    if spec["test_action"] and not test_available:
        limiting_evidence = [
            "recorded symptoms that justify a test",
            "recorded laboratory result that justifies a test",
        ]
        action = (
            f"{action} A diagnostic test is not recommended here because neither symptom nor "
            "laboratory evidence is recorded; record that evidence first."
        )
    else:
        action = f"{action} {spec['test_action']}" if spec["test_action"] else action

    return {
        **base,
        "status": "proposed_for_review",
        "available": True,
        "recommendation_available": True,
        "test_recommendation_available": test_available,
        "scenario": spec["key"],
        "title": spec["title"],
        "action": action,
        "review_action": spec["review_action"],
        "test_action": spec["test_action"],
        "follow_up_days": days,
        "timing": timing,
        "timing_basis": spec["timing_basis"],
        "source_url": spec["source_url"],
        "outcome_trend": outcome_trend,
        "limiting_evidence": limiting_evidence,
        "reason": (
            "Matched to the recorded diagnosis and built only from this member's recorded inputs; "
            "symptoms, treatment history and suitability must still be confirmed."
        ),
        "evidence_quality": build_evidence_quality(
            history_start=history.get("history_start"),
            history_end=history.get("history_end"),
            history_claim_count=history.get("history_claim_count", 0),
            missing_evidence=[key for key in INPUT_KEYS if key not in inputs],
            recorded_inputs=sorted(inputs),
            cohort_claim_count=(inputs.get("treatment_history") or {}).get("related_claim_count", 0)
            if isinstance(inputs.get("treatment_history"), dict) else 0,
            data_source_type=evidence.source_type(claim, dataset_synthetic),
        ),
    }


def _cohort_claims(claims, anchor):
    family = _claim_family(anchor)
    return [row for row in claims
            if row.get("claimId") != anchor.get("claimId")
            and family and _claim_family(row) == family
            and str(row.get("dos") or "") < str(anchor.get("dos") or "")
            and evidence.source_type(row) == evidence.source_type(anchor)
            and row.get("paid") is not None]


def _cohort_paid_values(claims, anchor):
    return [float(row["paid"]) for row in _cohort_claims(claims, anchor)]


def build_member_intervention_plans(claims, dataset_synthetic=False, workbook_hash=""):
    """Group a member's selectable claims into patient-specific review plans."""
    grouped = {}
    claims = list(claims)
    for claim in sorted(
        claims,
        key=lambda row: (str(row.get("dos") or ""), str(row.get("claimId") or "")),
        reverse=True,
    ):
        clinical = clinical_inputs(claims, claim, dataset_synthetic)
        plan = build_intervention_plan(claim, clinical, dataset_synthetic)
        key = plan.get("scenario") or plan["matched_diagnosis"] or "unknown"
        if key not in grouped:
            plan = dict(plan)
            plan.update(
                anchor_claim_id=claim.get("claimId"),
                anchor_service_date=claim.get("dos"),
                source_data_type=evidence.source_type(claim, dataset_synthetic),
                profile_version=PROFILE_VERSION,
                source_claim_ids=[],
                diagnosis_codes=[],
            )
            identity = json.dumps([
                workbook_hash, claim.get("memberId"), key, claim.get("claimId"), PROFILE_VERSION,
                plan.get("action"), plan.get("follow_up_days"),
            ])
            plan["review_id"] = sha256(identity.encode()).hexdigest()
            plan["evidence_quality"]["claims_reviewed"] = len(claims)
            plan["evidence_quality"]["data_source_type"] = plan["source_data_type"]
            plan["evidence_quality"]["history_start"] = plan["evidence_quality"].get("history_start") or claim.get("dos")
            plan["evidence_quality"]["history_end"] = plan["evidence_quality"].get("history_end") or claim.get("dos")
            plan["evidence_type"] = evidence.RECOMMENDATION
            plan["evidence_label"] = evidence.label(evidence.RECOMMENDATION)
            grouped[key] = plan
        item = grouped[key]
        item["source_claim_ids"].append(claim.get("claimId"))
        if plan["matched_diagnosis"] not in item["diagnosis_codes"]:
            item["diagnosis_codes"].append(plan["matched_diagnosis"])
    for plan in grouped.values():
        plan["savings_validation"] = _plan_savings_validation(claims, plan)
        validation = plan["savings_validation"]
        if validation:
            cohort = validation["comparison_cohort"]
            follow_up = validation["follow_up_completeness"]
            plan["evidence_quality"].update({
                "cohort_size": cohort["size"],
                "comparison_strength": f"Within-member historical reference: {cohort['size']} earlier claim(s); not an independent comparison cohort.",
                "matching_strength": cohort["similarity"],
                "matching_strength_label": cohort["matching_strength_label"],
                "follow_up_completeness": follow_up["ratio"],
                "follow_up_status": follow_up["status"],
            })
    return list(grouped.values())


def _plan_savings_validation(claims, plan):
    anchor_claim = next(
        (row for row in claims if row.get("claimId") == plan.get("anchor_claim_id")), None
    )
    if anchor_claim is None:
        return None
    cohort_claims = _cohort_claims(claims, anchor_claim)
    cohort_values = [float(row["paid"]) for row in cohort_claims]
    related_after = [
        {"claim_id": row.get("claimId"), "service_date": row.get("dos"),
         "paid": row.get("paid"),
         "related": str(row.get("workbookFields", {}).get("Related_Claim_Flag") or "").upper() == "Y"}
        for row in claims
        if row.get("claimId") != anchor_claim.get("claimId")
        and str(row.get("dos") or "") > str(anchor_claim.get("dos") or "")
        and _claim_family(row) == _claim_family(anchor_claim)
    ]
    matched_dimensions = {
        "same diagnosis family": True,
        "same payer": all(
            row.get("payer") == anchor_claim.get("payer")
            for row in claims if _claim_family(row) == _claim_family(anchor_claim)
        ),
        "related claim flag": bool(related_after),
        "recorded outcome": bool(plan.get("clinical_inputs", {}).get("previous_outcomes")),
        "similar units": any(
            row.get("units") == anchor_claim.get("units") for row in claims
            if _claim_family(row) == _claim_family(anchor_claim)
        ),
    }
    expected_follow_up = max(len(related_after), 1)
    observed_follow_up = sum(
        1 for row in related_after
        if str(row.get("related")).upper() == "TRUE" or row.get("related") is True
    )
    return build_savings_validation(
        anchor_service_date=anchor_claim.get("dos"),
        intervention_date=None,
        predicted_opportunity=None,
        billed_charge=float(anchor_claim.get("totalCharge") or 0),
        paid_amount=float(anchor_claim.get("paid") or 0),
        estimated_savings=None,
        cohort_paid_values=cohort_values,
        cohort_claim_ids=[row.get("claimId") for row in cohort_claims][:20],
        cohort_member_count=1 if cohort_claims else 0,
        matching_dimensions=matched_dimensions,
        expected_follow_up_contacts=expected_follow_up,
        observed_follow_up_contacts=observed_follow_up,
        verification_claims=related_after,
        review=(plan.get("review") or {}),
        source_data_type=plan.get("source_data_type", evidence.RECORDED_CLAIM_FACT),
    )
