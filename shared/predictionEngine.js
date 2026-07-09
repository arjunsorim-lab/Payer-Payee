function safeNumber(value) {
  return Number.isFinite(value) ? value : 0
}

function roundMoney(value) {
  return Number(safeNumber(value).toFixed(2))
}

function clamp(value, min = 0, max = 100) {
  return Math.min(max, Math.max(min, safeNumber(value)))
}

function ratio(numerator, denominator, fallback = 0) {
  return denominator ? numerator / denominator : fallback
}

function daysBetween(a, b) {
  const left = new Date(`${a}T00:00:00`)
  const right = new Date(`${b}T00:00:00`)
  return Math.round((left - right) / 86_400_000)
}

function riskLevel(score) {
  if (score >= 50) return 'High'
  if (score >= 35) return 'Medium'
  return 'Low'
}

function confidenceLevel(peerCount, specificity) {
  if (peerCount >= 25 && specificity <= 2) return 'High'
  if (peerCount >= 8) return 'Medium'
  return 'Low'
}

function confidenceBand(value, confidence) {
  const spread = confidence === 'High' ? 0.07 : confidence === 'Medium' ? 0.12 : 0.2
  return {
    low: roundMoney(value * (1 - spread)),
    high: roundMoney(value * (1 + spread)),
  }
}

function getPeerGroups(claims, claim) {
  const previousClaims = claims.filter((item) => item.number !== claim.number && item.dos <= claim.dos)
  const candidates = previousClaims.length ? previousClaims : claims.filter((item) => item.number !== claim.number)
  const rules = [
    {
      label: 'payer + provider + CPT + POS',
      test: (item) => item.payer === claim.payer &&
        item.billingProviderNpi === claim.billingProviderNpi &&
        item.cptCode === claim.cptCode &&
        item.placeOfServiceCode === claim.placeOfServiceCode,
    },
    {
      label: 'payer + CPT + POS',
      test: (item) => item.payer === claim.payer &&
        item.cptCode === claim.cptCode &&
        item.placeOfServiceCode === claim.placeOfServiceCode,
    },
    {
      label: 'payer + CPT',
      test: (item) => item.payer === claim.payer && item.cptCode === claim.cptCode,
    },
    {
      label: 'payer + provider',
      test: (item) => item.payer === claim.payer && item.billingProviderNpi === claim.billingProviderNpi,
    },
    {
      label: 'payer',
      test: (item) => item.payer === claim.payer,
    },
    {
      label: 'all claims',
      test: () => true,
    },
  ]

  for (let index = 0; index < rules.length; index += 1) {
    const peers = candidates.filter(rules[index].test)
    if (peers.length >= (index <= 2 ? 5 : 8) || index === rules.length - 1) {
      return { peers, basis: rules[index].label, specificity: index + 1 }
    }
  }

  return { peers: candidates, basis: 'all claims', specificity: rules.length }
}

function summarizeClaims(claims) {
  const totals = claims.reduce((acc, claim) => {
    acc.charge += claim.totalCharge || 0
    acc.allowed += claim.allowed || 0
    acc.paid += claim.paid || 0
    acc.patientResp += claim.patientResp || 0
    acc.adjustment += claim.adjustment || 0
    if (claim.status === 'Denied') acc.denied += 1
    if (claim.status?.includes('Forwarded')) acc.forwarded += 1
    if (claim.status?.includes('Secondary')) acc.secondary += 1
    if (claim.status?.includes('Reversal')) acc.reversal += 1
    return acc
  }, {
    charge: 0,
    allowed: 0,
    paid: 0,
    patientResp: 0,
    adjustment: 0,
    denied: 0,
    forwarded: 0,
    secondary: 0,
    reversal: 0,
  })

  return {
    count: claims.length,
    allowedRate: clamp(ratio(totals.allowed, totals.charge, 0.72), 0.05, 1.15),
    paidToAllowedRate: clamp(ratio(totals.paid, totals.allowed, 0.82), 0, 1.2),
    patientToAllowedRate: clamp(ratio(totals.patientResp, totals.allowed, 0.18), 0, 1),
    adjustmentRate: clamp(ratio(totals.adjustment, totals.charge, 0.25), 0, 1),
    denialRate: ratio(totals.denied, claims.length, 0),
    forwardedRate: ratio(totals.forwarded, claims.length, 0),
    secondaryRate: ratio(totals.secondary, claims.length, 0),
    reversalRate: ratio(totals.reversal, claims.length, 0),
  }
}

function mostLikelyDenialReason(peers) {
  const reasons = new Map()
  peers
    .filter((claim) => claim.denialReason)
    .forEach((claim) => {
      reasons.set(claim.denialReason, (reasons.get(claim.denialReason) || 0) + 1)
    })

  return [...reasons.entries()].sort((a, b) => b[1] - a[1])[0]?.[0] || ''
}

function isAuthSensitive(claim) {
  return ['21', '22', '23', '31', '32', '51', '81'].includes(claim.placeOfServiceCode) ||
    /^[2347]/.test(claim.cptCode || '')
}

function providerStats(claims, claim) {
  const providerClaims = claims.filter((item) => (
    item.billingProviderNpi === claim.billingProviderNpi ||
    item.billingProvider === claim.billingProvider
  ))
  return summarizeClaims(providerClaims)
}

function repeatClaimStats(claims, claim) {
  const priorMemberClaims = claims.filter((item) => (
    item.number !== claim.number &&
    item.memberId === claim.memberId &&
    item.dos < claim.dos
  ))
  const relatedClaims = priorMemberClaims.filter((item) => (
    item.diagnosisCode === claim.diagnosisCode ||
    item.cptCode === claim.cptCode ||
    item.diagnosisCode?.slice(0, 3) === claim.diagnosisCode?.slice(0, 3)
  ))
  const relatedWithin30 = relatedClaims.filter((item) => {
    const days = daysBetween(claim.dos, item.dos)
    return days >= 0 && days <= 30
  })
  const relatedWithin90 = relatedClaims.filter((item) => {
    const days = daysBetween(claim.dos, item.dos)
    return days >= 0 && days <= 90
  })

  return {
    relatedWithin30: relatedWithin30.length,
    relatedWithin90: relatedWithin90.length,
    projectedAvoidableCost: roundMoney(relatedWithin90.reduce((total, item) => total + (item.allowed || 0), 0) * 0.25),
  }
}

function procedureLabel(claim) {
  return `${claim.cptCode || 'procedure'} ${claim.cptDescription || ''}`.trim()
}

function getPlaceOrFallback(claim) {
  return claim.placeOfService
    ? `${claim.placeOfServiceCode || ''} ${claim.placeOfService}`.trim()
    : 'this service location'
}

function authorizationScore(claim) {
  const authSensitive = isAuthSensitive(claim)
  if (!authSensitive) return 4
  if (claim.priorAuth) return 14

  let score = 32
  if (['21', '22', '23', '51'].includes(claim.placeOfServiceCode)) score += 14
  if (/^[27]/.test(claim.cptCode || '')) score += 8
  if (/^9/.test(claim.cptCode || '')) score += 5
  if ((claim.totalCharge || 0) > 1000) score += 5
  return Math.round(clamp(score, 0, 68))
}

function referralScore(claim) {
  if (claim.referral) return 8
  if (!['HM', 'MC', 'MB'].includes(claim.filingIndicator)) return 6

  let score = 30
  if (['22', '23', '31', '32', '51'].includes(claim.placeOfServiceCode)) score += 7
  if ((claim.totalCharge || 0) > 1000) score += 5
  return Math.round(clamp(score, 0, 54))
}

function fixForDriver(claim, driver, likelyDenialReason) {
  switch (driver.label) {
    case 'Authorization':
      return `Confirm prior authorization for ${procedureLabel(claim)} with ${claim.payer} before submission.`
    case 'Referral':
      return `Validate referral requirements for ${claim.filingIndicator || '837'} filing and ${claim.payer}.`
    case 'Repeat':
      return `Review ${claim.memberId}'s ${claim.diagnosisCode || 'related diagnosis'} history and consider care-management outreach before another ${claim.placeOfService} encounter.`
    case 'Adjustment':
      return `Compare the ${claim.cptCode} charge against ${claim.payer} allowed-rate history and contract terms before final billing.`
    case 'Collection':
      return `Prepare a patient estimate or payment-plan outreach for the expected member balance.`
    case 'COB':
      return `Verify payer order and coordination-of-benefits details for this ${claim.filingIndicator || '837'} filing.`
    case 'Provider':
      return `Review ${claim.billingProvider}'s coding and contract performance pattern for this payer/service mix.`
    case 'Payment':
      return `Review expected underpayment risk before submission; forecasted payer paid is materially below billed charges.`
    case 'Denial':
      if (/eligibility|coverage/i.test(likelyDenialReason)) {
        return `Verify member eligibility and coverage for ${claim.memberId} before the claim is released.`
      }
      if (/medical necessity/i.test(likelyDenialReason)) {
        return `Attach medical-necessity support for ${procedureLabel(claim)} and the ${claim.diagnosisCode || 'diagnosis'} diagnosis.`
      }
      if (/coding|documentation/i.test(likelyDenialReason)) {
        return `Review coding and documentation for ${procedureLabel(claim)} before submission.`
      }
      if (/authorization/i.test(likelyDenialReason)) {
        return `Verify authorization evidence and payer rules for ${claim.payer}.`
      }
      if (/timely/i.test(likelyDenialReason)) {
        return `Check filing deadline evidence and resubmission timing before release.`
      }
      return likelyDenialReason
        ? `Correct likely denial driver: ${likelyDenialReason}.`
        : `Route to denial-prevention review before submission.`
    default:
      return ''
  }
}

function buildFixChecklist(claim, scores, likelyDenialReason, riskDrivers) {
  const fixes = []
  const addFix = (fix) => {
    if (fix && !fixes.includes(fix)) fixes.push(fix)
  }

  riskDrivers
    .filter((driver) => driver.score >= 30)
    .slice(0, 4)
    .forEach((driver) => addFix(fixForDriver(claim, driver, likelyDenialReason)))

  if (scores.denialRisk >= 50) addFix(fixForDriver(claim, { label: 'Denial' }, likelyDenialReason))
  if (isAuthSensitive(claim) && !claim.priorAuth) addFix(fixForDriver(claim, { label: 'Authorization' }, likelyDenialReason))
  if (!claim.referral && ['HM', 'MC', 'MB'].includes(claim.filingIndicator)) addFix(fixForDriver(claim, { label: 'Referral' }, likelyDenialReason))
  if (!fixes.length) fixes.push('Submit with standard claim review.')
  return fixes
}

function percent(value) {
  return `${(safeNumber(value) * 100).toFixed(1)}%`
}

function formatMoney(value) {
  return safeNumber(value).toLocaleString('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
}

function buildRiskDrivers({ claim, peerStats, provider, repeat, predictedAllowed, predictedPaid, predictedPatientResp, predictedAdjustment, scores, likelyDenialReason }) {
  const authSensitive = isAuthSensitive(claim)
  const needsReferralReview = !claim.referral && ['HM', 'MC', 'MB'].includes(claim.filingIndicator)
  const authScore = authorizationScore(claim)
  const referralDriverScore = referralScore(claim)

  return [
    {
      label: 'Authorization',
      score: authScore,
      reason: authSensitive && !claim.priorAuth
        ? `${procedureLabel(claim)} at ${claim.placeOfServiceCode} - ${claim.placeOfService} is authorization-sensitive for ${claim.payer}, and no prior authorization number is present.`
        : claim.priorAuth
          ? `Prior authorization ${claim.priorAuth} is present for this authorization-sensitive service.`
          : `No prior authorization signal is triggered for this CPT/place-of-service combination.`,
    },
    {
      label: 'Referral',
      score: referralDriverScore,
      reason: needsReferralReview
        ? `${claim.filingIndicator} filing for ${claim.payer} has no referral number for ${getPlaceOrFallback(claim)}, so referral rules should be checked before submission.`
        : claim.referral
          ? `Referral ${claim.referral} is present on the claim.`
          : `No referral rule signal is triggered by the current filing indicator.`,
    },
    {
      label: 'Adjustment',
      score: scores.adjustmentRisk,
      reason: `Expected adjustment is ${percent(ratio(predictedAdjustment, claim.totalCharge, 0))} of charge because ${peerStats.count} peer claim(s) for this service pattern allowed ${percent(peerStats.allowedRate)} of billed charges.`,
    },
    {
      label: 'Denial',
      score: scores.denialRisk,
      reason: likelyDenialReason
        ? `Denial signal is driven by ${peerStats.count} peer claim(s) and likely denial pattern: ${likelyDenialReason}.`
        : `Peer denial rate is ${percent(peerStats.denialRate)} for the selected payer/service pattern.`,
    },
    {
      label: 'Collection',
      score: scores.collectionRisk,
      reason: `Predicted patient responsibility is $${formatMoney(predictedPatientResp)}, or ${percent(ratio(predictedPatientResp, predictedAllowed, 0))} of expected allowed amount for ${claim.memberId}.`,
    },
    {
      label: 'COB',
      score: scores.cobRisk,
      reason: `${claim.filingIndicator || '837'} filing history shows ${percent(peerStats.secondaryRate)} secondary outcomes and ${percent(peerStats.forwardedRate)} forwarded outcomes.`,
    },
    {
      label: 'Repeat',
      score: scores.repeatRisk,
      reason: `${repeat.relatedWithin90} related ${claim.diagnosisCode || claim.cptCode} member claim(s) were found within 90 days, with estimated avoidable cost of $${formatMoney(repeat.projectedAvoidableCost)}.`,
    },
    {
      label: 'Provider',
      score: scores.providerPerformanceRisk,
      reason: `${claim.billingProvider} history has ${percent(provider.denialRate)} denials and a ${percent(provider.adjustmentRate)} adjustment rate across billing records.`,
    },
    {
      label: 'Payment',
      score: Math.round(clamp(100 - ratio(predictedPaid, claim.totalCharge, 0) * 100)),
      reason: `Expected payer paid amount is $${formatMoney(predictedPaid)}, or ${percent(ratio(predictedPaid, claim.totalCharge, 0))} of the submitted charge.`,
    },
  ].sort((a, b) => b.score - a.score)
}

export function predictClaim(claim, claims) {
  const { peers, basis, specificity } = getPeerGroups(claims, claim)
  const peerStats = summarizeClaims(peers)
  const provider = providerStats(claims, claim)
  const repeat = repeatClaimStats(claims, claim)
  const confidence = confidenceLevel(peerStats.count, specificity)

  const predictedAllowed = roundMoney(claim.totalCharge * peerStats.allowedRate)
  const predictedPaid = roundMoney(predictedAllowed * peerStats.paidToAllowedRate)
  const predictedPatientResp = roundMoney(predictedAllowed * peerStats.patientToAllowedRate)
  const predictedAdjustment = roundMoney(Math.max(0, claim.totalCharge - predictedAllowed))
  const adjustmentRate = ratio(predictedAdjustment, claim.totalCharge, 0)

  const authPenalty = Math.max(0, authorizationScore(claim) - 34) * 0.42
  const referralPenalty = Math.max(0, referralScore(claim) - 30) * 0.38
  const unitPenalty = (claim.units || 1) > 3 ? 6 : 0
  const denialRisk = clamp((peerStats.denialRate * 65) + authPenalty + referralPenalty + unitPenalty + (adjustmentRate > 0.35 ? 5 : 0), 0, 95)
  const adjustmentRisk = clamp((adjustmentRate * 72) + (peerStats.adjustmentRate * 18) + (predictedAdjustment > 500 ? 6 : 0), 0, 96)
  const collectionRisk = clamp((ratio(predictedPatientResp, predictedAllowed, 0) * 65) + (predictedPatientResp > 250 ? 12 : 0) + (predictedPatientResp > 500 ? 8 : 0), 0, 95)
  const cobRisk = clamp((peerStats.forwardedRate * 48) + (peerStats.secondaryRate * 34) + (['MB', 'BL'].includes(claim.filingIndicator) ? 8 : 0), 0, 90)
  const repeatRisk = clamp(Math.min(repeat.relatedWithin30 * 14, 30) + Math.min(repeat.relatedWithin90 * 7, 28) + (['23', '21', '51'].includes(claim.placeOfServiceCode) ? 8 : 0), 0, 90)
  const providerPerformanceRisk = clamp((provider.denialRate * 30) + (provider.adjustmentRate * 30) + (provider.secondaryRate * 12), 0, 88)
  const highestRisk = Math.max(denialRisk, adjustmentRisk, collectionRisk, cobRisk, repeatRisk, providerPerformanceRisk)
  const blendedRisk =
    (denialRisk * 0.24) +
    (adjustmentRisk * 0.22) +
    (collectionRisk * 0.16) +
    (cobRisk * 0.14) +
    (repeatRisk * 0.12) +
    (providerPerformanceRisk * 0.12)
  const overallRisk = clamp((blendedRisk * 0.72) + (highestRisk * 0.28), 0, 96)

  let likelyOutcome = 'Processed as Primary'
  if (denialRisk >= 75) likelyOutcome = 'High denial review'
  else if (cobRisk >= 55) likelyOutcome = 'Secondary or forwarded review'
  else if (peerStats.secondaryRate > peerStats.forwardedRate && peerStats.secondaryRate > 0.25) likelyOutcome = 'Processed as Secondary'

  const likelyDenialReason = mostLikelyDenialReason(peers) ||
    (isAuthSensitive(claim) && !claim.priorAuth ? 'Authorization review required' : '') ||
    (!claim.referral && ['HM', 'MC', 'MB'].includes(claim.filingIndicator) ? 'Referral or filing rule review required' : '') ||
    (adjustmentRisk >= 70 ? 'Contractual adjustment outlier' : '')

  const scores = {
    denialRisk: Math.round(denialRisk),
    adjustmentRisk: Math.round(adjustmentRisk),
    collectionRisk: Math.round(collectionRisk),
    cobRisk: Math.round(cobRisk),
    repeatRisk: Math.round(repeatRisk),
    providerPerformanceRisk: Math.round(providerPerformanceRisk),
    overallRisk: Math.round(overallRisk),
  }
  const riskDrivers = buildRiskDrivers({
    claim,
    peerStats,
    provider,
    repeat,
    predictedAllowed,
    predictedPaid,
    predictedPatientResp,
    predictedAdjustment,
    scores,
    likelyDenialReason,
  })
  const dominantReasons = riskDrivers.filter((driver) => driver.score >= 28).slice(0, 4)
  const reasons = [
    ...(dominantReasons.length ? dominantReasons : riskDrivers.slice(0, 3)).map((driver) => driver.reason),
    `Money forecast uses ${peerStats.count} historical peer claim(s) matched by ${basis}; allowed rate ${percent(peerStats.allowedRate)} and paid-to-allowed rate ${percent(peerStats.paidToAllowedRate)}.`,
  ]

  return {
    claimNumber: claim.number,
    basis,
    peerCount: peerStats.count,
    confidence,
    money: {
      predictedAllowed,
      predictedPaid,
      predictedPatientResp,
      predictedAdjustment,
      paidRange: confidenceBand(predictedPaid, confidence),
      allowedRate: Number((peerStats.allowedRate * 100).toFixed(1)),
      paidToAllowedRate: Number((peerStats.paidToAllowedRate * 100).toFixed(1)),
      patientToAllowedRate: Number((peerStats.patientToAllowedRate * 100).toFixed(1)),
      adjustmentRate: Number((adjustmentRate * 100).toFixed(1)),
    },
    risks: {
      denial: { score: scores.denialRisk, level: riskLevel(scores.denialRisk), reason: likelyDenialReason || 'No dominant denial pattern found.' },
      adjustment: { score: scores.adjustmentRisk, level: riskLevel(scores.adjustmentRisk) },
      collection: { score: scores.collectionRisk, level: riskLevel(scores.collectionRisk) },
      cob: { score: scores.cobRisk, level: riskLevel(scores.cobRisk) },
      repeat: { score: scores.repeatRisk, level: riskLevel(scores.repeatRisk), projectedAvoidableCost: repeat.projectedAvoidableCost },
      provider: { score: scores.providerPerformanceRisk, level: riskLevel(scores.providerPerformanceRisk) },
      overall: { score: scores.overallRisk, level: riskLevel(scores.overallRisk) },
    },
    outcome: {
      likely: likelyOutcome,
      confidence,
      explanation: `Forecast uses ${peerStats.count} historical peer claim(s) by ${basis}.`,
    },
    riskDrivers,
    reasons,
    resubmissionSuccess: claim.status === 'Denied'
      ? {
        score: Math.round(clamp(42 + (claim.priorAuth ? 12 : 0) + (claim.referral ? 8 : 0) - (scores.denialRisk * 0.25))),
        action: 'Correct denial driver and resubmit with supporting documentation.',
      }
      : null,
    fixes: buildFixChecklist(claim, scores, likelyDenialReason, riskDrivers),
  }
}

export function buildRiskQueue(claims, allClaims = claims, limit = 10) {
  return claims
    .map((claim) => ({ claim, prediction: predictClaim(claim, allClaims) }))
    .sort((a, b) => b.prediction.risks.overall.score - a.prediction.risks.overall.score)
    .slice(0, limit)
}

export function buildPredictionSummary(claims, allClaims = claims) {
  const predictions = claims.map((claim) => predictClaim(claim, allClaims))
  const totalPredictedPaid = roundMoney(predictions.reduce((total, prediction) => total + prediction.money.predictedPaid, 0))
  const totalPredictedAdjustment = roundMoney(predictions.reduce((total, prediction) => total + prediction.money.predictedAdjustment, 0))
  const highRiskCount = predictions.filter((prediction) => prediction.risks.overall.level === 'High').length
  const atRiskCount = predictions.filter((prediction) => prediction.risks.overall.level !== 'Low').length
  const denialQueueCount = predictions.filter((prediction) => prediction.risks.denial.level === 'High').length

  return {
    totalPredictedPaid,
    totalPredictedAdjustment,
    atRiskCount,
    highRiskCount,
    denialQueueCount,
    averageOverallRisk: Math.round(ratio(predictions.reduce((total, prediction) => total + prediction.risks.overall.score, 0), predictions.length, 0)),
  }
}
