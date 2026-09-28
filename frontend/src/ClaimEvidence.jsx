import { financialImpactSummaryFromBilledResult } from './financialImpactSummary.js'
import { ShieldAlert } from 'lucide-react'
import { formatDate } from './claimFormatting.js'
import { formatOptionalCurrency, formatProbability } from './providerLlmFormat.js'
import { evidenceBadgeClass, evidenceLabel } from './evidenceUi.js'

export function PlainLanguageClaimNarrative({ scenario, facts, summary, snapshot, historicalPeerCount, billedComparison }) {
  const peers = scenario.similar_historical_claims || []
  const avoidable = snapshot.predicted_avoidable_spend || {}
  const billed = facts.charge || 0
  const allowed = facts.allowed || 0
  const discount = Math.max(0, billed - allowed)
  const patientPaid = facts.patient_payment_received || 0
  const patientBalance = facts.outstanding_patient_balance || 0
  const lowestPeer = peers.reduce((lowest, peer) => (
    !lowest || peer.allowed < lowest.allowed ? peer : lowest
  ), null)
  const lowestPeerAllowed = Number.isFinite(lowestPeer?.allowed) ? lowestPeer.allowed : null
  const peerDifference = Number.isFinite(lowestPeerAllowed) ? Math.max(0, allowed - lowestPeerAllowed) : null
  const serviceName = facts.cpt_description || 'a healthcare service'
  const action = summary.best_action || {}
  const historicalEvidence = facts.outcome_evidence || facts.outcomeEvidence || null
  const financialImpactSummary = financialImpactSummaryFromBilledResult(billedComparison)

  return (
    <section className="plain-claim-narrative" aria-labelledby="plain-claim-narrative-title">
      <header>
        <span>A simple story about this visit</span>
        {!financialImpactSummary ? (
          <>
            <h2 id="plain-claim-narrative-title">What this claim prediction is saying</h2>
            <p>This section explains claim <strong>{facts.claim_id}</strong> in plain language: what happened, what the app is estimating, and which recorded claims support the recommendation. Model estimates are separate from recorded facts, so they should not be added together.</p>
          </>
        ) : null}
      </header>

      {financialImpactSummary ? (
        <aside className="financial-impact-summary" role="note">
          {financialImpactSummary}
        </aside>
      ) : null}

      {historicalEvidence?.reference_outcome_supported === true ? (
        <aside className="start-here-strongest-claim" role="note">
          <strong>This claim is one of the strongest demo claims.</strong> The historical reference patient
          received {historicalEvidence.reference_intervention} (claim {historicalEvidence.reference_intervention_claim_id || historicalEvidence.reference_claim_id})
          . This prediction patient has the same diagnosis pattern but has not received that intervention
          {historicalEvidence.prediction_readmission_billed_amount != null
            ? <> and later had a related hospitalization (claim {historicalEvidence.prediction_readmission_claim_id}) billed at {formatOptionalCurrency(historicalEvidence.prediction_readmission_billed_amount)}</>
            : null}
          . Use the outcome evidence below to verify the claim IDs, dates, and billed amounts used in this example.
        </aside>
      ) : null}

      <div className="claim-story-grid">
        <article>
          <span className="story-number">1</span>
          <div>
            <h3>What happened</h3>
            <p>This was a <strong>{serviceName}</strong> visit at {facts.provider || 'the provider'} on {formatDate(facts.service_date)}. The recorded billed amount was <strong>{formatOptionalCurrency(billed)}</strong>. The insurance plan allowed <strong>{formatOptionalCurrency(allowed)}</strong>, which is {formatOptionalCurrency(discount)} below the billed amount.</p>
            <p>The insurance company paid <strong>{formatOptionalCurrency(facts.paid)}</strong>. The patient’s share was <strong>{formatOptionalCurrency(facts.patient_responsibility)}</strong>. The record says {formatOptionalCurrency(patientPaid)} was already paid and <strong>{formatOptionalCurrency(patientBalance)} still needs to be checked</strong>.</p>
          </div>
        </article>

        <article>
          <span className="story-number">2</span>
          <div>
            <h3>What the computer is guessing</h3>
            <p>It looked at <strong>{historicalPeerCount} older bills that look similar</strong> to estimate the provider payment: <strong>{formatOptionalCurrency(snapshot.predicted_provider_payment?.value)}</strong>. This is a prediction, not a recorded payment.</p>
            <p>It also estimates a <strong>{formatProbability(avoidable.repeat_probability_90d)}</strong> chance of another related bill in the next three months. The possible future cost is <strong>{formatOptionalCurrency(avoidable.value)}</strong>. This is separate from the billed amount. This is only a guess, not a promise.</p>
          </div>
        </article>

        <article>
          <span className="story-number">3</span>
          <div>
            <h3>What to do next</h3>
            <p>{action.action || 'Review the recorded claim information before taking action.'}</p>
            <p>For a billed comparison, the application uses the same member’s earlier and later episodes only when it can show the source claims and intervention lines. The predicted difference is a review flag—not confirmed savings. A person must check the records before doing anything.</p>
          </div>
        </article>
      </div>

      {Number.isFinite(lowestPeerAllowed) ? (
        <p className="plain-peer-comparison">
          <strong>Why we looked at these {peers.length} older bills:</strong> They are the closest bills we found for the same kind of problem and visit. <strong>Why bill {lowestPeer.claim_id} is shown:</strong> {lowestPeer.match_reason || 'It was one of the closest matches.'} Its insurance price is the lowest. The difference is {formatOptionalCurrency(peerDifference)} because {formatOptionalCurrency(allowed)} minus {formatOptionalCurrency(lowestPeerAllowed)} equals {formatOptionalCurrency(peerDifference)}. This only tells us that the prices are different. It does not prove that anyone did something wrong.
        </p>
      ) : null}

      <div className="narrative-guardrails">
        <p><strong>Do not add these numbers together:</strong> {formatOptionalCurrency(summary.recoverable_now)} is money to check now. {formatOptionalCurrency(avoidable.value)} is a possible future cost. {formatOptionalCurrency(summary.future_denial_exposure)} is money that might be at risk.</p>
        <p><strong>About this practice data:</strong> Some added payment and older-reference information is made-up example data. Real use needs real bills and payment records.</p>
      </div>

      <details className="plain-glossary-toggle">
        <summary>Tap here if a word is new</summary>
        <dl>
          <div><dt>Insurance price</dt><dd>The price the insurance plan uses for this visit.</dd></div>
          <div><dt>Patient share</dt><dd>The part of the bill the patient may need to pay.</dd></div>
          <div><dt>Another bill</dt><dd>A related bill in the next three months. It may still be needed.</dd></div>
          <div><dt>Money at risk</dt><dd>Money that could be lost if insurance does not pay. It is not lost yet.</dd></div>
        </dl>
      </details>
    </section>
  )
}


export function InterventionProposal({ plan }) {
  if (!plan) return null
  return (
    <section aria-label="Scenario-specific intervention proposal">
      <span className={evidenceBadgeClass('recommendation')}>{evidenceLabel('recommendation')}</span>
      <strong>{plan.title || 'Intervention: insufficient evidence'}</strong>
      <p>{plan.action || plan.reason}</p>
      {plan.available ? (
        <>
          <p><strong>{plan.timing_basis === 'illustrative_demo' ? 'Example follow-up' : 'Conditional reassessment'}: {plan.follow_up_days} days.</strong> {plan.timing}</p>
          <small>Proposed for clinical review. This is not a recorded service and is not included in savings calculations.</small>
          {plan.source_url ? <p><a href={plan.source_url} target="_blank" rel="noreferrer">Clinical guidance</a></p> : null}
        </>
      ) : null}
    </section>
  )
}


export function ClaimOutcomeEvidencePanel({ facts }) {
  const raw = facts.outcome_evidence || facts.outcomeEvidence || facts.workbookFields || facts.syntheticEnrichment || {}
  const evidence = facts.outcome_evidence || facts.outcomeEvidence ? raw : {
    condition_resolved: raw.Condition_Resolved || raw.condition_resolved || 'Not recorded',
    treatment_outcome: raw.Treatment_Outcome || raw.treatment_outcome || 'Not recorded',
    follow_up_completed: raw.Follow_Up_Completed || raw.follow_up_completed || 'Not recorded',
    outcome_claim_flag: raw.Outcome_Claim_Flag || raw.outcome_claim_flag || 'Not recorded',
    related_claim_flag: raw.Related_Claim_Flag || raw.related_claim_flag || 'Not recorded',
    synthetic: String(raw.Reason_Code || '').toUpperCase() === 'SYNTHETIC_PRESENTATION_CASE' || String(raw.reason_code || '').toUpperCase() === 'SYNTHETIC_PRESENTATION_CASE',
    status: 'Not established',
    conclusion: 'No outcome fields are recorded for this claim.',
  }
  const outcomeText = [evidence.condition_resolved, evidence.treatment_outcome, evidence.follow_up_completed].filter(Boolean).join(' | ').toLowerCase()
  const positiveOutcome = /(resolved|improved|recovered|documented outcome assessment|clinical improvement)/i.test(outcomeText)
  const historicalReference = evidence.reference_outcome_supported === true
  const recordedOutcome = [
    evidence.condition_resolved,
    evidence.treatment_outcome,
    evidence.follow_up_completed,
    evidence.outcome_claim_flag,
    evidence.related_claim_flag,
  ].some((value) => value && !/^not recorded$/i.test(String(value).trim()))
  const status = historicalReference ? 'Historical reference evidence' : positiveOutcome ? 'Recorded outcome evidence' : (evidence.status || 'Not established')
  const conclusion = historicalReference
    ? evidence.conclusion
    : positiveOutcome
    ? (evidence.conclusion || 'Recorded claim fields show a resolved or improved outcome.')
    : recordedOutcome
      ? 'Recorded claim fields are shown below. They do not record a resolved or improved outcome for this claim.'
      : 'No outcome fields are recorded for this claim.'
  const heading = historicalReference
    ? 'Historical evidence supports this prediction'
    : positiveOutcome
    ? 'Recorded outcome evidence'
    : recordedOutcome
      ? 'Recorded outcome fields'
      : 'Outcome not recorded'
  const personLabel = (name, id) => [name, id ? `(${id})` : ''].filter(Boolean).join(' ') || id || name || 'Not recorded'
  const claimWithMemberLabel = (claimId, name, memberId) => (
    claimId ? `${claimId} · ${personLabel(name, memberId)}` : personLabel(name, memberId)
  )
  const fields = historicalReference ? [
    ['Matched member', personLabel(evidence.member_name, evidence.member_id)],
    ['Matched episode', evidence.episode_id],
    ['Historical reference claim', claimWithMemberLabel(evidence.reference_claim_id, evidence.reference_member_name, evidence.reference_member_id)],
    ['Matched diagnosis', evidence.reference_diagnosis],
    ['Intervention that improved the historical outcome', evidence.reference_intervention_claim_id ? `${evidence.reference_intervention} (${evidence.reference_intervention_claim_id})` : evidence.reference_intervention],
    ['Recorded historical outcome', evidence.reference_treatment_outcome],
    ['Prediction claim', claimWithMemberLabel(evidence.prediction_claim_id, evidence.prediction_member_name, evidence.prediction_member_id || evidence.member_id)],
    ['Intervention already performed', evidence.prediction_intervention_performed === 'N' ? 'No — the preventive intervention was not performed for this patient' : evidence.prediction_intervention_performed],
    ['Later hospitalization', evidence.claim_is_later_hospitalization ? `This claim (${evidence.prediction_readmission_claim_id}) is the later hospitalization` : (evidence.prediction_readmission_claim_id || 'Not recorded')],
    ['Time to later hospitalization', evidence.prediction_readmission_gap_days == null ? 'Not recorded' : `${evidence.prediction_readmission_gap_days} days`],
    ['Recommended earlier intervention', evidence.reference_intervention_claim_id ? `${evidence.recommended_intervention} (${evidence.reference_intervention_claim_id})` : evidence.recommended_intervention],
  ] : [
    ['Preventive claim', evidence.preventive_claim_id || 'Not recorded'],
    ['Preventive service date', evidence.preventive_service_date || 'Not recorded'],
    ['Preventive procedure', evidence.preventive_procedure || 'Not recorded'],
    ['Outcome claim', evidence.source_claim_id || facts.claim_id || 'Not recorded'],
    ['Reference claim', evidence.reference_claim_id || 'Not recorded'],
    ['Outcome service date', evidence.source_service_date || 'Not recorded'],
    ['Outcome procedure', evidence.source_procedure || 'Not recorded'],
    ['Condition resolved', evidence.condition_resolved],
    ['Treatment outcome', evidence.treatment_outcome],
    ['Follow-up completed', evidence.follow_up_completed],
    ['Outcome claim flag', evidence.outcome_claim_flag],
    ['Reference claim flag', evidence.reference_claim_flag],
    ['Related claim flag', evidence.related_claim_flag],
    ['Later related claims after outcome', evidence.no_later_related_claims === true
      ? (evidence.historical_no_readmission_days != null
        ? `None recorded during ${evidence.historical_no_readmission_days} observed day(s) so far`
        : 'None recorded')
      : evidence.later_related_claim_ids?.join(', ') || 'Not evaluated'],
    ...(evidence.historical_no_readmission_days != null && evidence.no_later_related_claims === true
      ? [['Observed days without a related claim/readmission', `${evidence.historical_no_readmission_days} days`]]
      : []),
  ]

  return (
    <aside className="claim-outcome-evidence" aria-label="Claim outcome evidence">
      <InterventionProposal plan={evidence.intervention_plan} />
      <div className="claim-outcome-evidence-heading">
        <ShieldAlert size={18} />
        <div>
          <span>Outcome evidence for this claim</span>
          <strong>{heading}</strong>
        </div>
      </div>
      <p>{conclusion}</p>
      {historicalReference ? (
        <div className="outcome-evidence-traceability">
          <strong>Where the billed amounts come from</strong>
          <ul>
            {[evidence.reference_source_row, evidence.prediction_readmission_source].filter(Boolean).map((row) => (
              <li key={row.claim_id}>
                <strong>{formatOptionalCurrency(row.billed_amount)}</strong> — recorded billed (charge) amount from claim{' '}
                <strong>{row.claim_id}</strong> dated {row.service_date}. Why included: {row.why_included}
              </li>
            ))}
          </ul>
          <small>The later hospitalization's billed charge is the potentially avoidable amount. The historical reference claim is evidence for choosing the intervention; its billed charge is shown only for traceability and is not added to the predicted saving.</small>
        </div>
      ) : null}
      <dl>
        {fields.map(([label, value]) => (
          <div key={label}><dt>{label}</dt><dd>{value || 'Not recorded'}</dd></div>
        ))}
        <div><dt>Data type</dt><dd>{evidence.synthetic ? 'Synthetic demonstration' : 'Recorded claim data'}</dd></div>
      </dl>
      <small>Evidence status: {status}. Clinical results, diagnosis resolution, and causal attribution require evidence outside the claim record.</small>
    </aside>
  )
}

