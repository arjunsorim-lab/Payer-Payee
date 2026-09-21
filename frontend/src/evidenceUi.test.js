import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'

import {
  EVIDENCE_LABELS,
  EVIDENCE_TYPES,
  MISSING_EVIDENCE_LABELS,
  REVIEW_TRANSITIONS,
  evidenceBadgeClass,
  evidenceLabel,
  evidenceQualityRows,
  missingEvidenceRows,
  observationWindowSummary,
  reviewStatusOptions,
  savingsAmountRows,
  uncertaintySummary,
  verificationSummary,
  verifiedSavingsLabel,
} from './evidenceUi.js'

const app = readFileSync(new URL('./App.jsx', import.meta.url), 'utf8')

test('every evidence type has a distinct label and an unknown type is flagged', () => {
  const labels = Object.values(EVIDENCE_LABELS)
  assert.equal(labels.length, 6)
  assert.equal(new Set(labels).size, 6)
  assert.equal(evidenceLabel(EVIDENCE_TYPES.RECORDED_CLAIM_FACT), 'Recorded claim fact')
  assert.equal(evidenceLabel(EVIDENCE_TYPES.SYNTHETIC_DEMONSTRATION), 'Synthetic demonstration data')
  assert.equal(evidenceLabel(EVIDENCE_TYPES.RECOMMENDATION), 'Recommendation (not recorded care)')
  assert.equal(evidenceLabel(EVIDENCE_TYPES.REVIEWER_OBSERVATION), 'Reviewer observation (not independently verified)')
  assert.equal(evidenceLabel(EVIDENCE_TYPES.MODEL_ESTIMATE), 'Model estimate')
  assert.equal(evidenceLabel(EVIDENCE_TYPES.VERIFIED_SAVINGS), 'Independently verified savings')
  assert.equal(evidenceLabel('something_else'), 'Unclassified evidence')
  assert.match(evidenceBadgeClass(EVIDENCE_TYPES.MODEL_ESTIMATE), /evidence-badge model-estimate/)
})

test('recommendations are never presented as recorded care', () => {
  assert.match(EVIDENCE_LABELS.recommendation, /not recorded care/)
  assert.match(EVIDENCE_LABELS.reviewer_observation, /not independently verified/)
})

test('review transitions never offer an invalid move', () => {
  // Assignment must be offered from a pending review: requirement 4 allows reviewers
  // to assign work without accepting it first.
  assert.ok(REVIEW_TRANSITIONS.pending.includes('assigned'))
  // A review cannot jump from an open state straight to a terminal completion.
  assert.equal(REVIEW_TRANSITIONS.pending.includes('completed'), false)
  assert.equal(REVIEW_TRANSITIONS.deferred.includes('completed'), false)
  // Terminal states only allow their documented successors.
  assert.deepEqual(
    reviewStatusOptions('completed').map((option) => option.value),
    ['completed', 'outcome_recorded'],
  )
  // Every offered option is a real status, and an unknown status still offers valid options.
  for (const options of Object.values(REVIEW_TRANSITIONS)) {
    for (const value of options) assert.ok(Object.keys(REVIEW_TRANSITIONS).includes(value))
  }
  assert.ok(reviewStatusOptions('unknown-status').length > 0)
  assert.equal(reviewStatusOptions('accepted').some((option) => option.value === 'pending'), false)
})

test('amount rows keep predicted, billed, paid, estimated, reviewer and verified savings separate', () => {
  const rows = savingsAmountRows({
    predicted_opportunity: { value: 1200, evidence_type: 'model_estimate', evidence_label: 'Model estimate' },
    billed_charge: { value: 900, evidence_type: 'recorded_claim_fact' },
    paid_amount: { value: 400, evidence_type: 'recorded_claim_fact' },
    estimated_savings: { value: 300, evidence_type: 'model_estimate' },
    reviewer_reported_outcome: { value: { outcome: 'improved' }, evidence_type: 'reviewer_observation' },
    verified_savings: { value: null, evidence_type: 'verified_savings' },
  })
  assert.deepEqual(rows.map((row) => row.key), [
    'predicted_opportunity',
    'billed_charge',
    'paid_amount',
    'estimated_savings',
    'reviewer_reported_outcome',
    'verified_savings',
  ])
  assert.equal(rows[0].display, '$1,200.00')
  assert.equal(rows[1].evidenceLabel, 'Recorded claim fact')
  assert.equal(rows[5].verified, false)
  assert.equal(rows[5].display, 'Not recorded')
  assert.equal(verifiedSavingsLabel({ verified_savings: { value: 210.5 } }), 'Verified: 210.5')
  assert.equal(verifiedSavingsLabel({ verified_savings: { value: null } }), 'Not verified')
})

test('verification summary distinguishes verified, unverified and insufficient evidence', () => {
  assert.match(verificationSummary({ verification_status: 'verified' }).label, /Independently verified/)
  assert.match(verificationSummary({ verification_status: 'unverified' }).label, /do not establish a saving/)
  assert.match(verificationSummary({ verification_status: 'insufficient_evidence' }).label, /no post-intervention claims/)
  assert.match(verificationSummary(null).label, /No validation available/)
})

test('evidence-quality rows are calculated values, never the old placeholders', () => {
  const rows = evidenceQualityRows({
    history_start: '2026-01-01',
    history_end: '2026-06-01',
    history_claim_count: 8,
    claims_reviewed: 8,
    cohort_size: 6,
    matching_strength: 0.8,
    matching_strength_label: 'Moderate',
    follow_up_completeness: 0.5,
    follow_up_status: 'partial',
    data_source_type: 'recorded_claim',
    causal_evidence_status: 'not_established',
    comparison_strength: 'Supported by a comparable cohort',
  })
  const byLabel = Object.fromEntries(rows.map((row) => [row.label, row.value]))
  assert.equal(byLabel['History start'], '2026-01-01')
  assert.equal(byLabel['History end'], '2026-06-01')
  assert.equal(byLabel['Claims reviewed'], 8)
  assert.equal(byLabel['Cohort size'], 6)
  assert.match(String(byLabel['Matching strength']), /Moderate \(0\.80\)/)
  assert.match(String(byLabel['Follow-up completeness']), /50% \(partial\)/)
  assert.equal(byLabel['Data source type'], 'recorded claim')
  assert.equal(byLabel['Causal evidence'], 'not_established')
  assert.equal(byLabel['Comparison strength'], 'Supported by a comparable cohort')
  assert.doesNotMatch(JSON.stringify(rows), /Not assessed|not assessed/)
})

test('evidence-quality rows fall back to explicit calculated-unavailable text', () => {
  const rows = evidenceQualityRows({})
  assert.match(String(rows.find((row) => row.label === 'Matching strength').value), /Not calculated/)
  assert.equal(rows.find((row) => row.label === 'Cohort size').value, 0)
})

test('missing evidence names medications, allergies, symptoms, labs and follow-up data', () => {
  const { rows, missing, missingText } = missingEvidenceRows({
    missing_information: ['medications', 'allergies', 'symptoms', 'labs', 'follow_up'],
  })
  assert.deepEqual(rows, ['Medications', 'Allergies', 'Symptoms', 'Laboratory results', 'Follow-up data'])
  assert.deepEqual(missing, rows)
  assert.match(missingText, /Medications/)
  assert.equal(MISSING_EVIDENCE_LABELS.follow_up, 'Follow-up data')
  assert.match(missingEvidenceRows({}).missingText, /All reviewed evidence is recorded/)
})

test('uncertainty and observation-window summaries explain insufficiency', () => {
  assert.match(
    uncertaintySummary({ statistical_uncertainty: { sample_size: 0 } }),
    /Not enough comparable episodes/,
  )
  assert.match(
    uncertaintySummary({ statistical_uncertainty: { sample_size: 6, mean: 420.5, ci_low: 300, ci_high: 540 } }),
    /95% interval \$300–\$540/,
  )
  assert.match(observationWindowSummary({ observation_window: { start: '2026-01-01', end: '2026-07-01', days: 180, status: 'in_progress' } }), /2026-01-01 to 2026-07-01/)
  assert.match(observationWindowSummary(null), /No observation window is defined/)
})

test('the browser sends the CSRF token and secure same-origin credentials on writes', () => {
  assert.match(app, /credentials: 'same-origin'/)
  assert.match(app, /headers\['X-CSRF-Token'\] = sessionState\.csrfToken/)
  assert.match(app, /bootstrapSession/)
  assert.match(app, /\/api\/session/)
  assert.match(app, /\/api\/login/)
})

test('the UI renders evidence labels, savings validation and review history from the backend', () => {
  assert.match(app, /from '\.\/evidenceUi\.js'/)
  assert.match(app, /evidenceLabel/)
  assert.match(app, /savingsAmountRows/)
  assert.match(app, /verificationSummary/)
})
