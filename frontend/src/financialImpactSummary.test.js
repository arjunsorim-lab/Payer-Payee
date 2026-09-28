import test from 'node:test'
import assert from 'node:assert/strict'
import { financialImpactSummaryFromBilledResult } from './financialImpactSummary.js'

test('financial summary uses supplied patient identities and amounts', () => {
  const result = {
    available: true,
    calculation_type: 'reference_intervention_counterfactual',
    prediction_patient: { member_id: 'PATIENT-A', patient_name: 'Test Patient' },
    reference_patient: { member_id: 'PATIENT-B', patient_name: 'Reference Patient' },
    calculation: { actual_cost: 900, proposed_cost: 350, potential_savings: 550 },
  }
  const summary = financialImpactSummaryFromBilledResult(result)
  assert.match(summary, /Test Patient \(PATIENT-A\)/)
  assert.match(summary, /Reference Patient \(PATIENT-B\)/)
  assert.match(summary, /\$900\.00/)
  assert.match(summary, /\$350\.00/)
  assert.match(summary, /\$550\.00/)
  result.calculation.potential_savings = 12
  assert.match(financialImpactSummaryFromBilledResult(result), /\$12\.00/)
})

test('missing patient names are never replaced with fixed demo identities', () => {
  const summary = financialImpactSummaryFromBilledResult({
    available: true, member_id: 'MBR00016',
    calculation: { actual_two_episode_billed: 20, proposed_earlier_episode_with_add_on_billed: 10, potential_billed_difference: 10 },
  })
  assert.match(summary, /MBR00016/)
  assert.doesNotMatch(summary, /Karen Miller|Richard Taylor/)
})

test('unsupported calculations do not produce a financial narrative', () => {
  assert.equal(financialImpactSummaryFromBilledResult(null), '')
  assert.equal(financialImpactSummaryFromBilledResult({ available: false }), '')
  assert.equal(financialImpactSummaryFromBilledResult({ available: true }), '')
})
