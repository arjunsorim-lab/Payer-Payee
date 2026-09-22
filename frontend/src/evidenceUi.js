// Pure helpers shared by the UI and its unit tests.
//
// The browser must never present a model estimate, a recommendation, a reviewer
// observation or synthetic demonstration data as recorded care or verified
// savings. These helpers keep the vocabulary in one place and mirror the review
// transition rules enforced by the backend.

export const EVIDENCE_TYPES = {
  RECORDED_CLAIM_FACT: 'recorded_claim_fact',
  SYNTHETIC_DEMONSTRATION: 'synthetic_demonstration',
  RECOMMENDATION: 'recommendation',
  REVIEWER_OBSERVATION: 'reviewer_observation',
  MODEL_ESTIMATE: 'model_estimate',
  VERIFIED_SAVINGS: 'verified_savings',
}

export const EVIDENCE_LABELS = {
  recorded_claim_fact: 'Recorded claim fact',
  synthetic_demonstration: 'Synthetic demonstration data',
  recommendation: 'Recommendation (not recorded care)',
  reviewer_observation: 'Reviewer observation (not independently verified)',
  model_estimate: 'Model estimate',
  verified_savings: 'Independently verified savings',
}

export const MISSING_EVIDENCE_LABELS = {
  medications: 'Medications',
  allergies: 'Allergies',
  symptoms: 'Symptoms',
  labs: 'Laboratory results',
  follow_up: 'Follow-up data',
}

// Mirrors backend/review_store.py TRANSITIONS so the UI cannot offer an invalid move.
export const REVIEW_TRANSITIONS = {
  pending: ['assigned', 'accepted', 'rejected', 'deferred'],
  assigned: ['assigned', 'accepted', 'rejected', 'deferred', 'completed'],
  accepted: ['accepted', 'rejected', 'deferred', 'completed'],
  deferred: ['assigned', 'accepted', 'rejected', 'deferred'],
  rejected: ['assigned', 'accepted', 'deferred', 'rejected'],
  completed: ['completed', 'outcome_recorded'],
  outcome_recorded: ['outcome_recorded'],
}

export function evidenceLabel(evidenceType) {
  return EVIDENCE_LABELS[evidenceType] || 'Unclassified evidence'
}

export function evidenceBadgeClass(evidenceType) {
  return `evidence-badge ${(evidenceType || 'unknown').replace(/_/g, '-')}`
}

export function reviewStatusOptions(currentStatus) {
  const allowed = REVIEW_TRANSITIONS[currentStatus] || ['accepted', 'rejected', 'deferred']
  return allowed.map((value) => ({ value, label: value.replace(/_/g, ' ') }))
}

export function verifiedSavingsLabel(amounts) {
  const verified = amounts?.verified_savings
  if (!verified || verified.value === null || verified.value === undefined) {
    return 'Not verified'
  }
  return `Verified: ${verified.value}`
}

export function verificationSummary(validation) {
  if (!validation) return { status: 'insufficient_evidence', label: 'No validation available', reasons: [] }
  const status = validation.verification_status || 'insufficient_evidence'
  const labels = {
    verified: 'Independently verified from post-intervention claims',
    unverified: 'Unverified: post-intervention claims exist but do not establish a saving',
    insufficient_evidence: 'Insufficient evidence: no post-intervention claims are recorded',
  }
  return { status, label: labels[status] || labels.insufficient_evidence, reasons: validation.reliability_reasons || [] }
}

export function evidenceValue(value) {
  if (value === null || value === undefined || value === '') return 'Not recorded'
  if (Array.isArray(value)) return value.map(evidenceValue).join('; ')
  if (typeof value === 'object') return Object.entries(value)
    .filter(([, item]) => item !== null && item !== undefined && item !== '')
    .map(([key, item]) => `${key.replace(/_/g, ' ')}: ${evidenceValue(item)}`).join(' · ') || 'Not recorded'
  return String(value)
}

// Amount rows keep six money concepts visibly separate.
export function savingsAmountRows(amounts) {
  if (!amounts) return []
  const order = [
    ['predicted_opportunity', 'Predicted opportunity'],
    ['billed_charge', 'Billed charge'],
    ['paid_amount', 'Paid amount'],
    ['estimated_savings', 'Estimated savings'],
    ['reviewer_reported_outcome', 'Reviewer-reported outcome'],
    ['verified_savings', 'Independently verified savings'],
  ]
  return order
    .filter(([key]) => amounts[key])
    .map(([key, label]) => {
      const item = amounts[key]
      const isMoney = !['reviewer_reported_outcome'].includes(key)
      return {
        key,
        label,
        value: item.value,
        evidenceType: item.evidence_type,
        evidenceLabel: item.evidence_label || evidenceLabel(item.evidence_type),
        basis: item.basis,
        display: isMoney && typeof item.value === 'number'
          ? `$${Number(item.value).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
          : evidenceValue(item.value),
        verified: key === 'verified_savings' && item.value !== null && item.value !== undefined,
      }
    })
}

export function missingEvidenceRows(quality) {
  const missing = (quality?.missing_information || []).map((item) => MISSING_EVIDENCE_LABELS[item] || item)
  return {
    rows: Object.values(MISSING_EVIDENCE_LABELS),
    missing,
    missingText: missing.length ? missing.join(', ') : 'All reviewed evidence is recorded',
  }
}

// Evidence-quality rows replace placeholder text such as "not assessed".
export function evidenceQualityRows(quality) {
  if (!quality) return []
  const formatRatio = (value) => (value === null || value === undefined ? 'Not calculated' : `${Math.round(value * 100)}%`)
  const formatScore = (value) => (value === null || value === undefined ? 'Not calculated' : Number(value).toFixed(2))
  return [
    { label: 'History start', value: quality.history_start || 'Not recorded' },
    { label: 'History end', value: quality.history_end || 'Not recorded' },
    { label: 'Claims reviewed', value: quality.claims_reviewed ?? quality.history_claim_count ?? 0 },
    { label: 'Cohort size', value: quality.cohort_size ?? 0 },
    { label: 'Matching strength', value: `${quality.matching_strength_label || 'Not calculated'} (${formatScore(quality.matching_strength)})` },
    { label: 'Follow-up completeness', value: `${formatRatio(quality.follow_up_completeness)} (${quality.follow_up_status || 'not_assessed'})` },
    { label: 'Data source type', value: (quality.data_source_type || 'recorded_claim').replace(/_/g, ' ') },
    { label: 'Causal evidence', value: quality.causal_evidence_status || 'not_established' },
    { label: 'Comparison strength', value: quality.comparison_strength || 'No comparable cohort available' },
  ]
}

export function uncertaintySummary(validation) {
  const stats = validation?.statistical_uncertainty
  if (!stats || stats.sample_size === 0) {
    return 'Not enough comparable episodes to calculate statistical uncertainty.'
  }
  if (stats.ci_low === null || stats.ci_high === null) {
    return 'Statistical uncertainty could not be calculated from the available cohort.'
  }
  return `Cohort mean $${stats.mean} (95% interval $${stats.ci_low}–$${stats.ci_high}, n=${stats.sample_size})`
}

export function observationWindowSummary(validation) {
  const window = validation?.observation_window
  if (!window || !window.start) return 'No observation window is defined.'
  return `${window.start} to ${window.end} (${window.days} days, ${window.status})`
}

// CSV cells are quoted and formula-like text is escaped before download.
export function claimsExportCsv(claims) {
  const columns = ['claimId', 'memberId', 'dos', 'diagnosisCode', 'cptCode', 'totalCharge', 'allowed', 'paid', 'evidence_type', 'evidence_label']
  const cell = (value) => {
    let text = value == null ? '' : String(value)
    if (/^[\s]*[=+@-]/.test(text)) text = "'" + text
    return '"' + text.replace(/"/g, '""') + '"'
  }
  return [columns, ...claims.map(claim => {
    const source = claim.evidence_source || {}
    return columns.map(key => key === 'evidence_type' ? (source.evidence_type || 'unknown')
      : key === 'evidence_label' ? evidenceLabel(source.evidence_type) : claim[key])
  })].map(row => row.map(cell).join(',')).join('\r\n')
}
