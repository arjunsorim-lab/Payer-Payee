export function formatProbability(value) {
  return Number.isFinite(value) ? `${(value * 100).toFixed(1)}%` : 'Unavailable'
}

export function formatOptionalCurrency(value) {
  if (!Number.isFinite(value)) return 'Unavailable'
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value)
}

export function formatPredictionRange(value) {
  if (!value || !Number.isFinite(value.low) || !Number.isFinite(value.high)) return 'Range unavailable'
  return `${formatOptionalCurrency(value.low)}–${formatOptionalCurrency(value.high)}`
}
