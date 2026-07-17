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

test('member summary labels the database-backed count as Total Claims', () => {
  const source = readFileSync(new URL('./App.jsx', import.meta.url), 'utf8')
  const buildMemberStats = source.slice(
    source.indexOf('function buildMemberStats'),
    source.indexOf('function buildDashboardMetrics'),
  )
  assert.match(buildMemberStats, /label: 'Total Claims'/)
  assert.match(buildMemberStats, /non-denied/)
  assert.doesNotMatch(buildMemberStats, /label: 'Active Claims'/)
})

test('Run LLM Analysis opens an accessible money-oriented modal', () => {
  const source = readFileSync(new URL('./App.jsx', import.meta.url), 'utf8')
  assert.match(source, /role="dialog"/)
  assert.match(source, /aria-modal="true"/)
  assert.match(source, /Provider Financial Opportunity Summary/)
  assert.match(source, /Backtest Against Actual Result/)
  assert.match(source, /Provider Money Scenario Map/)
  assert.match(source, /Ask About This Prediction/)
  assert.match(source, /Close Provider LLM Analysis/)
})

test('prediction cards expose metric-specific calculation bases', () => {
  const source = readFileSync(new URL('./App.jsx', import.meta.url), 'utf8')
  assert.match(source, /function MetricBasisDetails/)
  assert.match(source, /local_sample_size/)
  assert.match(source, /external_sample_size/)
  assert.match(source, /blend_weights/)
  assert.doesNotMatch(source, /Peer sample: 1417/)
})

test('chat sends only claim and conversation scope identifiers plus the question', () => {
  const source = readFileSync(new URL('./App.jsx', import.meta.url), 'utf8')
  assert.match(source, /\/api\/provider-llm\/chat/)
  assert.match(source, /claim_id: claimId, episode_id: episodeId, message: text, conversation_id: conversationId/)
  assert.doesNotMatch(source, /body: JSON\.stringify\(\{[^}]*patient:/)
  assert.match(source, /Shift\+Enter for a new line/)
  assert.match(source, /Clear chat/)
})

test('provider money scenario UI does not copy dental sample content', () => {
  const source = readFileSync(new URL('./App.jsx', import.meta.url), 'utf8')
  const moneyResult = source.slice(source.indexOf('function ProviderMoneyLlmResult'), source.indexOf('function ProviderPredictionChat'))
  assert.doesNotMatch(moneyResult, /cavity|filling|root canal/i)
})
