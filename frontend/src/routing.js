export const VALID_VIEWS = new Set(['home', 'member', 'predictions', 'claims', 'compare'])

function findClaimByNumber(claimsData, claimNumber) {
  return claimsData.find((claim) => claim.number === claimNumber || claim.claimId === claimNumber) || null
}

export function normalizePredictionClaim(claim) {
  return claim
}

export function getNavForView(view) {
  if (view === 'member') return 'Patient 360'
  if (view === 'predictions') return 'Predictions'
  if (view === 'claims') return 'Claims'
  if (view === 'compare') return 'Patient Comparison'
  return 'home'
}

export function routeToHash(route) {
  const params = new URLSearchParams()
  const view = VALID_VIEWS.has(route.activeView) ? route.activeView : 'home'
  params.set('view', view)

  if (view === 'member' && route.selectedMemberId) {
    params.set('member', route.selectedMemberId)
  }
  if (view === 'claims' && route.selectedClaim?.number) {
    params.set('claim', route.selectedClaim.number)
  }
  if (view === 'predictions' && route.selectedPredictionClaim?.number) {
    params.set('prediction', route.selectedPredictionClaim.number)
  }

  return `#${params.toString()}`
}

export function routeFromHash(hash, claimsData) {
  const params = new URLSearchParams(hash.replace(/^#/, ''))
  const requestedView = params.get('view') || 'home'
  const activeView = VALID_VIEWS.has(requestedView) ? requestedView : 'home'
  const claimParam = params.get('claim')
  const predictionParam = params.get('prediction')
  const selectedClaim = activeView === 'claims' && claimParam
    ? findClaimByNumber(claimsData, claimParam) || { claimId: claimParam, number: claimParam }
    : null
  const selectedPredictionClaim = activeView === 'predictions' && predictionParam
    ? normalizePredictionClaim(findClaimByNumber(claimsData, predictionParam) || { claimId: predictionParam, number: predictionParam })
    : null
  const selectedMemberId = activeView === 'member' ? params.get('member') : null

  return {
    activeView,
    activeNav: getNavForView(activeView),
    selectedMemberId,
    selectedClaim,
    selectedPredictionClaim,
  }
}

