export function formatProbability(value) {
  return Number.isFinite(value) ? `${(value * 100).toFixed(1)}%` : ''
}

export function formatOptionalCurrency(value) {
  if (!Number.isFinite(value)) return ''
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value)
}

export function formatPredictionRange(value) {
  if (!value || !Number.isFinite(value.low) || !Number.isFinite(value.high)) return ''
  return `${formatOptionalCurrency(value.low)}–${formatOptionalCurrency(value.high)}`
}

export function formatFinancialStatus(category) {
  if (!category || category.status === 'insufficient_source_fields') return ''
  const amount = formatOptionalCurrency(category.amount)
  return category.status === 'supported_zero' ? `${amount} currently supported` : amount
}

const RECOVERABLE_OPPORTUNITY_TYPES = new Set([
  'underpayment',
  'correctable_denial',
  'excessive_adjustment',
  'patient_balance',
  'duplicate_or_correction',
])

export function formatFinancialOpportunityPurpose(items) {
  const opportunities = Array.isArray(items) ? items : []
  const recoverable = opportunities.filter((item) => (
    RECOVERABLE_OPPORTUNITY_TYPES.has(item?.type) && Number.isFinite(item?.amount) && item.amount > 0
  ))
  const recoverableTotal = recoverable.reduce((total, item) => total + item.amount, 0)

  if (recoverableTotal > 0) {
    const source = recoverable.length === 1
      ? ` from ${String(recoverable[0].label || recoverable[0].type).toLowerCase()}`
      : ` across ${recoverable.length} supported categories`
    return `Checks the current claim for money that may need follow-up. It found ${formatOptionalCurrency(recoverableTotal)}${source}.`
  }

  const supportedTotal = opportunities.reduce(
    (total, item) => total + (Number.isFinite(item?.amount) && item.amount > 0 ? item.amount : 0),
    0,
  )
  if (supportedTotal > 0) {
    return `Checks the current claim for money that may need follow-up. The supported amount is ${formatOptionalCurrency(supportedTotal)}.`
  }

  return 'Checks the current claim for money that may need follow-up. The available evidence supports no positive amount.'
}
