import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'

import { formatOptionalCurrency, formatPredictionRange, formatProbability } from './providerLlmFormat.js'

test('probabilities display as percentages with one decimal place', () => {
  assert.equal(formatProbability(0.18), '18.0%')
  assert.equal(formatProbability(0.101), '10.1%')
})

test('currency values display with two decimal places', () => {
  assert.equal(formatOptionalCurrency(471.2), '$471.20')
  assert.equal(formatOptionalCurrency(null), 'Unavailable')
})

test('prediction ranges display low and high values safely', () => {
  assert.equal(formatPredictionRange({ low: 506.8, high: 644.92 }), '$506.80–$644.92')
  assert.equal(formatPredictionRange({ low: null, high: null }), 'Range unavailable')
  assert.equal(formatPredictionRange(undefined), 'Range unavailable')
})

test('Provider LLM labels actual and predicted amounts separately', () => {
  const source = readFileSync(new URL('./App.jsx', import.meta.url), 'utf8')
  assert.match(source, /Actual allowed/)
  assert.match(source, /Predicted allowed/)
  assert.match(source, /Peer sample size/)
  assert.match(source, /Exact model output/)
})

test('Provider LLM production files contain no integration-claim hardcoding', () => {
  const frontend = readFileSync(new URL('./App.jsx', import.meta.url), 'utf8')
  const backend = readFileSync(new URL('../../backend/provider_prediction.py', import.meta.url), 'utf8')
  assert.doesNotMatch(`${frontend}\n${backend}`, /CLM-?0*143|CLM00000143/)
})
