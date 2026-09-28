import { formatOptionalCurrency } from './providerLlmFormat.js'

export function financialImpactSummaryFromBilledResult(result) {
  if (!result?.available || !result.calculation) return ''
  const memberId = result.member_id || result.prediction_patient?.member_id
  const memberName = (
    result.prediction_patient?.patient_name
    || result.prediction_patient?.member_name
    || result.episode_1?.source_rows?.[0]?.patient_name
    || result.earlier_episode?.claims?.[0]?.patient_name
  )
  const memberLabel = [memberName, memberId ? `(${memberId})` : ''].filter(Boolean).join(' ')
  const fmt = formatOptionalCurrency

  if (result.calculation_type === 'reference_intervention_counterfactual') {
    const actual = result.calculation.actual_cost
    const proposed = result.calculation.proposed_cost
    const savings = result.calculation.potential_savings
    const referenceName = result.reference_patient?.patient_name || result.intervention?.source_rows?.[0]?.patient_name || 'the reference patient'
    const referenceId = result.reference_patient?.member_id
    const referenceLabel = [referenceName, referenceId ? `(${referenceId})` : ''].filter(Boolean).join(' ')
    return `Financial impact for ${memberLabel}: Actual total billed cost (risk evaluation + hospital admission) = ${fmt(actual)}; proposed total billed cost (risk evaluation + diabetes self-management training, as applied for the reference patient ${referenceLabel}) = ${fmt(proposed)}; potential savings = ${fmt(savings)}. The savings represent the avoided hospital admission and are based on billed charges.`
  }

  const actual = result.calculation.actual_two_episode_billed
  const proposed = result.calculation.proposed_earlier_episode_with_add_on_billed
  const savings = result.calculation.potential_billed_difference
  return `Financial impact for ${memberLabel}: Actual total billed cost = ${fmt(actual)}; proposed total billed cost = ${fmt(proposed)}; potential savings = ${fmt(savings)}. The savings represent the avoided worsening follow-up visit and are based on billed charges.`
}

