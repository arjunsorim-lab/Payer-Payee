import { useEffect, useMemo, useRef, useState } from 'react'
import {
  ArrowLeftRight,
  CheckCircle2,
  ChevronRight,
  FileText,
  RefreshCw,
  Scale,
  Shield,
  ShieldAlert,
  Sparkles,
  TrendingDown,
  Users,
  X,
} from 'lucide-react'

function ReferenceInterventionCounterfactualView({ result, family, compact = false }) {
  const calculation = result?.calculation || {}
  const intervention = result?.intervention || {}
  const reference = result?.reference_patient || {}
  const episode1 = result?.episode_1 || {}
  const episode2 = result?.episode_2 || {}
  const fmt = (value) => new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(value || 0)
  const rowLabel = (rows = []) => rows.map((row) => `${row.claim_id} (${row.cpt || 'code n/a'})`).join(', ') || 'No claim ID'
  const interventionLabel = `${intervention.claim_id || 'intervention claim'}${intervention.cpt ? ` (${intervention.cpt})` : ''}`
  const episode1Claim = (episode1.source_rows || [])[0] || {}
  const episode2Claim = (episode2.source_rows || [])[0] || {}
  const referenceRows = result.reference_episode?.source_rows || []

  return (
    <section className="same-patient-billed-card" aria-labelledby="reference-counterfactual-heading">
      <header>
        <span className="same-patient-billed-kicker">Claim-anchored billed comparison · {family || reference.diagnosis_family}</span>
        <h2 id="reference-counterfactual-heading">Estimated billed impact of moving the specific intervention earlier</h2>
        <p>
          This compares the recorded two-visit journey for member <strong>{result.prediction_patient?.member_id}</strong> with a scenario that applies <strong>{intervention.description}</strong> at EP1. The intervention amount comes from the historical reference pathway for member <strong>{reference.member_id}</strong>, and only the specific line <strong>{interventionLabel}</strong> is counted.
        </p>
      </header>

      <div className="same-patient-billed-grid">
        <div><span>EP1 initial episode actually billed</span><strong>{fmt(calculation.episode_1_cost)}</strong><small>{rowLabel(episode1.source_rows)}.</small></div>
        <div><span>EP2 later hospitalization actually billed</span><strong>{fmt(calculation.episode_2_cost)}</strong><small>{rowLabel(episode2.source_rows)} · {result.days_between_episodes} days after EP1.</small></div>
        <div><span>Cost of the service proposed earlier</span><strong>{fmt(calculation.intervention_cost)}</strong><small>{interventionLabel} · {intervention.description}.</small></div>
        <div><span>What was billed across both visits</span><strong>{fmt(calculation.actual_cost)}</strong><small>{fmt(calculation.episode_1_cost)} EP1 + {fmt(calculation.episode_2_cost)} EP2.</small></div>
        <div><span>Estimated EP1 with that intervention</span><strong>{fmt(calculation.proposed_cost)}</strong><small>{fmt(calculation.episode_1_cost)} EP1 + {fmt(calculation.intervention_cost)} intervention.</small></div>
        <div className="difference"><span>Predicted billed amount that may be avoided</span><strong>{fmt(calculation.potential_savings)}</strong><small>{fmt(calculation.actual_cost)} actual total − {fmt(calculation.proposed_cost)} estimated earlier scenario.</small></div>
      </div>

      <div className="same-patient-billed-equation" role="note">
        <strong>Predicted billed difference</strong>
        <span>{fmt(calculation.actual_cost)} actual − {fmt(calculation.proposed_cost)} proposed = <b>{fmt(calculation.potential_savings)}</b></span>
      </div>

      {!compact ? (
        <>
        <div className="calculation-explanation">
          <h3>Where every number comes from</h3>
          <ol>
            <li><strong>EP1 source:</strong> {(result.episode_1_source_claim_ids || []).join(', ')} contributes {fmt(calculation.episode_1_cost)}.</li>
            <li><strong>EP2 source:</strong> {(result.episode_2_source_claim_ids || []).join(', ')} contributes {fmt(calculation.episode_2_cost)}.</li>
            <li><strong>Intervention source:</strong> {(result.intervention_source_claim_ids || []).join(', ')} contributes {fmt(calculation.intervention_cost)}. The reference preventive visit itself is not counted as the intervention.</li>
            <li><strong>Actual total:</strong> {fmt(calculation.episode_1_cost)} + {fmt(calculation.episode_2_cost)} = <strong>{fmt(calculation.actual_cost)}</strong>.</li>
            <li><strong>Estimated earlier scenario:</strong> {fmt(calculation.episode_1_cost)} + {fmt(calculation.intervention_cost)} = <strong>{fmt(calculation.proposed_cost)}</strong>.</li>
            <li><strong>Predicted billed difference:</strong> {fmt(calculation.actual_cost)} − {fmt(calculation.proposed_cost)} = <strong>{fmt(calculation.potential_savings)}</strong>.</li>
          </ol>
          <p><strong>Why propose the service earlier?</strong> The selected intervention is the distinct self-management training line from the positive-outcome reference pathway. The calculation asks what the billed total would look like if that line were applied at EP1 and the later hospitalization were avoided.</p>
          <p><strong>Why the difference is {fmt(calculation.potential_savings)}:</strong> EP1 appears in both totals. The difference is EP2 {fmt(calculation.episode_2_cost)} minus the intervention {fmt(calculation.intervention_cost)}.</p>
        </div>
        <section className="intervention-selection-reason" aria-labelledby="reference-selection-heading">
          <span>Selection reasoning</span>
          <h3 id="reference-selection-heading">Why this claim and intervention were chosen</h3>
          <div className="intervention-reason-grid">
            <div><strong>Why this claim?</strong><p>{episode2Claim.claim_id || 'The selected claim'} is the later related episode for member {result.prediction_patient?.member_id}.</p></div>
            <div><strong>Why this earlier visit?</strong><p>{episode1Claim.claim_id || 'EP1'} is the earlier claim in the same member and diagnosis family before the later hospitalization.</p></div>
            <div><strong>Why this diagnosis?</strong><p>Both prediction claims are in ICD-10 family {family || reference.diagnosis_family}. {episode1Claim.icd10 && episode2Claim.icd10 ? `The recorded codes are ${episode1Claim.icd10} and ${episode2Claim.icd10}.` : ''}</p></div>
            <div><strong>Why this intervention?</strong><p>{intervention.selection_reason}</p></div>
          </div>
          <p className="intervention-selection-rule"><strong>Selection rule:</strong> Use the selected later claim as EP2, find the closest earlier same-member claim for the same diagnosis family as EP1, then add only the distinct intervention line from the positive-outcome reference pathway.</p>
        </section>
        <details className="scenario-technical-details">
          <summary>
            <span className="technical-details-toggle-label"><FileText size={16} /><span>Show hard words, codes, and detailed math</span></span>
            <span className="technical-details-toggle-help">Full glossary + source rows</span>
            <ChevronRight size={16} />
          </summary>
          <div className="scenario-technical-content">
            <p className="technical-details-context">Prediction member {result.prediction_patient?.member_id} · reference member {reference.member_id} · diagnosis family {family || reference.diagnosis_family}.</p>
            <div className="episode-evidence-grid">
              <div><h4>EP1 · {episode1.service_date || 'date unavailable'}</h4><p>Actual billed total: <strong>{fmt(calculation.episode_1_cost)}</strong></p><ul>{(episode1.source_rows || []).map((row) => <li key={row.claim_id}><strong>{row.claim_id}</strong> · {row.procedure_description || row.cpt} · ICD-10 {row.icd10 || 'not recorded'} · CPT {row.cpt || 'not recorded'} · billed {fmt(row.billed_amount)}</li>)}</ul></div>
              <div><h4>EP2 · {episode2.service_date || 'date unavailable'}</h4><p>Actual billed total: <strong>{fmt(calculation.episode_2_cost)}</strong></p><ul>{(episode2.source_rows || []).map((row) => <li key={row.claim_id}><strong>{row.claim_id}</strong> · {row.procedure_description || row.cpt} · ICD-10 {row.icd10 || 'not recorded'} · CPT {row.cpt || 'not recorded'} · billed {fmt(row.billed_amount)}</li>)}</ul></div>
            </div>
            <div className="technical-details-section">
              <h4>Reference pathway used for the intervention</h4>
              <ul>
                {referenceRows.map((row) => <li key={row.claim_id}><strong>{row.claim_id}</strong> · {row.procedure_description || row.cpt} · billed {fmt(row.billed_amount)}</li>)}
              </ul>
            </div>
          </div>
        </details>
        </>
      ) : null}

      <p className="same-patient-billed-warning">{result.disclaimer}</p>
    </section>
  )
}

export function SamePatientBilledSavings({ memberId, diagnosisCode, claimId = '', claimAnchored = false, preloadedResult = null }) {
  const family = String(diagnosisCode || '').slice(0, 3).toUpperCase()
  const [result, setResult] = useState(preloadedResult)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    if (preloadedResult) {
      setResult(preloadedResult)
      setLoading(false)
      setError('')
      return undefined
    }
    if (!memberId || !family) return undefined
    let active = true
    setLoading(true)
    setError('')
    const anchor = claimId ? `&anchor_claim_id=${encodeURIComponent(claimId)}` : ''
    fetch(`/api/claims/same-patient-billed-savings?member_id=${encodeURIComponent(memberId)}&diagnosis_family=${encodeURIComponent(family)}${anchor}`)
      .then((response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}: Same-patient comparison failed`)
        return response.json()
      })
      .then((payload) => { if (active) setResult(payload) })
      .catch((requestError) => { if (active) setError(requestError.message || 'Could not calculate the billed scenario.') })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [claimId, family, memberId, preloadedResult])

  if (!memberId || !family) return null
  if (loading) return <div className="same-patient-billed-state"><RefreshCw className="spin" size={18} /> Calculating the same-patient billed scenario…</div>
  if (error) return <div className="same-patient-billed-state error"><ShieldAlert size={18} /> {error}</div>
  if (!result?.available) return <section className="same-patient-billed-card unavailable" aria-label="Claim-anchored billed comparison unavailable">
    <header>
      <span>{claimAnchored ? 'Claim-anchored billed comparison' : 'Spreadsheet intervention scenario'} · {family}</span>
      <h2>No supported earlier-intervention calculation for this claim</h2>
      <p>Member <strong>{memberId}</strong>{claimId ? <> · anchored claim <strong>{claimId}</strong></> : null}</p>
    </header>
    <p>{result?.reason || 'The required claim evidence was not found.'}</p>
    <div className="missing-template-evidence">
      <strong>The calculation requires all three items:</strong>
      <ol><li>An earlier visit for the same member and diagnosis family.</li><li>A later related visit anchored to the selected claim.</li><li>A separately identifiable billed intervention explicitly supported by the later visit.</li></ol>
    </div>
    <p className="same-patient-billed-warning">No amount is displayed because missing evidence must not be replaced with another member’s values.</p>
  </section>

  if (result.calculation_type === 'reference_intervention_counterfactual') {
    return <ReferenceInterventionCounterfactualView result={result} family={family} />
  }

  const calculation = result.calculation
  const selectionAudit = result.selection_audit
  const fmt = (value) => new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(value || 0)
  const claimNames = (episode) => (episode?.claims || []).map((line) => line.claim_id).filter(Boolean).join(', ') || 'no claim ID'
  const interventionLines = result.intervention_lines || result.culture_add_on_lines || []
  const interveningEpisodes = result.intervening_episodes || []
  const journeyMode = interveningEpisodes.length > 0
  const interveningNames = interveningEpisodes.flatMap((episode) => episode?.claims || []).map((line) => line.claim_id).filter(Boolean).join(', ')
  const billedSum = (episode) => (episode?.claims || []).map((line) => `${fmt(line.billed_amount)} (${line.claim_id})`).join(' + ')
  const interventionDetail = interventionLines.map((line) => `${line.claim_id || 'claim'} · ${line.procedure_description || line.cpt || 'intervention line'} · billed ${fmt(line.billed_amount)}`).join('; ') || 'No intervention lines identified.'
  const episodeLines = (episode) => (episode?.claims || []).map((line) => (
    <li key={line.claim_id}><strong>{line.claim_id}</strong> · {line.procedure_description || 'Billed service'} · ICD-10 {line.icd10 || 'not recorded'} · CPT {line.cpt || 'not recorded'} · units {line.units ?? 'not recorded'} · billed {fmt(line.billed_amount)}</li>
  ))

  return (
    <section className="same-patient-billed-card" aria-labelledby="same-patient-billed-heading">
      <header>
        <span className="same-patient-billed-kicker">{claimAnchored ? 'Claim-anchored billed comparison' : 'Spreadsheet intervention scenario'} · {family}</span>
        <h2 id="same-patient-billed-heading">Estimated billed impact of moving the additional service earlier</h2>
        <p>{journeyMode ? `This compares the recorded three-visit journey with a scenario that applies the preventive intervention at the first symptomatic visit. The worsening visit followed ${result.days_to_first_intervening_episode} days later` : 'This compares one earlier visit with the selected later visit'} for <strong>{result.member_id}</strong>{result.anchor_claim_id ? <>. The preventive visit is anchored to claim <strong>{result.anchor_claim_id}</strong></> : null}. All amounts come from this member's recorded billed claims.</p>
      </header>
      <div className="same-patient-billed-grid">
        <div><span>Earlier visit actually billed</span><strong>{fmt(calculation.earlier_episode_actual_billed)}</strong><small>Sum of every billed line in {claimNames(result.earlier_episode)}.</small></div>
        <div><span>Total charges for the later visit</span><strong>{fmt(calculation.later_episode_actual_billed)}</strong><small>{billedSum(result.later_episode)} = {fmt(calculation.later_episode_actual_billed)}. This adds the bills grouped into the same later visit.</small></div>
        <div><span>Cost of the service proposed earlier</span><strong>{fmt(calculation.culture_and_specimen_add_on_billed)}</strong><small>{interventionDetail}. This amount is copied from the selected service bill. It is already included in the later-visit total; the scenario assumes this service happens earlier instead.</small></div>
        {journeyMode ? <div><span>Worsening visit actually billed</span><strong>{fmt(calculation.intervening_episode_actual_billed)}</strong><small>{interveningNames} occurred between the first visit and preventive follow-up.</small></div> : null}
        <div><span>{journeyMode ? 'What was billed across all three visits' : 'What was billed across both visits'}</span><strong>{fmt(calculation.actual_two_episode_billed)}</strong><small>{journeyMode ? `${fmt(calculation.earlier_episode_actual_billed)} first + ${fmt(calculation.intervening_episode_actual_billed)} worsening + ${fmt(calculation.later_episode_actual_billed)} preventive` : `${fmt(calculation.earlier_episode_actual_billed)} earlier + ${fmt(calculation.later_episode_actual_billed)} later`}.</small></div>
        <div><span>Estimated earlier visit with those lines</span><strong>{fmt(calculation.proposed_earlier_episode_with_add_on_billed)}</strong><small>{fmt(calculation.earlier_episode_actual_billed)} earlier + {fmt(calculation.culture_and_specimen_add_on_billed)} intervention lines.</small></div>
        <div className="difference"><span>Predicted billed amount that may be avoided</span><strong>{fmt(calculation.potential_billed_difference)}</strong><small>{fmt(calculation.actual_two_episode_billed)} actual total − {fmt(calculation.proposed_earlier_episode_with_add_on_billed)} estimated earlier scenario.</small></div>
      </div>
      <div className="same-patient-billed-equation" role="note">
        <strong>Predicted billed difference</strong>
        <span>{fmt(calculation.actual_two_episode_billed)} actual total − {fmt(calculation.proposed_earlier_episode_with_add_on_billed)} estimated earlier scenario = <b>{fmt(calculation.potential_billed_difference)}</b></span>
      </div>
      <div className="calculation-explanation">
        <h3>Where every number comes from</h3>
        <ol>
          <li><strong>Earlier source:</strong> {claimNames(result.earlier_episode)} contributes {fmt(calculation.earlier_episode_actual_billed)} from all of its billed lines.</li>
          <li><strong>What makes up the later visit:</strong> {billedSum(result.later_episode)} = {fmt(calculation.later_episode_actual_billed)}.<ul>{episodeLines(result.later_episode)}</ul></li>
          {journeyMode ? <li><strong>Worsening visit:</strong> {interveningNames} contributes {fmt(calculation.intervening_episode_actual_billed)} and is the visit the earlier-intervention scenario may avoid.</li> : null}
          <li><strong>Actual total:</strong> {journeyMode ? <>{fmt(calculation.earlier_episode_actual_billed)} + {fmt(calculation.intervening_episode_actual_billed)} + {fmt(calculation.later_episode_actual_billed)}</> : <>{fmt(calculation.earlier_episode_actual_billed)} + {fmt(calculation.later_episode_actual_billed)}</>} = <strong>{fmt(calculation.actual_two_episode_billed)}</strong>.</li>
          <li><strong>Counterfactual:</strong> only these later lines are moved earlier: {interventionDetail}. Their billed total is {fmt(calculation.culture_and_specimen_add_on_billed)}. Therefore {fmt(calculation.earlier_episode_actual_billed)} earlier-visit billed amount + {fmt(calculation.culture_and_specimen_add_on_billed)} intervention billed amount = <strong>{fmt(calculation.proposed_earlier_episode_with_add_on_billed)}</strong>.</li>
          <li><strong>Predicted billed difference:</strong> {fmt(calculation.actual_two_episode_billed)} − {fmt(calculation.proposed_earlier_episode_with_add_on_billed)} = <strong>{fmt(calculation.potential_billed_difference)}</strong>. This is a review estimate, not confirmed savings.</li>
        </ol>
        <p><strong>Why propose the service earlier?</strong> The calculation selects a service identified as an intervention in the later visit that was absent from the earlier visit. It asks what the charges would be if that service were delivered earlier and the rest of the later visit were avoided. The billing records alone do not prove that earlier treatment would prevent that visit.</p>
        <p><strong>Why the difference is {fmt(calculation.potential_billed_difference)}:</strong> {journeyMode ? `The first-visit cost and preventive intervention appear in both totals. The difference is the ${fmt(calculation.intervening_episode_actual_billed)} worsening visit that the scenario may avoid.` : `The earlier-visit cost appears in both totals and cancels out. The difference is therefore ${fmt(calculation.later_episode_actual_billed)} later-visit charges − ${fmt(calculation.culture_and_specimen_add_on_billed)} service moved earlier. The proposed service is still paid for once.`}</p>
      </div>
      {selectionAudit ? <section className="intervention-selection-reason" aria-labelledby="intervention-selection-heading">
        <span>Selection reasoning</span>
        <h3 id="intervention-selection-heading">Why this claim and earlier visit were chosen</h3>
        <div className="intervention-reason-grid">
          <div><strong>Why this claim?</strong><p>{selectionAudit.anchor_reason}</p></div>
          <div><strong>Why this diagnosis?</strong><p>{selectionAudit.diagnosis_reason}</p></div>
          <div><strong>Why this earlier visit?</strong><p>{selectionAudit.earlier_visit_reason}</p></div>
          <div><strong>Why this intervention?</strong><p>{selectionAudit.intervention_reason}</p></div>
        </div>
        <p className="intervention-selection-rule"><strong>Selection rule:</strong> {selectionAudit.rule}</p>
        <details>
          <summary>See why the other earlier visits were not selected</summary>
          <div className="comparison-evidence-table-wrap">
            <table className="comparison-evidence-table">
              <thead><tr><th>Rank</th><th>Claim / date</th><th>Diagnosis</th><th>Procedure / units</th><th>Billed</th><th>Decision</th></tr></thead>
              <tbody>{(selectionAudit.candidate_visits || []).map((candidate) => <tr key={candidate.claim_id} className={candidate.selected ? 'selected-candidate' : ''}>
                <td>#{candidate.rank}</td><td><strong>{candidate.claim_id}</strong><small>{candidate.service_date}</small></td><td>{candidate.diagnosis_code}</td><td>{candidate.procedure_code} · {candidate.units} unit(s)</td><td>{fmt(candidate.billed_amount)}</td><td>{candidate.decision}</td>
              </tr>)}</tbody>
            </table>
          </div>
        </details>
      </section> : null}
      <details className="scenario-technical-details">
        <summary>
          <span className="technical-details-toggle-label">
            <FileText size={16} />
            <span>Show hard words, codes, and detailed math</span>
          </span>
          <span className="technical-details-toggle-help">Full glossary + step-by-step arithmetic</span>
          <ChevronRight size={16} />
        </summary>
        <div className="scenario-technical-content">
          <p className="technical-details-context">Member {result.member_id} · diagnosis family {family}{result.anchor_claim_id ? ` · selected claim ${result.anchor_claim_id}` : ''}. These are the same records and billed amounts used in the comparison above.</p>
          {result.synthetic_demo ? <p className="technical-details-data-note">Data note: this example includes generated demo claims.</p> : null}
          <div className="technical-details-section">
            <h4>Plain-language glossary</h4>
            <dl className="technical-glossary-grid">
              <div><dt>Episode</dt><dd>The claims grouped together as one visit in this calculation.</dd></div>
              <div><dt>ICD-10 / diagnosis family</dt><dd>The recorded diagnosis code / its broader condition group. Sharing a family does not guarantee identical conditions.</dd></div>
              <div><dt>CPT / units</dt><dd>The billed service code / recorded quantity of that service.</dd></div>
              <div><dt>Intervention</dt><dd>The later service selected for the hypothetical earlier visit.</dd></div>
              <div><dt>Billed amount</dt><dd>The recorded charge for the service, before insurance payments and adjustments.</dd></div>
            </dl>
          </div>
          <div className="technical-details-section">
            <h4>Step-by-step arithmetic</h4>
            <ol className="technical-math-list">
              <li>Earlier visit: {billedSum(result.earlier_episode)} = {fmt(calculation.earlier_episode_actual_billed)}.</li>
              <li>Later visit: {billedSum(result.later_episode)} = {fmt(calculation.later_episode_actual_billed)}.</li>
              <li>Selected intervention: {interventionLines.map((line) => `${fmt(line.billed_amount)} (${line.claim_id})`).join(' + ')} = {fmt(calculation.culture_and_specimen_add_on_billed)}.</li>
              <li>Both visits: {fmt(calculation.earlier_episode_actual_billed)} + {fmt(calculation.later_episode_actual_billed)} = {fmt(calculation.actual_two_episode_billed)}.</li>
              <li>Proposed earlier visit: {fmt(calculation.earlier_episode_actual_billed)} + {fmt(calculation.culture_and_specimen_add_on_billed)} = {fmt(calculation.proposed_earlier_episode_with_add_on_billed)}.</li>
              <li>Difference: {fmt(calculation.actual_two_episode_billed)} − {fmt(calculation.proposed_earlier_episode_with_add_on_billed)} = {fmt(calculation.potential_billed_difference)}.</li>
            </ol>
            <p className="technical-details-caution">This assumes the selected service could happen earlier and the remaining later charges could be avoided. The arithmetic does not establish that the later visit was preventable.</p>
          </div>
          <div className="episode-evidence-grid">
            <div><h4>Earlier episode · {result.earlier_episode?.start_date || 'date unavailable'}</h4><p>Actual billed total: <strong>{fmt(calculation.earlier_episode_actual_billed)}</strong></p><ul>{episodeLines(result.earlier_episode)}</ul></div>
            <div><h4>Later episode · {result.later_episode?.start_date || 'date unavailable'}</h4><p>Actual billed total: <strong>{fmt(calculation.later_episode_actual_billed)}</strong></p><ul>{episodeLines(result.later_episode)}</ul></div>
          </div>
        </div>
        <div className="technical-details-section">
          <h4>Separately identifiable intervention lines used as the proposed add-on</h4>
          <p>These are the only lines added to the earlier visit. Each line is shown with its claim ID, procedure, and billed amount so the add-on total can be checked directly.</p>
          <ul>
            {interventionLines.map((line) => (
              <li key={`${line.claim_id}-${line.cpt}`}>{line.claim_id} · {line.procedure_description || line.cpt} ({line.cpt}) — {fmt(line.billed_amount)}</li>
            ))}
          </ul>
        </div>
      </details>
      <p className="same-patient-billed-warning">{result.disclaimer}</p>
    </section>
  )
}

export function PatientWorkbookCalculation({ diagnosisCode }) {
  const family = String(diagnosisCode || '').slice(0, 3).toUpperCase()
  const [result, setResult] = useState(null)
  useEffect(() => {
    if (!['N30', 'N39'].includes(family)) return undefined
    let active = true
    fetch('/api/predictions/uti-case-workbook')
      .then((response) => response.json())
      .then((payload) => { if (active) setResult(payload) })
      .catch(() => { if (active) setResult({ available: false }) })
    return () => { active = false }
  }, [family])
  if (!result?.available || !['N30', 'N39'].includes(family)) return null
  const calculation = result.calculation
  const fmt = (value) => new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(value || 0)
  return <section className="same-patient-billed-card" aria-labelledby="patient-workbook-calculation-heading">
    <header><span>Reusable spreadsheet calculation template</span><h2 id="patient-workbook-calculation-heading">UTI culture billed-cost logic</h2><p>Template source: {result.source.workbook_name}. The calculation below is shown as the reference pattern; each member's result uses that member's own claims.</p></header>
    <div className="same-patient-billed-grid">
      <div><span>Episode 1 actual</span><strong>{fmt(calculation.earlier_episode_actual_billed)}</strong></div>
      <div><span>Episode 2 actual</span><strong>{fmt(calculation.later_episode_actual_billed)}</strong></div>
      <div><span>Culture/specimen add-on</span><strong>{fmt(calculation.culture_and_specimen_add_on_billed)}</strong></div>
      <div><span>Proposed Episode 1 with add-on</span><strong>{fmt(calculation.proposed_earlier_episode_with_add_on_billed)}</strong></div>
      <div className="difference"><span>Template example billed difference</span><strong>{fmt(calculation.potential_billed_difference)}</strong></div>
    </div>
    <p className="same-patient-billed-equation">{calculation.formula}</p>
    <p className="same-patient-billed-warning">{result.method}</p>
    {result.quality_of_care ? <div className="quality-of-care-note">
      <strong>Why this matters for quality of care</strong>
      <p>{result.quality_of_care.patient_benefit}</p>
      <p><strong>Recommended review:</strong> {result.quality_of_care.recommended_intervention}</p>
      <p><strong>How it connects to cost:</strong> {result.quality_of_care.cost_connection}</p>
    </div> : null}
  </section>
}

// eslint-disable-next-line no-unused-vars
function MemberWorkbookCalculations({ diagnosisCode, members }) {
  const family = String(diagnosisCode || '').slice(0, 3).toUpperCase()
  if (!members?.length) return null
  return <section className="member-template-results" aria-labelledby="member-template-results-heading">
    <header><span>Member-specific application of the spreadsheet template</span><h2 id="member-template-results-heading">Each member is calculated from their own billed claims</h2><p>The same workbook sequence is applied separately: earlier episode without the intervention, later episode with the recorded culture/specimen lines, then the billed counterfactual. Members without both required episodes are shown as unavailable rather than receiving another member's values.</p></header>
    <div className="member-template-result-list">
      {members.map((member) => <div className="member-template-result" key={member.member_id}>
        <h3>{member.patient_name} ({member.member_id})</h3>
        <SamePatientBilledSavings memberId={member.member_id} diagnosisCode={family} />
      </div>)}
    </div>
  </section>
}

// eslint-disable-next-line no-unused-vars
function QualityOfCareReason({ diagnosisCode, memberCount }) {
  const family = String(diagnosisCode || '').slice(0, 3).toUpperCase()
  if (!family || !memberCount) return null
  const isUti = ['N30', 'N39'].includes(family)
  return <section className="quality-of-care-note comparator-quality-note" aria-labelledby="quality-of-care-heading">
    <span className="quality-of-care-kicker">Quality-of-care review · {family}</span>
    <h2 id="quality-of-care-heading">Why this comparison may improve care</h2>
    {isUti ? <>
      <p><strong>Care goal:</strong> identify and manage the urinary infection earlier, with diagnostic evidence that can support targeted treatment and follow-up.</p>
      <p><strong>For all {memberCount} corresponding members:</strong> review each member's own earlier UTI episode for whether a urine culture and specimen collection were appropriate, then compare that member's later billed recurrence or escalation episode.</p>
      <p><strong>Cost connection:</strong> the attached workbook is a billed-cost counterfactual. It shows how earlier diagnostic work could be associated with lower total billed cost if a later recurrence did not occur; it does not prove prevention or confirmed savings.</p>
    </> : <>
      <p><strong>Care goal:</strong> compare each member's own episodes to identify earlier diagnostic, treatment, or follow-up opportunities that may improve care and avoid escalation.</p>
      <p><strong>For all {memberCount} corresponding members:</strong> use only that member's claims and billed lines. A cost difference is a review signal, not proof that care caused the difference or that savings are confirmed.</p>
    </>}
  </section>
}

export function CrossPatientComparatorContent({
  onOpenClaim,
  onSelectMember,
  modalMode = false,
  onClose,
  initialMemberId = '',
  initialDiagnosisCode = '',
}) {
  const [pairsData, setPairsData] = useState(null)
  const [loadingPairs, setLoadingPairs] = useState(true)
  const [pairsError, setPairsError] = useState('')

  const [selectedOrgan, setSelectedOrgan] = useState('')
  const [selectedFamily, setSelectedFamily] = useState('')
  const [member1, setMember1] = useState('')
  const [member2, setMember2] = useState('')

  const [comparisonResult, setComparisonResult] = useState(null)
  const [loadingComparison, setLoadingComparison] = useState(false)
  const [comparisonError, setComparisonError] = useState('')
  const [activeTab, setActiveTab] = useState('divergence') // 'divergence' | 'claims'
  const comparisonRequest = useRef(0)
  useEffect(() => () => { comparisonRequest.current += 1 }, [])

  // Pre-configured showcases for quick, powerful demonstrations
  const SHOWCASE_PRESETS = [
    {
      label: 'Kidneys · UTI Billed Demo (N39)',
      emoji: '💧',
      organ: 'Kidneys & Urinary Tract',
      family: 'N39',
      m1: 'MBRDEMO01',
      m2: 'MBR00017',
      desc: 'Synthetic presentation member vs Richard Jackson · billed-amount comparison',
    },
    {
      label: 'Bones · RA Biologic (M06)',
      emoji: '🦴',
      organ: 'Bones & Joints',
      family: 'M06',
      m1: 'MBR00013',
      m2: 'MBR00018',
      desc: 'John Williams vs Michael Anderson · Treatment pathway divergence',
    },
    {
      label: 'Brain · Epilepsy (G40)',
      emoji: '🧠',
      organ: 'Brain & Nerves',
      family: 'G40',
      m1: 'MBR00011',
      m2: 'MBR00016',
      desc: 'John Moore vs Karen Miller · Diagnostic & maintenance variation',
    },
    {
      label: 'Neoplasms · Colon Care (C18)',
      emoji: '🎗️',
      organ: 'Cancer / Neoplasms',
      family: 'C18',
      m1: 'MBR00016',
      m2: 'MBR00017',
      desc: 'Karen Miller vs Richard Jackson · Surveillance & procedure spread',
    },
  ]

  // Load comparable pairs grouped by organ system
  useEffect(() => {
    let active = true
    setLoadingPairs(true)
    fetch('/api/claims/comparable-pairs')
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}: Failed to fetch pairs`)
        return res.json()
      })
      .then((data) => {
        if (!active) return
        setPairsData(data)
        setPairsError('')

        // Prefer the patient and diagnosis that opened this comparison. This keeps
        // the embedded view scoped to the member's selected condition while still
        // using the same eligible cross-patient comparison cohort.
        const groups = data.organ_groups || []
        if (groups.length > 0) {
          const diagnosisPrefix = String(initialDiagnosisCode || '').slice(0, 3).toUpperCase()
          const scopedGroup = groups.find((group) => group.disease_families.some((disease) => (
            disease.diagnosis_family === diagnosisPrefix
            && disease.members.some((member) => member.member_id === initialMemberId)
          )))
          if (initialMemberId && diagnosisPrefix && !scopedGroup) {
            setPairsError(`No eligible second patient was found for ${initialDiagnosisCode} for this member.`)
            setLoadingPairs(false)
            return
          }
          const defaultGroup = scopedGroup || groups.find((g) => g.organ_system.includes('Kidneys')) || groups[0]
          setSelectedOrgan(defaultGroup.organ_system)
          const firstDisease = defaultGroup.disease_families.find((disease) => (
            (!diagnosisPrefix || disease.diagnosis_family === diagnosisPrefix)
            && (!initialMemberId || disease.members.some((member) => member.member_id === initialMemberId))
          )) || defaultGroup.disease_families[0]
          if (firstDisease) {
            setSelectedFamily(firstDisease.diagnosis_family)
            if (firstDisease.members.length >= 2) {
              // Sort members by billed amount descending
              const sorted = [...firstDisease.members].sort((a, b) => b.total_billed - a.total_billed)
              const scopedMember = sorted.find((member) => member.member_id === initialMemberId)
              const firstMember = scopedMember || sorted[0]
              const secondMember = [...sorted].reverse().find((member) => member.member_id !== firstMember.member_id)
              setMember1(firstMember.member_id)
              setMember2(secondMember.member_id)
              // Execute initial comparison
              runComparisonQuery(firstMember.member_id, secondMember.member_id, firstDisease.diagnosis_family)
            }
          }
        }
      })
      .catch((err) => {
        if (!active) return
        setPairsError(err.message || 'Could not load comparable patient pairs from backend.')
      })
      .finally(() => {
        if (active) setLoadingPairs(false)
      })
    return () => {
      active = false
    }
  }, [initialDiagnosisCode, initialMemberId])

  const currentOrganGroup = useMemo(() => {
    if (!pairsData?.organ_groups) return null
    return pairsData.organ_groups.find((g) => g.organ_system === selectedOrgan) || pairsData.organ_groups[0]
  }, [pairsData, selectedOrgan])

  const availableDiseases = useMemo(() => {
    return currentOrganGroup?.disease_families || []
  }, [currentOrganGroup])

  const currentDisease = useMemo(() => {
    return availableDiseases.find((d) => d.diagnosis_family === selectedFamily) || availableDiseases[0]
  }, [availableDiseases, selectedFamily])

  const availableMembers = useMemo(() => {
    return currentDisease?.members || []
  }, [currentDisease])

  const handleOrganChange = (organName) => {
    setSelectedOrgan(organName)
    const grp = pairsData?.organ_groups?.find((g) => g.organ_system === organName)
    if (grp?.disease_families?.length > 0) {
      const d = grp.disease_families[0]
      setSelectedFamily(d.diagnosis_family)
      if (d.members.length >= 2) {
        const sorted = [...d.members].sort((a, b) => b.total_billed - a.total_billed)
        setMember1(sorted[0].member_id)
        setMember2(sorted[sorted.length - 1].member_id)
        runComparisonQuery(sorted[0].member_id, sorted[sorted.length - 1].member_id, d.diagnosis_family)
      }
    }
  }

  const handleDiseaseChange = (familyCode) => {
    setSelectedFamily(familyCode)
    const d = availableDiseases.find((item) => item.diagnosis_family === familyCode)
    if (d && d.members.length >= 2) {
      const sorted = [...d.members].sort((a, b) => b.total_billed - a.total_billed)
      setMember1(sorted[0].member_id)
      setMember2(sorted[sorted.length - 1].member_id)
      runComparisonQuery(sorted[0].member_id, sorted[sorted.length - 1].member_id, familyCode)
    }
  }

  const handleApplyPreset = (preset) => {
    setSelectedOrgan(preset.organ)
    setSelectedFamily(preset.family)
    setMember1(preset.m1)
    setMember2(preset.m2)
    runComparisonQuery(preset.m1, preset.m2, preset.family)
  }

  const handleSwapMembers = () => {
    const temp = member1
    setMember1(member2)
    setMember2(temp)
    if (member2 && temp && selectedFamily) {
      runComparisonQuery(member2, temp, selectedFamily)
    }
  }

  const runComparisonQuery = (m1, m2, fam) => {
    if (!m1 || !m2 || !fam || m1 === m2) return
    const requestId = ++comparisonRequest.current
    setComparisonResult(null)
    setLoadingComparison(true)
    setComparisonError('')
    fetch(`/api/claims/compare-patients?member_id_1=${encodeURIComponent(m1)}&member_id_2=${encodeURIComponent(m2)}&diagnosis_family=${encodeURIComponent(fam)}`)
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}: Comparison request failed`)
        return res.json()
      })
      .then((data) => {
        if (requestId !== comparisonRequest.current) return
        setComparisonResult(data)
      })
      .catch((err) => {
        if (requestId !== comparisonRequest.current) return
        setComparisonError(err.message || 'Failed to compare patients.')
        setComparisonResult(null)
      })
      .finally(() => {
        if (requestId !== comparisonRequest.current) return
        setLoadingComparison(false)
      })
  }

  const selectMember = (side, id) => {
    comparisonRequest.current += 1
    setComparisonResult(null)
    setComparisonError('')
    setLoadingComparison(false)
    const first = side === 'a' ? id : member1
    const second = side === 'b' ? id : member2
    setMember1(first)
    setMember2(second)
    if (first && second && first !== second) runComparisonQuery(first, second, selectedFamily)
  }

  const handleManualCompare = (e) => {
    e?.preventDefault?.()
    if (member1 === member2) {
      setComparisonError('Please select two different patients to compare.')
      return
    }
    runComparisonQuery(member1, member2, selectedFamily)
  }

  const fmt = (val) =>
    new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 2 }).format(val || 0)

  return (
    <div className={`cross-patient-comparator-root ${modalMode ? 'in-modal' : ''}`}>
      {/* Header */}
      <div className="comparator-header">
        <div className="comparator-title-area">
          <div className="comparator-badge">
            <Scale size={14} />
            <span>Cross-Patient Clinical & Spend Comparator</span>
          </div>
          <h2>Compare Patients With Same Organ & Disease</h2>
          <p>
            Compare recorded billed amounts and services for two patients with the same diagnosis family. Differences require review before they can be attributed to savings.
          </p>
        </div>
        {modalMode && onClose && (
          <button className="comparator-close-btn" type="button" onClick={onClose} aria-label="Close modal">
            <X size={20} />
          </button>
        )}
      </div>

      {/* Showcase Presets */}
      <div className="comparator-presets-bar">
        <span className="presets-label">
          <Sparkles size={14} /> Quick Demonstrations:
        </span>
        <div className="presets-chips">
          {SHOWCASE_PRESETS.map((preset) => (
            <button
              key={preset.family}
              type="button"
              className={`preset-chip ${selectedFamily === preset.family && member1 === preset.m1 ? 'active' : ''}`}
              onClick={() => handleApplyPreset(preset)}
              title={preset.desc}
            >
              <span className="preset-emoji">{preset.emoji}</span>
              <span className="preset-name">{preset.label}</span>
            </button>
          ))}
        </div>
      </div>

      {/* Main Filter / Selection Card */}
      <div className="comparator-filter-card">
        {loadingPairs ? (
          <div className="comparator-loading-inline">
            <RefreshCw className="spin" size={18} />
            <span>Scanning patient episodes across all organ systems...</span>
          </div>
        ) : pairsError ? (
          <div className="comparator-error-banner">
            <ShieldAlert size={18} />
            <span>{pairsError}</span>
          </div>
        ) : (
          <>
            {/* Step 1: Organ System Pills */}
            <div className="filter-step">
              <label className="step-label">
                <strong>1. Select Organ System:</strong>
                <span className="step-hint">Categorised by anatomical chapter</span>
              </label>
              <div className="organ-pills-row">
                {pairsData?.organ_groups?.map((group) => {
                  const isSelected = selectedOrgan === group.organ_system
                  return (
                    <button
                      key={group.organ_system}
                      type="button"
                      className={`organ-pill ${isSelected ? 'active' : ''}`}
                      onClick={() => handleOrganChange(group.organ_system)}
                    >
                      <span className="organ-emoji">{group.organ_emoji}</span>
                      <span className="organ-name">{group.organ_system}</span>
                      <span className="organ-count">({group.total_diseases})</span>
                    </button>
                  )
                })}
              </div>
            </div>

            {/* Step 2 & 3: Disease & Patient Pair */}
            <form className="comparator-selection-grid" onSubmit={handleManualCompare}>
              <div className="selection-item">
                <label>
                  <span>2. Disease Family</span>
                  <select
                    value={selectedFamily}
                    onChange={(e) => handleDiseaseChange(e.target.value)}
                    disabled={!availableDiseases.length}
                  >
                    {availableDiseases.map((d) => (
                      <option key={d.diagnosis_family} value={d.diagnosis_family}>
                        {d.diagnosis_family} – {d.diagnosis_description || 'Unspecified'} ({d.member_count} patients)
                      </option>
                    ))}
                  </select>
                </label>
              </div>

              <div className="selection-item">
                <label>
                  <span>3. Patient A</span>
                  <select
                    value={member1}
                    onChange={(e) => selectMember('a', e.target.value)}
                    disabled={!availableMembers.length}
                  >
                    <option value="">Select Patient A</option>
                    {availableMembers.map((m) => (
                      <option key={`m1-${m.member_id}`} value={m.member_id}>
                        {m.patient_name} ({m.member_id}) · billed {fmt(m.total_billed)} · {m.claim_count} claims{m.historical_reference_claim_count ? ` · ${m.historical_reference_claim_count} reference` : ''}
                      </option>
                    ))}
                  </select>
                </label>
              </div>

              <div className="swap-btn-container">
                <button
                  type="button"
                  className="comparator-swap-button"
                  onClick={handleSwapMembers}
                  title="Swap Patient A and Patient B"
                >
                  <ArrowLeftRight size={18} />
                </button>
              </div>

              <div className="selection-item">
                <label>
                  <span>4. Patient B</span>
                  <select
                    value={member2}
                    onChange={(e) => selectMember('b', e.target.value)}
                    disabled={!availableMembers.length}
                  >
                    <option value="">Select Patient B</option>
                    {availableMembers.map((m) => (
                      <option key={`m2-${m.member_id}`} value={m.member_id}>
                        {m.patient_name} ({m.member_id}) · billed {fmt(m.total_billed)} · {m.claim_count} claims{m.historical_reference_claim_count ? ` · ${m.historical_reference_claim_count} reference` : ''}
                      </option>
                    ))}
                  </select>
                </label>
              </div>

              <div className="selection-actions">
                <button
                  type="submit"
                  className="comparator-compare-btn"
                  disabled={loadingComparison || !member1 || !member2 || member1 === member2}
                >
                  {loadingComparison ? (
                    <>
                      <RefreshCw className="spin" size={16} />
                      <span>Comparing...</span>
                    </>
                  ) : (
                    <>
                      <Sparkles size={16} />
                      <span>Compare Claims</span>
                    </>
                  )}
                </button>
              </div>
            </form>
            <details className="corresponding-members">
              <summary>All {availableMembers.length} corresponding members · {selectedFamily}</summary>
              <p>Both patient dropdowns contain every member listed here. Billed totals below cover all recorded episodes for this diagnosis family; the comparison uses each patient's highest-billed episode.</p>
              <div className="comparison-evidence-table-wrap">
                <table className="comparison-evidence-table">
                  <thead><tr><th>Member</th><th>Claims</th><th>Episodes</th><th>Total billed</th><th>Select patient</th></tr></thead>
                  <tbody>{availableMembers.map((member) => (
                    <tr key={member.member_id}>
                      <td>{member.patient_name} ({member.member_id})</td>
                      <td>{member.claim_count}{member.historical_reference_claim_count ? ` (${member.historical_reference_claim_count} reference)` : ''}</td><td>{member.episode_count}</td><td>{fmt(member.total_billed)}</td>
                      <td>
                        <button type="button" onClick={() => selectMember('a', member.member_id)}>Select A</button>{' '}
                        <button type="button" onClick={() => selectMember('b', member.member_id)}>Select B</button>
                      </td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
            </details>
            {member1 && member1 === member2 ? <p role="status">Select a different member for Patient B to compare two patients.</p> : null}
          </>
        )}
      </div>

      {/* Error message */}
      {comparisonError && (
        <div className="comparator-error-banner">
          <ShieldAlert size={18} />
          <span>{comparisonError}</span>
        </div>
      )}

      {/* Results Section */}
      {loadingComparison && !comparisonResult && (
        <div className="comparator-loading-state">
          <RefreshCw className="spin" size={28} />
          <p>Evaluating claim histories, matching service lines, and computing savings difference...</p>
        </div>
      )}

      {comparisonResult && (
        <div className="comparator-results-section">
          {comparisonResult.calculation_type === 'reference_intervention_counterfactual' ? (
            <ReferenceInterventionCounterfactualView result={comparisonResult} family={selectedFamily} />
          ) : (
          <>
          {(comparisonResult.higher_cost_patient.episode.synthetic_demo || comparisonResult.lower_cost_patient.episode.synthetic_demo) ? <div className="synthetic-demo-notice" role="note"><ShieldAlert size={18} /><span><strong>Synthetic presentation data is included.</strong> Use this comparison to demonstrate the billed-amount workflow, not as evidence about a real patient.</span></div> : null}
          {/* Executive Savings Banner */}
          <div className="comparator-savings-banner">
            <div className="savings-left">
              <span className="savings-kicker">Cross-Patient Billed-Amount Variance</span>
              <div className="savings-amount-row">
                <span className="savings-big-number">
                  {fmt(comparisonResult.savings_summary.total_savings)}
                </span>
                <span className="savings-pct-pill">
                  <TrendingDown size={18} />
                  {comparisonResult.savings_summary.savings_percentage}% Lower Billed
                </span>
              </div>
              <p className="savings-subtext">
                Recorded billed totals for {comparisonResult.higher_cost_patient.patient_name} and {comparisonResult.lower_cost_patient.patient_name} in diagnosis family {comparisonResult.diagnosis_family} are {fmt(comparisonResult.savings_summary.higher_total)} and {fmt(comparisonResult.savings_summary.lower_total)} respectively. Each patient's highest-billed episode is shown; this does not establish avoidable spending.
              </p>
            </div>
            <div className="savings-right">
              <div className="savings-meta-box">
                <span className="meta-label">Organ System</span>
                <strong>{comparisonResult.organ_emoji} {comparisonResult.organ_system}</strong>
              </div>
              <div className="savings-meta-box">
                <span className="meta-label">Disease Family</span>
                <strong>{comparisonResult.diagnosis_family} · {comparisonResult.diagnosis_description}</strong>
              </div>
              <div className="savings-meta-box">
                <span className="meta-label">Claim Volume Difference</span>
                <strong>
                  {Math.abs(comparisonResult.savings_summary.claim_count_difference)} claim{Math.abs(comparisonResult.savings_summary.claim_count_difference) === 1 ? '' : 's'} difference ({comparisonResult.higher_cost_patient.episode.claim_count} vs {comparisonResult.lower_cost_patient.episode.claim_count})
                </strong>
              </div>
            </div>
          </div>

          {/* Side-by-Side Patient Cards */}
          <div className="patient-comparison-grid">
            {/* Higher Cost Patient */}
            <div className="comparator-patient-card higher-cost-card">
              <div className="patient-card-top">
                <div className="patient-card-badge danger">
                  <span>Higher Spend Patient Episode</span>
                </div>
                <h3>{comparisonResult.higher_cost_patient.patient_name}</h3>
                <span className="member-id-tag">
                  Member ID: {comparisonResult.higher_cost_patient.member_id}{comparisonResult.higher_cost_patient.episode.synthetic_demo ? ' · Synthetic demo' : ''}
                </span>
              </div>
              <div className="patient-card-metrics">
                <div className="metric-cell highlight">
                  <span>Total Billed</span>
                  <strong>{fmt(comparisonResult.higher_cost_patient.episode.total_billed)}</strong>
                </div>
                <div className="metric-cell">
                  <span>Total Allowed</span>
                  <strong>{fmt(comparisonResult.higher_cost_patient.episode.total_allowed)}</strong>
                </div>
                <div className="metric-cell">
                  <span>Episode Claims</span>
                  <strong>{comparisonResult.higher_cost_patient.episode.claim_count} claims</strong>
                </div>
                <div className="metric-cell">
                  <span>Timeline</span>
                  <strong>
                    {comparisonResult.higher_cost_patient.episode.start_date} – {comparisonResult.higher_cost_patient.episode.end_date}
                  </strong>
                </div>
              </div>
              <div className="patient-card-services">
                <h4>Top Services Billed in Episode:</h4>
                <div className="service-tags">
                  {comparisonResult.higher_cost_patient.episode.cpt_breakdown?.slice(0, 4).map((cpt) => (
                    <div key={cpt.cpt} className="cpt-chip">
                      <code>{cpt.cpt}</code>
                      <span className="cpt-desc">{cpt.description || 'Service'}</span>
                      <span className="cpt-amt">{fmt(cpt.total_billed)} ({cpt.count}x)</span>
                    </div>
                  ))}
                </div>
              </div>
              {onSelectMember && (
                <button
                  type="button"
                  className="view-patient-profile-btn"
                  onClick={() => onSelectMember(comparisonResult.higher_cost_patient.member_id)}
                >
                  <Users size={14} />
                  <span>View Patient 360 ({comparisonResult.higher_cost_patient.member_id})</span>
                </button>
              )}
            </div>

            {/* Lower Cost Patient */}
            <div className="comparator-patient-card lower-cost-card">
              <div className="patient-card-top">
                <div className="patient-card-badge success">
                  <CheckCircle2 size={13} />
                  <span>Value-Based Benchmark Episode</span>
                </div>
                <h3>{comparisonResult.lower_cost_patient.patient_name}</h3>
                <span className="member-id-tag">
                  Member ID: {comparisonResult.lower_cost_patient.member_id}{comparisonResult.lower_cost_patient.episode.synthetic_demo ? ' · Synthetic demo' : ''}
                </span>
              </div>
              <div className="patient-card-metrics">
                <div className="metric-cell highlight success">
                  <span>Total Billed</span>
                  <strong>{fmt(comparisonResult.lower_cost_patient.episode.total_billed)}</strong>
                </div>
                <div className="metric-cell">
                  <span>Total Allowed</span>
                  <strong>{fmt(comparisonResult.lower_cost_patient.episode.total_allowed)}</strong>
                </div>
                <div className="metric-cell">
                  <span>Episode Claims</span>
                  <strong>{comparisonResult.lower_cost_patient.episode.claim_count} claims</strong>
                </div>
                <div className="metric-cell">
                  <span>Timeline</span>
                  <strong>
                    {comparisonResult.lower_cost_patient.episode.start_date} – {comparisonResult.lower_cost_patient.episode.end_date}
                  </strong>
                </div>
              </div>
              <div className="patient-card-services">
                <h4>Top Services Billed in Episode:</h4>
                <div className="service-tags">
                  {comparisonResult.lower_cost_patient.episode.cpt_breakdown?.slice(0, 4).map((cpt) => (
                    <div key={cpt.cpt} className="cpt-chip success">
                      <code>{cpt.cpt}</code>
                      <span className="cpt-desc">{cpt.description || 'Service'}</span>
                      <span className="cpt-amt">{fmt(cpt.total_billed)} ({cpt.count}x)</span>
                    </div>
                  ))}
                </div>
              </div>
              {onSelectMember && (
                <button
                  type="button"
                  className="view-patient-profile-btn"
                  onClick={() => onSelectMember(comparisonResult.lower_cost_patient.member_id)}
                >
                  <Users size={14} />
                  <span>View Patient 360 ({comparisonResult.lower_cost_patient.member_id})</span>
                </button>
              )}
            </div>
          </div>

          {/* Navigation Tabs for Divergence vs Claims */}
          <div className="comparator-tabs-bar">
            <button
              type="button"
              className={`comparator-tab ${activeTab === 'divergence' ? 'active' : ''}`}
              onClick={() => setActiveTab('divergence')}
            >
              <span>Service-by-Service Variance Breakdown</span>
              <span className="tab-badge">{comparisonResult.service_divergence?.length || 0}</span>
            </button>
            <button
              type="button"
              className={`comparator-tab ${activeTab === 'claims' ? 'active' : ''}`}
              onClick={() => setActiveTab('claims')}
            >
              <span>Underlying Claims Audit Trail</span>
              <span className="tab-badge">
                {(comparisonResult.higher_cost_patient.episode.claims?.length || 0) +
                  (comparisonResult.lower_cost_patient.episode.claims?.length || 0)}
              </span>
            </button>
          </div>

          {/* Tab 1: Service Divergence Breakdown */}
          {activeTab === 'divergence' && (
            <div className="divergence-table-wrap">
              <div className="divergence-table-header">
                <h4>Cost Divergence by Procedure / CPT Code</h4>
                <p>
                  Identifies specific clinical services that caused the {fmt(comparisonResult.savings_summary.total_savings)} spend difference.
                </p>
              </div>
              <div className="table-responsive">
                <table className="comparator-data-table">
                  <thead>
                    <tr>
                      <th>CPT Code</th>
                      <th>Clinical Description</th>
                      <th>Variance Driver</th>
                      <th>{comparisonResult.higher_cost_patient.patient_name}</th>
                      <th>{comparisonResult.lower_cost_patient.patient_name}</th>
                      <th>Billed Amount Difference</th>
                    </tr>
                  </thead>
                  <tbody>
                    {comparisonResult.service_divergence?.map((row) => {
                      const isPositive = row.difference > 0
                      return (
                        <tr key={row.cpt}>
                          <td>
                            <span className="cpt-code-pill">{row.cpt}</span>
                          </td>
                          <td>
                            <strong>{row.description || 'Medical service'}</strong>
                          </td>
                          <td>
                            <span className={`divergence-badge ${row.category}`}>
                              {row.category === 'additional'
                                ? 'Additional Service'
                                : row.category === 'excess'
                                ? 'Excess Utilisation'
                                : 'Price Variation'}
                            </span>
                          </td>
                          <td>
                            <span className="patient-spend-val">
                              {fmt(row.higher_cost_patient.total_billed)}
                            </span>
                            <span className="count-sub">({row.higher_cost_patient.count}x)</span>
                          </td>
                          <td>
                            <span className="patient-spend-val">
                              {fmt(row.lower_cost_patient.total_billed)}
                            </span>
                            <span className="count-sub">({row.lower_cost_patient.count}x)</span>
                          </td>
                          <td>
                            <strong className={`diff-amount ${isPositive ? 'savings' : 'negative'}`}>
                              {isPositive ? `+${fmt(row.difference)}` : fmt(row.difference)}
                            </strong>
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* Tab 2: Underlying Claims Audit Trail */}
          {activeTab === 'claims' && (
            <div className="claims-audit-split">
              <div className="claims-split-col">
                <div className="split-col-header">
                  <h4>{comparisonResult.higher_cost_patient.patient_name} Claims ({comparisonResult.higher_cost_patient.episode.claims?.length})</h4>
                  <span className="badge-count danger">{fmt(comparisonResult.higher_cost_patient.episode.total_billed)}</span>
                </div>
                <div className="table-responsive">
                  <table className="comparator-data-table mini">
                    <thead>
                      <tr>
                        <th>Claim ID</th>
                        <th>DOS</th>
                        <th>Diagnosis</th>
                        <th>CPT</th>
                        <th>Paid</th>
                      </tr>
                    </thead>
                    <tbody>
                      {comparisonResult.higher_cost_patient.episode.claims?.map((c) => (
                        <tr key={c.claim_id}>
                          <td>
                            {onOpenClaim ? (
                              <button
                                type="button"
                                className="claim-link-button"
                                onClick={() => onOpenClaim({ number: c.claim_id, claimId: c.claim_id })}
                              >
                                {c.claim_id}
                              </button>
                            ) : (
                              <strong>{c.claim_id}</strong>
                            )}
                          </td>
                          <td>{c.dos}</td>
                          <td><code>{c.diagnosis}</code></td>
                          <td><code>{c.cpt}</code></td>
                          <td><strong>{fmt(c.paid)}</strong></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>

              <div className="claims-split-col">
                <div className="split-col-header">
                  <h4>{comparisonResult.lower_cost_patient.patient_name} Claims ({comparisonResult.lower_cost_patient.episode.claims?.length})</h4>
                  <span className="badge-count success">{fmt(comparisonResult.lower_cost_patient.episode.total_billed)}</span>
                </div>
                <div className="table-responsive">
                  <table className="comparator-data-table mini">
                    <thead>
                      <tr>
                        <th>Claim ID</th>
                        <th>DOS</th>
                        <th>Diagnosis</th>
                        <th>CPT</th>
                        <th>Paid</th>
                      </tr>
                    </thead>
                    <tbody>
                      {comparisonResult.lower_cost_patient.episode.claims?.map((c) => (
                        <tr key={c.claim_id}>
                          <td>
                            {onOpenClaim ? (
                              <button
                                type="button"
                                className="claim-link-button"
                                onClick={() => onOpenClaim({ number: c.claim_id, claimId: c.claim_id })}
                              >
                                {c.claim_id}
                              </button>
                            ) : (
                              <strong>{c.claim_id}</strong>
                            )}
                          </td>
                          <td>{c.dos}</td>
                          <td><code>{c.diagnosis}</code></td>
                          <td><code>{c.cpt}</code></td>
                          <td><strong>{fmt(c.paid)}</strong></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          )}

          {/* Clinical Governance & Disclaimer */}
          <div className="comparator-governance-card">
            <div className="governance-icon">
              <Shield size={20} />
            </div>
            <div className="governance-content">
              <strong>Clinical Governance & Value-Based Review Notice</strong>
              <p>{comparisonResult.disclaimer}</p>
            </div>
          </div>
          </>
          )}
        </div>
      )}
    </div>
  )
}
