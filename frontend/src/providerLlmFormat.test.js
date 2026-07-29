import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'

import {
  formatFinancialStatus,
  formatOptionalCurrency,
  formatPredictionRange,
  formatProbability,
} from './providerLlmFormat.js'

const app = readFileSync(new URL('./App.jsx', import.meta.url), 'utf8')
const styles = readFileSync(new URL('./App.css', import.meta.url), 'utf8')
const formatter = readFileSync(new URL('./providerLlmFormat.js', import.meta.url), 'utf8')

test('status-aware formatters preserve backend meaning', () => {
  assert.equal(formatProbability(0.18), '18.0%')
  assert.equal(formatOptionalCurrency(471.2), '$471.20')
  assert.equal(formatOptionalCurrency(null), '')
  assert.equal(formatPredictionRange({ low: 506.8, high: 644.92 }), '$506.80–$644.92')
  assert.equal(formatPredictionRange(undefined), '')
  assert.equal(formatFinancialStatus({ status: 'supported', amount: 16.42 }), '$16.42')
  assert.equal(formatFinancialStatus({ status: 'supported_zero', amount: 0 }), '$0.00 currently supported')
  assert.equal(formatFinancialStatus({ status: 'insufficient_source_fields', amount: 0 }), '')
})

test('provider prediction consumes one canonical backend result', () => {
  const result = app.slice(app.indexOf('export function ProviderMoneyLlmResult'), app.indexOf('function ChatFinancialExplanation'))
  assert.match(result, /result\.supported_money_summary/)
  assert.match(result, /result\.financial_prediction_snapshot/)
  assert.match(result, /result\.supported_financial_opportunities/)
  assert.match(result, /result\.non_actionable_evidence/)
  assert.match(result, /result\.scenario_map\?\.sections/)
  assert.match(result, /Predicted provider payment/)
  assert.match(result, /Predicted contractual adjustment/)
  assert.match(result, /Supported Financial Opportunity/)
  assert.match(app, /Why Other Actions Were Not Selected/)
  assert.match(result, /explanation\.sections/)
  assert.match(result, /layman-explanation/)
  assert.doesNotMatch(result, /predictClaim/)
  assert.doesNotMatch(result, /result\.financial_opportunities/)
  assert.match(app, /encodeURIComponent\(claim\.claimId \|\| claim\.number\)/)
})

test('Render fallback prediction displays content instead of a blank modal', () => {
  assert.match(app, /function ProviderRenderPredictionResult/)
  assert.match(app, /!result\?\.supported_money_summary && result\?\.forecast/)
  assert.match(app, /Provider Financial Forecast/)
  assert.match(app, /Supported Financial Opportunity/)
  assert.match(app, /Actual vs Predicted/)
  assert.match(app, /Provider Money Scenario Map/)
  assert.match(app, /Prediction Explanation/)
  assert.match(app, /<ProviderPredictionChat/)
  assert.match(app, /Deterministic forecast ready/)
  assert.match(app, /result\.configured === false/)
  assert.match(app, /predictionReady/)
})

test('scenario map is a structured eight-step pathway', () => {
  assert.match(app, /function ScenarioMapSection/)
  assert.match(app, /section\.step === 6/)
  assert.match(app, /section\.step === 7/)
  assert.match(app, /section\.step === 8/)
  assert.match(app, /scenario-purpose-grid/)
  assert.match(app, /scenario-calculations/)
  assert.match(app, /scenario-action-card/)
  assert.match(app, /items\.workbook_sheet/)
  assert.doesNotMatch(app, /function ScenarioMapData/)
})

test('chat renders the same canonical amounts below every response', () => {
  const chat = app.slice(app.indexOf('function ChatFinancialExplanation'), app.indexOf('function SelectButton'))
  assert.match(chat, /explanation\.recoverable_now/)
  assert.match(chat, /explanation\.potentially_avoidable_spend_supported/)
  assert.match(chat, /explanation\.future_denial_exposure/)
  assert.match(chat, /explanation\.future_repeat_payment_exposure/)
  assert.match(chat, /message\.meta\?\.financial_explanation/)
  assert.match(chat, /claim_id: claimId, episode_id: episodeId, message: text, conversation_id: conversationId/)
  assert.match(chat, /result\.source\?\.workbook_hash/)
  assert.match(chat, /result\.financial_result_hash/)
})

test('modal fills the screen and chat does not shift on hover', () => {
  assert.match(app, /role="dialog"/)
  assert.match(app, /aria-modal="true"/)
  assert.match(styles, /\.provider-llm-modal\s*\{[^}]*inset:\s*0;[^}]*width:\s*100vw;[^}]*height:\s*100vh;/s)
  assert.match(styles, /\.provider-llm-workspace\s*\{[^}]*grid-template-columns:/s)
  assert.match(styles, /\.provider-chat-prompt:hover,\s*\.provider-chat-prompt:focus-within\s*\{[^}]*transform:\s*none;/s)
})

test('workbook source banner is hidden while versioned browser cache remains active', () => {
  assert.doesNotMatch(app, /WorkbookSourceBanner/)
  assert.doesNotMatch(app, /Workbook demonstration database active/)
  assert.match(app, /payerpayee\.claims\.workbook\./)
  assert.match(app, /source\?\.workbook_hash/)
  assert.doesNotMatch(app, /loadBundledClaims/)
})

test('legacy renderer and forbidden empty-state wording are absent', () => {
  assert.doesNotMatch(app, /export function ProviderLlmResult/)
  const forbidden = [
    ['Not', 'identified'].join(' '),
    ['None', 'identified'].join(' '),
    ['Not', 'calculated'].join(' '),
    ['Not', 'supported'].join(' '),
    ['Un', 'available'].join(''),
    ['Un', 'known'].join(''),
    ['Range', 'un' + 'available'].join(' '),
    ['Claims', 'Original'].join('_'),
    ['Dummy', 'Enrichment'].join('_'),
  ]
  forbidden.forEach((phrase) => assert.equal(`${app}\n${formatter}`.includes(phrase), false))
})

test('production provider code contains no integration-claim constants', () => {
  const engine = readFileSync(new URL('../../backend/financial_engine.py', import.meta.url), 'utf8')
  assert.doesNotMatch(`${app}\n${engine}`, /CLM00001092|CLM00000143/)
})

test('provider scenario UI contains no dental sample language', () => {
  const result = app.slice(app.indexOf('function ScenarioMapSection'), app.indexOf('function SelectButton'))
  assert.doesNotMatch(result, /cavity|filling|root canal/i)
})
