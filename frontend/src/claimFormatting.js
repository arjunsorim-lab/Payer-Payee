export function uniqueValues(rows, key) {
  return [...new Set(rows.map((row) => row[key]).filter(Boolean))].sort((a, b) => a.localeCompare(b))
}

export function getDateRange(rows) {
  const dates = rows.map((row) => row.dos).filter(Boolean).sort()
  if (!dates.length) return { from: '', to: '' }
  return { from: dates[0], to: dates[dates.length - 1] }
}

export function formatCurrency(value) {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 2,
  }).format(value || 0)
}

export function formatCompactCurrency(value) {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    notation: Math.abs(value) >= 1_000_000 ? 'compact' : 'standard',
    maximumFractionDigits: Math.abs(value) >= 1_000_000 ? 1 : 0,
  }).format(value || 0)
}

export function formatPercent(value) {
  return `${Number.isFinite(value) ? value.toFixed(1) : '0.0'}%`
}

export function formatDate(value) {
  if (!value) return '-'
  return new Date(`${value}T00:00:00`).toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  })
}

export function calculateAge(dob) {
  if (!dob) return ''
  const birthDate = new Date(`${dob}T00:00:00`)
  const today = new Date()
  let age = today.getFullYear() - birthDate.getFullYear()
  const monthOffset = today.getMonth() - birthDate.getMonth()
  if (monthOffset < 0 || (monthOffset === 0 && today.getDate() < birthDate.getDate())) {
    age -= 1
  }
  return age
}

export function sum(rows, key) {
  return rows.reduce((total, row) => total + (row[key] || 0), 0)
}

export function isDeniedClaimStatus(status) {
  return String(status || '').trim().toLowerCase() === 'denied'
}

export function isOpenClaimStatus(status) {
  const normalized = String(status || '').trim().toLowerCase()
  if (!normalized) return false
  if (normalized.includes('processed') || normalized.includes('paid') || normalized.includes('adjudicated')) return false
  return normalized.includes('open')
    || normalized.includes('pending')
    || normalized.includes('submitted')
    || normalized.includes('review')
    || normalized.includes('appeal')
    || normalized.includes('rejected')
    || normalized === 'denied'
}

export function getInitials(member) {
  return `${member.firstName?.[0] || ''}${member.lastName?.[0] || ''}`.toUpperCase()
}

export function getDiagnosis(claim) {
  return `${claim.diagnosisCode} ${claim.diagnosisDescription}`.trim()
}

export function getService(claim) {
  return `${claim.placeOfServiceCode} - ${claim.placeOfService}`.trim()
}

export function getPayerContact(claim) {
  return claim.payerContact || 'Not recorded'
}

