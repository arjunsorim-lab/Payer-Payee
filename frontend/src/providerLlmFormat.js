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
