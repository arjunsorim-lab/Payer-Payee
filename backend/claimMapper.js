const currencyFields = new Set([
  'Charge_Amount',
  'Allowed_Amount',
  'Paid_Amount',
  'Patient_Responsibility',
  'Adjustment_Amount',
])

function parseAmount(value) {
  const numberValue = Number(value)
  return Number.isFinite(numberValue) ? Number(numberValue.toFixed(2)) : 0
}

function parseInteger(value) {
  const numberValue = Number.parseInt(value, 10)
  return Number.isFinite(numberValue) ? numberValue : 0
}

function parseEdiDate(value) {
  const text = String(value || '').trim()
  if (!/^\d{8}$/.test(text)) return ''
  return `${text.slice(0, 4)}-${text.slice(4, 6)}-${text.slice(6, 8)}`
}

function parseEdiTimestamp(value) {
  const text = String(value || '').trim()
  if (!/^\d{14}$/.test(text)) return ''

  const year = text.slice(0, 4)
  const month = text.slice(4, 6)
  const day = text.slice(6, 8)
  let hour = Number(text.slice(8, 10))
  const minute = text.slice(10, 12)
  const period = hour >= 12 ? 'PM' : 'AM'
  hour = hour % 12 || 12

  return `${year}-${month}-${day} ${hour}:${minute} ${period}`
}

function formatClaimNumber(claimId) {
  const digits = String(claimId || '').replace(/\D/g, '')
  return `CLM-${digits.slice(-6).padStart(6, '0')}`
}

function cleanRawRow(row) {
  return Object.fromEntries(
    Object.entries(row).map(([key, value]) => {
      if (currencyFields.has(key)) return [key, parseAmount(value)]
      if (key === 'Units') return [key, parseInteger(value)]
      return [key, typeof value === 'string' ? value.trim() : value]
    }),
  )
}

export function normalizeClaim(row) {
  const firstName = row.Patient_First_Name?.trim() || ''
  const lastName = row.Patient_Last_Name?.trim() || ''
  const claimId = row.Claim_ID?.trim() || ''

  return {
    claimId,
    number: formatClaimNumber(claimId),
    memberClaimNumber: row.Claim_Number_For_Member?.trim() || '',
    memberId: row.Member_ID?.trim() || '',
    groupId: row.Group_ID?.trim() || '',
    groupName: row.Group_Name?.trim() || '',
    payer: row.Payer_Name?.trim() || '',
    payerId: row.Payer_ID?.trim() || '',
    patient: [firstName, lastName].filter(Boolean).join(' '),
    patientFirstName: firstName,
    patientLastName: lastName,
    dob: parseEdiDate(row.Patient_DOB),
    gender: row.Patient_Gender?.trim() || '',
    accountNumber: row.Patient_Account_Number?.trim() || '',
    subscriberId: row.Subscriber_Member_ID?.trim() || '',
    billingProviderNpi: row.Billing_Provider_NPI?.trim() || '',
    billingProvider: row.Billing_Provider_Name?.trim() || '',
    renderingProviderNpi: row.Rendering_Provider_NPI?.trim() || '',
    renderingProvider: row.Rendering_Provider_Name?.trim() || '',
    dos: parseEdiDate(row.Service_Date_From),
    serviceEnd: parseEdiDate(row.Service_Date_To),
    placeOfServiceCode: row.Place_of_Service_Code?.trim() || '',
    placeOfService: row.Place_of_Service_Description?.trim() || '',
    cptCode: row.CPT_Code?.trim() || '',
    cptDescription: row.CPT_Description?.trim() || '',
    diagnosisCode: row.ICD10_Diagnosis_Code?.trim() || '',
    diagnosisDescription: row.ICD10_Diagnosis_Description?.trim() || '',
    units: parseInteger(row.Units),
    totalCharge: parseAmount(row.Charge_Amount),
    allowed: parseAmount(row.Allowed_Amount),
    paid: parseAmount(row.Paid_Amount),
    patientResp: parseAmount(row.Patient_Responsibility),
    adjustment: parseAmount(row.Adjustment_Amount),
    statusCode: row.Claim_Status_Code?.trim() || '',
    status: row.Claim_Status_Description?.trim() || '',
    denialReason: row.Denial_Reason?.trim() || '',
    filingIndicator: row.Claim_Filing_Indicator?.trim() || '',
    priorAuth: row.Prior_Auth_Number?.trim() || '',
    referral: row.Referral_Number?.trim() || '',
    transactionVersion: row.HIPAA_Transaction_Version?.trim() || '',
    submissionDate: parseEdiDate(row.Submission_Date),
    createdAt: parseEdiTimestamp(row.Created_Timestamp),
    edi: {
      isaControlNumber: row.ISA_Control_Number?.trim() || '',
      gsControlNumber: row.GS_Control_Number?.trim() || '',
      stTransactionSetId: row.ST_Transaction_Set_ID?.trim() || '',
    },
    raw: cleanRawRow(row),
    importedAt: new Date(),
  }
}

export function buildMemberDocuments(claims) {
  const grouped = new Map()

  for (const claim of claims) {
    const existing = grouped.get(claim.memberId) || []
    existing.push(claim)
    grouped.set(claim.memberId, existing)
  }

  return Array.from(grouped.entries()).map(([memberId, memberClaims]) => {
    const sortedClaims = [...memberClaims].sort((a, b) => b.dos.localeCompare(a.dos))
    const latestClaim = sortedClaims[0]
    const totals = sortedClaims.reduce((acc, claim) => {
      acc.totalCharges += claim.totalCharge
      acc.totalAllowed += claim.allowed
      acc.totalPaid += claim.paid
      acc.totalPatientResp += claim.patientResp
      acc.totalAdjustment += claim.adjustment
      if (claim.status === 'Denied') acc.deniedClaimCount += 1
      return acc
    }, {
      totalCharges: 0,
      totalAllowed: 0,
      totalPaid: 0,
      totalPatientResp: 0,
      totalAdjustment: 0,
      deniedClaimCount: 0,
    })

    return {
      memberId,
      patient: latestClaim.patient,
      patientFirstName: latestClaim.patientFirstName,
      patientLastName: latestClaim.patientLastName,
      dob: latestClaim.dob,
      gender: latestClaim.gender,
      subscriberId: latestClaim.subscriberId,
      groupId: latestClaim.groupId,
      groupName: latestClaim.groupName,
      primaryPayer: latestClaim.payer,
      latestServiceDate: latestClaim.dos,
      latestClaimNumber: latestClaim.number,
      billingProviders: [...new Set(sortedClaims.map((claim) => claim.billingProvider).filter(Boolean))],
      payers: [...new Set(sortedClaims.map((claim) => claim.payer).filter(Boolean))],
      claimCount: sortedClaims.length,
      totalCharges: Number(totals.totalCharges.toFixed(2)),
      totalAllowed: Number(totals.totalAllowed.toFixed(2)),
      totalPaid: Number(totals.totalPaid.toFixed(2)),
      totalPatientResp: Number(totals.totalPatientResp.toFixed(2)),
      totalAdjustment: Number(totals.totalAdjustment.toFixed(2)),
      deniedClaimCount: totals.deniedClaimCount,
      importedAt: new Date(),
    }
  })
}
