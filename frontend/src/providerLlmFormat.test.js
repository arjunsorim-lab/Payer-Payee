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
  assert.match(result, /PredictionEvidence/)
  assert.match(result, /PredictionValidationPanel/)
  assert.match(result, /Ollama Prediction Explanation/)
  assert.doesNotMatch(result, /predictClaim/)
  assert.doesNotMatch(result, /result\.financial_opportunities/)
  assert.match(app, /encodeURIComponent\(claim\.claimId \|\| claim\.number\)/)
})

test('claim-anchored payer popup displays canonical content instead of a blank modal', () => {
  assert.match(app, /result\?\.benchmark_summary \? <ClaimPayerPredictionResult result=\{result\} \/>/)
  assert.match(app, /\/api\/predictions\/payer\/claim\//)
  assert.match(app, /Payer Savings Prediction/)
  assert.match(app, /Benchmark Summary/)
  assert.match(app, /Peer Members Used/)
  assert.match(app, /Prediction Range \/ Calculation Summary/)
  assert.match(app, /Supporting Evidence/)
  assert.match(app, /calculation\.utilisation_reduction_opportunity/)
  assert.match(app, /calculation\.lower_spend_benchmark/)
  assert.match(app, /result\.scenario_selection/)
  assert.match(app, /peer\.peer_episode_count/)
  assert.match(app, /calculation\.payer_spend_reduction_opportunity/)
  assert.match(app, /Payer perspective · Rule-based/)
  assert.match(app, /It does not use or modify the separate provider forecast/)
  assert.match(app, /Open Separate Provider Forecast/)
  assert.doesNotMatch(
    app.slice(app.indexOf('function ClaimPayerPredictionResult'), app.indexOf('function ProviderRenderPredictionResult')),
    /lower of the count-based and cost-based estimates/,
  )
})

test('provider forecast and payer savings are clearly separated and centered', () => {
  const providerView = app.slice(app.indexOf('function PredictionScenarioMap'), app.indexOf('function filterClaimsByTime'))
  assert.match(providerView, /Provider perspective · Forecast/)
  assert.match(providerView, /Expected avoidable repeat cost/)
  assert.match(providerView, /It is not the rule-based payer cohort savings amount/)
  assert.match(providerView, /scenario\.historical_comparison\?\.sample_size/)
  assert.doesNotMatch(providerView, /Provider case forecast/)
  assert.match(app, /Claim Financial Predictions/)
  assert.match(app, /Open Provider Forecast/)
  assert.match(app, /Open Payer Savings/)
  assert.match(styles, /\.provider-forecast-detail,\s*\.payer-cohort-modal \.claim-payer-result\s*\{[^}]*width:\s*min\(100%, 1480px\);[^}]*margin-inline:\s*auto;/s)
})

test('scenario map is a structured nine-step pathway', () => {
  assert.match(app, /function ScenarioMapSection/)
  assert.match(app, /section\.title === 'Financial Opportunity'/)
  assert.match(app, /section\.title === 'Best Provider Action'/)
  assert.match(app, /section\.title === 'Supporting Evidence'/)
  assert.match(app, /scenario-purpose-grid/)
  assert.match(app, /scenario-calculations/)
  assert.match(app, /scenario-action-card/)
  assert.match(app, /items\.peer_claim_ids/)
  assert.doesNotMatch(app, /function ScenarioMapData/)
})

test('chat renders the same canonical amounts below every response', () => {
  const chat = app.slice(app.indexOf('function ChatFinancialExplanation'), app.indexOf('function SelectButton'))
  assert.match(chat, /explanation\.recoverable_now/)
  assert.match(chat, /explanation\.predicted_avoidable_spend\?\.value/)
  assert.match(chat, /explanation\.validated_avoidable_spend\.value/)
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

test('prediction evidence shows hybrid workbook retrieval without vectors', () => {
  assert.match(app, /Retrieved workbook evidence:/)
  assert.match(app, /document\.vector_similarity/)
  assert.match(app, /document\.structured_match_score/)
  assert.match(app, /document\.fields_used/)
  assert.doesNotMatch(app, /embedding_vector/)
  assert.match(app, /Ollama Prediction Explanation/)
  assert.doesNotMatch(app, />Groq provider assistant</)
})

test('Member 360 loads the canonical member money summary from the backend', () => {
  assert.match(app, /fetchJson\(`\/api\/members\/\$\{encodeURIComponent\(member\.memberId\)\}`\)/)
  assert.match(app, /buildMemberStats\(member, memberMoney, payerCohortSavings\)/)
  assert.match(app, /payerCohortSavingsSummary/)
  assert.match(app, /Predicted Payer Avoidable Spend — Cohort/)
  assert.doesNotMatch(app, /buildMemberStats\(member\)\s*$/m)
})

test('Member 360 paginates every member claim and adjudication explanations use information buttons', () => {
  assert.match(app, /const memberEncountersPageSize = 10/)
  assert.match(app, /member\.claims\.slice\(/)
  assert.match(app, /title="Member Encounters"/)
  assert.match(app, /claims=\{memberClaimsPage\}/)
  assert.match(app, /totalCount=\{member\.claims\.length\}/)
  assert.match(app, /onPageChange=\{setMemberEncountersPage\}/)
  assert.doesNotMatch(app, /member\.claims\.slice\(0,\s*8\)/)

  const reasons = app.slice(app.indexOf('function ClaimReasonCard'), app.indexOf('function RecentEncounters'))
  assert.match(reasons, /<details className="reason-info">/)
  assert.match(reasons, /aria-label=\{`Explain \$\{row\.field\}`\}/)
  assert.match(reasons, /<dd>\{row\.source\}<\/dd>/)
  assert.match(reasons, /<dd>\{row\.method\}<\/dd>/)
  assert.doesNotMatch(reasons, /<p>\{row\.reason\}<\/p>\s*<\/div>\s*\)\)\}/)
})

test('prediction UI keeps predicted and validated avoidable spend separate', () => {
  assert.match(app, /snapshot\.predicted_avoidable_spend\?\.value/)
  assert.match(app, /result\.validated_avoidable_spend\.value/)
  assert.match(app, /<details className="validated-avoidable-detail">/)
  assert.match(app, /Highest Predicted Avoidable Spend/)
  assert.match(app, /scenario\.predicted_avoidable_spend\.value/)
  assert.match(app, /Predicted Avoidable Spend — Next 90 Days/)
  assert.match(app, /Expected avoidable repeat cost/)
  assert.match(app, /snapshot\.future_denial_exposure\?\.value/)
  assert.match(app, /denialDetail\.denial_probability/)
  assert.doesNotMatch(app, /repeat_probability_90d\s*\*\s*avoidable_given_repeat_probability/)
})
