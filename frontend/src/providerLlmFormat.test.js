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
  const styles = readFileSync(new URL('./App.css', import.meta.url), 'utf8')
  assert.match(source, /role="dialog"/)
  assert.match(source, /aria-modal="true"/)
  assert.match(source, /createPortal/)
  assert.match(source, /Provider Financial Opportunity Summary/)
  assert.match(source, /Backtest Against Actual Result/)
  assert.match(source, /Provider Money Scenario Map/)
  assert.match(source, /Ask About This Prediction/)
  assert.match(source, /provider-chat-prompt/)
  assert.match(source, /Close Provider LLM Analysis/)
  assert.match(styles, /\.provider-llm-modal\s*\{[^}]*inset:\s*0;[^}]*width:\s*100vw;[^}]*height:\s*100vh;/s)
  assert.match(styles, /\.provider-chat-prompt\s*\{[^}]*position:\s*fixed;[^}]*left:\s*50%;/s)
})

test('prediction chat uses a floating prompt and inline result cards', () => {
  const source = readFileSync(new URL('./App.jsx', import.meta.url), 'utf8')
  const result = source.slice(source.indexOf('function ProviderMoneyLlmResult'), source.indexOf('function ProviderPredictionChat'))
  const chat = source.slice(source.indexOf('function ProviderPredictionChat'), source.indexOf('export function ProviderLlmResult'))
  assert.match(result, /<ProviderPredictionChat result=\{result\}/)
  assert.doesNotMatch(result, /Exact model output/)
  assert.match(chat, /provider-chat-prompt/)
  assert.match(chat, /provider-chat-results/)
  assert.match(chat, /chatgpt-composer/)
})

test('prediction cards expose metric-specific calculation bases', () => {
  const source = readFileSync(new URL('./App.jsx', import.meta.url), 'utf8')
  assert.match(source, /function MetricBasisDetails/)
  assert.match(source, /local_sample_size/)
  assert.match(source, /external_sample_size/)
  assert.match(source, /blend_weights/)
  assert.doesNotMatch(source, /Peer sample: 1417/)
})

test('provider savings section follows Prediction Snapshot and separates current from future opportunity', () => {
  const source = readFileSync(new URL('./App.jsx', import.meta.url), 'utf8')
  const result = source.slice(source.indexOf('function ProviderMoneyLlmResult'), source.indexOf('function ProviderPredictionChat'))
  assert.match(result, /Estimated financial opportunity/)
  assert.match(result, /Where Provider Money Can Be Saved/)
  assert.match(result, /Validated current-claim opportunity/)
  assert.match(result, /Future financial exposure/)
  assert.match(result, /Repeat allowed exposure/)
  assert.match(result, /Repeat provider-payment exposure/)
  assert.match(result, /futureExposure\.label/)
  assert.match(result, /No validated current-claim savings opportunity identified/)
  assert.match(result, /Forecast reconciliation difference/)
  assert.match(result, /recurrenceEvidence\[horizon\]/)
  assert.match(result, /futureExposure\.repeat_probability_90d/)
  assert.ok(result.indexOf('Where Provider Money Can Be Saved') > result.indexOf('Prediction Snapshot'))
  assert.ok(result.indexOf('Where Provider Money Can Be Saved') < result.indexOf('Actual Claim Facts'))
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
