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
  assert.match(source, /Provider Money Scenario Map/)
  assert.match(source, /Ask About This Prediction/)
  assert.match(source, /provider-chat-prompt/)
  assert.match(source, /Close Provider LLM Analysis/)
  assert.match(styles, /\.provider-llm-modal\s*\{[^}]*inset:\s*0;[^}]*width:\s*100vw;[^}]*height:\s*100vh;/s)
  assert.match(styles, /\.provider-llm-workspace\s*\{[^}]*grid-template-columns:/s)
  assert.match(styles, /\.provider-chat-prompt\s*\{[^}]*position:\s*relative;/s)
})

test('prediction chat is integrated into the full-screen analysis workspace', () => {
  const source = readFileSync(new URL('./App.jsx', import.meta.url), 'utf8')
  const result = source.slice(source.indexOf('function ProviderMoneyLlmResult'), source.indexOf('function ProviderPredictionChat'))
  const chat = source.slice(source.indexOf('function ProviderPredictionChat'), source.indexOf('export function ProviderLlmResult'))
  assert.match(result, /<ProviderPredictionChat[^>]*result=\{result\}/)
  assert.doesNotMatch(result, /Exact model output/)
  assert.match(chat, /provider-chat-prompt/)
  assert.match(chat, /chat-results-list/)
  assert.doesNotMatch(chat, /Prediction Assistant Results|provider-chat-results/)
  assert.match(chat, /cached\.filter\(\(message\) => message\?\.text !== legacyWelcome/)
  assert.match(chat, /const clear = \(\) => \{ setMessages\(\[\]\)/)
  assert.match(chat, /chatgpt-composer/)
  const styles = readFileSync(new URL('./App.css', import.meta.url), 'utf8')
  assert.match(styles, /\.provider-llm-modal\s*\{[^}]*inset:\s*0;[^}]*width:\s*100vw;[^}]*height:\s*100vh;/s)
  assert.match(styles, /\.provider-chat-prompt\s*\{[^}]*height:\s*100%;/s)
  assert.match(styles, /\.provider-chat-prompt:hover,\s*\.provider-chat-prompt:focus-within\s*\{[^}]*transform:\s*none;/s)
})

test('Prediction Snapshot is removed from the active provider modal', () => {
  const source = readFileSync(new URL('./App.jsx', import.meta.url), 'utf8')
  const result = source.slice(source.indexOf('function ProviderMoneyLlmResult'), source.indexOf('function ProviderPredictionChat'))
  assert.doesNotMatch(result, /Prediction Snapshot|prediction-snapshot|money-snapshot-grid|MetricBasisDetails/)
})

test('provider suggestions keep real savings, synthetic opportunity and future exposure separate', () => {
  const source = readFileSync(new URL('./App.jsx', import.meta.url), 'utf8')
  const result = source.slice(source.indexOf('function ProviderMoneyLlmResult'), source.indexOf('function ProviderPredictionChat'))
  assert.match(result, /Where Provider Money Can Be Saved/)
  assert.match(result, /Current Claim Performance vs Historical Claims/)
  assert.match(result, /Future Financial Exposure/)
  assert.match(result, /Repeat allowed exposure/)
  assert.match(result, /Repeat provider-payment exposure/)
  assert.match(result, /futureExposure\.label/)
  assert.match(result, /Best Next Provider Action/)
  assert.match(result, /futureExposure\.repeat_probability_90d/)
  assert.doesNotMatch(result, /Prediction Snapshot|Actual Claim Facts|Backtest Against Actual Result/)
  assert.doesNotMatch(result, /Historical Comparison|Recurrence Evidence|Data Availability/)
  assert.match(result, /Validated real savings|Synthetic demo opportunity|Expected contractual adjustment/)
  assert.doesNotMatch(result, /provider revenue at risk/i)
  assert.ok(result.indexOf('provider-savings-section') > result.indexOf('Provider Financial Opportunity Summary'))
})

test('synthetic savings fields are visibly separated from original actual facts', () => {
  const source = readFileSync(new URL('./App.jsx', import.meta.url), 'utf8')
  const styles = readFileSync(new URL('./App.css', import.meta.url), 'utf8')
  const result = source.slice(source.indexOf('function ProviderMoneyLlmResult'), source.indexOf('function ProviderPredictionChat'))
  assert.match(result, /Synthetic enrichment data is active/)
  assert.match(result, /demonstration only and must not be used for real billing decisions/)
  assert.match(result, /data_provenance/)
  assert.match(styles, /\.synthetic-data-banner/)
  assert.match(result, /View demonstration evidence/)
  assert.match(result, /Dummy_Enrichment/)
  assert.match(result, /syntheticOpportunity\.warning/)
})

test('provider scenario map renders only the three requested provider views', () => {
  const source = readFileSync(new URL('./App.jsx', import.meta.url), 'utf8')
  const result = source.slice(source.indexOf('function ProviderMoneyLlmResult'), source.indexOf('function ProviderPredictionChat'))
  assert.match(result, /Provider Claim and Payment Prediction|Where Provider Money Can Be Saved|Cost-Leakage Risks/)
  assert.match(result, /provider_claim_payment_prediction|where_provider_money_may_be_saved|cost_leakage_risks/)
  assert.doesNotMatch(result, /Member History View|Current Encounter View|member_claim_history|encounter_and_coding/)
  assert.doesNotMatch(result, /claim_workflow|Pathway Financial Comparison|provider_money_comparison/)
  assert.doesNotMatch(result, /Financial Risk Drivers|Ranked Provider Actions|Prediction Assistant Results/)
  assert.match(result, /ProviderSavingsScenario/)
  assert.match(result, /CostLeakageRiskList/)
})

test('chat renders the backend financial explanation below the concise answer', () => {
  const source = readFileSync(new URL('./App.jsx', import.meta.url), 'utf8')
  assert.match(source, /function ChatFinancialExplanation/)
  assert.match(source, /Savings and Financial Explanation/)
  assert.match(source, /message\.meta\?\.financial_explanation/)
  assert.match(source, /<p>\{message\.text\}<\/p>[\s\S]*<ChatFinancialExplanation/)
  assert.match(source, /Validated real savings|Expected denial exposure|Expected repeat-service exposure/)
})

test('chat sends only claim and conversation scope identifiers plus the question', () => {
  const source = readFileSync(new URL('./App.jsx', import.meta.url), 'utf8')
  assert.match(source, /\/api\/provider-llm\/chat/)
  assert.match(source, /claim_id: claimId, episode_id: episodeId, message: text, conversation_id: conversationId/)
  assert.doesNotMatch(source, /body: JSON\.stringify\(\{[^}]*patient:/)
  assert.match(source, /Shift\+Enter for a new line/)
  assert.match(source, /Clear chat/)
  assert.match(source, /prediction_basis\?\.source_csv_hash/)
  assert.match(source, /prediction_basis\?\.calculation_version/)
})

test('provider money scenario UI does not copy dental sample content', () => {
  const source = readFileSync(new URL('./App.jsx', import.meta.url), 'utf8')
  const moneyResult = source.slice(source.indexOf('function ProviderMoneyLlmResult'), source.indexOf('function ProviderPredictionChat'))
  assert.doesNotMatch(moneyResult, /cavity|filling|root canal/i)
})
