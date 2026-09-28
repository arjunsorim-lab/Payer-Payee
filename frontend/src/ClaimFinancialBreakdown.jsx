import { useState } from 'react'
import { DollarSign, Target } from 'lucide-react'
import { formatOptionalCurrency, formatProbability } from './providerLlmFormat.js'
import { PlainTooltip } from './PlainTooltip.jsx'

export function DetailedClaimFinancialBreakdown({ scenario, facts, snapshot, summary }) {
  const [activeTab, setActiveTab] = useState('overview')
  const avoidable = snapshot.predicted_avoidable_spend || {}
  const similarClaims = scenario.similar_historical_claims || []
  const patterns = scenario.historical_patterns || {}
  const avoidableBasis = snapshot.avoidable_prediction_basis || {}
  const repeatEvidence = avoidableBasis.repeat_probability?.evidence || {}
  const avoidabilityEvidence = avoidableBasis.avoidable_probability?.evidence || {}
  const repeatCostEvidence = avoidableBasis.repeat_cost || {}

  const billed = facts.charge || 0
  const allowed = facts.allowed || 0
  const discount = Math.max(0, billed - allowed)
  const paid = facts.paid || 0
  const patientResp = facts.patient_responsibility || 0
  const patientPaid = facts.patient_payment_received || 0
  const patientUnpaid = Math.round((patientResp - patientPaid) * 100) / 100
  const recordedPatientBalance = facts.outstanding_patient_balance

  return (
    <div className="financial-responsibility-breakdown-card">
      <div className="resp-breakdown-header">
        <div className="resp-title">
          <DollarSign size={20} className="resp-icon" />
          <div>
            <h3>Money for this bill</h3>
            <span className="resp-subtitle">Who paid, what is left, and one separate future guess</span>
          </div>
        </div>
        <div className="resp-tab-nav">
          <button
            type="button"
            className={`resp-tab-btn ${activeTab === 'overview' ? 'active' : ''}`}
            onClick={() => setActiveTab('overview')}
          >
            Who paid
          </button>
          <button
            type="button"
            className={`resp-tab-btn ${activeTab === 'avoidable_math' ? 'active' : ''}`}
            onClick={() => setActiveTab('avoidable_math')}
          >
            Why we guessed {formatOptionalCurrency(avoidable.value)}
          </button>
          <button
            type="button"
            className={`resp-tab-btn ${activeTab === 'avoid_action' ? 'active' : ''}`}
            onClick={() => setActiveTab('avoid_action')}
          >
            What a person should check
          </button>
          <button
            type="button"
            className={`resp-tab-btn ${activeTab === 'evidence_claims' ? 'active' : ''}`}
            onClick={() => setActiveTab('evidence_claims')}
          >
            Older bills we looked at ({similarClaims.length})
          </button>
        </div>
      </div>

      {activeTab === 'overview' && (
        <div className="tab-pane-content">
          <div className="financial-waterfall-bar-wrap">
            <div className="waterfall-header">
              <span className="waterfall-title">How the {formatOptionalCurrency(billed)} bill was split</span>
              <span className="waterfall-discount-pill">Price lowered by insurance: -{formatOptionalCurrency(discount)}</span>
            </div>
            <div className="waterfall-progress-bar">
              <div className="bar-segment bar-payer" style={{ width: `${billed > 0 ? (paid / billed) * 100 : 0}%` }} title={`Payer Paid: ${formatOptionalCurrency(paid)}`}>
                <span>Insurance paid: {formatOptionalCurrency(paid)}</span>
              </div>
              <div className="bar-segment bar-patient" style={{ width: `${billed > 0 ? (patientResp / billed) * 100 : 0}%` }} title={`Patient: ${formatOptionalCurrency(patientResp)}`}>
                <span>Patient share: {formatOptionalCurrency(patientResp)}</span>
              </div>
              <div className="bar-segment bar-discount" style={{ width: `${billed > 0 ? (discount / billed) * 100 : 0}%` }} title={`Discount: ${formatOptionalCurrency(discount)}`}>
                <span>Price lowered: -{formatOptionalCurrency(discount)}</span>
              </div>
            </div>
          </div>

          <div className="resp-columns-grid">
            <div className="resp-column-box billed-box">
              <span className="box-kicker">1. What the hospital asked for</span>
              <div className="box-main-num">
                <span>First price</span>
                <PlainTooltip text="This is the provider's starting price before insurance contract rules were applied."><strong>{formatOptionalCurrency(billed)}</strong></PlainTooltip>
              </div>
              <div className="box-sub-items">
                <div className="sub-row">
                  <span>Insurance price:</span>
                  <PlainTooltip text="This is the amount the health plan recognizes for the service. It is often called the allowed amount."><strong>{formatOptionalCurrency(allowed)}</strong></PlainTooltip>
                </div>
                <div className="sub-row discount-row">
                  <span>Price lowered by insurance:</span>
                  <strong className="success-text">-{formatOptionalCurrency(discount)}</strong>
                </div>
                <div className="sub-row">
                  <span>How much lower:</span>
                  <span>{billed > 0 ? ((discount / billed) * 100).toFixed(1) : 0}% lower</span>
                </div>
              </div>
              <details className="box-provenance-explainer">
                <summary>Show where these numbers came from</summary>
                <div>
                  <p><strong>Scope:</strong> One recorded claim (Claim {scenario.claim_id}) from {facts.provider || 'the provider'} for billing code {facts.cpt_code || 'not recorded'}.</p>
                  <p><strong>Insurance-agreed price:</strong> The allowed amount recorded on this claim.</p>
                  <p><strong>Discount calculation:</strong> {formatOptionalCurrency(billed)} billed − {formatOptionalCurrency(allowed)} agreed price = {formatOptionalCurrency(discount)} price reduction.</p>
                </div>
              </details>
            </div>

            <div className="resp-column-box payer-box">
              <span className="box-kicker">2. What insurance paid</span>
              <div className="box-main-num">
                <span>Money insurance paid</span>
                <PlainTooltip text="This is the payer-paid amount stored on the claim. It is money recorded as paid, not the model's estimate."><strong className="payer-highlight">{formatOptionalCurrency(paid)}</strong></PlainTooltip>
              </div>
              <div className="box-sub-items">
                <div className="sub-row">
                  <span>Computer’s guess from older bills:</span>
                  <strong>{formatOptionalCurrency(snapshot.predicted_provider_payment?.value)}</strong>
                </div>
                <div className="sub-row">
                  <span>Insurance name:</span>
                  <span>{facts.payer || 'Not recorded'}</span>
                </div>
                <div className="sub-row">
                  <span>Part of the insurance price paid:</span>
                  <strong className="payer-highlight">{allowed > 0 ? ((paid / allowed) * 100).toFixed(1) : 0}%</strong>
                </div>
              </div>
              <details className="box-provenance-explainer">
                <summary>Show where these numbers came from</summary>
                <div>
                  <p><strong>Recorded insurance payment:</strong> The paid amount stored in the workbook for this claim. In a production system, this would normally be checked against the payer remittance.</p>
                  <p><strong>Estimated payment:</strong> Calculated from {snapshot.peer_sample_size || similarClaims.length || 0} earlier claims selected by the prediction engine. The exact matching rule is available in the technical details.</p>
                </div>
              </details>
            </div>

            <div className="resp-column-box patient-box">
              <span className="box-kicker">3. What the patient may pay</span>
              <div className="box-main-num">
                <span>Patient share</span>
                <PlainTooltip text="This is the amount the workbook assigns to the patient, including deductible, copay, or coinsurance components."><strong className="patient-highlight">{formatOptionalCurrency(patientResp)}</strong></PlainTooltip>
              </div>
              <div className="box-sub-items">
                <div className="sub-row">
                  <span>Already paid:</span>
                  <strong className="paid-amount">{formatOptionalCurrency(patientPaid)} ({patientResp > 0 ? ((patientPaid / patientResp) * 100).toFixed(0) : 0}%)</strong>
                </div>
                <div className="sub-row unpaid-row">
                  <span>Calculated remaining balance:</span>
                  <PlainTooltip text="This is the recorded patient amount minus patient payments received. Staff should confirm it before contacting the patient."><strong className="warning-text">{formatOptionalCurrency(patientUnpaid)}</strong></PlainTooltip>
                </div>
                {Number.isFinite(recordedPatientBalance) && Math.abs(recordedPatientBalance - patientUnpaid) > 0.01 ? <p role="note">Balance reconciliation: {formatOptionalCurrency(patientResp)} − {formatOptionalCurrency(patientPaid)} = {formatOptionalCurrency(patientUnpaid)}. Review totals use the calculated amount due. The source balance of {formatOptionalCurrency(recordedPatientBalance)} is retained for audit.</p> : null}
                <div className="sub-row">
                  <span>How long it has been waiting:</span>
                  <span className="badge-broken-plan">{facts.days_outstanding ?? 0} days · {facts.payment_plan_status || facts.balance_status || 'Status not recorded'}</span>
                </div>
              </div>
              <details className="box-provenance-explainer">
                <summary>Show where these numbers came from</summary>
                <div>
                  <p><strong>Amount assigned to the patient:</strong> Copied from the claim's recorded patient-responsibility field. It is not calculated as agreed price minus insurance payment.</p>
                  <p><strong>Calculated balance:</strong> {formatOptionalCurrency(patientResp)} recorded patient amount − {formatOptionalCurrency(patientPaid)} patient payments = {formatOptionalCurrency(patientUnpaid)}.</p>
                  {Number.isFinite(recordedPatientBalance) && Math.abs(recordedPatientBalance - patientUnpaid) > 0.01 ? <p><strong>Source discrepancy:</strong> The workbook separately records {formatOptionalCurrency(recordedPatientBalance)} outstanding. The calculated amount due is {formatOptionalCurrency(Math.max(0, patientUnpaid))}; this is the balance used when calculating the actionable review amount.</p> : null}
                </div>
              </details>
            </div>

            <div className="resp-column-box avoidable-box">
              <span className="box-kicker">4. A possible future cost</span>
              <div className="box-main-num">
                <span>Possible extra money if another bill happens</span>
                <PlainTooltip text="This is an average estimate across similar cases. It is not a confirmed saving for this patient."><strong className="avoidable-highlight">{formatOptionalCurrency(avoidable.value)}</strong></PlainTooltip>
              </div>
              <div className="box-sub-items">
                <div className="sub-row formula-row">
                  <span>Calculation using stored probabilities:</span>
                  <code>{avoidable.repeat_probability_90d} × {avoidable.avoidable_given_repeat_probability} × {formatOptionalCurrency(avoidable.expected_extra_repeat_allowed_cost)}</code>
                </div>
                <div className="sub-row">
                  <span>What this guess means:</span>
                  <span className="avoid-tip">Another related bill might happen in the next three months. A person must decide if it would be needed.</span>
                </div>
              </div>
              <details className="box-provenance-explainer">
                <summary>Show the math and where it came from</summary>
                <div>
                  <p><strong>{formatProbability(avoidable.repeat_probability_90d)} chance of another related claim:</strong> Estimated from earlier claim timing.</p>
                  <p><strong>{formatProbability(avoidable.avoidable_given_repeat_probability)} with avoidability evidence:</strong> The share of earlier repeats carrying the workbook's avoidability signals. This is not a medical-necessity decision.</p>
                  <p><strong>{formatOptionalCurrency(avoidable.expected_extra_repeat_allowed_cost)} possible extra cost:</strong> The historical cost estimate used if such a repeat occurs.</p>
                  <p><strong>Calculation:</strong> {avoidable.repeat_probability_90d} × {avoidable.avoidable_given_repeat_probability} × {formatOptionalCurrency(avoidable.expected_extra_repeat_allowed_cost)} = {formatOptionalCurrency(avoidable.value)} average per similar case, rounded to cents. Percentage labels elsewhere are rounded for display.</p>
                </div>
              </details>
            </div>
          </div>
        </div>
      )}

      {activeTab === 'avoidable_math' && (
        <div className="tab-pane-content math-deepdive-pane">
          <p className="math-plain-summary">The estimate combines the chance of another related claim, the share of earlier repeats with avoidability evidence, and the possible extra cost. The result is an average across similar cases, not a guaranteed saving.</p>
          <details className="technical-detail-toggle math-detail-toggle">
            <summary>Show the math behind the estimate</summary>
            <div className="math-hero-banner">
              <div className="math-equation-badge">THE MATH BEHIND THE ESTIMATE</div>
              <div className="math-equation-row">
                <div className="math-term-box">
                  <PlainTooltip text="Out of 100 similar cases, this is how many would be expected to have another related claim within three months."><span className="term-name">1. Chance of another related claim within 3 months</span></PlainTooltip>
                  <strong className="term-val">{formatProbability(avoidable.repeat_probability_90d)}</strong>
                  <small className="term-sub">{repeatEvidence.external_numerator ?? 0} repeats in {repeatEvidence.external_denominator ?? 0} broader historical observations; blended with local evidence</small>
                </div>
                <span className="math-op">×</span>
                <div className="math-term-box">
                  <PlainTooltip text="This is the share of earlier repeat claims carrying the workbook's avoidability signals. It does not decide whether care was medically necessary."><span className="term-name">2. Repeats with avoidability evidence</span></PlainTooltip>
                  <strong className="term-val">{formatProbability(avoidable.avoidable_given_repeat_probability)}</strong>
                  <small className="term-sub">{avoidabilityEvidence.external_numerator ?? 0} of {avoidabilityEvidence.external_denominator ?? 0} broader repeat observations carried avoidability evidence</small>
                </div>
                <span className="math-op">×</span>
                <div className="math-term-box">
                  <PlainTooltip text="This is the historical estimate of additional health-plan allowed cost if another related claim occurs."><span className="term-name">3. Extra cost if another claim happens</span></PlainTooltip>
                  <strong className="term-val">{formatOptionalCurrency(avoidable.expected_extra_repeat_allowed_cost)}</strong>
                  <small className="term-sub">Expected extra allowed cost from {repeatCostEvidence.peer_level || 'available historical'} evidence</small>
                </div>
                <span className="math-op">=</span>
                <div className="math-result-box">
                  <span className="result-name">Average possible savings per similar case</span>
                  <strong className="result-val">{formatOptionalCurrency(avoidable.value)}</strong>
                  <small className="result-sub">Probability-weighted estimate, not confirmed savings</small>
                </div>
              </div>
            </div>

            <div className="math-cards-grid">
              <div className="math-detail-card">
                <h4>1. Chance of another related claim within three months ({formatProbability(avoidable.repeat_probability_90d)})</h4>
                <p>The engine blends this member’s available history with broader historical observations. It uses smoothing so a small local sample does not produce an extreme result.</p>
                <ul>
                  <li><strong>Member history:</strong> <strong>{patterns.earlier_member_claims ?? 0} earlier claims</strong> are recorded before this claim.</li>
                  <li><strong>Timing evidence:</strong> <strong>{patterns.within_90_days ?? 0} related claim pairs</strong> are no more than 90 days apart.</li>
                  <li><strong>Typical gap:</strong> The median gap between related earlier claims is <strong>{patterns.median_days_between_related_claims ?? 'not available'} days</strong>.</li>
                </ul>
              </div>

              <div className="math-detail-card">
                <h4>2. Repeats with avoidability evidence ({formatProbability(avoidable.avoidable_given_repeat_probability)})</h4>
                <p>This comes from historical rows carrying avoidability evidence. It is not a clinical decision about this member.</p>
                <ul>
                  <li><strong>Broader evidence:</strong> {avoidabilityEvidence.external_numerator ?? 0} of {avoidabilityEvidence.external_denominator ?? 0} historical repeat observations carried the workbook’s avoidability signal.</li>
                  <li><strong>Limit:</strong> The app cannot determine medical necessity from claim codes alone.</li>
                </ul>
              </div>

              <div className="math-detail-card">
                <h4>3. Extra cost if another related claim happens ({formatOptionalCurrency(avoidable.expected_extra_repeat_allowed_cost)})</h4>
                <p>The engine estimates the extra health-plan allowed amount associated with a repeat using the available historical cost evidence.</p>
                <ul>
                  <li><strong>Evidence level:</strong> {repeatCostEvidence.peer_level || 'Not recorded'}.</li>
                  <li><strong>Historical episodes used:</strong> {repeatCostEvidence.peer_count ?? 0}.</li>
                </ul>
              </div>
            </div>
          </details>
        </div>
      )}

      {activeTab === 'avoid_action' && (
        <div className="tab-pane-content action-deepdive-pane">
          <div className="action-plan-grid">
            <div className="action-item-card avoid-card">
              <div className="action-card-header">
                <span className="card-badge red">Review signal</span>
                <h4>Possible related repeat involving CPT {facts.cpt_code || 'not recorded'} within 90 days</h4>
              </div>
              <p>The historical pattern has a {patterns.median_days_between_related_claims ?? 'not available'}-day median gap between related claims. This is a reason to review timing and documentation, not proof of duplicate or unnecessary care.</p>
              <div className="action-consequence">
                <span>Separate forecasts:</span> <strong>{formatOptionalCurrency(avoidable.value)} possible repeat-cost exposure and {formatOptionalCurrency(summary.future_denial_exposure)} probability-weighted denial exposure.</strong>
              </div>
            </div>

            <div className="action-item-card save-card">
              <div className="action-card-header">
                <span className="card-badge green">Suggested review</span>
                <h4>Checks a person can perform before taking action</h4>
              </div>
              <ol className="action-steps-list">
                <li>
                  <strong>Review related claims:</strong> Check whether the earlier and possible repeat services differ in diagnosis details, clinical circumstances, or billed work.
                </li>
                <li>
                  <strong>Check administrative requirements:</strong> Confirm authorization and referral requirements from the payer contract; an “N/A” workbook value is not proof that authorization was missing.
                </li>
                <li>
                  <strong>Confirm the patient balance:</strong> Review the recorded <strong>{formatOptionalCurrency(patientUnpaid)}</strong> balance and {facts.payment_plan_status || 'payment-plan'} status before contacting the patient.
                </li>
              </ol>
            </div>
          </div>

          <div className="total-savings-summary-banner">
            <div className="summary-left">
              <Target size={24} color="#15803d" />
              <div>
                <strong>Keep the amounts separate</strong>
                <p>The current follow-up amount ({formatOptionalCurrency(summary.recoverable_now)}), possible repeat-cost forecast ({formatOptionalCurrency(avoidable.value)}), and denial exposure ({formatOptionalCurrency(summary.future_denial_exposure)}) answer different questions and should not be added together.</p>
              </div>
            </div>
            <span className="savings-pill-highlight">Human review required</span>
          </div>
        </div>
      )}

      {activeTab === 'evidence_claims' && (
        <div className="tab-pane-content evidence-deepdive-pane">
          <div className="cross-member-savings-banner">
            <div className="cross-member-badge">COMPARISON WITH EARLIER CLAIMS</div>
            <div className="cross-member-header-row">
              <div>
                <h4>How this claim compares with the earlier claims selected by the model</h4>
                <p>
                  The model selected {similarClaims.length} earlier claims using diagnosis, procedure, payer, provider, and service-setting fields when available. The technical codes are shown in the table only so a reviewer can verify the match.
                </p>
              </div>
              <div className="cross-member-kpis">
                <div className="cross-kpi-item">
                  <span>This claim's insurance price:</span>
                  <strong>{formatOptionalCurrency(allowed)}</strong>
                </div>
                <div className="cross-kpi-item highlight">
                  <span>Estimated insurance price from earlier claims:</span>
                  <strong>{formatOptionalCurrency(snapshot.predicted_allowed?.value)}</strong>
                </div>
              </div>
            </div>
            <div className="cross-member-reasoning-card">
              <strong>How to read this comparison:</strong>
              <p>Lower earlier prices show that variation exists. They do not establish savings by themselves. Payer, provider, location, contract terms, coding, and the work performed must be checked before drawing a conclusion.</p>
            </div>
          </div>

          <div className="evidence-table-heading">
            <h4>Earlier claims selected for comparison ({similarClaims.length})</h4>
            <p>These rows let a reviewer check the prices and matching fields used by the model.</p>
          </div>
          {similarClaims.length ? (
            <div className="evidence-table-wrapper">
              <table className="evidence-table-full">
                <thead>
                  <tr>
                    <th>Claim ID</th>
                    <th>Date</th>
                    <th>Medical code</th>
                    <th>Billing code</th>
                    <th>Provider</th>
                    <th>Insurance company</th>
                    <th>Insurance price</th>
                    <th>Difference from this claim</th>
                    <th>How to interpret it</th>
                  </tr>
                </thead>
                <tbody>
                  {similarClaims.map((item) => {
                    const diff = (allowed || 0) - (item.allowed || 0)
                    const isLower = diff > 0
                    return (
                      <tr key={item.claim_id}>
                        <td><strong>{item.claim_id}</strong></td>
                        <td>{item.service_date}</td>
                        <td><code>{item.icd10}</code></td>
                        <td><code>{item.cpt}</code></td>
                        <td>{item.provider}</td>
                        <td>{item.payer}</td>
                        <td><strong>{formatOptionalCurrency(item.allowed)}</strong></td>
                        <td>
                          {isLower ? (
                            <span className="savings-diff-pill favorable">
                              -{formatOptionalCurrency(diff)} ({((diff / (allowed || 1)) * 100).toFixed(1)}% less)
                            </span>
                          ) : (
                            <span className="savings-diff-pill neutral">
                              +{formatOptionalCurrency(Math.abs(diff))} higher
                            </span>
                          )}
                        </td>
                        <td>
                          <small className="peer-strategy-note">
                            {isLower
                              ? `This earlier claim has a lower recorded insurance price. Review the matching details before treating the difference as savings.`
                              : `This earlier claim was selected as a comparison, but its recorded insurance price is not lower.`}
                          </small>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          ) : (
            <p>No similar historical claims available.</p>
          )}
        </div>
      )}
    </div>
  )
}

