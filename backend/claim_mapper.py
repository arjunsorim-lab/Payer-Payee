from datetime import datetime, timezone

CURRENCY_FIELDS = {
    "Charge_Amount",
    "Allowed_Amount",
    "Paid_Amount",
    "Patient_Responsibility",
    "Adjustment_Amount",
}


def parse_amount(value):
    try:
        return round(float(value or 0), 2)
    except (TypeError, ValueError):
        return 0


def parse_integer(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def parse_edi_date(value):
    text = str(value or "").strip()
    if len(text) != 8 or not text.isdigit():
        return ""
    return f"{text[:4]}-{text[4:6]}-{text[6:8]}"


def parse_edi_timestamp(value):
    text = str(value or "").strip()
    if len(text) != 14 or not text.isdigit():
        return ""

    hour = int(text[8:10])
    minute = text[10:12]
    period = "PM" if hour >= 12 else "AM"
    hour = hour % 12 or 12
    return f"{text[:4]}-{text[4:6]}-{text[6:8]} {hour}:{minute} {period}"


def format_claim_number(claim_id):
    digits = "".join(character for character in str(claim_id or "") if character.isdigit())
    return f"CLM-{digits[-6:].rjust(6, '0')}"


def clean_raw_row(row):
    cleaned = {}
    for key, value in row.items():
        if key in CURRENCY_FIELDS:
            cleaned[key] = parse_amount(value)
        elif key == "Units":
            cleaned[key] = parse_integer(value)
        else:
            cleaned[key] = value.strip() if isinstance(value, str) else value
    return cleaned


def normalize_claim(row):
    first_name = str(row.get("Patient_First_Name", "") or "").strip()
    last_name = str(row.get("Patient_Last_Name", "") or "").strip()
    claim_id = str(row.get("Claim_ID", "") or "").strip()

    return {
        "claimId": claim_id,
        "number": format_claim_number(claim_id),
        "memberClaimNumber": str(row.get("Claim_Number_For_Member", "") or "").strip(),
        "memberId": str(row.get("Member_ID", "") or "").strip(),
        "groupId": str(row.get("Group_ID", "") or "").strip(),
        "groupName": str(row.get("Group_Name", "") or "").strip(),
        "payer": str(row.get("Payer_Name", "") or "").strip(),
        "payerId": str(row.get("Payer_ID", "") or "").strip(),
        "patient": " ".join(part for part in [first_name, last_name] if part),
        "patientFirstName": first_name,
        "patientLastName": last_name,
        "dob": parse_edi_date(row.get("Patient_DOB")),
        "gender": str(row.get("Patient_Gender", "") or "").strip(),
        "accountNumber": str(row.get("Patient_Account_Number", "") or "").strip(),
        "subscriberId": str(row.get("Subscriber_Member_ID", "") or "").strip(),
        "billingProviderNpi": str(row.get("Billing_Provider_NPI", "") or "").strip(),
        "billingProvider": str(row.get("Billing_Provider_Name", "") or "").strip(),
        "renderingProviderNpi": str(row.get("Rendering_Provider_NPI", "") or "").strip(),
        "renderingProvider": str(row.get("Rendering_Provider_Name", "") or "").strip(),
        "dos": parse_edi_date(row.get("Service_Date_From")),
        "serviceEnd": parse_edi_date(row.get("Service_Date_To")),
        "placeOfServiceCode": str(row.get("Place_of_Service_Code", "") or "").strip(),
        "placeOfService": str(row.get("Place_of_Service_Description", "") or "").strip(),
        "cptCode": str(row.get("CPT_Code", "") or "").strip(),
        "cptDescription": str(row.get("CPT_Description", "") or "").strip(),
        "diagnosisCode": str(row.get("ICD10_Diagnosis_Code", "") or "").strip(),
        "diagnosisDescription": str(row.get("ICD10_Diagnosis_Description", "") or "").strip(),
        "units": parse_integer(row.get("Units")),
        "totalCharge": parse_amount(row.get("Charge_Amount")),
        "allowed": parse_amount(row.get("Allowed_Amount")),
        "paid": parse_amount(row.get("Paid_Amount")),
        "patientResp": parse_amount(row.get("Patient_Responsibility")),
        "adjustment": parse_amount(row.get("Adjustment_Amount")),
        "statusCode": str(row.get("Claim_Status_Code", "") or "").strip(),
        "status": str(row.get("Claim_Status_Description", "") or "").strip(),
        "denialReason": str(row.get("Denial_Reason", "") or "").strip(),
        "filingIndicator": str(row.get("Claim_Filing_Indicator", "") or "").strip(),
        "priorAuth": str(row.get("Prior_Auth_Number", "") or "").strip(),
        "referral": str(row.get("Referral_Number", "") or "").strip(),
        "transactionVersion": str(row.get("HIPAA_Transaction_Version", "") or "").strip(),
        "submissionDate": parse_edi_date(row.get("Submission_Date")),
        "createdAt": parse_edi_timestamp(row.get("Created_Timestamp")),
        "edi": {
            "isaControlNumber": str(row.get("ISA_Control_Number", "") or "").strip(),
            "gsControlNumber": str(row.get("GS_Control_Number", "") or "").strip(),
            "stTransactionSetId": str(row.get("ST_Transaction_Set_ID", "") or "").strip(),
        },
        "raw": clean_raw_row(row),
        "importedAt": datetime.now(timezone.utc),
    }


def build_member_documents(claims):
    grouped = {}
    for claim in claims:
        grouped.setdefault(claim["memberId"], []).append(claim)

    documents = []
    for member_id, member_claims in grouped.items():
        sorted_claims = sorted(member_claims, key=lambda claim: claim.get("dos", ""), reverse=True)
        latest_claim = sorted_claims[0]
        denied_count = sum(1 for claim in sorted_claims if claim.get("status") == "Denied")

        documents.append({
            "memberId": member_id,
            "patient": latest_claim.get("patient", ""),
            "patientFirstName": latest_claim.get("patientFirstName", ""),
            "patientLastName": latest_claim.get("patientLastName", ""),
            "dob": latest_claim.get("dob", ""),
            "gender": latest_claim.get("gender", ""),
            "subscriberId": latest_claim.get("subscriberId", ""),
            "groupId": latest_claim.get("groupId", ""),
            "groupName": latest_claim.get("groupName", ""),
            "primaryPayer": latest_claim.get("payer", ""),
            "latestServiceDate": latest_claim.get("dos", ""),
            "latestClaimNumber": latest_claim.get("number", ""),
            "billingProviders": sorted({claim.get("billingProvider", "") for claim in sorted_claims if claim.get("billingProvider")}),
            "payers": sorted({claim.get("payer", "") for claim in sorted_claims if claim.get("payer")}),
            "claimCount": len(sorted_claims),
            "totalCharges": round(sum(claim.get("totalCharge", 0) for claim in sorted_claims), 2),
            "totalAllowed": round(sum(claim.get("allowed", 0) for claim in sorted_claims), 2),
            "totalPaid": round(sum(claim.get("paid", 0) for claim in sorted_claims), 2),
            "totalPatientResp": round(sum(claim.get("patientResp", 0) for claim in sorted_claims), 2),
            "totalAdjustment": round(sum(claim.get("adjustment", 0) for claim in sorted_claims), 2),
            "deniedClaimCount": denied_count,
            "importedAt": datetime.now(timezone.utc),
        })

    return documents
