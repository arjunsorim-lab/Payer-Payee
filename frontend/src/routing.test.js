import test from 'node:test'
import assert from 'node:assert/strict'
import { routeFromHash, routeToHash, normalizePredictionClaim } from './routing.js'

test('a claim outside the initial page retains its identity for detail loading', () => {
  const route = routeFromHash('#view=predictions&prediction=outside-page', [])
  assert.equal(route.selectedPredictionClaim.claimId, 'outside-page')
  assert.equal(routeToHash(route), '#view=predictions&prediction=outside-page')
})

test('historical references never redirect to a different patient', () => {
  const reference = { claimId: 'reference', memberId: 'patient', isHistoricalReference: true }
  assert.deepEqual(normalizePredictionClaim(reference), reference)
})

test('invalid views fall back safely', () => {
  assert.equal(routeFromHash('#view=invalid', []).activeView, 'home')
})
