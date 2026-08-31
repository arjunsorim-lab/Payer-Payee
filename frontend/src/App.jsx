import { createContext, useContext, useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import {
  formatFinancialOpportunityPurpose,
  formatOptionalCurrency,
  formatPredictionRange,
  formatProbability,
} from './providerLlmFormat.js'
import {
  Activity,
  ArrowLeft,
  ArrowRight,
  ArrowUpRight,
  Banknote,
  BarChart3,
  Bell,
  Brain,
  Building2,
  CalendarDays,
  CheckCircle2,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  CircleDollarSign,
  CircleUserRound,
  ClipboardCheck,
  ClipboardList,
  CreditCard,
  DollarSign,
  Download,
  FileText,
  Filter,
  HeartPulse,
  HelpCircle,
  Home,
  Hospital,
  Info,
  Landmark,
  LayoutDashboard,
  Mail,
  RefreshCw,
  Search,
  Send,
  Settings,
  Shield,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  Stethoscope,
  Target,
  TrendingDown,
  TrendingUp,
  UserRound,
  Users,
  Wind,
  X,
} from 'lucide-react'
import './App.css'

const navSections = [
  {
    title: 'Patient',
    items: [
      { label: 'Patient 360', icon: Users, view: 'member' },
      { label: 'Predictions', icon: TrendingUp, view: 'predictions' },
      { label: 'Encounters', icon: CalendarDays, view: 'member' },
      { label: 'Claims', icon: FileText, view: 'claims' },
      { label: 'Payments', icon: CircleDollarSign, view: 'member' },
      { label: 'Collections', icon: CreditCard, view: 'member' },
      { label: 'Correspondence', icon: Mail, view: 'member' },
    ],
  },
  {
    title: 'Provider',
    items: [
      { label: 'Search Providers', icon: Search, view: 'member' },
      { label: 'Provider 360', icon: Users, view: 'member' },
      { label: 'Contracts', icon: ClipboardList, view: 'member' },
      { label: 'Performance', icon: BarChart3, view: 'member' },
    ],
  },
  {
    title: 'Analytics',
    items: [
      { label: 'Payment Analytics', icon: BarChart3, view: 'member' },
      { label: 'Reports', icon: ClipboardCheck, view: 'member' },
      { label: 'Dashboards', icon: LayoutDashboard, view: 'home' },
    ],
  },
  {
    title: 'Admin',
    items: [
      { label: 'Users', icon: UserRound, view: 'member' },
      { label: 'Payers', icon: Building2, view: 'member' },
      { label: 'Settings', icon: Settings, view: 'member' },
    ],
  },
]

const RUNTIME_API_BASE_URL = typeof window !== 'undefined' ? window.__PAYER_PAYEE_API_URL__ : ''
const CONFIGURED_API_BASE_URL = (
  RUNTIME_API_BASE_URL
  || import.meta.env.VITE_API_BASE_URL
  || ''
).replace(/\/$/, '')
const LOCAL_API_BASE_URL = 'http://127.0.0.1:4000'
const RENDER_API_BASE_URL = 'https://payer-payee.onrender.com'
const CLAIMS_CACHE_PREFIX = 'payerpayee.claims.workbook.'
const EMPTY_DATE_RANGE = { from: '', to: '' }
const CLICKABLE_NAV_LABELS = new Set(['Patient 360', 'Predictions', 'Claims'])
const VALID_VIEWS = new Set(['home', 'member', 'predictions', 'claims'])

function buildDataModel(claimsData) {
  const defaultDateRange = getDateRange(claimsData)
  const members = buildMembers(claimsData)

  return {
    claimsData,
    defaultDateRange,
    recentClaims: claimsData.slice(0, 10),
    payerOptions: ['All Payers', ...uniqueValues(claimsData, 'payer')],
    planOptions: ['All Plans', ...uniqueValues(claimsData, 'filingIndicator')],
    providerOptions: ['All Groups', ...uniqueValues(claimsData, 'billingProvider')],
    members,
    membersById: new Map(members.map((member) => [member.memberId, member])),
  }
}

const EMPTY_DATA_MODEL = buildDataModel([])
const DataContext = createContext(EMPTY_DATA_MODEL)

function useAppData() {
  return useContext(DataContext)
}

async function fetchJson(path, options = {}) {
  const candidates = [...new Set([
    CONFIGURED_API_BASE_URL,
    window.location.origin,
    import.meta.env.DEV ? LOCAL_API_BASE_URL : '',
    import.meta.env.DEV ? '' : RENDER_API_BASE_URL,
  ].filter(Boolean))]
  let lastError = new Error('Backend API could not be reached')

  for (const baseUrl of candidates) {
    try {
      const response = await fetch(`${baseUrl}${path}`, {
        ...options,
        headers: { Accept: 'application/json', ...(options.headers || {}) },
      })
      if (!response.ok) {
        const errorPayload = await response.json().catch(() => null)
        const requestError = new Error(errorPayload?.message || `Request failed: ${response.status}`)
        if (![404, 405].includes(response.status)) throw requestError
        lastError = requestError
        continue
      }
      return await response.json()
    } catch (error) {
      if (!['Failed to fetch', 'Load failed', 'NetworkError when attempting to fetch resource.'].includes(error.message)) {
        throw error
      }
      lastError = error
    }
  }

  throw lastError
}

function writeClaimsCache(items, source) {
  try {
    const workbookHash = source?.workbook_hash
    if (!workbookHash) return
    Object.keys(window.localStorage)
      .filter((key) => key === `${'payerpayee.claims'}.v1` || (key.startsWith(CLAIMS_CACHE_PREFIX) && key !== `${CLAIMS_CACHE_PREFIX}${workbookHash}`))
      .forEach((key) => window.localStorage.removeItem(key))
    Object.keys(window.localStorage)
      .filter((key) => key.startsWith('payerpayee.provider-chat.') && !key.includes(workbookHash))
      .forEach((key) => window.localStorage.removeItem(key))
    window.localStorage.setItem(`${CLAIMS_CACHE_PREFIX}${workbookHash}`, JSON.stringify({
      items,
      workbookHash,
      savedAt: Date.now(),
    }))
  } catch {
    // Browser storage is optional; the backend workbook remains authoritative.
  }
}

function findClaimByNumber(claimsData, claimNumber) {
  return claimsData.find((claim) => claim.number === claimNumber || claim.claimId === claimNumber) || null
}

function getNavForView(view) {
  if (view === 'member') return 'Patient 360'
  if (view === 'predictions') return 'Predictions'
  if (view === 'claims') return 'Claims'
  return 'home'
}

function routeToHash(route) {
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

function routeFromHash(hash, claimsData) {
  const params = new URLSearchParams(hash.replace(/^#/, ''))
  const requestedView = params.get('view') || 'home'
  const activeView = VALID_VIEWS.has(requestedView) ? requestedView : 'home'
  const selectedClaim = activeView === 'claims' ? findClaimByNumber(claimsData, params.get('claim')) : null
  const selectedPredictionClaim = activeView === 'predictions' ? findClaimByNumber(claimsData, params.get('prediction')) : null
  const selectedMemberId = activeView === 'member' ? params.get('member') : null

  return {
    activeView,
    activeNav: getNavForView(activeView),
    selectedMemberId,
    selectedClaim,
    selectedPredictionClaim,
  }
}

function uniqueValues(rows, key) {
  return [...new Set(rows.map((row) => row[key]).filter(Boolean))].sort((a, b) => a.localeCompare(b))
}

function getDateRange(rows) {
  const dates = rows.map((row) => row.dos).filter(Boolean).sort()
  if (!dates.length) return EMPTY_DATE_RANGE
  return { from: dates[0], to: dates[dates.length - 1] }
}

function formatCurrency(value) {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 2,
  }).format(value || 0)
}

function formatCompactCurrency(value) {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    notation: Math.abs(value) >= 1_000_000 ? 'compact' : 'standard',
    maximumFractionDigits: Math.abs(value) >= 1_000_000 ? 1 : 0,
  }).format(value || 0)
}

function formatPercent(value) {
  return `${Number.isFinite(value) ? value.toFixed(1) : '0.0'}%`
}

function formatRange(range) {
  return `${formatCurrency(range.low)} - ${formatCurrency(range.high)}`
}

function formatDate(value) {
  if (!value) return '-'
  return new Date(`${value}T00:00:00`).toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  })
}

function calculateAge(dob) {
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

function sum(rows, key) {
  return rows.reduce((total, row) => total + (row[key] || 0), 0)
}

function getInitials(member) {
  return `${member.firstName?.[0] || ''}${member.lastName?.[0] || ''}`.toUpperCase()
}

function getDiagnosis(claim) {
  return `${claim.diagnosisCode} ${claim.diagnosisDescription}`.trim()
}

function getService(claim) {
  return `${claim.placeOfServiceCode} - ${claim.placeOfService}`.trim()
}

function getPayerContact(payer) {
  const slug = String(payer || 'payer').toLowerCase().replace(/[^a-z0-9]+/g, '')
  return `claims@${slug || 'payer'}.com`
}

function buildMembers(rows) {
  const grouped = new Map()
  rows.forEach((claim) => {
    const current = grouped.get(claim.memberId) || []
    current.push(claim)
    grouped.set(claim.memberId, current)
  })

  return [...grouped.entries()]
    .map(([memberId, claims]) => {
      const sortedClaims = [...claims].sort((a, b) => b.dos.localeCompare(a.dos) || b.number.localeCompare(a.number))
      const latestClaim = sortedClaims[0]
      const deniedCount = claims.filter((claim) => claim.status === 'Denied').length

      return {
        memberId,
        claims: sortedClaims,
        latestClaim,
        firstName: latestClaim.patientFirstName,
        lastName: latestClaim.patientLastName,
        patient: latestClaim.patient,
        dob: latestClaim.dob,
        gender: latestClaim.gender,
        groupId: latestClaim.groupId,
        groupName: latestClaim.groupName,
        payer: latestClaim.payer,
        payerId: latestClaim.payerId,
        subscriberId: latestClaim.subscriberId,
        accountNumber: latestClaim.accountNumber,
        totalCharge: sum(claims, 'totalCharge'),
        totalAllowed: sum(claims, 'allowed'),
        totalPaid: sum(claims, 'paid'),
        totalPatientResp: sum(claims, 'patientResp'),
        totalAdjustment: sum(claims, 'adjustment'),
        deniedCount,
        supportedMoneySummary: latestClaim.memberSupportedMoneySummary || {
          recoverable_now: 0,
          potentially_avoidable_spend_supported: 0,
          future_denial_exposure: 0,
          future_repeat_payment_exposure: 0,
        },
      }
    })
    .sort((a, b) => b.latestClaim.dos.localeCompare(a.latestClaim.dos))
}

function buildMemberStats(member, money, payerCohortSavings) {
  const claimCount = member.claims.length
  
  // Basic calculations purely from real dataset
  const totalAllowed = member.totalAllowed ?? member.claims.reduce((acc, c) => acc + (c.allowed || 0), 0)
  const totalPaid = member.totalPaid ?? member.claims.reduce((acc, c) => acc + (c.paid || 0), 0)
  const totalPatientResp = member.totalPatientResp ?? member.claims.reduce((acc, c) => acc + (c.patientResp || 0), 0)
  const totalCharge = member.totalCharge ?? member.claims.reduce((acc, c) => acc + (c.totalCharge || 0), 0)
  const insuranceSavings = Math.max(0, totalCharge - totalAllowed)
  
  // Calculate Avoidable from cohort savings or supported money summary
  const avoidableCosts = payerCohortSavings?.member_predicted_payer_avoidable_spend ?? money?.potentially_avoidable_spend_supported ?? 0
  const openClaimsCount = member.claims.filter(c => c.status !== 'Paid').length
  const deniedClaimsCount = member.claims.filter(c => c.status === 'Denied').length

  return [
    { label: 'Total Allowed', value: formatCurrency(totalAllowed), note: `Across ${claimCount.toLocaleString()} claims`, iconTone: 'green', Icon: CircleDollarSign },
    { label: 'Total Paid', value: formatCurrency(totalPaid), note: 'Payer payments recorded', iconTone: 'blue', Icon: Banknote },
    { label: 'Patient Responsibility', value: formatCurrency(totalPatientResp), note: 'Out of pocket + copay + coins', iconTone: 'orange', Icon: UserRound },
    { label: 'Primary Insurance Savings', value: formatCurrency(insuranceSavings), note: 'Contracted plan discounts', iconTone: 'green', Icon: ShieldCheck },
    { label: 'Potentially Avoidable Costs', value: formatCurrency(avoidableCosts), note: 'Identified opportunity', iconTone: 'purple', Icon: Target },
    { label: 'Open Claims', value: openClaimsCount.toString(), note: `${deniedClaimsCount} denied claims`, iconTone: openClaimsCount > 0 ? 'red' : 'green', Icon: FileText },
    { label: 'Last Encounter', value: formatDate(member.latestClaim.dos), note: member.latestClaim.placeOfService, iconTone: 'blue', Icon: CalendarDays },
  ]
}

function buildDashboardMetrics(rows) {
  const totalCharges = sum(rows, 'totalCharge')
  const totalAllowed = sum(rows, 'allowed')
  const totalPaid = sum(rows, 'paid')
  const totalPatientResp = sum(rows, 'patientResp')
  const totalAdjustments = sum(rows, 'adjustment')
  const percentOfCharges = (value) => (totalCharges ? `${((value / totalCharges) * 100).toFixed(2)}% of charges` : '0% of charges')

  return [
    { label: 'Total Charges', value: formatCurrency(totalCharges), note: '100% of charges', icon: DollarSign, tone: 'blue' },
    { label: 'Total Allowed', value: formatCurrency(totalAllowed), note: percentOfCharges(totalAllowed), icon: CheckCircle2, tone: 'teal' },
    { label: 'Total Paid', value: formatCurrency(totalPaid), note: percentOfCharges(totalPaid), icon: Banknote, tone: 'green' },
    { label: 'Patient Responsibility', value: formatCurrency(totalPatientResp), note: percentOfCharges(totalPatientResp), icon: CircleUserRound, tone: 'violet' },
    { label: 'Total Adjustments', value: formatCurrency(totalAdjustments), note: percentOfCharges(totalAdjustments), icon: RefreshCw, tone: 'orange' },
    { label: 'Total Claims', value: rows.length.toLocaleString(), note: 'Over selected period', icon: FileText, tone: 'blue' },
  ]
}

function getDashboardMetricDescription(label) {
  const descriptions = {
    'Total Charges': 'Total billed amount for all claims matching the selected date, payer, plan, provider, and filter settings.',
    'Total Allowed': 'Amount expected to be allowed by payers after contracted rates and claim edits are applied.',
    'Total Paid': 'Amount paid by payers across the filtered claims.',
    'Patient Responsibility': 'Member balance assigned to patients, including deductible, copay, coinsurance, or non-covered portions.',
    'Total Adjustments': 'Difference between billed charges and allowed amounts, usually contract write-off or claim adjustment.',
    'Total Claims': 'Number of claims included in the current dashboard filters.',
  }

  return descriptions[label] || 'Metric calculated from the current dashboard filters.'
}

function buildProviderKpis(claim, claimsData) {
  const providerAllClaims = claimsData.filter((row) => row.billingProvider === claim.billingProvider)
  const latestDate = providerAllClaims.reduce((latest, row) => row.dos > latest ? row.dos : latest, '')
  const latestYear = Number(latestDate.slice(0, 4))
  const priorYear = latestYear - 1
  const throughMonthDay = latestDate.slice(5)
  const providerClaims = providerAllClaims.filter((row) => Number(row.dos.slice(0, 4)) === latestYear)
  const priorClaims = providerAllClaims.filter((row) => (
    Number(row.dos.slice(0, 4)) === priorYear && row.dos.slice(5) <= throughMonthDay
  ))
  const totalAllowed = sum(providerClaims, 'allowed')
  const totalPaid = sum(providerClaims, 'paid')
  const priorAllowed = sum(priorClaims, 'allowed')
  const priorPaid = sum(priorClaims, 'paid')
  const denied = providerClaims.filter((row) => row.status === 'Denied').length
  const priorDenied = priorClaims.filter((row) => row.status === 'Denied').length
  const approvalRate = providerClaims.length ? ((providerClaims.length - denied) / providerClaims.length) * 100 : 0
  const denialRate = providerClaims.length ? (denied / providerClaims.length) * 100 : 0
  const reimbursementRate = totalAllowed ? (totalPaid / totalAllowed) * 100 : 0
  const priorApprovalRate = priorClaims.length ? ((priorClaims.length - priorDenied) / priorClaims.length) * 100 : 0
  const priorDenialRate = priorClaims.length ? (priorDenied / priorClaims.length) * 100 : 0
  const priorReimbursementRate = priorAllowed ? (priorPaid / priorAllowed) * 100 : 0
  const submissionLags = providerClaims
    .filter((row) => row.dos && row.submissionDate)
    .map((row) => Math.max(0, Math.round((new Date(`${row.submissionDate}T00:00:00`) - new Date(`${row.dos}T00:00:00`)) / 86_400_000)))
  const priorSubmissionLags = priorClaims
    .filter((row) => row.dos && row.submissionDate)
    .map((row) => Math.max(0, Math.round((new Date(`${row.submissionDate}T00:00:00`) - new Date(`${row.dos}T00:00:00`)) / 86_400_000)))
  const average = (values) => values.length ? values.reduce((total, value) => total + value, 0) / values.length : 0
  const percentDelta = (current, previous) => previous ? ((current - previous) / previous) * 100 : null
  const moneyDelta = (current, previous) => {
    const delta = percentDelta(current, previous)
    return delta === null ? null : `${delta >= 0 ? '+' : ''}${delta.toFixed(1)}%`
  }
  const pointsDelta = (current, previous) => `${current - previous >= 0 ? '+' : ''}${(current - previous).toFixed(1)} pts`
  const lag = average(submissionLags)
  const priorLag = average(priorSubmissionLags)

  return [
    { label: 'Total Paid', value: formatCompactCurrency(totalPaid), delta: moneyDelta(totalPaid, priorPaid) },
    { label: 'Claims Submitted', value: providerClaims.length.toLocaleString() },
    { label: 'Approval Rate', value: formatPercent(approvalRate), delta: pointsDelta(approvalRate, priorApprovalRate) },
    { label: 'Denial Rate', value: formatPercent(denialRate), delta: pointsDelta(denialRate, priorDenialRate), dir: denialRate <= priorDenialRate ? 'down' : 'up' },
    { label: 'Average Reimbursement %', value: formatPercent(reimbursementRate), delta: pointsDelta(reimbursementRate, priorReimbursementRate) },
    { label: 'Average Submission Lag', value: `${lag.toFixed(1)} days`, delta: `${lag - priorLag >= 0 ? '+' : ''}${(lag - priorLag).toFixed(1)} days`, dir: lag <= priorLag ? 'down' : 'up' },
  ]
}

function App() {
  const [activeView, setActiveView] = useState('home')
  const [activeNav, setActiveNav] = useState('home')
  const [selectedMemberId, setSelectedMemberId] = useState(null)
  const [selectedClaim, setSelectedClaim] = useState(null)
  const [selectedPredictionClaim, setSelectedPredictionClaim] = useState(null)
  const [searchQuery, setSearchQuery] = useState('')
  const [claimsData, setClaimsData] = useState([])
  const [workbookSource, setWorkbookSource] = useState(null)
  const [dataLoading, setDataLoading] = useState(true)
  const [dataError, setDataError] = useState('')
  const dataModel = useMemo(() => ({ ...buildDataModel(claimsData), workbookSource }), [claimsData, workbookSource])
  const routeInitializedRef = useRef(false)

  const setRouteState = (route, historyMode = 'push') => {
    const nextRoute = {
      activeView: route.activeView || 'home',
      activeNav: route.activeNav || getNavForView(route.activeView || 'home'),
      selectedMemberId: route.selectedMemberId || null,
      selectedClaim: route.selectedClaim || null,
      selectedPredictionClaim: route.selectedPredictionClaim || null,
    }

    setActiveView(nextRoute.activeView)
    setActiveNav(nextRoute.activeNav)
    setSelectedMemberId(nextRoute.selectedMemberId)
    setSelectedClaim(nextRoute.selectedClaim)
    setSelectedPredictionClaim(nextRoute.selectedPredictionClaim)

    if (historyMode) {
      const hash = routeToHash(nextRoute)
      if (window.location.hash !== hash) {
        if (historyMode === 'replace') {
          window.history.replaceState(null, '', hash)
        } else {
          window.history.pushState(null, '', hash)
        }
      }
    }
  }

  useEffect(() => {
    let active = true

    // Load the complete workbook list without running the expensive per-claim
    // prediction engine. Detailed financial calculations remain available from
    // the member, claim, and prediction endpoints when the user opens a record.
    fetchJson('/api/claims?limit=2000&includeFinancial=false&compact=true')
      .then((payload) => {
        if (!active) return
        const items = payload.items || []
        const source = payload.source || null
        setClaimsData(items)
        setWorkbookSource(source)
        writeClaimsCache(items, source)
        setDataError('')
      })
      .catch((apiError) => {
        if (!active) return
        setClaimsData([])
        setWorkbookSource(null)
        setDataError(apiError.message)
      })
      .finally(() => {
        if (active) setDataLoading(false)
      })

    return () => {
      active = false
    }
  }, [])

  useEffect(() => {
    if (dataLoading || dataError) return undefined

    const applyBrowserRoute = () => {
      setRouteState(routeFromHash(window.location.hash, claimsData), null)
    }

    if (!routeInitializedRef.current) {
      routeInitializedRef.current = true
      if (window.location.hash) {
        applyBrowserRoute()
      } else {
        setRouteState({
          activeView,
          activeNav,
          selectedMemberId,
          selectedClaim,
          selectedPredictionClaim,
        }, 'replace')
      }
    }

    window.addEventListener('popstate', applyBrowserRoute)
    window.addEventListener('hashchange', applyBrowserRoute)
    return () => {
      window.removeEventListener('popstate', applyBrowserRoute)
      window.removeEventListener('hashchange', applyBrowserRoute)
    }
  }, [dataLoading, dataError, claimsData, activeView, activeNav, selectedMemberId, selectedClaim, selectedPredictionClaim])

  const openClaimDetail = (claim) => {
    setRouteState({
      activeView: 'claims',
      activeNav: 'Claims',
      selectedClaim: claim,
    })
  }

  const openAllClaims = () => {
    setRouteState({
      activeView: 'claims',
      activeNav: 'Claims',
    })
  }

  const openMemberDetail = (memberId) => {
    setRouteState({
      activeView: 'member',
      activeNav: 'Patient 360',
      selectedMemberId: memberId,
    })
  }

  const backToEncounters = () => {
    setRouteState({
      activeView: 'member',
      activeNav: 'Patient 360',
    })
  }

  const updateSearchQuery = (value) => {
    setSearchQuery(value)
    if (activeView === 'predictions') {
      setSelectedPredictionClaim(null)
      return
    }
    if (value.trim()) {
      setSelectedMemberId(null)
      setSelectedClaim(null)
      if (activeView !== 'claims') {
        setRouteState({
          activeView: 'member',
          activeNav: 'Patient 360',
        })
      }
    }
  }

  const openPredictionDetail = (claim) => {
    setRouteState({
      activeView: 'predictions',
      activeNav: 'Predictions',
      selectedPredictionClaim: claim,
    })
  }

  const backToPredictions = () => {
    setRouteState({
      activeView: 'predictions',
      activeNav: 'Predictions',
    })
  }

  const navigate = (view, navKey = view) => {
    if (!VALID_VIEWS.has(view)) return
    setRouteState({
      activeView: view,
      activeNav: navKey,
    })
  }

  return (
    <DataContext.Provider value={dataModel}>
      <div className="app-shell">
        <Sidebar activeNav={activeNav} onNavigate={navigate} />
        <main className="workspace">
          {dataLoading ? (
            <>
              <TopBar />
              <section className="patient-page">
                <Card className="state-card">Loading the configured workbook database...</Card>
              </section>
            </>
          ) : dataError ? (
            <>
              <TopBar />
              <section className="patient-page">
                <Card className="state-card">
                  Unable to load claims from the backend API: {dataError}
                </Card>
              </section>
            </>
          ) : activeView === 'home' ? (
            <ExecutiveDashboard
              onOpenClaim={openClaimDetail}
              onViewAllClaims={openAllClaims}
            />
          ) : activeView === 'predictions' ? (
            <PredictionsWorkspace
              selectedClaim={selectedPredictionClaim}
              searchQuery={searchQuery}
              onOpenPrediction={openPredictionDetail}
              onBackToPredictions={backToPredictions}
            />
          ) : activeView === 'claims' ? (
            <ClaimsWorkspace
              selectedClaim={selectedClaim}
              searchQuery={searchQuery}
              onSearchChange={updateSearchQuery}
              onOpenClaim={openClaimDetail}
            />
          ) : (
            <PatientWorkspace
              selectedClaim={selectedClaim}
              selectedMemberId={selectedMemberId}
              searchQuery={searchQuery}
              onSearchChange={updateSearchQuery}
              onSelectMember={openMemberDetail}
              onOpenClaim={openClaimDetail}
              onOpenPrediction={openPredictionDetail}
              onBackToEncounters={backToEncounters}
            />
          )}
        </main>
      </div>
    </DataContext.Provider>
  )
}

function Sidebar({ activeNav, onNavigate }) {
  return (
    <aside className="sidebar">
      <button className="brand-button" type="button" onClick={() => onNavigate('home', 'home')}>
        <img className="brand-image" src="/claimsai-robot.png" alt="ClaimsAI home" />
        <span className="brand-primary">Claims</span>
        <span className="brand-accent">AI</span>
      </button>

      <button
        className={`nav-link home-link ${activeNav === 'home' ? 'active' : ''}`}
        type="button"
        onClick={() => onNavigate('home', 'home')}
      >
        <Home size={18} />
        Home
      </button>

      {navSections.map((section) => (
        <nav className="nav-section" key={section.title} aria-label={section.title}>
          <p className="nav-heading">{section.title}</p>
          {section.items.map((item) => {
            const Icon = item.icon
            const active = activeNav === item.label
            const enabled = CLICKABLE_NAV_LABELS.has(item.label)

            return (
              <button
                className={`nav-link ${active ? 'active' : ''} ${enabled ? '' : 'disabled'}`}
                type="button"
                aria-disabled={!enabled}
                disabled={!enabled}
                onClick={() => {
                  if (enabled) onNavigate(item.view, item.label)
                }}
                key={item.label}
              >
                <Icon size={17} />
                {item.label}
              </button>
            )
          })}
        </nav>
      ))}
    </aside>
  )
}

function TopBar() {
  return (
    <header className="topbar">
      <div className="topbar-welcome">
        <span>Welcome Back</span>
        <strong>Alex Admin</strong>
      </div>
      <div className="topbar-actions">
        <button className="icon-button has-alert" type="button" aria-label="Notifications">
          <Bell size={19} />
          <span>3</span>
        </button>
        <div className="user-chip">
          <span className="avatar">AA</span>
          <div>
            <strong>Alex Admin</strong>
            <span>Operations</span>
          </div>
          <ChevronDown size={18} />
        </div>
      </div>
    </header>
  )
}

function PatientWorkspace({ selectedClaim, selectedMemberId, searchQuery, onSearchChange, onSelectMember, onOpenClaim, onOpenPrediction, onBackToEncounters }) {
  const { membersById } = useAppData()
  const selectedMember = selectedMemberId ? membersById.get(selectedMemberId) : null

  return (
    <>
      <TopBar />
      <section className="patient-page">
        {selectedMember ? (
          <MemberDetail
            member={selectedMember}
            selectedClaim={selectedClaim}
            onBackToEncounters={onBackToEncounters}
            onSelectMember={onSelectMember}
            onOpenClaim={onOpenClaim}
            onOpenPrediction={onOpenPrediction}
          />
        ) : (
          <EncounterSearch
            searchQuery={searchQuery}
            onSearchChange={onSearchChange}
            onSelectMember={onSelectMember}
            onOpenClaim={onOpenClaim}
          />
        )}
      </section>
    </>
  )
}

function ClaimsWorkspace({ selectedClaim, searchQuery, onSearchChange, onOpenClaim }) {
  const { claimsData, defaultDateRange } = useAppData()
  const [timeFilter, setTimeFilter] = useState('All Time')
  const [currentPage, setCurrentPage] = useState(1)
  const pageSize = 10
  const normalizedQuery = searchQuery.trim().toLowerCase()
  const searchedClaims = useMemo(() => (
    normalizedQuery
      ? claimsData.filter((claim) => (
        claim.patient.toLowerCase().includes(normalizedQuery) ||
        claim.memberId.toLowerCase().includes(normalizedQuery)
      ))
      : claimsData
  ), [claimsData, normalizedQuery])
  const filteredClaims = useMemo(
    () => filterClaimsByTime(searchedClaims, timeFilter, defaultDateRange),
    [searchedClaims, timeFilter, defaultDateRange],
  )
  const pageCount = Math.max(1, Math.ceil(filteredClaims.length / pageSize))
  const safePage = Math.min(currentPage, pageCount)
  const pagedClaims = filteredClaims.slice((safePage - 1) * pageSize, safePage * pageSize)

  useEffect(() => {
    setCurrentPage(1)
  }, [searchQuery, timeFilter])

  return (
    <>
      <TopBar />
      <section className="claims-page">
        {selectedClaim ? (
          <ClaimDetailPage claim={selectedClaim} />
        ) : (
          <>
            <div className="claims-directory-header">
              <div>
                <h1>Claims</h1>
                <p>All 837 claim records from the current database</p>
              </div>
              <div className="claims-directory-controls">
                <label className="claims-directory-search">
                  <Search size={18} />
                  <input
                    type="search"
                    value={searchQuery}
                    onChange={(event) => onSearchChange(event.target.value)}
                    placeholder="Search claim"
                    aria-label="Search claims by patient name or member ID"
                  />
                </label>
                <label className="claims-time-filter">
                  <span>Time:</span>
                  <select value={timeFilter} onChange={(event) => setTimeFilter(event.target.value)}>
                    <option>All Time</option>
                    <option>Latest Month</option>
                    <option>Year to Date</option>
                  </select>
                  <ChevronDown size={16} />
                </label>
              </div>
            </div>
            <RecentClaims
              title="All Claims"
              claims={pagedClaims}
              onOpenClaim={onOpenClaim}
              emptyMessage="No claims match that patient name or member ID."
              footer={(
                <ClaimsTableFooter
                  currentPage={safePage}
                  pageCount={pageCount}
                  pageSize={pageSize}
                  totalCount={filteredClaims.length}
                  onPageChange={setCurrentPage}
                />
              )}
            />
          </>
        )}
      </section>
    </>
  )
}

function PredictionsWorkspace({ selectedClaim, searchQuery, onOpenPrediction, onBackToPredictions }) {
  const { claimsData, payerOptions } = useAppData()
  const [scenarios, setScenarios] = useState([])
  const [scenarioMeta, setScenarioMeta] = useState(null)
  const [scenarioSummary, setScenarioSummary] = useState(null)
  const [scenarioLoading, setScenarioLoading] = useState(true)
  const [scenarioError, setScenarioError] = useState('')
  const [riskFilter, setRiskFilter] = useState('All Scenarios')
  const [payerFilter, setPayerFilter] = useState('All Payers')
  const [sortBy, setSortBy] = useState('Highest Confidence')
  const [currentPage, setCurrentPage] = useState(1)
  const [payerModalOpen, setPayerModalOpen] = useState(false)
  const pageSize = 10
  const normalizedQuery = searchQuery.trim().toLowerCase()

  useEffect(() => {
    let cancelled = false
    setScenarioLoading(true)
    fetchJson('/api/predictions/scenarios')
      .then((payload) => {
        if (cancelled) return
        setScenarios(Array.isArray(payload.items) ? payload.items : [])
        setScenarioMeta({ ...payload.model, totalClaims: payload.totalClaims })
        setScenarioSummary(payload.summary || null)
        setScenarioError('')
      })
      .catch(() => {
        if (cancelled) return
        setScenarios([])
        setScenarioError('The Python prediction service could not be reached. Start the Flask backend and refresh this page.')
      })
      .finally(() => {
        if (!cancelled) setScenarioLoading(false)
      })
    return () => { cancelled = true }
  }, [claimsData.length])

  const filteredScenarios = useMemo(() => scenarios
    .filter((scenario) => {
      const matchesSearch = !normalizedQuery || [
        scenario.claim_id,
        scenario.member_id,
        scenario.actual_claim_facts?.payer,
        scenario.actual_claim_facts?.provider,
        scenario.actual_claim_facts?.diagnosis_description,
        scenario.actual_claim_facts?.diagnosis_code,
      ].some((value) => value?.toString().toLowerCase().includes(normalizedQuery))
      const matchesPayer = payerFilter === 'All Payers' || scenario.actual_claim_facts?.payer === payerFilter
      const matchesRisk = riskFilter === 'All Scenarios' || scenario.confidence?.level === riskFilter

      return matchesSearch && matchesPayer && matchesRisk
    })
    .sort((a, b) => {
      if (sortBy === 'Recoverable Now') return b.supported_money_summary.recoverable_now - a.supported_money_summary.recoverable_now
      if (sortBy === 'Highest Predicted Avoidable Spend') return b.predicted_avoidable_spend.value - a.predicted_avoidable_spend.value
      if (sortBy === 'Predicted Paid') return b.prediction.predicted_paid - a.prediction.predicted_paid
      if (sortBy === 'Newest Claim') return b.actual_claim_facts.service_date.localeCompare(a.actual_claim_facts.service_date)
      return b.confidence.score - a.confidence.score
    }), [scenarios, normalizedQuery, payerFilter, riskFilter, sortBy])

  const pageCount = Math.max(1, Math.ceil(filteredScenarios.length / pageSize))
  const safePage = Math.min(currentPage, pageCount)
  const displayedScenarios = useMemo(
    () => filteredScenarios.slice((safePage - 1) * pageSize, safePage * pageSize),
    [filteredScenarios, safePage],
  )

  useEffect(() => {
    setCurrentPage(1)
  }, [searchQuery, riskFilter, payerFilter, sortBy])

  return (
    <>
      <TopBar />
      <section className="predictions-page">
        {selectedClaim ? (
          <PredictionDetailPage claim={selectedClaim} onBackToPredictions={onBackToPredictions} />
        ) : (
          <>
            <div className="predictions-header">
              <div>
                <h1>Provider Case Predictions</h1>
                <p>Provider-focused payment forecasts, repeat-utilisation risk, and actionable claim opportunities.</p>
              </div>
              <div className="prediction-controls">
                <button className="payer-generate-button" type="button" onClick={() => setPayerModalOpen(true)}>
                  <Sparkles size={16} /> Generate Prediction
                </button>
                <label className="prediction-select">
                  <span>Risk</span>
                  <select value={riskFilter} onChange={(event) => setRiskFilter(event.target.value)}>
                    <option>All Scenarios</option>
                    <option>High</option><option>Medium</option><option>Low</option>
                  </select>
                  <ChevronDown size={16} />
                </label>
                <label className="prediction-select">
                  <span>Payer</span>
                  <select value={payerFilter} onChange={(event) => setPayerFilter(event.target.value)}>
                    {payerOptions.map((option) => (
                      <option key={option}>{option}</option>
                    ))}
                  </select>
                  <ChevronDown size={16} />
                </label>
                <label className="prediction-select">
                  <span>Sort</span>
                  <select value={sortBy} onChange={(event) => setSortBy(event.target.value)}>
                    <option>Highest Confidence</option>
                    <option>Recoverable Now</option>
                    <option>Highest Predicted Avoidable Spend</option>
                    <option>Predicted Paid</option>
                    <option>Newest Claim</option>
                  </select>
                  <ChevronDown size={16} />
                </label>
              </div>
            </div>

            {scenarioLoading ? <Card className="scenario-service-state"><RefreshCw className="spin" size={22} /> Building predictions from the claims database…</Card> : null}
            {scenarioError ? <Card className="scenario-service-state error"><Info size={22} /> {scenarioError}</Card> : null}
            {!scenarioLoading && !scenarioError ? (
              <>
                <PredictionSummary summary={scenarioSummary} />
                <PredictionScenarioDirectory
                  scenarios={displayedScenarios}
                  totalCount={filteredScenarios.length}
                  onOpenScenario={(scenario) => onOpenPrediction(claimsData.find((claim) => claim.claimId === scenario.claim_id))}
                  emptyMessage="No patient episodes match the current scenario filters."
                  footer={(
                    <ClaimsTableFooter
                      currentPage={safePage}
                      pageCount={pageCount}
                      pageSize={pageSize}
                      totalCount={filteredScenarios.length}
                      onPageChange={setCurrentPage}
                    />
                  )}
                />
                <PredictionMethodPanel totalCount={scenarioMeta?.totalClaims || claimsData.length} scenarioCount={filteredScenarios.length} model={scenarioMeta} />
              </>
            ) : null}
            {payerModalOpen ? (
              <PayerPredictionModal
                onClose={() => setPayerModalOpen(false)}
                onOpenProviderForecast={(claimId) => {
                  const targetClaim = claimsData.find((claim) => claim.claimId === claimId || claim.number === claimId)
                  if (targetClaim) onOpenPrediction(targetClaim)
                }}
              />
            ) : null}
          </>
        )}
      </section>
    </>
  )
}

const payerCurrency = (value) => new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(Number(value || 0))
const payerCurrencyOrDash = (value) => value == null ? '—' : payerCurrency(value)
const payerNumber = (value) => Number(value || 0).toLocaleString('en-US', { maximumFractionDigits: 1 })
const payerDate = (value) => {
  if (!value) return ''
  const [year, month, day] = value.slice(0, 10).split('-').map(Number)
  return new Intl.DateTimeFormat('en-AU', { day: '2-digit', month: 'short', year: 'numeric' }).format(new Date(year, month - 1, day))
}

function PayerPredictionModal({ onClose, onOpenProviderForecast }) {
  const [options, setOptions] = useState(null)
  const [memberId, setMemberId] = useState('')
  const [diseaseFamily, setDiseaseFamily] = useState('')
  const [episodeId, setEpisodeId] = useState('')
  const [result, setResult] = useState(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [completedSteps, setCompletedSteps] = useState(0)
  const [showAllEvidence, setShowAllEvidence] = useState(false)
  const steps = [
    'Finding comparison episode', 'Matching peer members', 'Selecting strongest scenario',
    'Building lower-utilisation benchmark', 'Calculating payer savings prediction',
    'Retrieving supporting evidence',
  ]

  useEffect(() => {
    const onKeyDown = (event) => { if (event.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKeyDown)
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    fetchJson('/api/predictions/payer/options')
      .then(setOptions)
      .catch((requestError) => setError(requestError.message || 'Prediction inputs could not be loaded.'))
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      document.body.style.overflow = previousOverflow
    }
  }, [onClose])

  useEffect(() => {
    if (!loading) return undefined
    setCompletedSteps(0)
    const interval = window.setInterval(() => setCompletedSteps((current) => Math.min(current + 1, steps.length - 1)), 420)
    return () => window.clearInterval(interval)
  }, [loading, steps.length])

  const selectedMember = options?.members?.find((member) => member.member_id === memberId)
  const diseases = selectedMember?.diseases || []
  const selectedDisease = diseases.find((disease) => disease.family === diseaseFamily)
  const episodes = selectedDisease?.episodes || []

  const generate = async () => {
    if (!memberId || !diseaseFamily || !episodeId) {
      setError('Select a member, disease family, and comparison episode before generating the prediction.')
      return
    }
    setLoading(true)
    setError('')
    setResult(null)
    setShowAllEvidence(false)
    try {
      const payload = await fetchJson('/api/predictions/payer/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ member_id: memberId, diagnosis_family: diseaseFamily, comparison_episode_id: episodeId }),
      })
      setCompletedSteps(steps.length)
      setResult(payload)
    } catch (requestError) {
      setError(requestError.message || 'The payer savings prediction could not be generated.')
    } finally {
      setLoading(false)
    }
  }

  const evidence = result?.supporting_evidence || []
  const visibleEvidence = showAllEvidence ? evidence : evidence.slice(0, 10)

  return createPortal(
    <div className="payer-modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}>
      <section className="payer-prediction-modal" role="dialog" aria-modal="true" aria-labelledby="payer-modal-title">
        <header className="payer-modal-header">
          <div><h2 id="payer-modal-title">Generate Prediction</h2><p>Rule-based payer savings prediction</p></div>
          <button className="payer-modal-close" type="button" aria-label="Close Generate Prediction" onClick={onClose}><X size={19} /></button>
        </header>

        <div className="payer-modal-body">
          {!result ? (
            <div className="payer-input-panel">
              <div className="payer-input-grid">
                <label><span>Member</span><select value={memberId} onChange={(event) => { setMemberId(event.target.value); setDiseaseFamily(''); setEpisodeId(''); setError('') }} disabled={!options || loading}><option value="">Select member</option>{options?.members?.map((member) => <option key={member.member_id} value={member.member_id}>{member.member_id}</option>)}</select></label>
                <label><span>Disease Family</span><select value={diseaseFamily} onChange={(event) => { setDiseaseFamily(event.target.value); setEpisodeId(''); setError('') }} disabled={!memberId || loading}><option value="">Select disease</option>{diseases.map((disease) => <option key={disease.family} value={disease.family}>{disease.family}{disease.description ? ` — ${disease.description}` : ''}</option>)}</select></label>
                <label><span>Comparison Episode</span><select value={episodeId} onChange={(event) => { setEpisodeId(event.target.value); setError('') }} disabled={!diseaseFamily || loading}><option value="">Select episode</option>{episodes.map((episode) => <option key={episode.episode_id} value={episode.episode_id}>{payerDate(episode.start_date)} – {payerDate(episode.end_date)} · {episode.claim_count} claims</option>)}</select></label>
              </div>
              <div className="payer-scenario-line"><span>Comparison Scenario</span><strong>Auto-select strongest valid scenario</strong></div>
              {loading ? <div className="payer-progress" aria-live="polite"><strong>Generating prediction…</strong>{steps.map((step, index) => <div key={step} className={index <= completedSteps ? 'done' : ''}>{index <= completedSteps ? <CheckCircle2 size={16} /> : <span className="payer-step-dot" />} {step}</div>)}</div> : null}
              {error ? <div className="payer-modal-error"><Info size={17} /> {error}</div> : null}
              {!loading ? <div className="payer-input-actions"><button className="payer-generate-button" type="button" onClick={generate} disabled={!options}><Sparkles size={16} /> Generate Prediction</button></div> : null}
            </div>
          ) : (
            <PayerPredictionResult result={result} evidence={visibleEvidence} evidenceCount={evidence.length} showAllEvidence={showAllEvidence} onShowAllEvidence={() => setShowAllEvidence(true)} />
          )}
        </div>

        <footer className="payer-modal-footer">
          <button className="payer-secondary-button" type="button" onClick={onClose}>Close</button>
          {result ? <button className="payer-secondary-button" type="button" onClick={() => { setResult(null); setError('') }}>New Prediction</button> : null}
          {result ? <button className="payer-secondary-button" type="button" onClick={() => onOpenProviderForecast(result.target?.claim_id)}>Open Separate Provider Forecast</button> : null}
        </footer>
      </section>
    </div>,
    document.body,
  )
}

function PayerPredictionResult({ result, evidence, evidenceCount, showAllEvidence, onShowAllEvidence }) {
  const benchmark = result.benchmark_summary || {}
  const scenario = result.scenario || {}
  const calculation = result.calculation_summary || {}
  const confidence = calculation.confidence || {}
  const trace = result.evidence_trace || {}
  return (
    <div className="payer-result">
      <section className="payer-result-section">
        <h3>Benchmark Summary</h3>
        <div className="payer-table-wrap"><table><thead><tr><th>Metric</th><th>Target Episode</th><th>Benchmark</th></tr></thead><tbody>
          <tr><td>Related Claims</td><td>{payerNumber(benchmark.target_claim_count)}</td><td>{payerNumber(benchmark.benchmark_claim_count)}</td></tr>
          <tr><td>Payer Paid Amount</td><td>{payerCurrency(benchmark.target_payer_spend)}</td><td>{payerCurrency(benchmark.benchmark_payer_spend)}</td></tr>
          <tr><td>Median Paid per Claim</td><td>{payerCurrency(benchmark.target_median_paid_per_claim)}</td><td>{payerCurrency(benchmark.benchmark_median_paid_per_claim)}</td></tr>
          <tr><td>Episode Duration</td><td>{payerNumber(benchmark.target_episode_duration)} days</td><td>{payerNumber(benchmark.benchmark_episode_duration)} days</td></tr>
        </tbody></table></div>
        <dl className="payer-summary-meta"><div><dt>Selected Scenario</dt><dd>Scenario {scenario.number} — {scenario.name}</dd></div><div><dt>Disease Family</dt><dd>{result.target?.diagnosis_family}</dd></div><div><dt>Peer Members</dt><dd>{scenario.peer_member_count}</dd></div><div><dt>Benchmark Method</dt><dd>{benchmark.benchmark_method}</dd></div></dl>
      </section>

      <section className="payer-result-section">
        <h3>Peer Members Used</h3>
        <div className="payer-table-wrap"><table><thead><tr><th>Member ID</th><th>Related Claims</th><th>Payer Spend</th><th>Similarity / Match</th><th>Role</th></tr></thead><tbody>{result.peer_members_used.map((peer) => <tr key={peer.member_id}><td>{peer.member_id}</td><td>{payerNumber(peer.related_claims)} claims</td><td>{payerCurrency(peer.payer_spend)}</td><td>{Math.round(peer.similarity * 100)}%</td><td>{peer.role}</td></tr>)}</tbody></table></div>
        <p className={`payer-peer-note ${scenario.peer_member_count === 1 ? 'low' : ''}`}>{scenario.peer_member_count === 1 ? '1 external peer used · Low confidence' : `${scenario.peer_member_count} different-member peers used`}</p>
      </section>

      <section className="payer-result-section">
        <h3>Prediction Range / Calculation Summary</h3>
        <div className="payer-calculation-overview"><div><span>Target Episode Payer Spend</span><strong>{payerCurrency(benchmark.target_payer_spend)}</strong></div><div><span>Benchmark Payer Spend</span><strong>{payerCurrency(benchmark.benchmark_payer_spend)}</strong></div><div><span>Target Related Claims</span><strong>{payerNumber(benchmark.target_claim_count)}</strong></div><div><span>Benchmark Related Claims</span><strong>{payerNumber(benchmark.benchmark_claim_count)}</strong></div><div><span>Excess Claims</span><strong>{payerNumber(calculation.excess_claim_count)}</strong></div></div>
        <div className="payer-formulas"><article><h4>Count-Based Estimate</h4><p>{payerNumber(calculation.excess_claim_count)} × {payerCurrency(calculation.median_peer_paid_per_claim)} = <strong>{payerCurrency(calculation.count_based_excess_spend)}</strong></p><small>Excess claims × median peer paid per claim</small></article><article><h4>Cost-Based Estimate</h4><p>{payerCurrency(benchmark.target_payer_spend)} − {payerCurrency(benchmark.benchmark_payer_spend)} = <strong>{payerCurrency(calculation.cost_based_excess_spend)}</strong></p><small>Target episode payer spend − benchmark payer spend</small></article></div>
        <div className="payer-final-value"><span>Predicted Payer Avoidable Spend</span><strong>{payerCurrency(calculation.predicted_payer_avoidable_spend)}</strong><small>Conservative estimate using the lower of the two rule-based methods</small>{calculation.zero_reason ? <p><b>Reason:</b> {calculation.zero_reason}</p> : null}</div>
        <div className="payer-range"><div><span>Prediction Range</span>{calculation.range ? <strong>{payerCurrency(calculation.range.low)} – {payerCurrency(calculation.range.high)}</strong> : <p>{calculation.range_reason}</p>}</div>{calculation.range ? <dl><div><dt>Q25 peer paid / claim</dt><dd>{payerCurrency(calculation.q25_peer_paid_per_claim)}</dd></div><div><dt>Median peer paid / claim</dt><dd>{payerCurrency(calculation.median_peer_paid_per_claim)}</dd></div><div><dt>Q75 peer paid / claim</dt><dd>{payerCurrency(calculation.q75_peer_paid_per_claim)}</dd></div></dl> : null}</div>
        <p className={`payer-confidence ${confidence.level?.toLowerCase()}`}>Confidence: {confidence.score}% · {confidence.level}</p>
        <p className="payer-confidence-basis">Based on: {scenario.peer_member_count} external peer members · {scenario.peer_claim_count} peer claims · Scenario {scenario.number} match · {confidence.dispersion}</p>
      </section>

      <section className="payer-result-section">
        <h3>Supporting Evidence</h3>
        <div className="payer-table-wrap evidence"><table><thead><tr><th>Claim ID</th><th>Member ID</th><th>Service Date</th><th>ICD-10</th><th>CPT</th><th>Payer</th><th>Provider</th><th>POS</th><th>Paid Amount</th><th>Evidence Role</th></tr></thead><tbody>{evidence.map((row, index) => <tr key={`${row.claim_id}-${index}`}><td>{row.claim_id}</td><td>{row.member_id}</td><td>{payerDate(row.service_date)}</td><td>{row.icd10}</td><td>{row.cpt}</td><td>{row.payer}</td><td>{row.provider}</td><td>{row.pos}</td><td>{payerCurrency(row.paid_amount)}</td><td>{row.evidence_role}</td></tr>)}</tbody></table></div>
        {!showAllEvidence && evidenceCount > evidence.length ? <button className="payer-evidence-button" type="button" onClick={onShowAllEvidence}>View All Supporting Evidence</button> : null}
        <p className="payer-evidence-trace">Prediction calculated from workbook data using: Scenario {trace.scenario} · {trace.peer_member_count} peer members · {trace.peer_claim_count} peer claims · Workbook source: {trace.source}</p>
      </section>
    </div>
  )
}

function PredictionDetailPage({ claim, onBackToPredictions }) {
  const [scenario, setScenario] = useState(null)
  const [caseError, setCaseError] = useState('')

  useEffect(() => {
    let cancelled = false
    setScenario(null)
    setCaseError('')
    fetchJson(`/api/predictions/provider-case/${encodeURIComponent(claim.claimId || claim.number)}`)
      .then((payload) => {
        if (!cancelled) setScenario(payload || null)
      })
      .catch(() => {
        if (!cancelled) setCaseError('Unable to build this provider case prediction from the current claim data.')
      })
    return () => { cancelled = true }
  }, [claim.number, claim.claimId])

  if (caseError) {
    return (
      <Card className="scenario-service-state error">
        <Info size={22} /> {caseError}
        <button className="back-link" type="button" onClick={onBackToPredictions}>Back to predictions</button>
      </Card>
    )
  }

  if (!scenario) {
    return <Card className="scenario-service-state"><RefreshCw className="spin" size={22} /> Building provider case prediction…</Card>
  }

  return (
    <>
      <div className="patient-header-row prediction-detail-nav">
        <button className="back-link" type="button" onClick={onBackToPredictions}>
          <ArrowLeft size={16} />
          Back to Predictions
        </button>
        <div className="data-stamp">
          {scenario.historical_comparison.sample_size.toLocaleString()} earlier matched claims · Evidence strength: {scenario.confidence.level} ({formatProbability(scenario.confidence.score)})
          <RefreshCw size={15} />
        </div>
      </div>
      <PredictionScenarioMap scenario={scenario} />
    </>
  )
}

function PredictionScenarioDirectory({ scenarios, totalCount, onOpenScenario, emptyMessage, footer }) {
  return (
    <Card className="scenario-directory">
      <div className="scenario-directory-heading">
        <div>
          <span className="section-kicker">Episode worklist</span>
          <h2>Provider case predictions</h2>
          <p>{totalCount.toLocaleString()} provider-focused episodes built from diagnosis, utilisation, payer, and payment history.</p>
        </div>
        <div className="scenario-view-legend" aria-label="Scenario viewpoints">
          <span><Hospital size={15} /> Provider perspective</span>
        </div>
      </div>

      {scenarios.length ? (
        <div className="scenario-card-grid">
          {scenarios.map((scenario) => (
            <article className="scenario-card scenario-workbook" key={scenario.claim_id}>
              <header className="scenario-card-header">
                <div className="scenario-condition-icon"><ShieldCheck size={24} /></div>
                <div>
                  <span>{scenario.episode_id} · {scenario.actual_claim_facts.diagnosis_code}</span>
                  <h3>{scenario.actual_claim_facts.diagnosis_description}</h3>
                  <p>{scenario.claim_id} · {scenario.member_id}</p>
                </div>
                <span className="risk-badge medium">{scenario.financial_prediction_snapshot.confidence.level} · {formatProbability(scenario.financial_prediction_snapshot.confidence.score)}</span>
              </header>

              <div className="scenario-card-views">
                <div>
                  <span><Hospital size={15} /> Provider</span>
                  <strong>{scenario.actual_claim_facts.provider}</strong>
                  <small>{scenario.actual_claim_facts.payer}</small>
                </div>
                <div>
                  <span><Banknote size={15} /> Financial forecast</span>
                  <strong>{formatOptionalCurrency(scenario.financial_prediction_snapshot.predicted_provider_payment.value)} predicted paid</strong>
                  <small>{formatOptionalCurrency(scenario.financial_prediction_snapshot.predicted_allowed.value)} predicted allowed</small>
                </div>
                <div>
                  <span><Target size={15} /> Provider opportunity</span>
                  <strong>{formatOptionalCurrency(scenario.supported_money_summary.recoverable_now)} recoverable now</strong>
                  <small>{scenario.supported_money_summary.best_action.stage}</small>
                </div>
              </div>

              <div className="scenario-card-footer">
                <div>
                  <span>Predicted avoidable spend</span>
                  <strong>{formatOptionalCurrency(scenario.predicted_avoidable_spend.value)}</strong>
                  <small>90-day repeat probability {formatProbability(scenario.predicted_avoidable_spend.repeat_probability_90d)} · Confidence {formatProbability(scenario.predicted_avoidable_spend.confidence)}</small>
                </div>
                <button type="button" onClick={() => onOpenScenario?.(scenario)}>
                  Open scenario <ArrowRight size={16} />
                </button>
              </div>
            </article>
          ))}
        </div>
      ) : <div className="empty-state">{emptyMessage}</div>}

      {footer}
    </Card>
  )
}

function DetailedClaimFinancialBreakdown({ scenario, facts, snapshot, summary }) {
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
  const patientUnpaid = facts.outstanding_patient_balance || 0

  return (
    <div className="financial-responsibility-breakdown-card">
      <div className="resp-breakdown-header">
        <div className="resp-title">
          <DollarSign size={20} className="resp-icon" />
          <div>
            <h3>Claim money: recorded amounts and forecasts</h3>
            <span className="resp-subtitle">Recorded payer and patient amounts, plus the separate {formatOptionalCurrency(avoidable.value)} repeat-cost forecast</span>
          </div>
        </div>
        <div className="resp-tab-nav">
          <button
            type="button"
            className={`resp-tab-btn ${activeTab === 'overview' ? 'active' : ''}`}
            onClick={() => setActiveTab('overview')}
          >
            Who paid what
          </button>
          <button
            type="button"
            className={`resp-tab-btn ${activeTab === 'avoidable_math' ? 'active' : ''}`}
            onClick={() => setActiveTab('avoidable_math')}
          >
            How {formatOptionalCurrency(avoidable.value)} was calculated
          </button>
          <button
            type="button"
            className={`resp-tab-btn ${activeTab === 'avoid_action' ? 'active' : ''}`}
            onClick={() => setActiveTab('avoid_action')}
          >
            What needs human review
          </button>
          <button
            type="button"
            className={`resp-tab-btn ${activeTab === 'evidence_claims' ? 'active' : ''}`}
            onClick={() => setActiveTab('evidence_claims')}
          >
            Earlier comparison claims ({similarClaims.length})
          </button>
        </div>
      </div>

      {activeTab === 'overview' && (
        <div className="tab-pane-content">
          <div className="financial-waterfall-bar-wrap">
            <div className="waterfall-header">
              <span className="waterfall-title">Where the {formatOptionalCurrency(billed)} billed amount went</span>
              <span className="waterfall-discount-pill">Automatic insurance discount: -{formatOptionalCurrency(discount)}</span>
            </div>
            <div className="waterfall-progress-bar">
              <div className="bar-segment bar-payer" style={{ width: `${billed > 0 ? (paid / billed) * 100 : 0}%` }} title={`Payer Paid: ${formatOptionalCurrency(paid)}`}>
                <span>Insurance paid: {formatOptionalCurrency(paid)}</span>
              </div>
              <div className="bar-segment bar-patient" style={{ width: `${billed > 0 ? (patientResp / billed) * 100 : 0}%` }} title={`Patient: ${formatOptionalCurrency(patientResp)}`}>
                <span>Patient assigned: {formatOptionalCurrency(patientResp)}</span>
              </div>
              <div className="bar-segment bar-discount" style={{ width: `${billed > 0 ? (discount / billed) * 100 : 0}%` }} title={`Discount: ${formatOptionalCurrency(discount)}`}>
                <span>Discount: -{formatOptionalCurrency(discount)}</span>
              </div>
            </div>
          </div>

          <div className="resp-columns-grid">
            <div className="resp-column-box billed-box">
              <span className="box-kicker">1. What the provider charged</span>
              <div className="box-main-num">
                <span>Starting billed price</span>
                <PlainTooltip text="This is the provider's starting price before insurance contract rules were applied."><strong>{formatOptionalCurrency(billed)}</strong></PlainTooltip>
              </div>
              <div className="box-sub-items">
                <div className="sub-row">
                  <span>Insurance-agreed price:</span>
                  <PlainTooltip text="This is the amount the health plan recognizes for the service. It is often called the allowed amount."><strong>{formatOptionalCurrency(allowed)}</strong></PlainTooltip>
                </div>
                <div className="sub-row discount-row">
                  <span>Automatic insurance discount:</span>
                  <strong className="success-text">-{formatOptionalCurrency(discount)}</strong>
                </div>
                <div className="sub-row">
                  <span>Price reduction:</span>
                  <span>{billed > 0 ? ((discount / billed) * 100).toFixed(1) : 0}% below the billed price</span>
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
              <span className="box-kicker">2. What the insurance company paid</span>
              <div className="box-main-num">
                <span>Recorded insurance payment</span>
                <PlainTooltip text="This is the payer-paid amount stored on the claim. It is money recorded as paid, not the model's estimate."><strong className="payer-highlight">{formatOptionalCurrency(paid)}</strong></PlainTooltip>
              </div>
              <div className="box-sub-items">
                <div className="sub-row">
                  <span>Estimated payment from earlier claims:</span>
                  <strong>{formatOptionalCurrency(snapshot.predicted_provider_payment?.value)}</strong>
                </div>
                <div className="sub-row">
                  <span>Insurance company:</span>
                  <span>{facts.payer || 'Not recorded'}</span>
                </div>
                <div className="sub-row">
                  <span>Recorded payment as a share of the agreed price:</span>
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
              <span className="box-kicker">3. What the patient owes</span>
              <div className="box-main-num">
                <span>Amount assigned to the patient</span>
                <PlainTooltip text="This is the amount the workbook assigns to the patient, including deductible, copay, or coinsurance components."><strong className="patient-highlight">{formatOptionalCurrency(patientResp)}</strong></PlainTooltip>
              </div>
              <div className="box-sub-items">
                <div className="sub-row">
                  <span>Patient already paid:</span>
                  <strong className="paid-amount">{formatOptionalCurrency(patientPaid)} ({patientResp > 0 ? ((patientPaid / patientResp) * 100).toFixed(0) : 0}%)</strong>
                </div>
                <div className="sub-row unpaid-row">
                  <span>Amount still owed:</span>
                  <PlainTooltip text="This is the recorded patient amount minus patient payments received. Staff should confirm it before contacting the patient."><strong className="warning-text">{formatOptionalCurrency(patientUnpaid)}</strong></PlainTooltip>
                </div>
                <div className="sub-row">
                  <span>How long it has been outstanding:</span>
                  <span className="badge-broken-plan">{facts.days_outstanding ?? 0} days · {facts.payment_plan_status || facts.balance_status || 'Status not recorded'}</span>
                </div>
              </div>
              <details className="box-provenance-explainer">
                <summary>Show where these numbers came from</summary>
                <div>
                  <p><strong>Amount assigned to the patient:</strong> Copied from the claim's recorded patient-responsibility field. It is not calculated as agreed price minus insurance payment.</p>
                  <p><strong>Amount still owed:</strong> {formatOptionalCurrency(patientResp)} recorded patient amount − {formatOptionalCurrency(patientPaid)} patient payments = {formatOptionalCurrency(patientUnpaid)} outstanding balance.</p>
                </div>
              </details>
            </div>

            <div className="resp-column-box avoidable-box">
              <span className="box-kicker">4. Potential future savings</span>
              <div className="box-main-num">
                <span>Possible savings if an avoidable repeat is prevented</span>
                <PlainTooltip text="This is an average estimate across similar cases. It is not a confirmed saving for this patient."><strong className="avoidable-highlight">{formatOptionalCurrency(avoidable.value)}</strong></PlainTooltip>
              </div>
              <div className="box-sub-items">
                <div className="sub-row formula-row">
                  <span>How we calculated this:</span>
                  <code>{formatProbability(avoidable.repeat_probability_90d)} × {formatProbability(avoidable.avoidable_given_repeat_probability)} × {formatOptionalCurrency(avoidable.expected_extra_repeat_allowed_cost)}</code>
                </div>
                <div className="sub-row">
                  <span>What the forecast assumes:</span>
                  <span className="avoid-tip">A related claim could happen within three months. A reviewer must decide whether it would actually be avoidable.</span>
                </div>
              </div>
              <details className="box-provenance-explainer">
                <summary>Show the math and where it came from</summary>
                <div>
                  <p><strong>{formatProbability(avoidable.repeat_probability_90d)} chance of another related claim:</strong> Estimated from earlier claim timing.</p>
                  <p><strong>{formatProbability(avoidable.avoidable_given_repeat_probability)} with avoidability evidence:</strong> The share of earlier repeats carrying the workbook's avoidability signals. This is not a medical-necessity decision.</p>
                  <p><strong>{formatOptionalCurrency(avoidable.expected_extra_repeat_allowed_cost)} possible extra cost:</strong> The historical cost estimate used if such a repeat occurs.</p>
                  <p><strong>Calculation:</strong> {formatProbability(avoidable.repeat_probability_90d)} × {formatProbability(avoidable.avoidable_given_repeat_probability)} × {formatOptionalCurrency(avoidable.expected_extra_repeat_allowed_cost)} = {formatOptionalCurrency(avoidable.value)} average per similar case.</p>
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

function PlainTooltip({ text, children }) {
  const [open, setOpen] = useState(false)
  return (
    <span className={`plain-tooltip ${open ? 'is-open' : ''}`}>
      <span className="plain-tooltip-content">{children}</span>
      <button
        type="button"
        aria-label="What does this mean?"
        aria-expanded={open}
        onClick={() => setOpen((current) => !current)}
      >
        <Info size={13} />
      </button>
      <span className="plain-tooltip-popover" role="tooltip">{text}</span>
    </span>
  )
}

function PlainLanguageClaimNarrative({ scenario, facts, summary, snapshot, historicalPeerCount }) {
  const peers = scenario.similar_historical_claims || []
  const avoidable = snapshot.predicted_avoidable_spend || {}
  const billed = facts.charge || 0
  const allowed = facts.allowed || 0
  const discount = Math.max(0, billed - allowed)
  const discountPercent = billed > 0 ? (discount / billed) * 100 : 0
  const patientPaid = facts.patient_payment_received || 0
  const patientBalance = facts.outstanding_patient_balance || 0
  const peerAllowedValues = peers.map((peer) => peer.allowed).filter(Number.isFinite)
  const lowestPeerAllowed = peerAllowedValues.length ? Math.min(...peerAllowedValues) : null
  const peerDifference = Number.isFinite(lowestPeerAllowed) ? Math.max(0, allowed - lowestPeerAllowed) : null
  const serviceName = facts.cpt_description || 'a healthcare service'
  const action = summary.best_action || {}

  return (
    <section className="plain-claim-narrative" aria-labelledby="plain-claim-narrative-title">
      <header>
        <span>Start here</span>
        <h2 id="plain-claim-narrative-title">What happened, what the estimate means, and what to do</h2>
      </header>

      <div className="claim-story-grid">
        <article>
          <span className="story-number">1</span>
          <div>
            <h3>What happened</h3>
            <p>The patient received <strong>{serviceName}</strong> at {facts.provider || 'the provider'} on {formatDate(facts.service_date)}. The provider billed <strong>{formatOptionalCurrency(billed)}</strong>. The health plan recorded an insurance-agreed price of <strong>{formatOptionalCurrency(allowed)}</strong>, which is {formatOptionalCurrency(discount)} ({discountPercent.toFixed(1)}%) below the billed price.</p>
            <p>The workbook records <strong>{formatOptionalCurrency(facts.paid)}</strong> paid by {facts.payer || 'the insurance company'} and <strong>{formatOptionalCurrency(facts.patient_responsibility)}</strong> assigned to the patient. It also records {formatOptionalCurrency(patientPaid)} already paid by the patient and <strong>{formatOptionalCurrency(patientBalance)} still to verify</strong>.</p>
          </div>
        </article>

        <article>
          <span className="story-number">2</span>
          <div>
            <h3>What the prediction says</h3>
            <p>The system used <strong>{historicalPeerCount} earlier matched claims</strong> to estimate that the provider payment may be {formatOptionalCurrency(snapshot.predicted_provider_payment?.value)}.</p>
            <p>It also estimates a <strong>{formatProbability(avoidable.repeat_probability_90d)}</strong> chance of another related claim within three months. After allowing for repeats with avoidability evidence, the average possible repeat cost is <strong>{formatOptionalCurrency(avoidable.value)}</strong> per similar case. This is not an exact bill or guaranteed saving.</p>
          </div>
        </article>

        <article>
          <span className="story-number">3</span>
          <div>
            <h3>What someone should do</h3>
            <p>{action.action || 'Review the recorded claim information before taking action.'}</p>
            <p>The amount connected to that review is <strong>{formatOptionalCurrency(summary.recoverable_now)}</strong>. A person must confirm the source records first.</p>
          </div>
        </article>
      </div>

      {Number.isFinite(lowestPeerAllowed) ? (
        <p className="plain-peer-comparison"><strong>Price comparison:</strong> Among the {peers.length} claims selected for comparison, the lowest insurance-agreed price was {formatOptionalCurrency(lowestPeerAllowed)}. That is {formatOptionalCurrency(peerDifference)} below this claim's {formatOptionalCurrency(allowed)} insurance-agreed price. This difference is a review clue, not proof that this claim was overpriced; payer, provider, location, contract, and service details may differ.</p>
      ) : null}

      <div className="narrative-guardrails">
        <p><strong>Keep the numbers separate:</strong> The {formatOptionalCurrency(summary.recoverable_now)} current amount, {formatOptionalCurrency(avoidable.value)} repeat-cost estimate, and {formatOptionalCurrency(summary.future_denial_exposure)} denial-risk estimate answer different questions. Do not add them together.</p>
        <p><strong>Demo-data note:</strong> The workbook marks added patient-payment fields and historical reference rows as synthetic illustrative data. Production use requires real claims, remittance, billing, and collections feeds.</p>
      </div>

      <details className="plain-glossary-toggle">
        <summary>Plain-English definitions for terms used below</summary>
        <dl>
          <div><dt>Insurance-agreed price</dt><dd>The maximum amount the health plan recognizes for the service. It is often called the allowed amount.</dd></div>
          <div><dt>Patient responsibility</dt><dd>The amount assigned to the patient, such as a deductible, copay, or coinsurance.</dd></div>
          <div><dt>Repeat claim</dt><dd>Another related claim within three months. It is not automatically unnecessary or a duplicate.</dd></div>
          <div><dt>Denial risk</dt><dd>An estimate of money that may be at risk if insurance denies the claim. It is not a confirmed loss.</dd></div>
        </dl>
      </details>
    </section>
  )
}

function getOrganSystemInfo(icdCode = '', description = '') {
  const code = String(icdCode || '').toUpperCase().trim()
  const desc = String(description || '').toLowerCase()

  if (code.startsWith('Z01.4') || code.startsWith('Z13.6') || code.startsWith('N8') || code.startsWith('N9') || desc.includes('gynecolog') || desc.includes('pelvic') || desc.includes('pap')) {
    return {
      organSystem: 'Reproductive & Gynecological System',
      organIcon: '🌸',
      organBadge: 'Reproductive & Pelvic Health',
      primaryOrgans: 'Uterus, Ovaries, Cervix, Pelvic Region',
      clinicalCategory: 'Gynecological & Preventive Care',
      tag: 'gynecology_reproductive',
    }
  }
  if (code.startsWith('I') || desc.includes('cardio') || desc.includes('heart') || desc.includes('hypertens') || desc.includes('vascular')) {
    return {
      organSystem: 'Cardiovascular System',
      organIcon: '❤️',
      organBadge: 'Heart & Blood Vessels',
      primaryOrgans: 'Heart, Arteries, Circulatory System',
      clinicalCategory: 'Cardiology & Vascular Medicine',
      tag: 'cardiovascular',
    }
  }
  if (code.startsWith('E') || desc.includes('diabet') || desc.includes('thyroid') || desc.includes('metabol')) {
    return {
      organSystem: 'Endocrine & Metabolic System',
      organIcon: '🩸',
      organBadge: 'Endocrine & Metabolism',
      primaryOrgans: 'Pancreas, Thyroid, Hormone Regulation',
      clinicalCategory: 'Endocrinology & Metabolism',
      tag: 'endocrine',
    }
  }
  if (code.startsWith('M') || desc.includes('musculo') || desc.includes('bone') || desc.includes('joint') || desc.includes('spine') || desc.includes('arthr')) {
    return {
      organSystem: 'Musculoskeletal System',
      organIcon: '🦴',
      organBadge: 'Bones & Joints',
      primaryOrgans: 'Spine, Joints, Muscles, Skeletal Framework',
      clinicalCategory: 'Orthopedics & Rheumatology',
      tag: 'musculoskeletal',
    }
  }
  if (code.startsWith('J') || desc.includes('respirat') || desc.includes('lung') || desc.includes('asthma') || desc.includes('copd')) {
    return {
      organSystem: 'Respiratory System',
      organIcon: '🫁',
      organBadge: 'Lungs & Airways',
      primaryOrgans: 'Lungs, Bronchial Pathways, Upper Airway',
      clinicalCategory: 'Pulmonology & Respiratory Care',
      tag: 'respiratory',
    }
  }
  if (code.startsWith('K') || desc.includes('gastro') || desc.includes('digest') || desc.includes('stomach') || desc.includes('colon')) {
    return {
      organSystem: 'Gastrointestinal System',
      organIcon: '🩺',
      organBadge: 'Digestive Organs',
      primaryOrgans: 'Stomach, Intestines, Liver, Colon',
      clinicalCategory: 'Gastroenterology',
      tag: 'digestive',
    }
  }
  if (code.startsWith('N0') || code.startsWith('N1') || code.startsWith('N2') || code.startsWith('N3') || desc.includes('renal') || desc.includes('kidney') || desc.includes('urinar')) {
    return {
      organSystem: 'Renal & Urinary System',
      organIcon: '💧',
      organBadge: 'Kidneys & Bladder',
      primaryOrgans: 'Kidneys, Bladder, Urinary Tract',
      clinicalCategory: 'Nephrology & Urology',
      tag: 'renal_urinary',
    }
  }
  return {
    organSystem: 'General Healthcare & Prevention',
    organIcon: '⚕️',
    organBadge: 'General Medical System',
    primaryOrgans: 'General Physical & Routine Care',
    clinicalCategory: 'Preventive Primary Care',
    tag: 'general_care',
  }
}

function OrganSystemComparativeSavingsCard({ scenario, facts }) {
  const targetOrgan = getOrganSystemInfo(facts.diagnosis_code, facts.diagnosis_description)

  // Strictly filter peer claims to only compare against peer members with the matching organ system
  const organMatchedPeers = useMemo(() => {
    return (scenario.similar_historical_claims || []).filter((peer) => {
      const peerOrgan = getOrganSystemInfo(peer.icd10, peer.match_reason)
      return peerOrgan.tag === targetOrgan.tag || (peer.icd10 && peer.icd10.slice(0, 3) === (facts.diagnosis_code || '').slice(0, 3))
    })
  }, [scenario.similar_historical_claims, targetOrgan.tag, facts.diagnosis_code])

  const [selectedPeerId, setSelectedPeerId] = useState(null)
  const [showAllPeers, setShowAllPeers] = useState(false)
  const orderedPeers = useMemo(
    () => [...organMatchedPeers].sort((a, b) => (a.allowed || 0) - (b.allowed || 0)),
    [organMatchedPeers],
  )

  const activePeer = useMemo(() => {
    if (!orderedPeers.length) return null
    if (selectedPeerId) {
      const found = orderedPeers.find((p) => p.claim_id === selectedPeerId)
      if (found) return found
    }
    return orderedPeers[0]
  }, [orderedPeers, selectedPeerId])

  const memberAllowed = facts.allowed || 0
  const memberPaid = facts.paid || 0
  const memberPatient = facts.patient_responsibility || 0

  const peerAllowed = activePeer?.allowed || 0
  const peerPaid = activePeer?.paid || 0
  const peerPatient = Math.max(0, peerAllowed - peerPaid)

  const savingsAmount = Math.max(0, memberAllowed - peerAllowed)
  const savingsPercent = memberAllowed > 0 ? ((savingsAmount / memberAllowed) * 100).toFixed(1) : '0.0'
  const payerSavings = Math.max(0, memberPaid - peerPaid)
  const patientSavings = Math.max(0, memberPatient - peerPatient)

  const peerOrgan = activePeer ? getOrganSystemInfo(activePeer.icd10, activePeer.match_reason) : targetOrgan
  const visiblePeers = showAllPeers ? orderedPeers : orderedPeers.slice(0, 3)

  return (
    <section className="organ-comparative-savings-section" aria-labelledby="organ-savings-heading">
      <div className="organ-savings-header">
        <div className="organ-header-left">
          <div className="organ-title-badge">
            <span className="organ-icon">{targetOrgan.organIcon}</span>
            <span>PRICE COMPARISON WITH EARLIER CLAIMS</span>
          </div>
          <h3 id="organ-savings-heading">How this claim's insurance price compares with another claim</h3>
          <p className="organ-subtitle">
            The comparison uses earlier claims in the same diagnosis grouping. It shows price variation for review; it does not prove that either service was unnecessary or incorrectly priced.
          </p>
        </div>
        <div className="organ-scope-pill">
          <span>Type of care:</span>
          <strong>{targetOrgan.organBadge}</strong>
        </div>
      </div>

      {activePeer ? (
        <>
          <p className="organ-comparison-one-liner">Another selected claim has an insurance-agreed price that is {formatOptionalCurrency(savingsAmount)} lower. Here is what was compared.</p>
          <div className="organ-savings-comparator-grid">
            <div className="member-comparator-card member-a-card">
              <div className="comp-card-kicker">THIS PATIENT'S VISIT</div>
              <div className="comp-member-identity">
                <h4>This patient</h4>
                <span className="comp-claim-badge">Claim {scenario.claim_id}</span>
              </div>
              <div className="comp-meta-rows">
                <div className="comp-row">
                  <span>Type of care:</span>
                  <strong>{targetOrgan.organIcon} {targetOrgan.organSystem}</strong>
                </div>
                <div className="comp-row">
                  <span>Recorded condition:</span>
                  <small>{facts.diagnosis_description || 'Description not recorded'}<br />Medical code {facts.diagnosis_code || 'not recorded'}</small>
                </div>
                <div className="comp-row">
                  <span>Recorded service:</span>
                  <small>{facts.cpt_description || 'Description not recorded'}<br />Billing code {facts.cpt_code || 'not recorded'}</small>
                </div>
                <div className="comp-row">
                  <span>Date of service:</span>
                  <span>{facts.service_date}</span>
                </div>
              </div>
              <div className="comp-financial-summary">
                <div className="comp-money-row">
                  <span>Starting billed price:</span>
                  <span>{formatOptionalCurrency(facts.charge)}</span>
                </div>
                <div className="comp-money-row highlight-allowed">
                  <span>Insurance price:</span>
                  <PlainTooltip text="The maximum amount this health plan recognized for the service."><strong>{formatOptionalCurrency(memberAllowed)}</strong></PlainTooltip>
                </div>
                <div className="comp-money-row">
                  <span>Insurance paid:</span>
                  <span>{formatOptionalCurrency(memberPaid)}</span>
                </div>
                <div className="comp-money-row">
                  <span>Amount assigned to patient:</span>
                  <span>{formatOptionalCurrency(memberPatient)}</span>
                </div>
              </div>
            </div>

            <div className="member-comparator-card savings-middle-card">
              <div className="savings-middle-badge">HOW MUCH LOWER WAS THE OTHER PRICE?</div>
              <span className="savings-middle-label">Insurance-price difference to review</span>
              <div className="savings-hero-amount">
                <strong>{formatOptionalCurrency(savingsAmount)}</strong>
                <span className="savings-percent-tag">{savingsPercent}% lower</span>
              </div>
              <p className="savings-equation-text">
                This claim's insurance price ({formatOptionalCurrency(memberAllowed)}) − other claim's price ({formatOptionalCurrency(peerAllowed)})
              </p>
              <div className="savings-split-pills">
                <div className="split-pill payer-split">
                  <span>Difference in insurance payment</span>
                  <strong>{formatOptionalCurrency(payerSavings)}</strong>
                </div>
                <div className="split-pill patient-split">
                  <span>Difference in patient amount</span>
                  <strong>{formatOptionalCurrency(patientSavings)}</strong>
                </div>
              </div>
              <div className="savings-guarantee-note">
                <Info size={16} />
                <span>Comparison only. Differences in contracts or service details may explain the lower price.</span>
              </div>
            </div>

            <div className="member-comparator-card member-b-card">
              <div className="comp-card-kicker">ANOTHER PATIENT'S CLAIM</div>
              <div className="comp-member-identity">
                <h4>Comparison claim</h4>
                <span className="comp-claim-badge peer-badge">{activePeer.claim_id}</span>
              </div>
              <div className="comp-meta-rows">
                <div className="comp-row">
                  <span>Type of care:</span>
                  <strong>{peerOrgan.organIcon} {peerOrgan.organSystem}</strong>
                </div>
                <div className="comp-row">
                  <span>Recorded condition:</span>
                  <small>{peerOrgan.clinicalCategory}<br />Medical code {activePeer.icd10}</small>
                </div>
                <div className="comp-row">
                  <span>Recorded service:</span>
                  <small>Billing code {activePeer.cpt}</small>
                </div>
                <div className="comp-row">
                  <span>Date of service:</span>
                  <span>{activePeer.service_date}</span>
                </div>
              </div>
              <div className="comp-financial-summary">
                <div className="comp-money-row">
                  <span>Starting billed price:</span>
                  <span>{formatOptionalCurrency(activePeer.charge)}</span>
                </div>
                <div className="comp-money-row highlight-allowed-peer">
                  <span>Insurance price:</span>
                  <PlainTooltip text="The maximum amount the other health plan recognized for this claim."><strong>{formatOptionalCurrency(peerAllowed)}</strong></PlainTooltip>
                </div>
                <div className="comp-money-row">
                  <span>Insurance paid:</span>
                  <span>{formatOptionalCurrency(peerPaid)}</span>
                </div>
                <div className="comp-money-row">
                  <span>Estimated patient amount:</span>
                  <span>{formatOptionalCurrency(peerPatient)}</span>
                </div>
              </div>
            </div>
          </div>

          <div className="organ-savings-drivers-card">
            <div className="drivers-title">
              <Info size={18} />
              <h4>What this comparison can and cannot tell us</h4>
            </div>
            <div className="drivers-grid">
              <div className="driver-box">
                <strong>What it shows</strong>
                <p>The selected comparison claim has an insurance-agreed price of {formatOptionalCurrency(peerAllowed)}, which is {formatOptionalCurrency(savingsAmount)} below this claim.</p>
              </div>
              <div className="driver-box">
                <strong>What must be checked</strong>
                <p>Payer, provider, place of service, contract terms, coding, and the work performed may differ. The price difference is not confirmed savings until those details are reviewed.</p>
              </div>
            </div>
          </div>

          <div className="organ-peers-table-wrap">
            <div className="peers-table-header">
              <h4>Earlier claims available for comparison ({organMatchedPeers.length})</h4>
              <p>Three are shown by default. Select a row to update the comparison above.</p>
            </div>
            <div className="organ-table-scroll">
              <table className="organ-peers-table">
                <thead>
                  <tr>
                    <th>Select</th>
                    <th>Claim ID</th>
                    <th>Date</th>
                    <th>Medical code</th>
                    <th>Billing code</th>
                    <th>Provider</th>
                    <th>Insurance price</th>
                    <th>Difference from this claim</th>
                  </tr>
                </thead>
                <tbody>
                  {visiblePeers.map((peer) => {
                    const diff = memberAllowed - (peer.allowed || 0)
                    const isSelected = activePeer.claim_id === peer.claim_id
                    return (
                      <tr
                        key={peer.claim_id}
                        className={isSelected ? 'selected-peer-row' : ''}
                        onClick={() => setSelectedPeerId(peer.claim_id)}
                      >
                        <td>
                          <input
                            type="radio"
                            name="peer_select"
                            checked={isSelected}
                            onChange={() => setSelectedPeerId(peer.claim_id)}
                          />
                        </td>
                        <td><strong>{peer.claim_id}</strong></td>
                        <td>{peer.service_date}</td>
                        <td><code>{peer.icd10}</code></td>
                        <td><code>{peer.cpt}</code></td>
                        <td>{peer.provider}</td>
                        <td><strong>{formatOptionalCurrency(peer.allowed)}</strong></td>
                        <td>
                          {diff > 0 ? (
                            <span className="savings-diff-pill favorable">
                              {formatOptionalCurrency(diff)} lower ({((diff / memberAllowed) * 100).toFixed(1)}%)
                            </span>
                          ) : (
                            <span className="savings-diff-pill neutral">
                              +{formatOptionalCurrency(Math.abs(diff))} higher
                            </span>
                          )}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
            {organMatchedPeers.length > 3 ? (
              <button className="show-more-peers-button" type="button" onClick={() => setShowAllPeers((current) => !current)}>
                {showAllPeers ? 'Show only 3 claims' : `Show all ${organMatchedPeers.length} claims`}
              </button>
            ) : null}
          </div>
        </>
      ) : (
        <p className="no-matched-peers">No peer claims available for this organ system.</p>
      )}
    </section>
  )
}

function PredictionScenarioMap({ scenario }) {
  const facts = scenario.actual_claim_facts
  const summary = scenario.supported_money_summary
  const snapshot = scenario.financial_prediction_snapshot
  const historicalPeerCount = snapshot.peer_sample_size ?? scenario.historical_comparison?.sample_size ?? 0
  const suggestedReviewLabel = summary.best_action?.type === 'patient_balance'
    ? 'Patient balance and payment plan'
    : summary.top_supported_opportunity?.label || 'Current claim amount'
  const topMetrics = [
    {
      label: 'Amount ready for review',
      value: formatOptionalCurrency(summary.recoverable_now),
      note: 'A recorded current-claim amount that must be confirmed before follow-up.',
      tone: 'green',
      icon: DollarSign,
      help: 'This is not a forecast. It is the current amount supported by the workbook for staff review.',
    },
    {
      label: 'Potential savings if a repeat is avoided',
      value: formatOptionalCurrency(snapshot.predicted_avoidable_spend.value),
      note: 'Average three-month model estimate per similar case; not guaranteed savings.',
      tone: 'purple',
      icon: TrendingDown,
      help: 'This combines the chance of another related claim, the share with avoidability evidence, and an estimated extra cost.',
    },
    {
      label: 'Money at risk if insurance denies the claim',
      value: formatOptionalCurrency(summary.future_denial_exposure),
      note: `${formatProbability(snapshot.denial_probability)} estimated denial chance; not a confirmed loss.`,
      tone: 'red',
      icon: ShieldAlert,
      help: 'This is the estimated denial chance multiplied by the estimated provider payment.',
    },
  ]

  return (
    <Card className="provider-forecast-detail">
      <header className="provider-forecast-heading">
        <div className="forecast-title-group">
          <span className="forecast-kicker">Prediction for one recorded visit</span>
          <h1>{facts.diagnosis_description}</h1>
          <p className="forecast-meta">{facts.provider} · {facts.payer} · claim {scenario.claim_id} · tracking ID {scenario.episode_id}</p>
        </div>
        <span className="priority-chip">Suggested review: {suggestedReviewLabel}</span>
      </header>

      <PlainLanguageClaimNarrative
        scenario={scenario}
        facts={facts}
        summary={summary}
        snapshot={snapshot}
        historicalPeerCount={historicalPeerCount}
      />

      <div className="provider-forecast-metrics">
        {topMetrics.map(({ label, value, note, tone, icon: Icon, help }) => (
          <div key={label} className={`forecast-metric-card ${tone || 'blue'}`}>
            <div className="forecast-metric-heading"><Icon size={18} /><span className="metric-label">{label}</span></div>
            <PlainTooltip text={help}><strong className="metric-value">{value}</strong></PlainTooltip>
            <small className="metric-note">{note}</small>
          </div>
        ))}
      </div>

      <aside className="prediction-secondary-context">
        <div>
          <PlainTooltip text="The amount the provider may receive, estimated from earlier matched claim payments."><span>Estimated provider payment</span></PlainTooltip>
          <strong>{formatOptionalCurrency(snapshot.predicted_provider_payment.value)}</strong>
          <small>Based on {historicalPeerCount} earlier matched claims</small>
        </div>
        <div>
          <PlainTooltip text="This score describes the amount and quality of matching evidence. It is not the chance that the prediction is correct."><span>How confident we are</span></PlainTooltip>
          <strong>{snapshot.confidence.level} ({formatProbability(snapshot.confidence.score)})</strong>
          <small>Treat a low score as a guide that requires review</small>
        </div>
      </aside>

      <DetailedClaimFinancialBreakdown
        scenario={scenario}
        facts={facts}
        snapshot={snapshot}
        summary={summary}
      />

      <OrganSystemComparativeSavingsCard
        scenario={scenario}
        facts={facts}
        snapshot={snapshot}
      />

      <details className="scenario-technical-details">
        <summary>Show technical calculation and evidence details</summary>
        <div className="scenario-pathway">
          {scenario.scenario_map.sections.map((section) => <ScenarioMapSection key={section.step} section={section} />)}
        </div>
      </details>
      <footer className="provider-forecast-note">{scenario.versions.prediction_version} · Workbook-only decision support.</footer>
    </Card>
  )
}

function filterClaimsByTime(claims, timeFilter, defaultDateRange) {
  if (!defaultDateRange.to) return claims

  if (timeFilter === 'Latest Month') {
    const latestMonth = defaultDateRange.to.slice(0, 7)
    return claims.filter((claim) => claim.dos.startsWith(latestMonth))
  }

  if (timeFilter === 'Year to Date') {
    const yearStart = `${defaultDateRange.to.slice(0, 4)}-01-01`
    return claims.filter((claim) => claim.dos >= yearStart && claim.dos <= defaultDateRange.to)
  }

  return claims
}

function ClaimsTableFooter({ currentPage, pageCount, pageSize, totalCount, onPageChange }) {
  return (
    <div className="claims-table-footer">
      <div className="entries-control">
        <span>Show</span>
        <span className="entry-count">{pageSize}</span>
        <span>entries</span>
        <span className="footer-divider"></span>
        <strong>{currentPage} / {pageCount}</strong>
        <span>({totalCount.toLocaleString()})</span>
      </div>
      <div className="pagination-control">
        <button type="button" disabled={currentPage === 1} onClick={() => onPageChange(1)}>First</button>
        <button type="button" disabled={currentPage === 1} onClick={() => onPageChange(currentPage - 1)}>Previous</button>
        <button className="active" type="button">{currentPage}</button>
        {currentPage < pageCount ? (
          <button type="button" onClick={() => onPageChange(currentPage + 1)}>{currentPage + 1}</button>
        ) : null}
        <button type="button" disabled={currentPage === pageCount} onClick={() => onPageChange(currentPage + 1)}>Next</button>
        <button type="button" disabled={currentPage === pageCount} onClick={() => onPageChange(pageCount)}>Last</button>
      </div>
    </div>
  )
}

function ClaimDetailPage({ claim }) {
  return (
    <>
      <div className="claim-detail-layout">
        <SelectedClaimDetail claim={claim} />
        <WorkbookFinancialPredictionCard claim={claim} />
        <ClaimReasonCard claim={claim} />
      </div>
    </>
  )
}

function WorkbookFinancialPredictionCard({ claim }) {
  const [result, setResult] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    let active = true
    setResult(null)
    setError('')
    fetchJson(`/api/predictions/provider-case/${encodeURIComponent(claim.claimId || claim.number)}`)
      .then((payload) => { if (active) setResult(payload) })
      .catch((requestError) => { if (active) setError(requestError.message) })
    return () => { active = false }
  }, [claim.claimId, claim.number])

  if (error) return <Card className="state-card">{error}</Card>
  if (!result) return <Card className="state-card"><RefreshCw className="spin" size={18} /> Loading workbook financial prediction…</Card>
  const prediction = result.financial_prediction_snapshot
  const summary = result.supported_money_summary
  return (
    <Card className="payment-forecast-card workbook-financial-prediction">
      <div className="forecast-header">
        <div>
          <span>Workbook prediction · {prediction.model_version}</span>
          <h2>Payment Forecast & Supported Money</h2>
          <p>{prediction.confidence.reason}</p>
        </div>
        <div className="workbook-confidence"><strong>{formatProbability(prediction.confidence.score)}</strong><span>{prediction.confidence.level} confidence</span></div>
      </div>
      <div className="forecast-money-grid">
        <div className="forecast-money-card"><span>Predicted allowed</span><strong>{formatOptionalCurrency(prediction.predicted_allowed.value)}</strong><small>{prediction.matching_level}</small></div>
        <div className="forecast-money-card"><span>Predicted paid</span><strong>{formatOptionalCurrency(prediction.predicted_provider_payment.value)}</strong><small>{prediction.peer_sample_size} historical references</small></div>
        <div className="forecast-money-card"><span>Recoverable now</span><strong>{formatOptionalCurrency(summary.recoverable_now)}</strong><small>{summary.best_action.stage}</small></div>
        <div className="forecast-money-card"><span>Predicted avoidable spend</span><strong>{formatOptionalCurrency(prediction.predicted_avoidable_spend.value)}</strong><small>Forecast · 90-day horizon</small></div>
      </div>
      <details className="validated-avoidable-detail"><summary>Historical validation detail</summary><p>Validated avoidable spend: {formatOptionalCurrency(result.validated_avoidable_spend.value)} · {result.validated_avoidable_spend.reason}</p></details>
    </Card>
  )
}

function EncounterSearch({ searchQuery, onSearchChange, onSelectMember, onOpenClaim }) {
  const { claimsData } = useAppData()
  const [statusFilter, setStatusFilter] = useState('All Statuses')
  const [currentPage, setCurrentPage] = useState(1)
  const pageSize = 10
  const normalizedQuery = searchQuery.trim().toLowerCase()
  const statusOptions = useMemo(() => ['All Statuses', ...uniqueValues(claimsData, 'status')], [claimsData])
  const filteredEncounters = useMemo(() => claimsData.filter((claim) => {
      const matchesSearch = !normalizedQuery || (
        claim.patient.toLowerCase().includes(normalizedQuery) ||
        claim.memberId.toLowerCase().includes(normalizedQuery)
      )
      const matchesStatus = statusFilter === 'All Statuses' || claim.status === statusFilter
      return matchesSearch && matchesStatus
    }), [claimsData, normalizedQuery, statusFilter])
  const pageCount = Math.max(1, Math.ceil(filteredEncounters.length / pageSize))
  const safePage = Math.min(currentPage, pageCount)
  const pagedEncounters = useMemo(
    () => filteredEncounters.slice((safePage - 1) * pageSize, safePage * pageSize),
    [filteredEncounters, safePage],
  )

  useEffect(() => {
    setCurrentPage(1)
  }, [searchQuery, statusFilter])

  return (
    <>
      <div className="patient-header-row search-results-header">
        <div>
          <h1>Patient 360</h1>
          <p>Recent encounters from the current 837 claims database</p>
        </div>
        <div className="patient-grid-controls">
          <label className="claims-directory-search patient-search-inline">
            <Search size={18} />
            <input
              type="search"
              value={searchQuery}
              onChange={(event) => onSearchChange(event.target.value)}
              placeholder="Search patient name or member ID"
              aria-label="Search patients by name or member ID"
            />
          </label>
          <label className="claims-time-filter">
            <span>Status:</span>
            <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
              {statusOptions.map((option) => (
                <option key={option}>{option}</option>
              ))}
            </select>
            <ChevronDown size={16} />
          </label>
        </div>
      </div>

      <RecentEncounters
        title="All Encounters"
        claims={pagedEncounters}
        onSelectMember={onSelectMember}
        onOpenClaim={onOpenClaim}
        emptyMessage="No encounters match that patient name, member ID, or status."
        footer={(
          <ClaimsTableFooter
            currentPage={safePage}
            pageCount={pageCount}
            pageSize={pageSize}
            totalCount={filteredEncounters.length}
            onPageChange={setCurrentPage}
          />
        )}
      />
    </>
  )
}

function getClinicalCategory(code = '', description = '') {
  const c = String(code || '').toUpperCase().trim()
  const d = String(description || '').toLowerCase()

  if (c.startsWith('N18') || c.startsWith('N17') || c.startsWith('N19') || d.includes('kidney') || d.includes('renal')) {
    return { category: 'Kidney Disease / Renal Failure', iconTone: 'teal' }
  }
  if (
    c.startsWith('I') || c.startsWith('Z13.6') || d.includes('heart') || d.includes('cardio') ||
    d.includes('atrial') || d.includes('hypertens') || d.includes('vein') || d.includes('artery')
  ) {
    return { category: 'Heart & Cardiovascular', iconTone: 'red' }
  }
  if (c.startsWith('E11') || c.startsWith('E10') || c.startsWith('E13') || d.includes('diabet') || d.includes('hyperglyc')) {
    return { category: 'Endocrine & Diabetes', iconTone: 'blue' }
  }
  if (c.startsWith('E03') || c.startsWith('E78') || d.includes('thyroid') || d.includes('lipid')) {
    return { category: 'Metabolic & Thyroid', iconTone: 'amber' }
  }
  if (c.startsWith('K00') || c.startsWith('K01') || c.startsWith('K02') || c.startsWith('K08') || d.includes('dental') || d.includes('teeth') || d.includes('oral')) {
    return { category: 'Dental & Oral Health', iconTone: 'teal' }
  }
  if (c.startsWith('J') || d.includes('pneumonia') || d.includes('copd') || d.includes('respirat') || d.includes('lung') || d.includes('pulmonary')) {
    return { category: 'Respiratory & Pulmonary', iconTone: 'blue' }
  }
  if (c.startsWith('M') || d.includes('spine') || d.includes('sciatica') || d.includes('osteo') || d.includes('rotator') || d.includes('lumbago') || d.includes('joint') || d.includes('shoulder')) {
    return { category: 'Musculoskeletal & Orthopedic', iconTone: 'amber' }
  }
  if (c.startsWith('C') || c.startsWith('D0') || c.startsWith('D4') || d.includes('neoplasm') || d.includes('cancer') || d.includes('malignant') || d.includes('tumor')) {
    return { category: 'Oncology & Neoplasms', iconTone: 'violet' }
  }
  if (c.startsWith('F') || d.includes('depress') || d.includes('anxiety') || d.includes('bipolar') || d.includes('mental')) {
    return { category: 'Behavioral & Mental Health', iconTone: 'violet' }
  }
  if (c.startsWith('G') || d.includes('epilepsy') || d.includes('migraine') || d.includes('dystonia') || d.includes('neuro') || d.includes('brain')) {
    return { category: 'Neurology & Brain', iconTone: 'blue' }
  }
  if (c.startsWith('K2') || c.startsWith('K3') || c.startsWith('K5') || d.includes('reflux') || d.includes('gerd') || d.includes('gastro') || d.includes('colon')) {
    return { category: 'Gastroenterology & Digestive', iconTone: 'amber' }
  }
  if (c.startsWith('N3') || d.includes('urinary') || d.includes('bladder')) {
    return { category: 'Urology & Urinary', iconTone: 'teal' }
  }
  if (c.startsWith('Z00') || c.startsWith('Z01') || c.startsWith('Z11') || c.startsWith('Z13') || c.startsWith('Z23') || c.startsWith('Z87') || d.includes('exam') || d.includes('screen') || d.includes('prevent') || d.includes('vaccine') || d.includes('immuniz')) {
    return { category: 'Preventive Care & Screening', iconTone: 'green' }
  }
  return { category: 'General Medical Treatment', iconTone: 'blue' }
}

function buildMemberConditions(claims) {
  const groups = new Map()
  claims.forEach((claim) => {
    const code = claim.diagnosisCode || 'Z00'
    const desc = claim.diagnosisDescription || 'General Medical Treatment'
    const key = `${code}|${desc}`
    if (!groups.has(key)) {
      const { category, iconTone } = getClinicalCategory(code, desc)
      groups.set(key, {
        key,
        code,
        description: desc,
        category,
        iconTone,
        claims: [],
        totalCharge: 0,
        totalAllowed: 0,
        totalPaid: 0,
        patientResponsibility: 0,
      })
    }
    const group = groups.get(key)
    group.claims.push(claim)
    group.totalCharge += claim.totalCharge || 0
    group.totalAllowed += claim.allowed || 0
    group.totalPaid += claim.paid || 0
    group.patientResponsibility += claim.patientResp || 0
  })

  return [...groups.values()].sort((a, b) => b.claims.length - a.claims.length || b.totalCharge - a.totalCharge)
}

function DiseaseOverviewTable({ conditions, totalClaimsCount, onOpenPrediction, memberClaims = [] }) {
  const [page, setPage] = useState(1);
  const rowsPerPage = 10;
  const pageCount = Math.max(1, Math.ceil(conditions.length / rowsPerPage));
  const visibleConditions = conditions.slice((page - 1) * rowsPerPage, page * rowsPerPage);

  const totalConditions = conditions.length;
  const avgAllowed = totalClaimsCount ? conditions.reduce((acc, c) => acc + c.totalAllowed, 0) / totalClaimsCount : 0;
  const avgPaid = totalClaimsCount ? conditions.reduce((acc, c) => acc + c.totalPaid, 0) / totalClaimsCount : 0;

  // Real utilization trend from claim dates
  const sortedDates = (memberClaims.length ? memberClaims : conditions.flatMap(c => c.claims || [])).map(c => c.dos).filter(Boolean).sort();
  let trendPct = 0;
  let isTrendUp = true;
  if (sortedDates.length >= 2) {
    const midIdx = Math.floor(sortedDates.length / 2);
    const midDate = sortedDates[midIdx];
    const recentCount = sortedDates.filter(d => d >= midDate).length;
    const priorCount = sortedDates.filter(d => d < midDate).length;
    if (priorCount > 0) {
      trendPct = ((recentCount - priorCount) / priorCount) * 100;
      isTrendUp = trendPct >= 0;
    }
  }

  const totalAllowedSum = conditions.reduce((acc, c) => acc + c.totalAllowed, 0);
  const riskLevel = totalAllowedSum > 50000 || totalConditions >= 4 ? 'High' : (totalAllowedSum > 15000 || totalConditions >= 2 ? 'Medium' : 'Low');

  // Helper for sparklines based on real claim allowed amounts
  const generateSparkline = (item) => {
    const claimAmounts = (item.claims || []).map(c => c.allowed || 0);
    if (!claimAmounts.length) return '0,15 60,15';
    const maxVal = Math.max(...claimAmounts, 1);
    const minVal = Math.min(...claimAmounts, 0);
    const range = maxVal - minVal || 1;
    const pts = claimAmounts.slice(0, 15).map((val, idx, arr) => {
      const x = Math.round((idx / Math.max(arr.length - 1, 1)) * 58);
      const y = Math.round(26 - ((val - minVal) / range) * 22);
      return `${x},${Math.max(2, Math.min(28, y))}`;
    });
    return pts.join(' ');
  };

  return (
    <Card className="disease-overview-card">
      <div className="disease-overview-header">
        <div className="disease-title-area">
          <Stethoscope size={20} className="disease-icon" />
          <div>
            <h2>Disease & Claims Overview</h2>
            <span className="disease-subtitle">All conditions for this patient with claims, costs, and utilization.</span>
          </div>
        </div>
        <div className="disease-controls">
          <select className="disease-select"><option>All Time</option></select>
          <select className="disease-select"><option>Group by: Condition</option></select>
          <button type="button" className="disease-export-btn"><Download size={14}/> Export</button>
        </div>
      </div>

      <div className="disease-table-wrapper">
        <table className="disease-table">
          <thead>
            <tr>
              <th>Condition / Diagnosis (ICD-10)</th>
              <th>Claim Count</th>
              <th>Total Allowed</th>
              <th>Total Paid</th>
              <th>Patient Responsibility</th>
              <th>Trend</th>
              <th>Est. Savings Potential</th>
              <th>Action</th>
            </tr>
          </thead>
          <tbody>
            {visibleConditions.map((item) => (
              <tr key={item.key}>
                <td className="condition-cell">
                  <span className={`condition-icon-badge ${item.iconTone}`}>
                    {item.iconTone === 'violet' ? <Activity size={16} /> : 
                     item.iconTone === 'blue' ? <Brain size={16} /> : 
                     item.iconTone === 'green' ? <Shield size={16} /> : 
                     item.iconTone === 'amber' ? <Wind size={16} /> : 
                     <HeartPulse size={16} />}
                  </span>
                  <div className="condition-name-group">
                    <strong>{item.description} ({item.code})</strong>
                    {item.code.startsWith('11') ? <span className="sub-code">110</span> : null}
                  </div>
                </td>
                <td>{item.claims.length}</td>
                <td>{formatCurrency(item.totalAllowed)}</td>
                <td>{formatCurrency(item.totalPaid)}</td>
                <td>{formatCurrency(item.patientResponsibility)}</td>
                <td className="trend-cell">
                  <svg width="60" height="30" className="sparkline">
                    <polyline fill="none" stroke="#3b82f6" strokeWidth="1.5" points={generateSparkline(item)} />
                  </svg>
                </td>
                <td className="savings-cell">{formatCurrency(Math.max(0, item.totalAllowed - item.totalPaid))}</td>
                <td>
                  <button type="button" className="action-icon-btn" title="Open Prediction" onClick={() => onOpenPrediction(item.claims[0])}>
                    <img className="action-icon-image" src="/claimsai-robot.png" alt="" aria-hidden="true" />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="disease-pagination">
        <span>Showing {(page - 1) * rowsPerPage + 1} to {Math.min(page * rowsPerPage, totalConditions)} of {totalConditions} conditions</span>
        <div className="disease-page-controls">
          <button type="button" onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page === 1}><ChevronLeft size={16} /></button>
          <span className="current-page">{page}</span>
          <button type="button" onClick={() => setPage(p => Math.min(pageCount, p + 1))} disabled={page === pageCount}><ChevronRight size={16} /></button>
        </div>
        <div className="disease-rows-per-page">
          Rows per page: <select><option>10</option></select>
        </div>
      </div>

      <div className="disease-footer-metrics">
        <div className="footer-metric">
          <div className="footer-icon blue"><Activity size={20} /></div>
          <div><span>Total Conditions</span><strong>{totalConditions}</strong></div>
        </div>
        <div className="footer-metric">
          <div className="footer-icon purple"><FileText size={20} /></div>
          <div><span>Total Claims</span><strong>{totalClaimsCount}</strong></div>
        </div>
        <div className="footer-metric">
          <div className="footer-icon orange"><TrendingUp size={20} /></div>
          <div><span>Avg. Allowed / Claim</span><strong>{formatCurrency(avgAllowed)}</strong></div>
        </div>
        <div className="footer-metric">
          <div className="footer-icon green"><DollarSign size={20} /></div>
          <div><span>Avg. Paid / Claim</span><strong>{formatCurrency(avgPaid)}</strong></div>
        </div>
        <div className="footer-metric trend-metric">
          <div className="footer-icon success-bg">
            {isTrendUp ? <ArrowUpRight size={20} color="#16a34a" /> : <TrendingDown size={20} color="#2563eb" />}
          </div>
          <div>
            <span>Utilization Trend</span>
            <strong>
              <span className={isTrendUp ? "success-text" : "favorable-text"}>
                {isTrendUp ? `↑ ${trendPct.toFixed(1)}%` : `↓ ${Math.abs(trendPct).toFixed(1)}%`}
              </span>
            </strong>
            <small>activity period trend</small>
          </div>
        </div>
        <div className="footer-metric">
          <div className={`footer-icon ${riskLevel === 'High' ? 'danger-bg' : riskLevel === 'Medium' ? 'warning-bg' : 'success-bg'}`}>
            <ShieldAlert size={20} color={riskLevel === 'High' ? '#dc2626' : riskLevel === 'Medium' ? '#ea580c' : '#16a34a'} />
          </div>
          <div><span>Risk Level</span><strong>{riskLevel}</strong></div>
        </div>
      </div>
    </Card>
  )
}

function MemberFinancialPredictionSidebar({ member, latestClaim, payerCohortSavings, onOpenPrediction, onBackToEncounters, onOpenClaim }) {
  const [windowDays, setWindowDays] = useState(365)
  const [prediction, setPrediction] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [downloadMsg, setDownloadMsg] = useState('')
  const [showWindowModal, setShowWindowModal] = useState(false)

  const loadPrediction = async (days = windowDays) => {
    setLoading(true)
    setError('')
    try {
      const data = await fetchJson(`/api/payer-prediction/${encodeURIComponent(member.memberId)}?window=${days}`)
      setPrediction(data)
    } catch (err) {
      setError(err.message || 'Unable to compute prediction from dataset')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadPrediction(windowDays)
  }, [member.memberId, windowDays])

  const forecast = prediction?.forecast || {}
  const historicalCohort = prediction?.historical_cohort || {}
  const target = prediction?.target || {}
  const potentialSavings = prediction?.potential_savings || {}

  const predictedAllowed = forecast.predicted_total_allowed ?? historicalCohort.median_allowed_amount ?? 0
  const currentAllowed = forecast.current_total_allowed ?? target.allowed_amount ?? member.totalAllowed ?? 0
  const savingsOpportunity = forecast.potential_savings ?? potentialSavings.median_opportunity ?? (payerCohortSavings?.member_predicted_payer_avoidable_spend || 0)
  const deltaPct = forecast.delta_percent ?? (predictedAllowed > 0 ? ((currentAllowed - predictedAllowed) / predictedAllowed) * 100 : 0)
  const isHigher = deltaPct >= 0
  const peersUsed = forecast.peers_used || historicalCohort.members || 0
  const selectionLevel = (forecast.selection_level || historicalCohort.selection_level || 'Condition Group').replace(/_/g, ' ')
  const confidenceLevel = forecast.confidence_level || prediction?.confidence?.level || 'Medium'

  const windowClaims = useMemo(() => {
    if (prediction?.potential_savings?.claim_level_attribution?.length) {
      return prediction.potential_savings.claim_level_attribution.map((c) => {
        const matchingFullClaim = member.claims.find((mc) => mc.claimId === c.claim_id || mc.number === c.claim_id)
        return {
          claimId: c.claim_id,
          date: c.service_date,
          cpt: c.cpt,
          allowed: c.allowed,
          description: matchingFullClaim?.cptDescription || matchingFullClaim?.diagnosisDescription || 'Encounter service',
          diagnosis: matchingFullClaim?.diagnosisCode || '',
          fullClaim: matchingFullClaim,
        }
      })
    }
    if (prediction?.timeline?.length) {
      return prediction.timeline.map((c) => {
        const matchingFullClaim = member.claims.find((mc) => mc.claimId === c.claim_id || mc.number === c.claim_id)
        return {
          claimId: c.claim_id,
          date: c.service_date,
          cpt: c.cpt,
          allowed: c.allowed,
          description: matchingFullClaim?.cptDescription || matchingFullClaim?.diagnosisDescription || 'Encounter service',
          diagnosis: matchingFullClaim?.diagnosisCode || '',
          fullClaim: matchingFullClaim,
        }
      })
    }
    return member.claims.slice(0, 5).map((c) => ({
      claimId: c.claimId || c.number,
      date: c.dos,
      cpt: c.cptCode,
      allowed: c.allowed,
      description: c.cptDescription || c.diagnosisDescription || 'Encounter service',
      diagnosis: c.diagnosisCode || '',
      fullClaim: c,
    }))
  }, [prediction, member.claims])

  const insights = useMemo(() => {
    if (prediction?.key_insights && prediction.key_insights.length > 0) {
      return prediction.key_insights
    }
    const list = []
    const topDiag = member.claims[0]?.diagnosisDescription || member.claims[0]?.diagnosisCode || 'Primary condition'
    list.push({
      icon: 'activity',
      tone: isHigher ? 'orange' : 'green',
      text: `${topDiag} costs are ${Math.abs(deltaPct).toFixed(1)}% ${isHigher ? 'higher than' : 'below'} peer benchmark median (${formatCurrency(predictedAllowed)})`,
      bold: `${Math.abs(deltaPct).toFixed(1)}%`,
    })
    if (savingsOpportunity > 0) {
      list.push({
        icon: 'dollar',
        tone: 'purple',
        text: `${formatCurrency(savingsOpportunity)} in total historical optimization opportunity`,
        bold: formatCurrency(savingsOpportunity),
      })
    }
    const denied = member.claims.filter(c => c.status === 'Denied').length
    const openClaims = member.claims.filter(c => c.status !== 'Paid').length
    list.push({
      icon: 'file-text',
      tone: denied > 0 ? 'orange' : 'blue',
      text: `${openClaims} open/pending claims detected (${denied} denied)`,
      bold: `${openClaims} open claims`,
    })
    return list
  }, [prediction, member.claims, isHigher, deltaPct, predictedAllowed, savingsOpportunity])

  const handleDownloadSummary = () => {
    const summary = {
      memberId: member.memberId,
      patientName: member.patient,
      dob: member.dob,
      payer: member.payer,
      subscriberId: member.subscriberId,
      totalClaimsCount: member.claims.length,
      currentAllowedSpend: currentAllowed,
      predictedAllowedSpend: predictedAllowed,
      predictionHorizonDays: windowDays,
      potentialSavingsOpportunity: savingsOpportunity,
      confidenceLevel,
      peerMembersUsed: peersUsed,
      matchingLevel: selectionLevel,
      generatedTimestamp: new Date().toISOString(),
    }
    const dataStr = 'data:text/json;charset=utf-8,' + encodeURIComponent(JSON.stringify(summary, null, 2))
    const dlAnchor = document.createElement('a')
    dlAnchor.setAttribute('href', dataStr)
    dlAnchor.setAttribute('download', `Patient_Prediction_${member.memberId}.json`)
    dlAnchor.click()
    setDownloadMsg('Summary downloaded!')
    setTimeout(() => setDownloadMsg(''), 3000)
  }

  return (
    <aside className="patient-right-sidebar">
      <Card className="prediction-beta-sidebar prediction-live-sidebar">
        <div className="prediction-scope-badge">
          <span className="scope-tag macro-tag">PATIENT ANNUAL CONDITION EPISODE</span>
          <span className="scope-horizon">{windowDays} Days Window</span>
        </div>

        <div className="prediction-beta-header">
          <div className="prediction-title-group">
            <h3>Financial Prediction</h3>
            <span className="live-dataset-badge"><Sparkles size={11} /> Live Dataset</span>
          </div>
          <span className={`confidence-pill ${confidenceLevel.toLowerCase()}`}>
            {confidenceLevel} Confidence
          </span>
        </div>
        <p className="prediction-beta-desc">
          AI statistical forecast based on {peersUsed} matched historical peers ({selectionLevel}) in the active claims dataset.
        </p>

        <div className="prediction-window-select">
          <label htmlFor="pred-window-select">
            Prediction Window <Info size={12} title="Observation & forecast horizon" />
          </label>
          <select
            id="pred-window-select"
            value={windowDays}
            onChange={(e) => setWindowDays(Number(e.target.value))}
            disabled={loading}
          >
            <option value={365}>Next 12 Months (365 Days)</option>
            <option value={180}>Next 6 Months (180 Days)</option>
            <option value={90}>Next 3 Months (90 Days)</option>
            <option value={30}>Next 30 Days</option>
          </select>
        </div>

        {error ? (
          <div className="prediction-sidebar-error">
            <span>{error}</span>
            <button type="button" onClick={() => loadPrediction(windowDays)}>Retry</button>
          </div>
        ) : (
          <div className="predicted-allowed-block">
            <div className="pred-label-row">
              <label>Predicted Total Allowed (Cohort Benchmark)</label>
              {loading && <RefreshCw size={12} className="spin" />}
            </div>
            <strong>{formatCurrency(predictedAllowed)}</strong>
            <div className="pred-trend-row">
              <span className={isHigher ? 'trend-up-warning' : 'trend-down-favorable'}>
                {isHigher ? <TrendingUp size={14} /> : <TrendingDown size={14} />}
                {Math.abs(deltaPct).toFixed(1)}% {isHigher ? 'above' : 'below'} peer benchmark
              </span>
              <div className="pred-subnote-group">
                <span className="pred-subnote">
                  Current window spend: <strong>{formatCurrency(currentAllowed)}</strong>
                </span>
                <button
                  type="button"
                  className="view-window-claims-btn"
                  onClick={() => setShowWindowModal(true)}
                  title="Click to view all claims that sum to this amount"
                >
                  View {prediction?.target?.claim_count || windowClaims.length} claims breakdown <ChevronRight size={12} />
                </button>
              </div>
            </div>
            {savingsOpportunity > 0 ? (
              <div className="pred-savings-pill">
                <Target size={14} />
                <div>
                  <span>Annual Over-Utilization Savings: <strong>{formatCurrency(savingsOpportunity)}</strong></span>
                  <small className="savings-pill-sub">{member.patient || 'Patient'} had {prediction?.target?.claim_count || windowClaims.length} visits vs. peer median of {prediction?.historical_cohort?.median_claim_count || 2.0} visits</small>
                </div>
              </div>
            ) : null}
          </div>
        )}

        <button
          type="button"
          className="primary-action-btn"
          onClick={() => loadPrediction(windowDays)}
          disabled={loading}
        >
          {loading ? <RefreshCw size={16} className="spin" /> : <Sparkles size={16} />}
          {loading ? 'Calculating…' : 'Generate / Refresh Prediction'}
        </button>
        <button
          type="button"
          className="secondary-action-btn"
          onClick={() => onOpenPrediction(latestClaim)}
        >
          <BarChart3 size={16} /> Open Single-Claim Prediction
        </button>
      </Card>

      <div className="sidebar-insights">
        <div className="insights-header">
          <h3>Key Insights</h3>
          <span className="insights-count">{insights.length}</span>
        </div>
        {insights.map((item, idx) => (
          <div className="insight-item" key={idx}>
            <div className={`insight-icon ${item.tone || 'blue'}`}>
              {item.icon === 'activity' ? <Activity size={14} /> :
               item.icon === 'dollar' ? <DollarSign size={14} /> :
               item.icon === 'bell' ? <Bell size={14} /> :
               <FileText size={14} />}
            </div>
            <p>{item.text}</p>
          </div>
        ))}
      </div>

      <div className="sidebar-quick-actions">
        <h3>Quick Actions</h3>
        <button type="button" onClick={() => onOpenPrediction(latestClaim)}>
          Generate AI Forecast <ChevronRight size={14}/>
        </button>
        <button type="button" onClick={handleDownloadSummary}>
          {downloadMsg || 'Download Patient Summary'} <ChevronRight size={14}/>
        </button>
        <button type="button" onClick={onBackToEncounters}>
          View All Encounters <ChevronRight size={14}/>
        </button>
      </div>

      {showWindowModal && (
        <div className="window-claims-modal-backdrop" role="presentation" onClick={() => setShowWindowModal(false)}>
          <div className="window-claims-modal" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
            <div className="window-claims-header">
              <div>
                <span className="scope-tag macro-tag">ANNUAL OBSERVATION WINDOW BREAKDOWN</span>
                <h3>{member.patient || 'Patient'}’s {windowClaims.length} Claims in Current Window</h3>
                <p>These {windowClaims.length} claims sum to <strong>{formatCurrency(currentAllowed)}</strong> over the {windowDays}-day observation period for {prediction?.member?.condition?.description || 'this condition'}.</p>
              </div>
              <button className="window-claims-close" type="button" aria-label="Close window claims breakdown" onClick={() => setShowWindowModal(false)}>
                <X size={18} />
              </button>
            </div>
            <div className="window-claims-body">
              <div className="window-claims-table-wrap">
                <table className="window-claims-table">
                  <thead>
                    <tr>
                      <th>Claim ID</th>
                      <th>Service Date</th>
                      <th>CPT Code</th>
                      <th>Service / Diagnosis</th>
                      <th>Allowed Amount</th>
                      <th>Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {windowClaims.map((item, idx) => (
                      <tr key={item.claimId || idx}>
                        <td><strong>{item.claimId}</strong></td>
                        <td>{formatDate(item.date)}</td>
                        <td><code>{item.cpt}</code></td>
                        <td>{item.description}</td>
                        <td><strong>{formatCurrency(item.allowed)}</strong></td>
                        <td>
                          {item.fullClaim ? (
                            <button
                              type="button"
                              className="table-action-link"
                              onClick={() => {
                                setShowWindowModal(false)
                                onOpenClaim(item.fullClaim)
                              }}
                            >
                              View Claim <ArrowRight size={13} />
                            </button>
                          ) : '—'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                  <tfoot>
                    <tr>
                      <td colSpan={4}><strong>Total Allowed Spend for {windowClaims.length} Claims in Window:</strong></td>
                      <td colSpan={2}><strong>{formatCurrency(currentAllowed)}</strong></td>
                    </tr>
                  </tfoot>
                </table>
              </div>
              <div className="window-claims-summary-footer">
                <div className="summary-col">
                  <span>{member.patient?.split(' ')[0] || 'Patient'}’s Actual Spend:</span>
                  <strong>{formatCurrency(currentAllowed)}</strong>
                  <small>({windowClaims.length} visits in {windowDays}d)</small>
                </div>
                <span className="summary-math-op">−</span>
                <div className="summary-col">
                  <span>Peer Median Benchmark:</span>
                  <strong>{formatCurrency(predictedAllowed)}</strong>
                  <small>({prediction?.historical_cohort?.median_claim_count || 2.0} visits across {peersUsed} peers)</small>
                </div>
                <span className="summary-math-op">=</span>
                <div className="summary-col highlight-col">
                  <span>Annual Over-Utilization Savings:</span>
                  <strong>{formatCurrency(savingsOpportunity)}</strong>
                  <small>Potential episode savings opportunity</small>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </aside>
  )
}

function MemberDetail({ member, selectedClaim, onBackToEncounters, onSelectMember, onOpenClaim, onOpenPrediction }) {
  const { defaultDateRange } = useAppData()
  const latestClaim = selectedClaim || member.latestClaim
  const [memberMoney, setMemberMoney] = useState(null)
  const [payerCohortSavings, setPayerCohortSavings] = useState(null)
  const [memberEncountersPage, setMemberEncountersPage] = useState(1)
  const [selectedCondition, setSelectedCondition] = useState(null)
  const memberEncountersPageSize = 10

  const memberConditions = useMemo(
    () => buildMemberConditions(member.claims),
    [member.claims],
  )

  const filteredMemberClaims = useMemo(() => {
    if (!selectedCondition) return member.claims
    return member.claims.filter((claim) => {
      const key = `${claim.diagnosisCode || 'Z00'}|${claim.diagnosisDescription || 'General Medical Treatment'}`
      return key === selectedCondition
    })
  }, [member.claims, selectedCondition])

  const memberEncountersPageCount = Math.max(1, Math.ceil(filteredMemberClaims.length / memberEncountersPageSize))
  const safeMemberEncountersPage = Math.min(memberEncountersPage, memberEncountersPageCount)
  const memberClaimsPage = useMemo(
    () => (selectedCondition
      ? filteredMemberClaims.slice(
        (safeMemberEncountersPage - 1) * memberEncountersPageSize,
        safeMemberEncountersPage * memberEncountersPageSize,
      )
      : member.claims.slice(
        (safeMemberEncountersPage - 1) * memberEncountersPageSize,
        safeMemberEncountersPage * memberEncountersPageSize,
      )),
    [member.claims, filteredMemberClaims, selectedCondition, safeMemberEncountersPage],
  )
  const memberStats = buildMemberStats(member, memberMoney, payerCohortSavings)

  useEffect(() => {
    setMemberEncountersPage(1)
    setSelectedCondition(null)
  }, [member.memberId])

  useEffect(() => {
    setMemberEncountersPage(1)
  }, [selectedCondition])

  useEffect(() => {
    let active = true
    setMemberMoney(null)
    setPayerCohortSavings(null)
    fetchJson(`/api/members/${encodeURIComponent(member.memberId)}`)
      .then((payload) => {
        if (active) {
          setMemberMoney(payload.item?.supportedMoneySummary || null)
          setPayerCohortSavings(payload.item?.payerCohortSavingsSummary || null)
        }
      })
      .catch(() => {
        if (active) {
          setMemberMoney(member.supportedMoneySummary || null)
          setPayerCohortSavings(null)
        }
      })
    return () => { active = false }
  }, [member.memberId, member.supportedMoneySummary])

  const activeConditionObj = memberConditions.find((c) => c.key === selectedCondition)

  return (
    <div className="patient-360-container">
      <div className="patient-header-row">
        <button className="back-link" type="button" onClick={onBackToEncounters}>
          <ArrowLeft size={16} />
          Back to Encounters
        </button>
        <div className="data-stamp">
          Data as of {formatDate(defaultDateRange.to)}
          <CalendarDays size={15} />
        </div>
      </div>

      <div className="patient-master-layout">
        <div className="patient-main-content">
          <Card className="patient-master-header">
            <div className="member-profile-section">
              <div className="initials-avatar">{getInitials(member)}</div>
              <div className="member-info-core">
                <div className="member-name-row">
                  <h1>{member.patient}</h1>
                  <span className="status-pill success">Active Member</span>
                </div>
                <div className="member-meta-row">
                  <div className="meta-item"><span>MEMBER ID</span><strong>{member.memberId}</strong></div>
                  <div className="meta-item"><span>DOB</span><strong>{formatDate(member.dob)} ({calculateAge(member.dob)})</strong></div>
                  <div className="meta-item"><span>GENDER</span><strong>{member.gender}</strong></div>
                  <div className="meta-item"><span>ACCOUNT #</span><strong>{member.accountNumber}</strong></div>
                </div>
              </div>
            </div>
            
            <div className="coverage-snapshot-section">
              <div className="coverage-header">
                <h3>Coverage Snapshot</h3>
                <button type="button" className="view-details-link">View Details</button>
              </div>
              <div className="coverage-meta-grid">
                <div className="meta-item"><span>PAYER</span><strong>{member.payer}</strong></div>
                <div className="meta-item"><span>GROUP</span><strong>{member.groupName}</strong></div>
                <div className="meta-item"><span>PLAN</span><strong>{latestClaim.filingIndicator}</strong></div>
                <div className="meta-item"><span>MEMBER SINCE</span><strong>{formatDate(member.claims[member.claims.length - 1].dos)}</strong></div>
                <div className="meta-item"><span>SUBSCRIBER ID</span><strong>{member.subscriberId}</strong></div>
                <div className="meta-item"><span>RELATIONSHIP</span><strong>Subscriber</strong></div>
              </div>
            </div>
          </Card>

          <div className="metrics-cards-grid">
            {memberStats.slice(0, 6).map((stat) => (
              <Card key={stat.label} className="kpi-card">
                <div className="kpi-header">
                  <div className={`kpi-icon-wrapper ${stat.iconTone}`}>
                    {stat.Icon ? <stat.Icon size={18} /> : null}
                  </div>
                  <h3>{stat.label}</h3>
                </div>
                <div className="kpi-value">{stat.value}</div>
                <div className="kpi-note">{stat.note}</div>
              </Card>
            ))}
          </div>
          
          <div className="metrics-cards-grid-secondary">
             {memberStats.slice(6).map((stat) => (
              <Card key={stat.label} className="kpi-card last-encounter-card">
                <div className="kpi-header">
                  <div className={`kpi-icon-wrapper ${stat.iconTone}`}>
                    {stat.Icon ? <stat.Icon size={18} /> : null}
                  </div>
                  <h3>{stat.label}</h3>
                </div>
                <div className="kpi-value">{stat.value}</div>
                <div className="kpi-note">{stat.note}</div>
              </Card>
            ))}
          </div>

          <DiseaseOverviewTable
            conditions={memberConditions}
            totalClaimsCount={member.claims.length}
            memberClaims={member.claims}
            onOpenPrediction={onOpenPrediction}
          />
        </div>
      </div>
    </div>
  )
}

function ExecutiveDashboard({ onOpenClaim, onViewAllClaims }) {
  const { claimsData, defaultDateRange, payerOptions, planOptions, providerOptions } = useAppData()
  const [openMenu, setOpenMenu] = useState(null)
  const [dateRange, setDateRange] = useState(defaultDateRange)
  const [payer, setPayer] = useState('All Payers')
  const [plan, setPlan] = useState('All Plans')
  const [providerGroup, setProviderGroup] = useState('All Groups')
  const [filters, setFilters] = useState({ deniedOnly: false, highValue: false })

  useEffect(() => {
    setDateRange(defaultDateRange)
  }, [defaultDateRange])

  const filteredClaims = claimsData.filter((claim) => {
    if (claim.dos < dateRange.from || claim.dos > dateRange.to) return false
    if (payer !== 'All Payers' && claim.payer !== payer) return false
    if (plan !== 'All Plans' && claim.filingIndicator !== plan) return false
    if (providerGroup !== 'All Groups' && claim.billingProvider !== providerGroup) return false
    if (filters.deniedOnly && claim.status !== 'Denied') return false
    if (filters.highValue && claim.totalCharge < 2000) return false
    return true
  })
  const dashboardMetrics = buildDashboardMetrics(filteredClaims)

  const resetFilters = () => {
    setDateRange(defaultDateRange)
    setPayer('All Payers')
    setPlan('All Plans')
    setProviderGroup('All Groups')
    setFilters({ deniedOnly: false, highValue: false })
    setOpenMenu(null)
  }

  return (
    <section className="executive-page">
      <header className="executive-topbar">
        <div className="topbar-welcome executive-welcome">
          <span>Welcome Back</span>
          <strong>Alex Admin</strong>
        </div>
        <div className="executive-actions">
          <button className="icon-button has-alert" type="button" aria-label="Notifications">
            <Bell size={21} />
            <span>3</span>
          </button>
          <button className="icon-button" type="button" aria-label="Help">
            <HelpCircle size={23} />
          </button>
          <div className="user-chip">
            <span className="avatar blue">AB</span>
            <div>
              <strong>Admin User</strong>
              <span>Administrator</span>
            </div>
            <ChevronDown size={18} />
          </div>
        </div>
      </header>

      <div className="executive-content">
        <div className="dashboard-header-card">
          <div className="dashboard-title-group">
            <h1>ClaimsAI Executive Dashboard</h1>
            <p>Executive overview of claims performance and payment analytics</p>
          </div>
          <div className="dashboard-controls">
            <div className="control-wrap date-control">
              <button
                className="date-range"
                type="button"
                aria-expanded={openMenu === 'date'}
                onClick={() => setOpenMenu(openMenu === 'date' ? null : 'date')}
              >
                <CalendarDays size={18} />
                <span>{dateRange.from}</span>
                <ArrowRight size={15} />
                <span>{dateRange.to}</span>
                <ChevronDown size={16} />
              </button>
              {openMenu === 'date' ? (
                <DateMenu dateRange={dateRange} onChange={setDateRange} />
              ) : null}
            </div>
            <SelectMenu
              label="Payer"
              value={payer}
              menuKey="payer"
              openMenu={openMenu}
              setOpenMenu={setOpenMenu}
              options={payerOptions}
              onChange={setPayer}
            />
            <SelectMenu
              label="Plan"
              value={plan}
              menuKey="plan"
              openMenu={openMenu}
              setOpenMenu={setOpenMenu}
              options={planOptions}
              onChange={setPlan}
            />
            <SelectMenu
              label="Provider Group"
              value={providerGroup}
              menuKey="group"
              openMenu={openMenu}
              setOpenMenu={setOpenMenu}
              options={providerOptions}
              onChange={setProviderGroup}
              wide
            />
            <div className="control-wrap filter-control">
              <button
                className="outline-button"
                type="button"
                aria-expanded={openMenu === 'filters'}
                onClick={() => setOpenMenu(openMenu === 'filters' ? null : 'filters')}
              >
                <Filter size={17} />
                Filters
              </button>
              {openMenu === 'filters' ? (
                <FilterMenu filters={filters} setFilters={setFilters} />
              ) : null}
            </div>
            <button className="text-button" type="button" onClick={resetFilters}>Reset</button>
            <button className="export-button" type="button">
              <Download size={18} />
              Export
            </button>
          </div>
        </div>

        <ClaimFlow />

        <div className="dashboard-metrics">
          {dashboardMetrics.map((metric) => (
            <DashboardMetric key={metric.label} {...metric} />
          ))}
        </div>

        <RecentClaims
          claims={filteredClaims.slice(0, 10)}
          featured
          compact
          onOpenClaim={onOpenClaim}
          onViewAllClaims={onViewAllClaims}
        />

        <footer className="dashboard-footer">
          <span>All amounts in USD</span>
          <Info size={16} />
          <span>Data as of {formatDate(defaultDateRange.to)}</span>
          <RefreshCw size={17} />
        </footer>
      </div>
    </section>
  )
}

function Card({ children, className = '' }) {
  return <div className={`card ${className}`}>{children}</div>
}

function SectionTitle({ title, action, onAction }) {
  return (
    <div className="section-title">
      <h2>{title}</h2>
      {action ? <button type="button" onClick={onAction}>{action}</button> : null}
    </div>
  )
}

function MetricCard({ label, value, delta, dir = 'up', note, compact = false }) {
  const TrendIcon = dir === 'down' ? TrendingDown : TrendingUp

  return (
    <Card className={`metric-card ${compact ? 'compact' : ''}`}>
      <span className="metric-label">{label}</span>
      <div className="metric-value-row">
        <strong>{value}</strong>
        {delta ? (
          <span className={`metric-delta ${dir}`}>
            <TrendIcon size={13} />
            {delta}
          </span>
        ) : null}
      </div>
      <small>{note || 'vs prior 12 months'}</small>
    </Card>
  )
}

function SelectedClaimDetail({ claim }) {
  const payerContact = getPayerContact(claim.payer)
  const financialSummary = [
    { label: 'Total Charge', value: formatCurrency(claim.totalCharge), note: `${claim.units || 1} unit(s) billed`, tone: 'blue' },
    { label: 'Allowed', value: formatCurrency(claim.allowed), note: `${formatCurrency(claim.adjustment)} adjusted`, tone: 'teal' },
    { label: 'Paid', value: formatCurrency(claim.paid), note: claim.paid > 0 ? 'Payer payment posted' : 'No payment posted', tone: 'green' },
    { label: 'Patient Resp.', value: formatCurrency(claim.patientResp), note: 'Member balance', tone: 'violet' },
  ]
  const claimFacts = [
    ['Claim Number', claim.number],
    ['Member ID', claim.memberId],
    ['Patient', claim.patient],
    ['Date of Service', formatDate(claim.dos)],
    ['Submitted', formatDate(claim.submissionDate)],
    ['Last Updated', claim.createdAt],
  ]
  const providerFacts = [
    ['Billing Provider', claim.billingProvider],
    ['Billing NPI', claim.billingProviderNpi],
    ['Rendering Provider', claim.renderingProvider],
    ['Rendering NPI', claim.renderingProviderNpi],
    ['Payer', claim.payer],
    ['Payer Contact', payerContact],
    ['Payer ID', claim.payerId],
  ]
  const clinicalFacts = [
    ['Place of Service', getService(claim)],
    ['Procedure', `${claim.cptCode} ${claim.cptDescription}`],
    ['Diagnosis', getDiagnosis(claim)],
    ['Filing Indicator', claim.filingIndicator || '-'],
    ['Prior Auth', claim.priorAuth || 'Not provided'],
    ['Denial Reason', claim.denialReason || 'None'],
  ]

  return (
    <div className="claim-detail-stack">
      <Card className="claim-hero-card">
        <div className="claim-hero-main">
          <h1>{claim.number}</h1>
          <p>{claim.patient} · {claim.memberId} · {claim.payer} · {payerContact}</p>
        </div>
        <div className="claim-hero-meta">
          <span>DOS</span>
          <strong>{formatDate(claim.dos)}</strong>
          <span>Submitted</span>
          <strong>{formatDate(claim.submissionDate)}</strong>
          <span>Status</span>
          <strong className="claim-hero-status">
            <span className={`claim-status ${statusClass(claim.status)}`} title={claim.status}>{statusLabel(claim.status)}</span>
          </strong>
        </div>
      </Card>

      <div className="claim-financial-grid">
        {financialSummary.map((item) => (
          <Card className={`claim-financial-card ${item.tone}`} key={item.label}>
            <span>{item.label}</span>
            <strong>{item.value}</strong>
            <small>{item.note}</small>
          </Card>
        ))}
      </div>

      <div className="claim-info-grid">
        <ClaimInfoPanel title="Claim Overview" rows={claimFacts} />
        <ClaimInfoPanel title="Provider & Payer" rows={providerFacts} />
        <ClaimInfoPanel title="Service Details" rows={clinicalFacts} />
      </div>
    </div>
  )
}

function ClaimInfoPanel({ title, rows }) {
  return (
    <Card className="claim-info-panel">
      <SectionTitle title={title} />
      <dl>
        {rows.map(([label, value]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
    </Card>
  )
}

function ClaimReasonCard({ claim }) {
  return (
    <Card className="claim-reason-card">
      <div className="claim-reason-header">
        <div>
          <h2>Adjudication Reasons</h2>
          <p>Select the information button beside a value to see its source and how it is displayed.</p>
        </div>
        <span className={`claim-status ${statusClass(claim.status)}`}>{statusLabel(claim.status)}</span>
      </div>
      <div className="reason-card-grid">
        {getClaimReasonRows(claim).map((row) => (
          <div className="reason-card" key={row.field}>
            <div className="reason-card-heading">
              <div>
                <span>{row.field}</span>
                <strong>{row.value}</strong>
              </div>
              <details className="reason-info">
                <summary aria-label={`Explain ${row.field}`} title={`Explain ${row.field}`}>
                  <Info size={16} aria-hidden="true" />
                </summary>
                <div className="reason-info-panel">
                  <strong>Why this is shown</strong>
                  <p>{row.reason}</p>
                  <dl>
                    <div>
                      <dt>Source</dt>
                      <dd>{row.source}</dd>
                    </div>
                    <div>
                      <dt>How it works</dt>
                      <dd>{row.method}</dd>
                    </div>
                  </dl>
                </div>
              </details>
            </div>
          </div>
        ))}
      </div>
    </Card>
  )
}

function getClaimReasonRows(claim) {
  const procedure = `${claim.cptCode} ${claim.cptDescription}`.trim()
  const statusReason = claim.status === 'Denied'
    ? `The claim is denied by ${claim.payer}${claim.denialReason ? ` because of ${claim.denialReason}` : ''}.`
    : `${claim.payer} returned claim status code ${claim.statusCode} (${claim.status}) for this ${claim.filingIndicator || '837'} filing.`

  return [
    {
      field: 'Status',
      value: statusLabel(claim.status),
      reason: statusReason,
      source: 'Current enriched 837 claim · Claim_Status_Code, Claim_Status_Description, Denial_Reason, Payer_Name, and Claim_Filing_Indicator.',
      method: 'The source status description is mapped to a concise display label. The payer, filing indicator, and denial reason add context when those source fields are present.',
    },
    {
      field: 'Total Charge',
      value: formatCurrency(claim.totalCharge),
      reason: `${claim.billingProvider} billed ${claim.units || 1} unit(s) for ${procedure} at ${getService(claim)}.`,
      source: 'Current enriched 837 claim · Charge_Amount, Units, CPT_Code, Billing_Provider_Name, and Place_of_Service_Description.',
      method: 'The displayed amount is Charge_Amount from the selected claim. The remaining source fields explain who billed it and which service the charge represents.',
    },
    {
      field: 'Allowed',
      value: formatCurrency(claim.allowed),
      reason: `${claim.payer} adjudicated the billed charge to the allowed amount after contract and claim edits. Adjustment recorded: ${formatCurrency(claim.adjustment)}.`,
      source: 'Current enriched 837 claim · Allowed_Amount, Adjustment_Amount, and Payer_Name.',
      method: 'The displayed amount is Allowed_Amount from the selected claim. Adjustment_Amount explains the recorded difference created during adjudication.',
    },
    {
      field: 'Paid',
      value: formatCurrency(claim.paid),
      reason: claim.paid > 0
        ? `${claim.payer} paid this amount toward the allowed claim after adjudication and member responsibility were applied.`
        : `No payer payment is recorded for this claim, typically because the claim is denied, pending, or forwarded to another payer.`,
      source: 'Current enriched 837 claim · Paid_Amount, Allowed_Amount, Patient_Responsibility, and Payer_Name.',
      method: 'The displayed value is Paid_Amount from the selected claim. It is a payer-spend source value and is not calculated by the browser.',
    },
    {
      field: 'Patient Resp.',
      value: formatCurrency(claim.patientResp),
      reason: `This is the member responsibility assigned on the claim, such as deductible, copay, coinsurance, or non-covered balance.`,
      source: 'Current enriched 837 claim · Patient_Responsibility.',
      method: 'The displayed value is Patient_Responsibility from the selected claim. It is shown directly and is not inferred from the charge, allowed, or paid amounts.',
    },
  ]
}

function RecentEncounters({
  claims,
  title = 'All Encounters',
  onSelectMember,
  onOpenClaim,
  emptyMessage = 'No encounters match that patient name or member ID.',
  footer,
}) {
  return (
    <Card className="encounters-card">
      <SectionTitle title={title} />
      <div className="table-wrap">
        <table className="data-table encounters-table">
          <thead>
            <tr>
              <th>Date</th>
              <th>Member ID</th>
              <th>Claim #</th>
              <th>Patient</th>
              <th>Provider</th>
              <th>Place of Service</th>
              <th>Diagnosis (Primary)</th>
              <th>Status</th>
              <th>Billed</th>
              <th>Patient Responsibility</th>
            </tr>
          </thead>
          <tbody>
            {claims.length ? claims.map((claim) => (
              <tr key={claim.claimId}>
                <td>{formatDate(claim.dos)}</td>
                <td>
                  <button className="member-link-button" type="button" onClick={() => onSelectMember(claim.memberId)}>
                    {claim.memberId}
                  </button>
                </td>
                <td>
                  <button className="claim-link-button" type="button" onClick={() => onOpenClaim?.(claim)}>
                    {claim.number}
                  </button>
                </td>
                <td>{claim.patient}</td>
                <td>{claim.billingProvider}</td>
                <td>{getService(claim)}</td>
                <td><span className="code-cell">{claim.diagnosisCode}</span>{claim.diagnosisDescription}</td>
                <td>
                  <span className={`claim-status ${statusClass(claim.status)}`} title={claim.status}>
                    {statusLabel(claim.status)}
                  </span>
                </td>
                <td>{formatCurrency(claim.totalCharge)}</td>
                <td>{formatCurrency(claim.patientResp)}</td>
              </tr>
            )) : (
              <tr>
                <td className="empty-table-cell" colSpan="10">{emptyMessage}</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      {footer}
    </Card>
  )
}

function ClaimTimeline({ claim }) {
  const steps = [
    [formatDate(claim.dos), 'Encounter', claim.placeOfService, 'done'],
    [formatDate(claim.submissionDate), 'Claim Created', '837 Generated', 'done'],
    [formatDate(claim.submissionDate), '837 Submitted', claim.transactionVersion, 'done'],
    [claim.status === 'Denied' ? formatDate(claim.submissionDate) : '-', claim.status === 'Denied' ? 'Denied' : 'Adjudicated', claim.denialReason || claim.status, claim.status === 'Denied' ? 'current' : 'done'],
    ['-', 'Payment Posted', claim.paid > 0 ? formatCurrency(claim.paid) : 'Pending', claim.paid > 0 ? 'done' : 'pending'],
    ['-', 'Patient Responsibility', formatCurrency(claim.patientResp), 'future'],
  ]

  return (
    <Card className="timeline-card">
      <SectionTitle title="Claim Timeline" />
      <div className="timeline">
        {steps.map(([date, title, note, state]) => (
          <div className={`timeline-step ${state}`} key={`${title}-${date}`}>
            <span className="timeline-dot"></span>
            <strong>{date}</strong>
            <span>{title}</span>
            <small>{note}</small>
          </div>
        ))}
      </div>
    </Card>
  )
}

function ProviderInformation({ claim }) {
  const providerRows = [
    { icon: Hospital, title: 'Billing Provider', value: claim.billingProvider, note: `NPI ${claim.billingProviderNpi}` },
    { icon: Stethoscope, title: 'Rendering Provider', value: claim.renderingProvider, note: `NPI ${claim.renderingProviderNpi}` },
  ]

  return (
    <Card className="provider-info">
      <SectionTitle title="Provider Information" />
      {providerRows.map((row) => {
        const Icon = row.icon
        return (
          <div className="provider-row" key={row.title}>
            <span className="soft-icon"><Icon size={24} /></span>
            <div>
              <span>{row.title}</span>
              <strong>{row.value}</strong>
              <small>{row.note}</small>
            </div>
          </div>
        )
      })}
      <div className="provider-mini-grid">
        <div className="provider-row compact">
          <span className="soft-icon"><Building2 size={21} /></span>
          <div>
            <span>Place of Service</span>
            <strong>{claim.placeOfServiceCode}</strong>
            <small>{claim.placeOfService}</small>
          </div>
        </div>
        <div className="provider-row compact">
          <span className="soft-icon payer"><ShieldCheck size={21} /></span>
          <div>
            <span>Primary Payer</span>
            <strong>{claim.payer}</strong>
          </div>
        </div>
      </div>
    </Card>
  )
}

function ProviderKpis({ claim }) {
  const { claimsData } = useAppData()
  const providerKpis = buildProviderKpis(claim, claimsData)

  return (
    <Card className="provider-kpis">
      <SectionTitle title="Provider KPIs (YTD)" />
      <div className="provider-kpi-grid">
        {providerKpis.map((kpi) => (
          <div className="provider-kpi" key={kpi.label}>
            <span>{kpi.label}</span>
            <strong>{kpi.value}</strong>
            {kpi.delta ? (
              <small className={kpi.dir === 'down' ? 'down' : 'up'}>{kpi.delta}</small>
            ) : null}
            <em>vs prior YTD</em>
          </div>
        ))}
      </div>
    </Card>
  )
}

function ProviderLlmPanel({ claim, onCasePrediction }) {
  const [result, setResult] = useState(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [modalOpen, setModalOpen] = useState(false)
  const predictionReady = Boolean(result?.benchmark_summary)

  useEffect(() => {
    setResult(null)
    setError('')
    setModalOpen(false)
  }, [claim.number, claim.claimId])

  const generatePrediction = async () => {
    setLoading(true)
    setError('')
    setModalOpen(true)
    try {
      const payload = await fetchJson(
        `/api/predictions/payer/claim/${encodeURIComponent(claim.claimId || claim.number)}`,
      )
      setResult(payload)
    } catch (requestError) {
      setError(requestError.message || 'Payer savings prediction could not be loaded.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <Card className="provider-llm-card">
      <div className="provider-llm-header">
        <div>
          <span className="provider-llm-kicker"><Sparkles size={14} /> Two financial perspectives</span>
          <h2>Claim Financial Predictions</h2>
          <p>Provider Forecast estimates payment and revenue outcomes. Payer Savings uses a separate rule-based matched-cohort benchmark.</p>
        </div>
        <div className="provider-llm-actions">
          <button className="llm-secondary-button" type="button" onClick={onCasePrediction}>Open Provider Forecast</button>
          <button className="llm-primary-button" type="button" onClick={generatePrediction} disabled={loading}>
            {loading ? <RefreshCw className="spin" size={16} /> : <Sparkles size={16} />}
            {loading ? 'Generating…' : 'Open Payer Savings'}
          </button>
        </div>
      </div>

      {!result && !error ? (
        <div className="llm-intro">These views use different methods and their dollar amounts should not be compared as if they were the same prediction.</div>
      ) : null}
      {error ? <div className="llm-config-note error">{error}</div> : null}
      {predictionReady ? <div className="llm-intro">Payer savings prediction ready for {result.target?.selected_claim_id}. Open it to review the cohort benchmark, calculation and supporting evidence.</div> : null}
      {predictionReady ? <button className="llm-secondary-button" type="button" onClick={() => setModalOpen(true)}>Open Payer Savings Prediction</button> : null}
      {modalOpen ? <ProviderLlmModal claim={claim} result={result} loading={loading} error={error} onClose={() => setModalOpen(false)} onRetry={generatePrediction} onOpenProviderForecast={onCasePrediction} /> : null}
    </Card>
  )
}

function ProviderLlmModal({ claim, result, loading, error, onClose, onRetry, onOpenProviderForecast }) {
  useEffect(() => {
    const onKeyDown = (event) => { if (event.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKeyDown)
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      document.body.style.overflow = previousOverflow
    }
  }, [onClose])

  return createPortal(
    <div className="provider-llm-modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}>
      <div className="provider-llm-modal payer-cohort-modal" role="dialog" aria-modal="true" aria-labelledby="provider-llm-modal-title">
        <header className="provider-llm-modal-header">
          <div>
            <h2 id="provider-llm-modal-title">Value-Based Intelligence</h2>
            <p>Historical comparison against clinically relevant member cohorts</p>
            {result?.member ? <small>Member: {result.member.member_id} · Condition: {result.member.condition?.icd10} · Observation Period: {result.observation?.days} days ({payerDate(result.observation?.start_date)} – {payerDate(result.observation?.end_date)})</small> : null}
          </div>
          <button className="provider-llm-close" type="button" aria-label="Close Prediction" onClick={onClose}><X size={20} /></button>
        </header>
        <div className="provider-llm-modal-body">
          {loading ? <div className="llm-modal-state"><RefreshCw className="spin" size={22} /> Building the observation window and matched cohort…</div> : null}
          {!loading && error ? <div className="llm-config-note error"><span>{error}</span><button className="llm-primary-button" type="button" onClick={onRetry}>Retry prediction</button></div> : null}
          {!loading && (result?.benchmark_summary ? <ClaimPayerPredictionResult result={result} /> : result?.historical_cohort ? <ClaimPayerPredictionResult result={result} /> : null)}
          {!loading && !error && result && !result?.historical_cohort ? (
            <div className="llm-modal-state provider-response-error">
              The prediction service returned an unsupported response. Please retry after the deployment finishes.
            </div>
          ) : null}
        </div>
        <footer className="payer-cohort-modal-footer">
          <button className="payer-secondary-button" type="button" onClick={onClose}>Close</button>
          {result?.historical_cohort ? <button className="payer-secondary-button" type="button" onClick={onOpenProviderForecast}>Open Separate Provider Forecast</button> : null}
        </footer>
      </div>
    </div>,
    document.body,
  )
}

function ClaimPayerPredictionResult({ result }) {
  const [showAllEvidence, setShowAllEvidence] = useState(false)
  const member = result.member || {}
  const observation = result.observation || {}
  const target = result.target || {}
  const cohort = result.historical_cohort || {}
  const quality = result.cohort_quality || {}
  const utilization = result.utilization || {}
  const cptAnalysis = result.cpt_analysis || []
  const savings = result.potential_savings || {}
  const evidence = result.evidence || []
  const confidence = result.confidence || {}
  const timeline = result.timeline || []
  const carePattern = result.care_pattern || {}
  const validation = result.validation || {}
  
  const visibleEvidence = showAllEvidence ? evidence : evidence.slice(0, 5)

  return (
    <div className="payer-result claim-payer-result v2-payer-result">
      <aside className="prediction-perspective-note payer-perspective-note">
        <span className="prediction-perspective-icon"><Landmark size={19} /></span>
        <div>
          <span>Value-Based Intelligence</span>
          <strong>Historical claims intelligence engine</strong>
          <p>Identifies condition-specific utilization patterns, compares current members against clinically relevant historical cohorts, and surfaces claim-level evidence.</p>
        </div>
        <dl>
          <div><dt>Current Cost</dt><dd>{payerCurrency(target.allowed_amount)}</dd></div>
          <div><dt>Potential Savings Opportunity</dt><dd>{payerCurrency(savings.median_opportunity)}</dd></div>
        </dl>
      </aside>

      <section className="payer-result-section">
        <h3>1. Member Overview</h3>
        <dl className="payer-summary-meta">
          <div><dt>Target Member</dt><dd>{member.member_id}</dd></div>
          <div><dt>Condition</dt><dd>{member.condition?.icd10} - {member.condition?.description}</dd></div>
          <div><dt>Observation Period</dt><dd>{payerDate(observation.start_date)} – {payerDate(observation.end_date)} ({observation.days} days)</dd></div>
          <div><dt>Target Claims</dt><dd>{payerNumber(target.claim_count)}</dd></div>
          <div><dt>Total Allowed</dt><dd>{payerCurrency(target.allowed_amount)}</dd></div>
          <div><dt>Total Paid</dt><dd>{payerCurrency(target.paid_amount)}</dd></div>
        </dl>
      </section>

      <section className="payer-result-section">
        <h3>2. Historical Benchmark</h3>
        <p className={`payer-peer-note ${quality.quality === 'LOW' ? 'low' : ''}`}>{quality.members_used} {quality.selection_level} peers used · Confidence: {confidence.level}</p>
        <dl className="payer-summary-meta">
          <div><dt>Historical Members</dt><dd>{cohort.members}</dd></div>
          <div><dt>Median Claims</dt><dd>{payerNumber(cohort.median_claim_count)}</dd></div>
          <div><dt>P25 Cost</dt><dd>{payerCurrency(cohort.p25_allowed_amount)}</dd></div>
          <div><dt>Median Cost</dt><dd>{payerCurrency(cohort.median_allowed_amount)}</dd></div>
          <div><dt>Average Cost</dt><dd>{payerCurrency(cohort.average_allowed_amount)}</dd></div>
          <div><dt>P75 Cost</dt><dd>{payerCurrency(cohort.p75_allowed_amount)}</dd></div>
        </dl>
      </section>

      <section className="payer-result-section">
        <h3>3. Utilization Comparison</h3>
        <div className="payer-table-wrap">
          <table>
            <thead><tr><th>CPT</th><th>Target Freq</th><th>Historical Med Freq</th><th>Difference</th><th>Target Cost</th></tr></thead>
            <tbody>
              {cptAnalysis.map((cpt, idx) => (
                <tr key={idx}>
                  <td>{cpt.cpt}</td>
                  <td>{cpt.target_frequency}</td>
                  <td>{cpt.historical_frequency}</td>
                  <td>{cpt.difference > 0 ? `+${cpt.difference}` : cpt.difference}</td>
                  <td>{payerCurrency(cpt.target_cost)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="payer-timeline-block">
          <h4>Timeline &amp; Care Pattern</h4>
          <div className="payer-journey">{carePattern.journey || 'No services recorded'}</div>
          <div className="payer-table-wrap">
            <table>
              <thead><tr><th>Date</th><th>CPT</th><th>Category</th><th>Days Since Prior</th><th>Prior 30d Claims</th><th>Allowed</th></tr></thead>
              <tbody>
                {timeline.map((event, idx) => (
                  <tr key={`${event.date}-${idx}`}>
                    <td>{payerDate(event.date)}</td>
                    <td>{event.cpt}</td>
                    <td>{event.category}</td>
                    <td>{event.days_since_prior == null ? '—' : payerNumber(event.days_since_prior)}</td>
                    <td>{event.prior_30d_claims}</td>
                    <td>{payerCurrency(event.allowed)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </section>

      <section className="payer-result-section">
        <h3>4. Potential Savings</h3>
        <div className="payer-calculation-overview">
          <div><span>Current Cost</span><strong>{payerCurrency(savings.current_amount)}</strong></div>
          <div><span>Historical Benchmark</span><strong>{payerCurrency(savings.median_benchmark)}</strong><small>{savings.benchmark_method}</small></div>
          <div><span>Potential Historical Savings Opportunity</span><strong>{payerCurrency(savings.median_opportunity)}</strong></div>
        </div>
        <div className="payer-range">
          <div><span>Historical Benchmark Range</span><strong>{payerCurrency(savings.p25_benchmark)} – {payerCurrency(savings.p75_benchmark)}</strong></div>
        </div>
        {(savings.claim_level_attribution || []).length ? (
          <div className="payer-timeline-block">
            <h4>Claim-Level Savings Attribution</h4>
            <div className="payer-table-wrap">
              <table>
                <thead><tr><th>Claim</th><th>CPT</th><th>Service Date</th><th>Allowed</th><th>Indicative Contribution</th></tr></thead>
                <tbody>
                  {(savings.claim_level_attribution || []).map((row, index) => (
                    <tr key={index}>
                      <td>{row.claim_id}</td>
                      <td>{row.cpt}</td>
                      <td>{payerDate(row.service_date)}</td>
                      <td>{payerCurrency(row.allowed)}</td>
                      <td>{payerCurrency(row.contribution)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ) : null}
      </section>

      <section className="payer-result-section">
        <h3>5. Historical Evidence</h3>
        <div className="payer-table-wrap evidence">
          <table>
            <thead><tr><th>Member ID</th><th>Condition Match</th><th>Claims</th><th>Historical Cost</th><th>Similarity</th></tr></thead>
            <tbody>
              {visibleEvidence.map((row, index) => (
                <tr key={index}>
                  <td>{row.historical_member_id}</td>
                  <td>{row.condition_match}</td>
                  <td>{row.claim_count}</td>
                  <td>{payerCurrency(row.historical_cost)}</td>
                  <td>{row.similarity_score}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {!showAllEvidence && evidence.length > visibleEvidence.length ? <button className="payer-evidence-button" type="button" onClick={() => setShowAllEvidence(true)}>View All Evidence</button> : null}
        {(validation.method || validation.note) ? (
          <div className="payer-validation-note" role="note">
            <Info size={18} />
            <div>
              <strong>Validation &amp; methodology</strong>
              <p>{validation.note || ''} {validation.method ? `Benchmark: ${validation.method}.` : ''} {validation.cohort_members != null ? `${validation.cohort_members} comparable member(s) used.` : ''}</p>
            </div>
          </div>
        ) : null}
      </section>
    </div>
  )
}

function ProviderRenderPredictionResult({ result }) {
  const forecast = result.forecast || {}
  const facts = result.actual_claim_facts || {}
  const opportunity = result.provider_financial_opportunity_summary || {}
  const metrics = result.provider_financial_metrics || {}
  const basis = result.prediction_basis || {}
  const savings = result.where_provider_money_can_be_saved || {}
  const scenario = result.provider_money_scenario_map || {}
  const action = savings.best_action || {}
  const validatedOpportunity = opportunity.validated_real_savings || savings.validated_real_savings || {}
  const confidence = forecast.confidence || opportunity.confidence || {}
  const recommendations = Array.isArray(result.recommended_actions) ? result.recommended_actions : []
  const risks = Array.isArray(result.risk_drivers) ? result.risk_drivers : []
  const limitations = Array.isArray(result.limitations) ? result.limitations : []

  return (
    <div className="provider-llm-workspace">
      <main className="provider-analysis-scroll">
        <div className="provider-llm-result provider-money-result">
        {result.configured === false && result.message ? (
          <div className="provider-deployment-note" role="note">
            <Info size={19} />
            <div><strong>Deterministic forecast ready</strong><p>{result.message} The calculated forecast below remains available.</p></div>
          </div>
        ) : null}

        <section className="llm-wide-section financial-prediction-snapshot">
          <div className="llm-section-heading"><span>Provider Financial Forecast</span><small>{basis.model_version || confidence.model_version}</small></div>
          <div className="prediction-primary-grid">
            <article><span>Predicted provider payment</span><strong>{formatOptionalCurrency(forecast.predicted_paid?.value ?? metrics.provider_expected_reimbursement)}</strong><small>{formatPredictionRange(forecast.predicted_paid)}</small></article>
            <article><span>Predicted allowed</span><strong>{formatOptionalCurrency(forecast.predicted_allowed?.value)}</strong><small>{formatPredictionRange(forecast.predicted_allowed)}</small></article>
            <article><span>Contractual adjustment</span><strong>{formatOptionalCurrency(forecast.predicted_adjustment?.value ?? opportunity.expected_contractual_adjustment)}</strong><small>{formatPredictionRange(forecast.predicted_adjustment)}</small></article>
            <article><span>Patient responsibility</span><strong>{formatOptionalCurrency(forecast.predicted_patient_responsibility?.value ?? metrics.predicted_patient_balance)}</strong><small>{formatPredictionRange(forecast.predicted_patient_responsibility)}</small></article>
          </div>
          <dl className="prediction-detail-grid">
            <div><dt>Denial exposure</dt><dd>{formatOptionalCurrency(opportunity.expected_denial_revenue_exposure ?? metrics.expected_denial_exposure)}</dd></div>
            <div><dt>Repeat-payment exposure</dt><dd>{formatOptionalCurrency(opportunity.expected_repeat_provider_payment_exposure ?? metrics.expected_repeat_provider_payment_exposure)}</dd></div>
            <div><dt>Provider payment gap</dt><dd>{formatOptionalCurrency(metrics.provider_payment_gap)}</dd></div>
            <div><dt>Model confidence</dt><dd>{formatProbability(confidence.score)} · {confidence.level}</dd></div>
            <div><dt>Historical claims used</dt><dd>{basis.peer_claims_used}</dd></div>
            <div><dt>Matching level</dt><dd>{basis.matching_level}</dd></div>
          </dl>
        </section>

        <section className="llm-wide-section provider-savings-section">
          <div className="llm-section-heading"><span>Supported Financial Opportunity</span><small>{opportunity.best_savings_phase || action.stage}</small></div>
          <div className="provider-supported-summary">
            <article>
              <span>Verified recovery amount</span>
              <strong>{validatedOpportunity.available ? formatOptionalCurrency(validatedOpportunity.amount) : 'No verified positive amount'}</strong>
              <p>{validatedOpportunity.reason || opportunity.opportunity_reason}</p>
            </article>
            <article>
              <span>Denial revenue exposure</span>
              <strong>{formatOptionalCurrency(opportunity.expected_denial_revenue_exposure ?? metrics.expected_denial_exposure)}</strong>
              <p>Forecast exposure is kept separate from verified recovery.</p>
            </article>
            <article>
              <span>Repeat-payment exposure</span>
              <strong>{formatOptionalCurrency(opportunity.expected_repeat_provider_payment_exposure ?? metrics.expected_repeat_provider_payment_exposure)}</strong>
              <p>90-day forecast based on earlier related claims.</p>
            </article>
          </div>
          <div className="best-action-card">
            <span>{action.stage || opportunity.best_savings_phase}</span>
            <strong>{action.action || recommendations[0]?.title}</strong>
            <p>{action.reason || opportunity.supporting_reason || opportunity.opportunity_reason}</p>
            <div className="savings-action-meta">
              <small>Owner: {action.owner || opportunity.responsible_operational_team}</small>
              <small>Confidence: {formatProbability(action.confidence ?? confidence.score)}</small>
              <small>Evidence: {(action.affected_claim_ids || opportunity.affected_claim_ids || []).join(', ')}</small>
            </div>
          </div>
          {recommendations.length ? (
            <div className="provider-render-actions">
              {recommendations.map((item) => (
                <article key={item.code || item.rank}>
                  <b>{item.rank}</b>
                  <div><strong>{item.title}</strong><p>{item.reason}</p><small>{item.operational_owner} · {item.urgency}</small></div>
                </article>
              ))}
            </div>
          ) : null}
        </section>

        <section className="llm-wide-section actual-predicted-section">
          <div className="llm-section-heading"><span>Actual vs Predicted</span><small>{result.claim_id}</small></div>
          <div className="actual-predicted-columns">
            <article><h3>Actual claim</h3><dl><div><dt>Status</dt><dd>{facts.claim_status}</dd></div><div><dt>Charge</dt><dd>{formatOptionalCurrency(facts.charge ?? facts.charge_amount)}</dd></div><div><dt>Allowed</dt><dd>{formatOptionalCurrency(facts.allowed ?? facts.allowed_amount)}</dd></div><div><dt>Paid</dt><dd>{formatOptionalCurrency(facts.paid ?? facts.paid_amount)}</dd></div><div><dt>Patient responsibility</dt><dd>{formatOptionalCurrency(facts.patient_responsibility)}</dd></div></dl></article>
            <article><h3>Predicted result</h3><dl><div><dt>Outcome</dt><dd>{forecast.predicted_claim_outcome?.display_value}</dd></div><div><dt>Allowed</dt><dd>{formatOptionalCurrency(forecast.predicted_allowed?.value)}</dd></div><div><dt>Provider payment</dt><dd>{formatOptionalCurrency(forecast.predicted_paid?.value)}</dd></div><div><dt>Patient responsibility</dt><dd>{formatOptionalCurrency(forecast.predicted_patient_responsibility?.value)}</dd></div><div><dt>Adjustment</dt><dd>{formatOptionalCurrency(forecast.predicted_adjustment?.value)}</dd></div></dl></article>
          </div>
          {risks.length ? <div className="provider-render-risks">{risks.map((item) => <article key={item.title}><strong>{item.title}</strong><b>{item.value}</b><p>{item.reason}</p></article>)}</div> : null}
        </section>

        <section className="llm-wide-section scenario-map-section">
          <div className="llm-section-heading"><span>Provider Money Scenario Map</span><small>Encounter → prediction → provider action</small></div>
          <div className="provider-render-workflow">
            {(scenario.claim_workflow || []).map((item, index) => (
              <article className={item.selected ? 'selected' : ''} key={item.stage}>
                <b>{index + 1}</b><span>{item.stage}</span>
              </article>
            ))}
          </div>
          <div className="provider-render-scenario-grid">
            <article>
              <span>Encounter and coding</span>
              <strong>{scenario.encounter_and_coding?.cpt_code} · {scenario.encounter_and_coding?.cpt_description}</strong>
              <p>{scenario.encounter_and_coding?.diagnosis} · {scenario.encounter_and_coding?.place_of_service}</p>
              <small>{scenario.encounter_and_coding?.payer} · {scenario.encounter_and_coding?.billing_provider}</small>
            </article>
            <article>
              <span>Payment prediction</span>
              <strong>{formatOptionalCurrency(scenario.provider_claim_payment_prediction?.expected_provider_payment)}</strong>
              <p>Allowed {formatOptionalCurrency(scenario.provider_claim_payment_prediction?.predicted_allowed?.value)} · Denial {formatProbability(scenario.provider_claim_payment_prediction?.denial_probability)}</p>
              <small>Repeat probability {formatProbability(scenario.provider_claim_payment_prediction?.repeat_probability_90d)} at 90 days</small>
            </article>
            <article>
              <span>Supported action</span>
              <strong>{scenario.where_provider_money_may_be_saved?.best_next_provider_action?.stage || action.stage}</strong>
              <p>{scenario.where_provider_money_may_be_saved?.best_next_provider_action?.action || action.action}</p>
              <small>Owner: {scenario.where_provider_money_may_be_saved?.best_next_provider_action?.owner || action.owner}</small>
            </article>
          </div>
        </section>

        <section className="llm-wide-section prediction-basis-section">
          <div className="llm-section-heading"><span>Prediction Explanation</span><small>{basis.model_version}</small></div>
          <div className="layman-explanation">
            <article><span>1</span><div><strong>What this prediction means</strong><p>The model forecasts a provider payment of {formatOptionalCurrency(forecast.predicted_paid?.value)} from a predicted allowed amount of {formatOptionalCurrency(forecast.predicted_allowed?.value)}.</p></div></article>
            <article><span>2</span><div><strong>How the forecast was determined</strong><p>It used {basis.peer_claims_used} earlier claims across {basis.peer_episodes_used} peer episodes, with {basis.matching_level} matching.</p></div></article>
            <article><span>3</span><div><strong>Where the financial risk sits</strong><p>Denial exposure is {formatOptionalCurrency(opportunity.expected_denial_revenue_exposure)} and 90-day repeat-payment exposure is {formatOptionalCurrency(opportunity.expected_repeat_provider_payment_exposure)}.</p></div></article>
            <article><span>4</span><div><strong>What the provider should do next</strong><p>{action.action || recommendations[0]?.reason}</p></div></article>
            <article><span>5</span><div><strong>Confidence and limits</strong><p>{confidence.explanation} Prediction cutoff: {basis.prediction_cutoff_date}.</p></div></article>
          </div>
        </section>

        {limitations.length ? (
          <section className="llm-wide-section provider-render-limitations">
            <div className="llm-section-heading"><span>Decision-support limits</span><small>{limitations.length}</small></div>
            <ul>{limitations.map((item) => <li key={item}>{item}</li>)}</ul>
          </section>
        ) : null}
        </div>
      </main>
      <ProviderPredictionChat key={`${basis.model_version}.${basis.calculation_version}.${result.claim_id}`} result={result} />
    </div>
  )
}

const CATEGORY_LABELS = {
  underpayment: 'Payment underpayment',
  correctable_denial: 'Correctable denial',
  excessive_adjustment: 'Excessive adjustment',
  patient_balance: 'Patient balance',
  authorization: 'Authorization',
  referral: 'Referral',
  duplicate_or_correction: 'Duplicate or correction',
  potentially_avoidable_episode_spend: 'Potentially avoidable episode spend',
}

const MONEY_ITEM_KEYS = new Set([
  'charge', 'allowed', 'paid', 'patient_responsibility', 'adjustment',
  'expected_reimbursement', 'contract_allowed', 'patient_payment_received',
  'predicted_allowed', 'predicted_paid', 'recoverable_now',
  'predicted_provider_payment', 'predicted_patient_responsibility',
  'predicted_contractual_adjustment', 'actual_paid', 'recovered_amount',
  'outstanding_patient_balance',
  'supported_avoidable_spend', 'predicted_avoidable_spend',
  'predicted_avoidable_provider_payment', 'expected_extra_repeat_allowed_cost',
  'prediction_low', 'prediction_high',
  'future_denial_exposure', 'future_repeat_payment_exposure',
  'amount_addressed',
])
const PROBABILITY_ITEM_KEYS = new Set(['denial_probability', 'repeat_probability_30d', 'repeat_probability_60d', 'repeat_probability_90d', 'avoidable_given_repeat_probability', 'confidence'])
const SCENARIO_ITEM_LABELS = {
  predicted_avoidable_spend: 'Expected Avoidable Repeat Cost',
  predicted_avoidable_provider_payment: 'Expected Avoidable Provider Payment',
}

function readableLabel(value) {
  return String(value || '').replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase())
}

const SECTION_PURPOSES = {
  1: 'Looks only at claims dated before this claim. This gives the app historical context without using the current result as evidence.',
  2: 'Shows the facts recorded on the selected claim. These values come from the workbook and are not predictions.',
  3: 'Shows how the procedure and diagnosis codes are grouped so the app can find reasonably comparable earlier claims.',
  4: 'Lists earlier claims that look similar to the selected claim. Their IDs are shown so a reviewer can inspect the comparison.',
  5: 'Checks whether related claims occurred close together in time. A short gap is a review signal, not proof that care was unnecessary.',
  6: 'Estimates possible future amounts and risks from earlier matched claims. These are forecasts, not amounts already owed or paid.',
  8: 'Shows where the selected claim and comparison evidence came from so the result can be checked against the workbook.',
  9: 'Turns the strongest supported result into a suggested follow-up. A person should review the evidence before acting.',
}

const SECTION_TITLES = {
  1: 'What happened before this claim?',
  2: 'What is recorded on this claim?',
  3: 'How does the app group this claim?',
  4: 'Which earlier claims are similar?',
  5: 'Did related claims happen close together?',
  6: 'What does the history predict?',
  7: 'Is there money to follow up now?',
  8: 'Where did the evidence come from?',
  9: 'What should the team do next?',
}

const STEP_SIX_ITEM_ORDER = [
  'predicted_allowed',
  'predicted_provider_payment',
  'predicted_patient_responsibility',
  'predicted_contractual_adjustment',
  'denial_probability',
  'future_denial_exposure',
  'repeat_probability_90d',
  'predicted_avoidable_spend',
  'predicted_avoidable_provider_payment',
]

const FIELD_EXPLANATIONS = {
  earlier_claims: {
    title: 'Earlier claims for this member',
    how: 'The app counts this member’s claims with a service date before the selected claim.',
    why: 'This shows how much member history is available for comparison.',
    tag: 'Recorded fact',
  },
  episode_id: {
    title: 'Episode ID',
    how: 'The backend assigns an identifier to claims grouped into the same diagnosis-related episode.',
    why: 'It lets a reviewer trace which claims the app treated as part of the same episode.',
    tag: 'Tracking ID',
  },
  peer_evidence_count: {
    title: 'Earlier comparison claims',
    how: 'The prediction engine counts earlier claims selected by its matching rules.',
    why: 'More relevant earlier claims usually provide a firmer basis for a forecast.',
    tag: 'Evidence count',
  },
  previous_denials: {
    title: 'Earlier denied claims',
    how: 'The app counts earlier claims for this member recorded as denied or rejected.',
    why: 'This is historical context. It does not mean the selected claim will be denied.',
    tag: 'Recorded history',
  },
  same_cpt_icd_claims: {
    title: 'Earlier claims with the same codes',
    how: 'The app counts earlier claims with the same procedure code and diagnosis grouping.',
    why: 'These claims may be useful comparisons. A matching code does not by itself mean the service was duplicated.',
    tag: 'Code match',
  },
  authorization: {
    title: 'Prior authorization status',
    how: 'Copied from the claim’s Authorization_Status workbook field.',
    why: 'It tells the reviewer whether the workbook records an authorization issue. “N/A” means no usable status was supplied.',
    tag: 'Recorded fact',
  },
  cpt: {
    title: 'Procedure code (CPT)',
    how: 'Copied from the selected claim. The code identifies the billed service or procedure.',
    why: 'The app uses it as one of the fields for finding earlier comparable claims.',
    tag: 'Recorded fact',
  },
  diagnosis: {
    title: 'Diagnosis code (ICD-10)',
    how: 'Copied from the selected claim. The code records the diagnosis associated with the billed service.',
    why: 'The app uses the diagnosis family for grouping; it does not determine medical necessity.',
    tag: 'Recorded fact',
  },
  payer: {
    title: 'Payer',
    how: 'Copied from the selected claim.',
    why: 'Payment amounts and matching rules can differ by payer.',
    tag: 'Recorded fact',
  },
  place_of_service: {
    title: 'Place of service',
    how: 'Copied from the selected claim. It describes where the service was provided.',
    why: 'The setting is one of the fields used when comparing claims.',
    tag: 'Recorded fact',
  },
  provider: {
    title: 'Billing provider',
    how: 'Copied from the selected claim.',
    why: 'The provider can affect contracted rates and the relevance of earlier comparisons.',
    tag: 'Recorded fact',
  },
  referral: {
    title: 'Referral status',
    how: 'Copied from the claim’s Referral_Status workbook field.',
    why: 'It tells the reviewer whether the workbook records a referral issue.',
    tag: 'Recorded fact',
  },
  icd_family: {
    title: 'Diagnosis family',
    how: 'The app groups related ICD-10 diagnosis codes under a broader family.',
    why: 'This provides a wider comparison group when an exact diagnosis match is too narrow.',
    tag: 'Grouping rule',
  },
  matching_basis: {
    title: 'How earlier claims were matched',
    how: 'This text is produced by the backend and lists the fields used to select earlier claims.',
    why: 'It explains whether the comparison was narrow or had to use broader matching.',
    tag: 'Matching rule',
  },
  matches: {
    title: 'Similar earlier claims found',
    how: 'The app compares the selected claim with earlier claims using claim and coding fields.',
    why: 'This count shows how many possible comparisons were found; relevance still depends on the matching basis.',
    tag: 'Evidence count',
  },
  top_claim_ids: {
    title: 'Earlier claim IDs',
    how: 'These are the identifiers of the closest earlier comparisons found by the app.',
    why: 'A reviewer can use the IDs to inspect the underlying claims.',
    tag: 'Audit trail',
  },
  related_pairs: {
    title: 'Related claim pairs',
    how: 'The app pairs earlier claims that belong to the same diagnosis-related history.',
    why: 'The pairs are used to measure the time between related claims.',
    tag: 'Timing check',
  },
  within_90_days: {
    title: 'Pairs within 90 days',
    how: 'The app counts related claim pairs whose service dates are no more than 90 days apart.',
    why: 'This is a timing signal for review. It does not prove that either service was avoidable.',
    tag: 'Timing check',
  },
  denial_probability: {
    title: 'Estimated denial chance',
    how: 'Calculated from denial outcomes among earlier matched claims, with smoothing when history is limited.',
    why: 'It is an estimate of risk, not a decision that this claim will be denied.',
    tag: 'Forecast',
  },
  future_denial_exposure: {
    title: 'Estimated payment at denial risk',
    how: 'Estimated denial chance × predicted provider payment.',
    why: 'This is probability-weighted exposure, not a confirmed loss.',
    tag: 'Forecast',
  },
  predicted_allowed: {
    title: 'Predicted allowed amount',
    how: 'Estimated from allowed amounts on earlier matched claims.',
    why: 'This is the amount the payer is expected to recognize under plan rules, not the current recorded amount.',
    tag: 'Forecast',
  },
  predicted_provider_payment: {
    title: 'Predicted provider payment',
    how: 'Estimated from paid amounts on earlier matched claims.',
    why: 'This is the expected payer payment to the provider, not money already received.',
    tag: 'Forecast',
  },
  predicted_patient_responsibility: {
    title: 'Predicted patient responsibility',
    how: 'Estimated from patient-responsibility amounts on earlier matched claims.',
    why: 'This is a forecast of the patient share, not the recorded balance on this claim.',
    tag: 'Forecast',
  },
  predicted_contractual_adjustment: {
    title: 'Predicted contractual adjustment',
    how: 'Estimated from adjustment amounts on earlier matched claims.',
    why: 'This is the expected contractual write-off, not a recovery opportunity.',
    tag: 'Forecast',
  },
  predicted_avoidable_spend: {
    title: 'Estimated avoidable repeat cost',
    how: '90-day repeat chance × estimated avoidable share × expected extra allowed cost.',
    why: 'This is a probability-weighted forecast. It does not establish that any recorded care was unnecessary.',
    tag: 'Forecast',
  },
  predicted_avoidable_provider_payment: {
    title: 'Estimated provider payment tied to an avoidable repeat',
    how: '90-day repeat chance × estimated avoidable share × expected extra provider payment.',
    why: 'This is a forecast of possible future payment exposure, not confirmed savings.',
    tag: 'Forecast',
  },
  repeat_probability_90d: {
    title: 'Estimated chance of a related claim within 90 days',
    how: 'Calculated from the timing of related earlier claims, with smoothing when history is limited.',
    why: 'This estimates the chance of a repeat claim. It does not say whether that repeat would be appropriate.',
    tag: 'Forecast',
  },
}

function ScenarioMapSection({ section }) {
  const items = section.items || {}
  const calculations = section.calculations || {}
  const plainTitle = SECTION_TITLES[section.step] || section.title
  const purpose = section.title === 'Financial Opportunity'
    ? formatFinancialOpportunityPurpose(items)
    : SECTION_PURPOSES[section.step]

  if (section.title === 'Financial Opportunity') {
    return (
      <article className="scenario-path-step calculation-step enhanced-step">
        <header className="scenario-step-header">
          <div className="step-header-left">
            <span className="step-badge">{section.step}</span>
            <div>
              <h3>{plainTitle}</h3>
              {purpose && <p className="step-purpose-subtitle"><strong>What this step does:</strong> {purpose}</p>}
            </div>
          </div>
        </header>
        <div className="scenario-calculations">
          {items.map((item) => (
            <div key={item.type || item.category} className="calc-item-card">
              <span className="calc-label">{item.label || CATEGORY_LABELS[item.category] || readableLabel(item.category)}</span>
              <strong className="calc-amount">{formatOptionalCurrency(item.amount)}</strong>
              <span className="plain-field-label">Calculation</span>
              <code className="calc-formula">{item.formula}</code>
              <span className="plain-field-label">Why it appears here</span>
              <small className="calc-reason">{item.reason}</small>
              {item.type === 'patient_balance' && item.details ? (
                <div className="patient-balance-provenance">
                  <strong>Where the two input numbers come from</strong>
                  <p><b>{formatOptionalCurrency(item.details.patient_responsibility)}</b> is the recorded <code>Patient_Responsibility</code> value in {item.details.workbook_sheet}, row {item.details.workbook_row}. The app does not choose this number.</p>
                  <p className="component-equation">It reconciles to the row’s remittance components: {formatOptionalCurrency(item.details.deductible_amount)} deductible + {formatOptionalCurrency(item.details.copay_amount)} copay + {formatOptionalCurrency(item.details.coinsurance_amount)} coinsurance = {formatOptionalCurrency(item.details.responsibility_component_total)}.</p>
                  <p><b>{formatOptionalCurrency(item.details.patient_payment_received)}</b> is the value stored in <code>Patient_Payment_Received</code> on that row. It is not calculated by this app and the Excel cell contains no formula.</p>
                  <p className="demo-data-note"><b>Demo-data warning:</b> The workbook identifies its patient-payment and outstanding-balance columns as synthetic illustrative placeholders. In production, the payment received must come from the real patient-billing or collections system.</p>
                  <p>These inputs can be different on another claim. If either source value changes, the outstanding balance is recalculated from those changed values.</p>
                </div>
              ) : null}
            </div>
          ))}
        </div>
      </article>
    )
  }
  if (section.title === 'Best Provider Action') {
    return (
      <article className="scenario-path-step action-step enhanced-step">
        <header className="scenario-step-header">
          <div className="step-header-left">
            <span className="step-badge">{section.step}</span>
            <div>
              <h3>{plainTitle}</h3>
              {purpose && <p className="step-purpose-subtitle"><strong>What this step does:</strong> {purpose}</p>}
            </div>
          </div>
        </header>
        <div className="scenario-action-card">
          <div className="action-card-top">
            <span className="action-stage-badge">{items.stage}</span>
            <strong className="action-title">{items.action}</strong>
          </div>
          <p className="action-reason">{items.reason}</p>
          <div className="action-meta-grid">
            <div className="action-meta-item"><span>Owner</span><strong>{items.owner}</strong></div>
            <div className="action-meta-item"><span>Amount Addressed</span><strong>{formatOptionalCurrency(items.amount_addressed)}</strong></div>
            <div className="action-meta-item"><span>Confidence</span><strong>{formatProbability(items.confidence)}</strong></div>
          </div>
          {items.evidence_claim_ids?.length ? (
            <div className="action-evidence">
              <span>Evidence:</span> <code>{items.evidence_claim_ids.join(', ')}</code>
            </div>
          ) : null}
        </div>
      </article>
    )
  }
  if (section.title === 'Supporting Evidence') {
    return (
      <article className="scenario-path-step trace-step enhanced-step">
        <header className="scenario-step-header">
          <div className="step-header-left">
            <span className="step-badge">{section.step}</span>
            <div>
              <h3>{plainTitle}</h3>
              {purpose && <p className="step-purpose-subtitle"><strong>What this step does:</strong> {purpose}</p>}
            </div>
          </div>
        </header>
        <dl className="scenario-purpose-grid">
          <div className="purpose-item"><dt>Selected claim location</dt><dd>{items.workbook_sheet} sheet · row {items.workbook_row}</dd></div>
          <div className="purpose-item"><dt>Earlier claim IDs used for comparison</dt><dd>{(items.peer_claim_ids || []).join(', ') || 'No earlier comparison claims were used'}</dd></div>
        </dl>
      </article>
    )
  }
  return (
    <article className={`scenario-path-step step-${section.step} enhanced-step`}>
      <header className="scenario-step-header">
        <div className="step-header-left">
          <span className="step-badge">{section.step}</span>
          <div>
            <h3>{plainTitle}</h3>
            {purpose && <p className="step-purpose-subtitle"><strong>What this step does:</strong> {purpose}</p>}
          </div>
        </div>
      </header>
      <div className="scenario-purpose-grid enhanced-grid">
        {(section.step === 6
          ? STEP_SIX_ITEM_ORDER.filter((key) => Object.hasOwn(items, key)).map((key) => [key, items[key]])
          : Object.entries(items)
        ).map(([key, value]) => {
          const exp = FIELD_EXPLANATIONS[key] || {}
          const calculation = calculations[key]
          let displayVal = MONEY_ITEM_KEYS.has(key)
            ? formatOptionalCurrency(value)
            : PROBABILITY_ITEM_KEYS.has(key)
            ? formatProbability(value)
            : String(value ?? '')
          if (Array.isArray(value)) {
            displayVal = value.join(', ')
          }

          return (
            <div key={key} className="purpose-item-card">
              <div className="purpose-item-top">
                <span className="purpose-field-title">{exp.title || SCENARIO_ITEM_LABELS[key] || readableLabel(key)}</span>
                {exp.tag && <span className="purpose-field-tag">{exp.tag}</span>}
              </div>
              <div className="purpose-field-value-row">
                <strong className="purpose-main-value">{displayVal}</strong>
              </div>
              {calculation ? (
                <div className="forecast-calculation-box">
                  <div>
                    <span className="forecast-detail-label">Calculation</span>
                    <code>{calculation.formula}</code>
                  </div>
                  <div>
                    <span className="forecast-detail-label">Numbers and evidence used</span>
                    <p>{calculation.evidence}</p>
                  </div>
                </div>
              ) : null}
              {(exp.how || exp.why) && (
                <div className="purpose-explanation-box">
                  {exp.how && (
                    <div className="exp-line exp-how">
                      <span className="exp-label">WHERE IT COMES FROM:</span>
                      <span className="exp-text">{exp.how}</span>
                    </div>
                  )}
                  {exp.why && (
                    <div className="exp-line exp-why">
                      <span className="exp-label">WHAT IT MEANS:</span>
                      <span className="exp-text">{exp.why}</span>
                    </div>
                  )}
                </div>
              )}
            </div>
          )
        })}
      </div>
      {section.step === 1 && (
        <div className="step1-cross-member-savings-card">
          <div className="cross-card-header">
            <span className="scope-tag micro-tag">CROSS-MEMBER DISEASE & TREATMENT COMPARISON</span>
            <h4>Where Money Can Be Saved by Comparing Other Members with this Same Disease</h4>
          </div>
          <p className="cross-card-desc">
            The prediction engine compares this member’s claim history against other members in the database diagnosed with the same disease. By evaluating how other members were treated, what providers charged, and which contracted schedules were applied, the system identifies where money can be saved on this specific encounter:
          </p>
          <div className="cross-savings-pillars">
            <div className="pillar-item">
              <strong>1. Treatment & Contract Rate Variations</strong>
              <p>Different members with this same condition received procedures with contracted allowed amounts lower than this claim. Aligning to lower-cost peer fee schedules reduces unit cost.</p>
            </div>
            <div className="pillar-item">
              <strong>2. Preventable Repeat Timing</strong>
              <p>Peer episodes that spaced routine visits beyond 90 days avoided duplicate procedure and administrative charges compared to premature re-encounters.</p>
            </div>
            <div className="pillar-item">
              <strong>3. Care Setting & Preventive Bundling</strong>
              <p>Other members receiving preventive care under bundled codes achieved lower total out-of-pocket and payer allowed liability.</p>
            </div>
          </div>
        </div>
      )}
    </article>
  )
}

function NonActionableEvidence({ items }) {
  return (
    <details className="non-actionable-evidence">
      <summary>Why Other Actions Were Not Selected <span>{items.length}</span></summary>
      <div className="non-actionable-list">
        {items.map((item) => (
          <article key={item.type}>
            <header><strong>{item.label}</strong><small>{item.reason_code}</small></header>
            <p>{item.reason}</p>
            <dl>
              {(item.evidence || []).map((evidence) => (
                <div key={evidence.field}><dt>{readableLabel(evidence.field)}</dt><dd>{evidence.display_value}</dd></div>
              ))}
              <div><dt>Action selected</dt><dd>No</dd></div>
            </dl>
            {item.formula ? <code>{item.formula}</code> : null}
          </article>
        ))}
      </div>
    </details>
  )
}

function PredictionEvidence({ rag }) {
  const documents = rag?.retrieved_documents || rag?.retrieved_chunks || []
  return (
    <details className="prediction-evidence-panel">
      <summary>Prediction Evidence <span>Retrieved workbook evidence: {documents.length} documents</span></summary>
      {rag?.error ? <p>{rag.error}</p> : null}
      <div className="prediction-evidence-table-wrap">
        <table>
          <thead><tr><th>Source</th><th>Claim ID</th><th>Service date</th><th>Evidence type</th><th>Fields used</th><th>Similarity</th><th>Structured match</th><th>Reason code</th></tr></thead>
          <tbody>
            {documents.map((document) => (
              <tr key={document.document_id || `${document.source_sheet}-${document.source_row}-${document.document_type}`}>
                <td>{document.source_sheet} · row {document.source_row}</td>
                <td>{document.claim_id || 'Reference'}</td>
                <td>{document.service_date || 'Reference'}</td>
                <td>{readableLabel(document.document_type || 'workbook evidence')}</td>
                <td>{(document.fields_used || []).join(', ')}</td>
                <td>{Number(document.vector_similarity ?? document.similarity ?? 0).toFixed(3)}</td>
                <td>{Number(document.structured_match_score ?? 0).toFixed(3)}</td>
                <td>{document.reason_code || 'Reference context'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  )
}

function PredictionValidationPanel({ claimValidation }) {
  const [report, setReport] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const loadReport = async () => {
    if (report || loading) return
    setLoading(true); setError('')
    try { setReport(await fetchJson('/api/predictions/validation')) }
    catch (requestError) { setError(requestError.message) }
    finally { setLoading(false) }
  }
  return (
    <details className="prediction-validation-panel" onToggle={(event) => { if (event.currentTarget.open) loadReport() }}>
      <summary>Prediction Validation <span>Retrospective actual-versus-predicted quality</span></summary>
      <div className="claim-validation-grid">
        {Object.entries(claimValidation || {}).map(([name, metric]) => (
          <article key={name}>
            <strong>{readableLabel(name)}</strong>
            <span>Predicted {formatOptionalCurrency(metric.predicted)}</span>
            <span>Actual {formatOptionalCurrency(metric.actual)}</span>
            <span>Absolute error {formatOptionalCurrency(metric.absolute_error)}</span>
            <span>{metric.percentage_error == null ? 'Percentage error excluded because actual is zero' : `Percentage error ${formatProbability(metric.percentage_error)}`}</span>
            <span>Interval {formatOptionalCurrency(metric.prediction_interval?.low)}–{formatOptionalCurrency(metric.prediction_interval?.high)}</span>
            <span>Actual {metric.actual_inside_interval ? 'inside' : 'outside'} interval</span>
          </article>
        ))}
      </div>
      {loading ? <p>Calculating workbook-wide retrospective validation…</p> : null}
      {error ? <p>{error}</p> : null}
      {report ? (
        <dl className="validation-report-grid">
          <div><dt>Evaluated claims</dt><dd>{report.evaluated_claims}</dd></div>
          <div><dt>Allowed MAE</dt><dd>{formatOptionalCurrency(report.financial.allowed_mae)}</dd></div>
          <div><dt>Paid MAE</dt><dd>{formatOptionalCurrency(report.financial.paid_mae)}</dd></div>
          <div><dt>Allowed MAPE</dt><dd>{formatProbability(report.financial.allowed_mape)}</dd></div>
          <div><dt>Paid MAPE</dt><dd>{formatProbability(report.financial.paid_mape)}</dd></div>
          <div><dt>Denial accuracy</dt><dd>{formatProbability(report.denial.accuracy)}</dd></div>
          <div><dt>Denial Brier score</dt><dd>{Number(report.denial.brier_score).toFixed(4)}</dd></div>
          <div><dt>Repeat-risk Brier score</dt><dd>{Number(report.repeat_risk.brier_score_90d).toFixed(4)}</dd></div>
          <div><dt>Avoidable anchors</dt><dd>{report.avoidable_spend?.evaluated_anchors}</dd></div>
          <div><dt>Mean predicted avoidable spend</dt><dd>{formatOptionalCurrency(report.avoidable_spend?.mean_predicted_avoidable_spend)}</dd></div>
          <div><dt>Median predicted avoidable spend</dt><dd>{formatOptionalCurrency(report.avoidable_spend?.median_predicted_avoidable_spend)}</dd></div>
          <div><dt>Avoidable-spend MAE</dt><dd>{formatOptionalCurrency(report.avoidable_spend?.mae)}</dd></div>
          <div><dt>Mathematical zero predictions</dt><dd>{Number(report.avoidable_spend?.zero_prediction_percentage || 0).toFixed(1)}%</dd></div>
          <div><dt>Average avoidable confidence</dt><dd>{formatProbability(report.avoidable_spend?.average_confidence)}</dd></div>
        </dl>
      ) : null}
    </details>
  )
}

export function ProviderMoneyLlmResult({ result }) {
  const summary = result.supported_money_summary || {}
  const action = summary.best_action || {}
  const snapshot = result.financial_prediction_snapshot || {}
  const actual = result.actual_claim_facts || {}
  const supported = result.supported_financial_opportunities || []
  const primaryOpportunity = supported[0]
  const nonActionable = result.non_actionable_evidence || []
  const basis = result.historical_prediction_basis || {}
  const sections = result.scenario_map?.sections || []
  const rag = result.rag || {}
  const explanation = result.prediction_explanation || result.explanation || {}
  const validatedAvoidable = result.validated_avoidable_spend || {}
  const patterns = result.historical_patterns || {}
  const similarClaims = result.similar_historical_claims || []
  const shortPatterns = result.short_timeframe_patterns || []
  const predictionHelp = {
    allowed: 'The amount the payer is expected to recognize. Calculated in Python from the median allowed amount of earlier matched workbook claims; the range comes from the matched-peer distribution.',
    providerPayment: 'The amount the provider is expected to receive. Calculated in Python from earlier matched paid amounts. This is a forecast, not the payment already recorded on this claim.',
    patientResponsibility: 'The amount the patient is expected to owe. Calculated in Python from patient-responsibility amounts on earlier matched workbook claims.',
    adjustment: 'The amount expected to be written off or adjusted. Calculated in Python from adjustment amounts on earlier matched workbook claims.',
    avoidableSpend: 'Plain meaning: the expected extra allowed cost if this claim leads to related care that might have been avoided within 90 days. Formula: 90-day repeat probability × chance the repeat is avoidable × expected extra repeat allowed cost. It is a forecast, not confirmed savings.',
    avoidablePayment: 'The provider-cash version of the avoidable-repeat forecast. Formula: 90-day repeat probability × chance the repeat is avoidable × expected extra repeat provider payment.',
    denialExposure: 'The provider payment at risk from a possible future denial. Formula: denial probability × predicted provider payment. It is not a confirmed loss.',
    repeatPayment: 'The expected provider payment associated with a possible related repeat claim. Formula: 90-day repeat probability × predicted provider payment.',
    avoidableProbability: 'Among earlier matched repeat episodes, the estimated chance that a repeat showed workbook evidence of being potentially avoidable. Planned follow-ups are not automatically counted.',
    excessRepeatCost: 'The typical extra allowed cost added by a repeat episode. For earlier matched episodes: total episode allowed amount − initial claim allowed amount; the model uses the median.',
    avoidableConfidence: 'How strongly the workbook history supports the avoidable-cost forecast. It reflects peer count, match specificity, history depth, and prediction-range width.',
    repeat30: 'Estimated chance of a related repeat claim within 30 days, calculated from earlier workbook history with peer fallback and Bayesian smoothing.',
    repeat60: 'Estimated chance of a related repeat claim within 60 days, calculated from earlier workbook history with peer fallback and Bayesian smoothing.',
    repeat90: 'Estimated chance of a related repeat claim within 90 days, calculated from earlier workbook history with peer fallback and Bayesian smoothing.',
    modelConfidence: 'Overall prediction reliability based on the amount, quality, and specificity of earlier matched workbook evidence.',
    method: 'The matching and smoothing method used by the Python prediction engine. Broader historical peers are used when exact peers are limited.',
    version: 'The backend calculation version that produced these values. It helps ensure the page, scenario map, and chat use the same result.',
  }
  const HelpCard = ({ help, children, className = '' }) => (
    <article className={`prediction-help-card ${className}`.trim()} tabIndex="0">
      {children}
      <span className="prediction-help-icon" aria-label="Hover or focus for explanation"><Info size={14} /></span>
      <span className="prediction-help-tooltip" role="tooltip">{help}</span>
    </article>
  )
  const HelpDetail = ({ help, children }) => (
    <div className="prediction-help-card prediction-help-detail" tabIndex="0">
      {children}
      <span className="prediction-help-icon" aria-label="Hover or focus for explanation"><Info size={13} /></span>
      <span className="prediction-help-tooltip" role="tooltip">{help}</span>
    </div>
  )
  const PredictionBasis = ({ value, rateLabel }) => <small className="prediction-rate-basis">{rateLabel}: {formatProbability(value?.historical_rate)} · Matched historical claims: {value?.peer_count || 0}<br />Matching basis: {value?.peer_level || basis.readable_basis}<br />Prediction range: {formatPredictionRange(value)}</small>
  return (
    <div className="provider-llm-workspace">
      <main className="provider-analysis-scroll">
        <div className="provider-llm-result provider-money-result">
          <section className="llm-wide-section claim-facts-section">
            <div className="llm-section-heading"><span>Claim Facts — Directly from claims data</span><small>837_Claims row {result.source?.source_row}</small></div>
            <dl className="llm-facts-grid">
              {[
                ['Claim ID', actual.claim_id], ['Service date', actual.service_date], ['ICD-10', `${actual.diagnosis_code || ''} — ${actual.diagnosis_description || ''}`],
                ['CPT', `${actual.cpt_code || ''} — ${actual.cpt_description || ''}`], ['Units', actual.units], ['Place of service', `${actual.place_of_service_code || ''} — ${actual.place_of_service_description || ''}`],
                ['Payer', actual.payer], ['Billing provider', actual.billing_provider], ['Rendering provider', actual.rendering_provider || 'Rendering provider is not populated in this workbook row.'],
                ['Charge', formatOptionalCurrency(actual.charge)], ['Allowed', formatOptionalCurrency(actual.allowed)], ['Paid', formatOptionalCurrency(actual.paid)],
                ['Patient responsibility', formatOptionalCurrency(actual.patient_responsibility)], ['Adjustment', formatOptionalCurrency(actual.adjustment)], ['Claim status', actual.claim_status],
                ['Authorization status', actual.authorization_status || 'Authorization status is not populated in this workbook row.'], ['Referral status', actual.referral_status || 'Referral status is not populated in this workbook row.'],
              ].map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}
            </dl>
          </section>
          <section className="llm-wide-section financial-prediction-snapshot">
            <div className="llm-section-heading"><span>Financial Prediction Snapshot</span><small>{snapshot.model_version}</small></div>
            <div className="prediction-plain-language-note">
              <Info size={16} />
              <p><strong>Forecasts are not confirmed savings.</strong> “Expected avoidable repeat cost” estimates the extra allowed cost of a potentially avoidable related repeat within 90 days. The supported opportunity below is money the workbook currently supports acting on.</p>
            </div>
            <div className="prediction-primary-grid">
              <HelpCard help={predictionHelp.allowed}><span>Predicted allowed</span><strong>{formatOptionalCurrency(snapshot.predicted_allowed?.value)}</strong><PredictionBasis value={snapshot.predicted_allowed} rateLabel="Historical allowed-to-charge rate" /></HelpCard>
              <HelpCard help={predictionHelp.providerPayment}><span>Predicted provider payment</span><strong>{formatOptionalCurrency(snapshot.predicted_provider_payment?.value)}</strong><PredictionBasis value={snapshot.predicted_provider_payment} rateLabel="Historical paid-to-allowed rate" /></HelpCard>
              <HelpCard help={predictionHelp.patientResponsibility}><span>Predicted patient responsibility</span><strong>{formatOptionalCurrency(snapshot.predicted_patient_responsibility?.value)}</strong><PredictionBasis value={snapshot.predicted_patient_responsibility} rateLabel="Historical patient-to-allowed rate" /></HelpCard>
              <HelpCard help={predictionHelp.adjustment}><span>Predicted contractual adjustment</span><strong>{formatOptionalCurrency(snapshot.predicted_contractual_adjustment?.value)}</strong><PredictionBasis value={snapshot.predicted_contractual_adjustment} rateLabel="Historical adjustment-to-charge rate" /></HelpCard>
              <HelpCard help={predictionHelp.avoidableSpend} className="predicted-avoidable-card"><span>Expected avoidable repeat cost</span><strong>{formatOptionalCurrency(snapshot.predicted_avoidable_spend?.value)}</strong><small>Forecast for the next 90 days · {formatPredictionRange(snapshot.predicted_avoidable_spend)}</small><small>Repeat risk: {formatProbability(snapshot.predicted_avoidable_spend?.repeat_probability_90d)} × avoidable if repeated: {formatProbability(snapshot.predicted_avoidable_spend?.avoidable_given_repeat_probability)} × extra repeat cost: {formatOptionalCurrency(snapshot.predicted_avoidable_spend?.expected_extra_repeat_allowed_cost)}</small></HelpCard>
              <HelpCard help={predictionHelp.avoidablePayment}><span>Expected avoidable provider payment</span><strong>{formatOptionalCurrency(snapshot.predicted_avoidable_provider_payment?.value)}</strong><small>Provider cash forecast · {formatPredictionRange(snapshot.predicted_avoidable_provider_payment)}</small></HelpCard>
              <HelpCard help={predictionHelp.denialExposure}><span>Future denial exposure</span><strong>{formatOptionalCurrency(snapshot.future_denial_exposure?.value)}</strong><small>Forecast only</small><small>Denial probability: {formatProbability(snapshot.future_denial_exposure?.denial_probability)} × predicted provider payment: {formatOptionalCurrency(snapshot.future_denial_exposure?.predicted_paid)}</small></HelpCard>
              <HelpCard help={predictionHelp.repeatPayment}><span>Predicted repeat-payment exposure</span><strong>{formatOptionalCurrency(snapshot.predicted_repeat_payment_exposure)}</strong><small>{formatProbability(snapshot.repeat_probability_90d)} at 90 days</small></HelpCard>
            </div>
            <dl className="prediction-detail-grid">
              <HelpDetail help={predictionHelp.avoidableProbability}><dt>Avoidable if repeat</dt><dd>{formatProbability(snapshot.predicted_avoidable_spend?.avoidable_given_repeat_probability)}</dd></HelpDetail>
              <HelpDetail help={predictionHelp.excessRepeatCost}><dt>Expected excess repeat cost</dt><dd>{formatOptionalCurrency(snapshot.predicted_avoidable_spend?.expected_extra_repeat_allowed_cost)}</dd></HelpDetail>
              <HelpDetail help={predictionHelp.avoidableConfidence}><dt>Avoidable forecast confidence</dt><dd>{formatProbability(snapshot.predicted_avoidable_spend?.confidence)}</dd><small>{snapshot.predicted_avoidable_spend?.peer_count} peers · {snapshot.predicted_avoidable_spend?.peer_level}</small></HelpDetail>
              <HelpDetail help={predictionHelp.repeat30}><dt>30-day repeat probability</dt><dd>{formatProbability(snapshot.repeat_probability_30d)}</dd></HelpDetail>
              <HelpDetail help={predictionHelp.repeat60}><dt>60-day repeat probability</dt><dd>{formatProbability(snapshot.repeat_probability_60d)}</dd></HelpDetail>
              <HelpDetail help={predictionHelp.repeat90}><dt>90-day repeat probability</dt><dd>{formatProbability(snapshot.repeat_probability_90d)}</dd></HelpDetail>
              <HelpDetail help={predictionHelp.modelConfidence}><dt>Model confidence</dt><dd>{formatProbability(snapshot.confidence?.score)} · {snapshot.confidence?.level}</dd></HelpDetail>
              <HelpDetail help={predictionHelp.method}><dt>Prediction method</dt><dd>{snapshot.prediction_method}</dd></HelpDetail>
              <HelpDetail help={predictionHelp.version}><dt>Calculation version</dt><dd>{snapshot.calculation_version}</dd></HelpDetail>
            </dl>
            <details className="validated-avoidable-summary">
              <summary>Historical validation detail</summary>
              <div><span>Validated Avoidable Spend</span><strong>{formatOptionalCurrency(validatedAvoidable.value)}</strong><small>Retrospective evidence · separate from the forecast</small></div>
              <p>{validatedAvoidable.reason}</p>
            </details>
          </section>

          <section className="llm-wide-section historical-prediction-basis">
            <div className="llm-section-heading"><span>Historical Claim Patterns</span><small>Earlier than {basis.cutoff_date}</small></div>
            <dl className="llm-facts-grid">{Object.entries(patterns).map(([key, value]) => <div key={key}><dt>{readableLabel(key)}</dt><dd>{value == null ? 'No related earlier pair was available to calculate a median.' : value}</dd></div>)}</dl>
            <p>{basis.readable_basis}</p>
          </section>

          <section className="llm-wide-section similar-claims-section">
            <div className="llm-section-heading"><span>Similar Historical Claims</span><small>Strongest earlier matches</small></div>
            <div className="evidence-table-wrap"><table><thead><tr><th>Claim ID</th><th>Service date</th><th>ICD-10</th><th>CPT</th><th>Provider</th><th>Payer</th><th>Charge</th><th>Allowed</th><th>Paid</th><th>Similarity</th><th>Why matched</th></tr></thead><tbody>{similarClaims.map((item) => <tr key={item.claim_id}><td>{item.claim_id}</td><td>{item.service_date}</td><td>{item.icd10}</td><td>{item.cpt}</td><td>{item.provider}</td><td>{item.payer}</td><td>{formatOptionalCurrency(item.charge)}</td><td>{formatOptionalCurrency(item.allowed)}</td><td>{formatOptionalCurrency(item.paid)}</td><td>{Number(item.similarity_score).toFixed(1)}</td><td>{item.match_reason}</td></tr>)}</tbody></table></div>
          </section>

          <section className="llm-wide-section short-patterns-section">
            <div className="llm-section-heading"><span>Short-Timeframe / Repeated-Service Patterns</span><small>Observable earlier claim pairs only</small></div>
            {shortPatterns.length ? <div className="evidence-table-wrap"><table><thead><tr><th>Claims</th><th>Dates</th><th>Days apart</th><th>Relationship</th><th>Score</th></tr></thead><tbody>{shortPatterns.slice(0, 10).map((item) => <tr key={`${item.claim_1}-${item.claim_2}`}><td>{item.claim_1} → {item.claim_2}</td><td>{item.date_1} → {item.date_2}</td><td>{item.days_apart}</td><td>{[item.same_cpt && 'same CPT', item.same_icd_family && 'same ICD family', item.same_provider && 'same provider', item.same_payer && 'same payer', item.same_episode && 'same episode'].filter(Boolean).join(', ')}</td><td>{item.relationship_score}</td></tr>)}</tbody></table></div> : <p>No earlier same-member claim pairs within 90 days shared a CPT, procedure family, ICD family, provider, payer, or episode identifier.</p>}
            <p>The claims data shows dates and coded relationships only; it does not establish the clinical reason for repeated activity.</p>
          </section>

          <section className="llm-wide-section provider-savings-section">
            <div className="llm-section-heading"><span>Supported Financial Opportunity</span><small>{supported.length} current opportunity category</small></div>
            {primaryOpportunity ? (
              <article className="supported-opportunity-hero">
                <span>{primaryOpportunity.label}</span>
                <strong>{formatOptionalCurrency(primaryOpportunity.amount)}</strong>
                <dl>
                  {(primaryOpportunity.evidence || []).map((item) => (
                    <div key={item.field}><dt>{readableLabel(item.field)}</dt><dd>{item.display_value}</dd></div>
                  ))}
                </dl>
                <p>{primaryOpportunity.reason}</p>
                <code>{primaryOpportunity.formula}</code>
                <small>{primaryOpportunity.reason_code} · Evidence: {(primaryOpportunity.evidence_fields || []).join(', ')}</small>
              </article>
            ) : (
              <p className="supported-opportunity-zero">Workbook evidence selected no positive financial action for this claim.</p>
            )}
            {supported.length > 1 ? (
              <div className="additional-supported-opportunities">
                {supported.slice(1).map((item) => <div key={item.type}><span>{item.label}</span><strong>{formatOptionalCurrency(item.amount)}</strong><small>{item.reason}</small></div>)}
              </div>
            ) : null}
            <div className="best-action-card">
              <span>Best Provider Action</span><strong>{action.stage}</strong><p>{action.action}</p><p>{action.reason}</p>
              <div className="savings-action-meta"><small>Owner: {action.owner}</small><small>Amount addressed: {formatOptionalCurrency(action.amount_addressed)}</small><small>Confidence: {formatProbability(action.confidence)}</small></div>
            </div>
            <NonActionableEvidence items={nonActionable} />
          </section>

          <section className="llm-wide-section actual-predicted-section">
            <div className="llm-section-heading"><span>Actual vs Predicted</span><small>Workbook result and model forecast remain separate</small></div>
            <div className="actual-predicted-columns">
              <article><h3>Actual Claim Result</h3><dl><div><dt>Claim status</dt><dd>{actual.claim_status}</dd></div><div><dt>Charge</dt><dd>{formatOptionalCurrency(actual.charge)}</dd></div><div><dt>Allowed</dt><dd>{formatOptionalCurrency(actual.allowed)}</dd></div><div><dt>Paid</dt><dd>{formatOptionalCurrency(actual.paid)}</dd></div><div><dt>Patient responsibility</dt><dd>{formatOptionalCurrency(actual.patient_responsibility)}</dd></div><div><dt>Adjustment</dt><dd>{formatOptionalCurrency(actual.adjustment)}</dd></div></dl></article>
              <article><h3>Financial Prediction</h3><dl><div><dt>Predicted allowed</dt><dd>{formatOptionalCurrency(snapshot.predicted_allowed?.value)}</dd></div><div><dt>Predicted provider payment</dt><dd>{formatOptionalCurrency(snapshot.predicted_provider_payment?.value)}</dd></div><div><dt>Predicted patient responsibility</dt><dd>{formatOptionalCurrency(snapshot.predicted_patient_responsibility?.value)}</dd></div><div><dt>Predicted adjustment</dt><dd>{formatOptionalCurrency(snapshot.predicted_contractual_adjustment?.value)}</dd></div><div><dt>Predicted avoidable spend</dt><dd>{formatOptionalCurrency(snapshot.predicted_avoidable_spend?.value)}</dd></div><div><dt>Predicted avoidable provider payment</dt><dd>{formatOptionalCurrency(snapshot.predicted_avoidable_provider_payment?.value)}</dd></div><div><dt>Denial probability</dt><dd>{formatProbability(snapshot.denial_probability)}</dd></div><div><dt>90-day repeat probability</dt><dd>{formatProbability(snapshot.repeat_probability_90d)}</dd></div></dl></article>
            </div>
            <PredictionValidationPanel claimValidation={result.retrospective_validation} />
          </section>

          <section className="llm-wide-section scenario-map-section">
            <div className="llm-section-heading"><span>How the app reaches this result</span><small>Read the numbered steps from top to bottom</small></div>
            <div className="scenario-walkthrough-intro">
              <strong>Start with recorded facts, then separate forecasts from current money.</strong>
              <p>Steps 1–5 collect the selected claim and its earlier comparisons. Step 6 makes estimates from that history. Step 7 checks for a current amount that may need follow-up. Step 8 shows the source evidence. Step 9 suggests the next human review.</p>
            </div>
            <div className="scenario-pathway">{sections.map((section) => <ScenarioMapSection key={section.step} section={section} />)}</div>
          </section>

          <section className="llm-wide-section">
            <div className="llm-section-heading"><span>Prediction Evidence / RAG</span><small>{rag.embedding_model || 'Local workbook vectors'}</small></div>
            <PredictionEvidence rag={rag} />
          </section>

          <section className="llm-wide-section prediction-basis-section">
            <div className="llm-section-heading"><span>Ollama Prediction Explanation</span><small>Explanation only; Python owns every numeric result</small></div>
            {explanation.summary ? <p className="ollama-prediction-summary">{explanation.summary}</p> : null}
            {explanation.sections?.length ? (
              <div className="layman-explanation">
                {explanation.sections.map((section, index) => (
                  <article key={section.title}>
                    <span>{index + 1}</span>
                    <div><strong>{section.title}</strong><p>{section.body}</p></div>
                  </article>
                ))}
              </div>
            ) : null}
            <div className="llm-evidence-list">{(rag.retrieved_chunks || []).map((chunk, index) => <article key={`${chunk.source_sheet}-${chunk.source_row}-${index}`}><strong>{chunk.source_sheet} · row {chunk.source_row}</strong><small>Claim {chunk.claim_id || 'supporting reference'} · {chunk.reason_code || 'workbook context'}</small><small>Similarity {Number(chunk.similarity || 0).toFixed(3)} · Fields: {(chunk.fields_used || []).join(', ')}</small></article>)}</div>
          </section>
        </div>
      </main>
      <ProviderPredictionChat key={`${result.source?.workbook_hash}.${result.financial_result_hash}`} result={result} />
    </div>
  )
}

function ChatFinancialExplanation({ explanation }) {
  if (!explanation) return null
  const action = explanation.best_action || {}
  const future = explanation.future_financial_exposure || {}
  const validated = explanation.validated_real_savings || {}
  const denialDetail = explanation.future_denial_exposure_detail || {}
  if (explanation.future_financial_exposure || explanation.validated_real_savings) {
    return (
      <section className="chat-financial-explanation">
        <strong>Prediction and Money Breakdown</strong>
        <div className="chat-financial-grid">
          <article><span>Verified recovery</span><b>{validated.available ? formatOptionalCurrency(validated.amount) : 'No verified positive amount'}</b></article>
          <article><span>Denial exposure</span><b>{formatOptionalCurrency(future.denial_exposure)}</b><small>Forecast only</small></article>
          <article><span>Repeat-payment exposure</span><b>{formatOptionalCurrency(future.repeat_provider_payment_exposure)}</b><small>Forecast only</small></article>
          <article><span>Best action</span><b>{action.stage}</b><small>{action.action}</small></article>
        </div>
        <footer>Confidence: {formatProbability(explanation.confidence?.score)}{explanation.limitations?.length ? ` · ${explanation.limitations[0]}` : ''}</footer>
      </section>
    )
  }
  return (
    <section className="chat-financial-explanation">
      <strong>Prediction and Money Breakdown</strong>
      <div className="chat-financial-grid">
        <article><span>Recoverable now</span><b>{formatOptionalCurrency(explanation.recoverable_now)}</b></article>
        <article><span>Predicted avoidable spend</span><b>{formatOptionalCurrency(explanation.predicted_avoidable_spend?.value)}</b><small>Forecast · 90-day horizon</small></article>
        <article><span>Predicted avoidable provider payment</span><b>{formatOptionalCurrency(explanation.predicted_avoidable_provider_payment?.value)}</b><small>Provider cash view</small></article>
        <article><span>Predicted provider payment</span><b>{formatOptionalCurrency(explanation.predicted_provider_payment?.value)}</b><small>{formatPredictionRange(explanation.predicted_provider_payment)}</small></article>
        <article><span>Predicted contractual adjustment</span><b>{formatOptionalCurrency(explanation.predicted_contractual_adjustment?.value)}</b><small>{formatPredictionRange(explanation.predicted_contractual_adjustment)}</small></article>
        <article><span>Future denial exposure</span><b>{formatOptionalCurrency(explanation.future_denial_exposure)}</b><small>Forecast only · {formatProbability(denialDetail.denial_probability)} denial probability · {formatOptionalCurrency(denialDetail.predicted_paid)} predicted provider payment</small></article>
        <article><span>Future repeat-payment exposure</span><b>{formatOptionalCurrency(explanation.future_repeat_payment_exposure)}</b><small>Forecast only</small></article>
        <article><span>Best action</span><b>{action.stage}</b><small>{action.action}</small></article>
      </div>
      {explanation.validated_avoidable_spend ? <details className="validated-avoidable-detail"><summary>Historical validation detail</summary><p>Validated avoidable spend: {formatOptionalCurrency(explanation.validated_avoidable_spend.value)} · {explanation.validated_avoidable_spend.reason}</p></details> : null}
      {explanation.formula_trace?.length ? <details><summary>Formula and workbook evidence</summary>{explanation.formula_trace.map((item) => <p key={item.category}><b>{readableLabel(item.category)}</b>: {item.formula} = {formatOptionalCurrency(item.amount)} · {item.reason_code}</p>)}<p>Evidence claims: {(explanation.evidence_claim_ids || []).join(', ')}</p><p>Evidence fields: {(explanation.evidence_fields || []).join(', ')}</p></details> : null}
      <footer>Confidence: {formatProbability(explanation.confidence?.score)}{explanation.limitations?.length ? ` · ${explanation.limitations[0]}` : ''}</footer>
    </section>
  )
}

function ProviderPredictionChat({ result }) {
  const claimId = result.claim_id
  const episodeId = result.episode_id
  const predictionIdentity = [
    result.source?.workbook_hash,
    result.financial_result_hash,
    result.source?.calculation_version,
    result.source?.rag_index_version,
    result.source?.groq_prompt_version,
    result.prediction_basis?.model_version,
    result.prediction_basis?.calculation_version,
  ].filter(Boolean).join('.') || 'current'
  const storageKey = `payerpayee.provider-chat.${claimId}.${episodeId}.${predictionIdentity}`
  const conversationId = useMemo(() => `${claimId}-${episodeId}-${Date.now().toString(36)}`, [claimId, episodeId])
  const [messages, setMessages] = useState(() => {
    const legacyWelcome = 'Ask me to explain any backend-calculated prediction, financial exposure, sample basis, backtest result or provider action. I cannot change the calculated values.'
    try {
      const cached = JSON.parse(window.localStorage.getItem(storageKey) || 'null')
      if (Array.isArray(cached)) return cached.filter((message) => message?.text !== legacyWelcome && message?.text !== 'Chat cleared. Ask a question about this prediction.')
    } catch { /* start with an empty conversation */ }
    return []
  })
  const [draft, setDraft] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [lastQuestion, setLastQuestion] = useState('')
  const resultsRef = useRef(null)
  const suggested = result.suggested_questions || ['How much can be saved?', 'How was the predicted allowed amount calculated?', 'How much provider revenue is at risk?', 'Which historical claims were used?', 'How confident is the model and why?']

  useEffect(() => {
    try { window.localStorage.setItem(storageKey, JSON.stringify(messages)) } catch { /* storage optional */ }
  }, [messages, storageKey])

  const submit = async (question = draft) => {
    const text = question.trim()
    if (!text || loading) return
    setLoading(true); setError(''); setLastQuestion(text); setDraft('')
    setMessages((current) => [...current, { role: 'user', text }])
    try {
      const response = await fetchJson('/api/provider-llm/chat', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ claim_id: claimId, episode_id: episodeId, message: text, conversation_id: conversationId }),
      })
      setMessages((current) => [...current, { role: 'assistant', text: response.answer, meta: response }])
      window.requestAnimationFrame(() => {
        if (resultsRef.current) resultsRef.current.scrollTop = resultsRef.current.scrollHeight
      })
    } catch (requestError) {
      setError(requestError.message || 'Chat response could not be loaded.')
    } finally { setLoading(false) }
  }
  const clear = () => { setMessages([]); setError('') }

  return (
    <aside className="provider-chat-prompt" aria-label="Ask About This Prediction">
      <header className="provider-chat-header">
        <div className="provider-chat-heading">
          <span className="provider-chat-icon"><Sparkles size={18} /></span>
          <div><strong>Ask About This Prediction</strong><small>Canonical prediction and workbook evidence only</small></div>
        </div>
        {messages.length ? <button className="provider-chat-clear" type="button" onClick={clear}>Clear chat</button> : null}
      </header>
      <div className="provider-chat-body">
        {!messages.length && !loading ? (
          <div className="provider-chat-empty">
            <span><Sparkles size={20} /></span>
            <strong>Explore the prediction</strong>
            <p>Ask about predicted payments, savings opportunities, risk exposure, or the evidence behind this result.</p>
          </div>
        ) : null}
        <div className="chat-results-list" ref={resultsRef} aria-live="polite">{messages.map((message, index) => <div className={`chat-message-block ${message.role}`} key={`${message.role}-${index}`}><article className={`chat-result-card ${message.role}`}><strong>{message.role === 'user' ? 'You' : 'Provider assistant'}</strong><p>{message.text}</p>{message.meta?.evidence_claim_ids?.length ? <small>Evidence: {message.meta.evidence_claim_ids.join(', ')}</small> : null}</article>{message.role === 'assistant' ? <ChatFinancialExplanation explanation={message.meta?.financial_explanation} /> : null}</div>)}{loading ? <article className="chat-result-card assistant loading"><RefreshCw className="spin" size={16} /> Reviewing this claim…</article> : null}</div>
        <div className="chat-suggestions" aria-label="Suggested questions">{suggested.slice(0, 4).map((question) => <button type="button" key={question} onClick={() => submit(question)}>{question}</button>)}</div>
        {error ? <div className="chat-error">{error}<button type="button" onClick={() => submit(lastQuestion)}>Retry</button></div> : null}
      </div>
      <footer className="provider-chat-composer-area">
        <div className="chatgpt-composer"><textarea value={draft} onChange={(event) => setDraft(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); submit() } }} placeholder="Ask about this claim prediction…" rows="2" /><button type="button" aria-label="Send chat question" onClick={() => submit()} disabled={loading || !draft.trim()}><Send size={18} /></button></div>
        <small>Enter to send · Shift+Enter for a new line</small>
      </footer>
    </aside>
  )
}

function SelectButton({ label, value, onClick, expanded }) {
  return (
    <button className="select-button" type="button" aria-expanded={expanded} onClick={onClick}>
      <span className="select-label">{label}</span>
      <strong>{value}</strong>
      <ChevronDown size={16} />
    </button>
  )
}

function SelectMenu({ label, value, menuKey, openMenu, setOpenMenu, options, onChange, wide = false }) {
  return (
    <div className={`control-wrap select-control ${wide ? 'provider-select' : ''}`}>
      <SelectButton
        label={label}
        value={value}
        expanded={openMenu === menuKey}
        onClick={() => setOpenMenu(openMenu === menuKey ? null : menuKey)}
      />
      {openMenu === menuKey ? (
        <div className="control-popover option-menu">
          {options.map((option) => (
            <button
              className={option === value ? 'selected' : ''}
              type="button"
              key={option}
              onClick={() => {
                onChange(option)
                setOpenMenu(null)
              }}
            >
              {option}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  )
}

function DateMenu({ dateRange, onChange }) {
  const { defaultDateRange } = useAppData()
  const latestMonthStart = defaultDateRange.to ? `${defaultDateRange.to.slice(0, 8)}01` : ''
  const latestYearStart = defaultDateRange.to ? `${defaultDateRange.to.slice(0, 4)}-01-01` : ''
  const presets = [
    ['Full Range', defaultDateRange.from, defaultDateRange.to],
    ['Latest Month', latestMonthStart, defaultDateRange.to],
    ['Year to Date', latestYearStart, defaultDateRange.to],
  ]

  return (
    <div className="control-popover date-popover">
      <div className="preset-row">
        {presets.map(([label, from, to]) => (
          <button type="button" key={label} onClick={() => onChange({ from, to })}>
            {label}
          </button>
        ))}
      </div>
      <label>
        From
        <input
          type="date"
          value={dateRange.from}
          onChange={(event) => onChange({ ...dateRange, from: event.target.value })}
        />
      </label>
      <label>
        To
        <input
          type="date"
          value={dateRange.to}
          onChange={(event) => onChange({ ...dateRange, to: event.target.value })}
        />
      </label>
    </div>
  )
}

function FilterMenu({ filters, setFilters }) {
  return (
    <div className="control-popover filter-popover">
      <label>
        <input
          type="checkbox"
          checked={filters.deniedOnly}
          onChange={(event) => setFilters({ ...filters, deniedOnly: event.target.checked })}
        />
        Denied claims only
      </label>
      <label>
        <input
          type="checkbox"
          checked={filters.highValue}
          onChange={(event) => setFilters({ ...filters, highValue: event.target.checked })}
        />
        Charges above $2,000
      </label>
    </div>
  )
}

function ClaimFlow() {
  const flow = [
    [CircleUserRound, 'Patient', 'Visit Occurs'],
    [ClipboardList, 'Encounter', 'Provider creates encounter'],
    [FileText, 'Claim Creation', 'Assign diagnosis & procedure'],
    [Send, '837 Submission', 'Submit electronic claim'],
    [ShieldCheck, 'Adjudication', 'Payer reviews & adjudicates'],
    [Landmark, '835 Remittance', 'Payment & posting to patient account'],
  ]

  return (
    <Card className="claim-flow">
      {flow.map(([Icon, title, note], index) => (
        <div className="flow-step" key={title}>
          <span className="flow-icon"><Icon size={32} /></span>
          <div>
            <strong>{title}</strong>
            <span>{note}</span>
          </div>
          {index < flow.length - 1 ? <span className="flow-connector"><ArrowRight size={28} /></span> : null}
        </div>
      ))}
    </Card>
  )
}

function DashboardMetric({ label, value, note, icon: Icon, tone }) {
  const description = getDashboardMetricDescription(label)
  const tooltipId = `metric-tip-${label.toLowerCase().replace(/[^a-z0-9]+/g, '-')}`

  return (
    <Card className="dashboard-metric">
      <button
        className="metric-info-button"
        type="button"
        aria-label={`${label} information`}
        aria-describedby={tooltipId}
      >
        <Info size={18} />
      </button>
      <span className="metric-tooltip" id={tooltipId} role="tooltip">{description}</span>
      <div>
        <span>{label}</span>
        <strong>{value}</strong>
        <small>{note}</small>
      </div>
      <span className={`metric-icon ${tone}`}>
        <Icon size={30} />
      </span>
    </Card>
  )
}

function PredictionSummary({ summary }) {
  if (!summary) return null
  const cards = [
    { label: 'Selectable Claims', value: summary.totalClaims.toLocaleString(), note: 'current workbook claims', tone: 'blue' },
    { label: 'Recoverable Now', value: formatCurrency(summary.recoverableNow), note: 'canonical claim opportunities', tone: 'green' },
    { label: 'Predicted Avoidable Spend — 90 Days', value: formatCurrency(summary.predictedAvoidableSpend90d), note: 'latest prediction per episode', tone: 'orange' },
    { label: 'Future Denial Exposure', value: formatCurrency(summary.futureDenialExposure), note: 'forecast kept separate', tone: 'violet' },
    { label: 'Future Repeat Exposure', value: formatCurrency(summary.futureRepeatPaymentExposure), note: 'forecast kept separate', tone: 'orange' },
  ]

  return (
    <div className="prediction-summary-grid">
      {cards.map((card) => (
        <Card className={`prediction-summary-card ${card.tone}`} key={card.label}>
          <span>{card.label}</span>
          <strong>{card.value}</strong>
          <small>{card.note}</small>
        </Card>
      ))}
    </div>
  )
}

function PredictionMethodPanel({ totalCount, scenarioCount, model }) {
  const methods = [
    ['Episode source', `${scenarioCount.toLocaleString()} scenarios grouped in Python from ${totalCount.toLocaleString()} current database claim records`],
    ['Provider view', 'Uses provider, diagnosis, utilisation, payer, service setting, and adjudication history.'],
    ['Money forecast', 'Allowed, paid, patient balance, and adjustment use peer rates by payer, provider, CPT, and place of service.'],
    ['Model status', `${model?.name || 'Explainable episode forecast'} · ${model?.source || 'database'} source`],
  ]

  return (
    <Card className="prediction-method-card">
      {methods.map(([label, value]) => (
        <div className="prediction-method-item" key={label}>
          <span>{label}</span>
          <strong>{value}</strong>
        </div>
      ))}
    </Card>
  )
}

function RiskBadge({ level, score }) {
  return (
    <span className={`risk-badge ${level.toLowerCase()}`}>
      {level} · {score}%
    </span>
  )
}

function RecentClaims({
  claims,
  title = 'Recent Claims',
  featured = false,
  compact = false,
  onOpenClaim,
  onViewAllClaims,
  emptyMessage = 'No claims match the current filters.',
  footer,
}) {
  const { recentClaims } = useAppData()
  const tableClaims = claims || recentClaims
  const emptyColSpan = compact ? 8 : 10

  return (
    <Card className={`recent-claims ${featured ? 'featured' : ''} ${compact ? 'compact' : ''}`}>
      <SectionTitle title={title} action={onViewAllClaims ? 'View All Claims' : null} onAction={onViewAllClaims} />
      <div className="table-wrap">
        <table className="data-table claims-table">
          <thead>
            <tr>
              <th>Claim Number</th>
              <th>Patient Name</th>
              <th>DOS</th>
              {!compact ? <th>Provider</th> : null}
              <th>Payer</th>
              <th>Status</th>
              <th>Total Charge</th>
              {!compact ? <th>Allowed</th> : null}
              <th>Paid</th>
              <th>Patient Resp.</th>
            </tr>
          </thead>
          <tbody>
            {tableClaims.length ? tableClaims.map((claim) => (
              <tr key={claim.number}>
                <td>
                  <button className="claim-link-button" type="button" onClick={() => onOpenClaim?.(claim)}>
                    {claim.number}
                  </button>
                </td>
                <td>{claim.patient}</td>
                <td>{formatDate(claim.dos)}</td>
                {!compact ? <td>{claim.billingProvider}</td> : null}
                <td>{claim.payer}</td>
                <td>
                  <span className={`claim-status ${statusClass(claim.status)}`} title={claim.status}>
                    {statusLabel(claim.status)}
                  </span>
                </td>
                <td>{formatCurrency(claim.totalCharge)}</td>
                {!compact ? <td>{formatCurrency(claim.allowed)}</td> : null}
                <td>{formatCurrency(claim.paid)}</td>
                <td>{formatCurrency(claim.patientResp)}</td>
              </tr>
            )) : (
              <tr>
                <td className="empty-table-cell" colSpan={emptyColSpan}>{emptyMessage}</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      {footer}
    </Card>
  )
}

function statusClass(status) {
  if (status === 'Denied') return 'denied'
  if (status.includes('Reversal')) return 'reversal'
  if (status.includes('Forwarded')) return 'forwarded'
  if (status.includes('Secondary')) return 'secondary'
  return 'primary'
}

function statusLabel(status) {
  if (status === 'Denied') return 'Denied'
  if (status.includes('Reversal')) return 'Reversal'
  if (status.includes('Primary') && status.includes('Forwarded')) return 'Primary + Forwarded'
  if (status.includes('Secondary') && status.includes('Forwarded')) return 'Secondary + Forwarded'
  if (status.includes('Secondary')) return 'Processed Secondary'
  return 'Processed Primary'
}

export default App
