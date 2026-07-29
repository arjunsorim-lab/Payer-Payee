import { createContext, useContext, useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { formatOptionalCurrency, formatPredictionRange, formatProbability } from './providerLlmFormat.js'
import {
  ArrowLeft,
  ArrowRight,
  Banknote,
  BarChart3,
  Bell,
  Building2,
  CalendarDays,
  CheckCircle2,
  ChevronDown,
  CircleDollarSign,
  CircleUserRound,
  ClipboardCheck,
  ClipboardList,
  CreditCard,
  DollarSign,
  Download,
  FileText,
  Filter,
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
  ShieldCheck,
  Sparkles,
  Stethoscope,
  Target,
  TrendingDown,
  TrendingUp,
  UserRound,
  Users,
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

function buildMemberStats(member) {
  const claimCount = member.claims.length
  const money = member.supportedMoneySummary
  return [
    { label: 'Total Allowed', value: formatCurrency(member.totalAllowed), note: `Across ${claimCount.toLocaleString()} claims` },
    { label: 'Total Paid', value: formatCurrency(member.totalPaid), note: 'Payer payments in the database' },
    { label: 'Recoverable Now', value: formatCurrency(money.recoverable_now), note: 'Sum of canonical claim-level opportunities' },
    { label: 'Supported Avoidable Spend', value: formatCurrency(money.potentially_avoidable_spend_supported), note: 'Deduplicated by episode' },
    { label: 'Future Denial Exposure', value: formatCurrency(money.future_denial_exposure), note: 'Forecast kept separate' },
    { label: 'Future Repeat Exposure', value: formatCurrency(money.future_repeat_payment_exposure), note: 'Forecast kept separate' },
    { label: 'Total Claims', value: claimCount.toLocaleString(), note: `${claimCount - member.deniedCount} non-denied, ${member.deniedCount} denied` },
    { label: 'Last Encounter', value: formatDate(member.latestClaim.dos), note: member.latestClaim.placeOfService },
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
        <span className="brand-primary">Claims</span>
        <span className="brand-accent">AI</span>
        <span className="brand-ring" aria-hidden="true"></span>
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
      if (sortBy === 'Supported Avoidable Spend') return b.supported_money_summary.potentially_avoidable_spend_supported - a.supported_money_summary.potentially_avoidable_spend_supported
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
                    <option>Supported Avoidable Spend</option>
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
          </>
        )}
      </section>
    </>
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
          {scenario.historical_comparison.sample_size.toLocaleString()} historical peers · {formatProbability(scenario.confidence.score)} confidence
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
                  <span>Supported avoidable spend</span>
                  <strong>{formatOptionalCurrency(scenario.supported_money_summary.potentially_avoidable_spend_supported)}</strong>
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

function PredictionScenarioMap({ scenario }) {
  const facts = scenario.actual_claim_facts
  const summary = scenario.supported_money_summary
  const snapshot = scenario.financial_prediction_snapshot
  const topMetrics = [
    ['Recoverable now', formatOptionalCurrency(summary.recoverable_now), summary.best_action.stage],
    ['Supported avoidable spend', formatOptionalCurrency(summary.potentially_avoidable_spend_supported), 'Current evidence'],
    ['Predicted paid', formatOptionalCurrency(snapshot.predicted_provider_payment.value), `${snapshot.peer_sample_size} historical peers`],
    ['Future denial exposure', formatOptionalCurrency(summary.future_denial_exposure), 'Forecast kept separate'],
    ['Model confidence', formatProbability(snapshot.confidence.score), snapshot.confidence.level],
  ]

  return (
    <Card className="provider-forecast-detail">
      <header className="provider-forecast-heading">
        <div>
          <span>Provider case forecast · {scenario.episode_id}</span>
          <h1>{facts.diagnosis_description}</h1>
          <p>{facts.provider} · {facts.payer} · claim {scenario.claim_id}</p>
        </div>
        <span className="priority-chip">{summary.best_action.stage}</span>
      </header>

      <div className="provider-forecast-metrics">
        {topMetrics.map(([label, value, note]) => (
          <div key={label}><span>{label}</span><strong>{value}</strong><small>{note}</small></div>
        ))}
      </div>

      <div className="scenario-pathway">{scenario.scenario_map.sections.map((section) => <ScenarioMapSection key={section.step} section={section} />)}</div>
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
        <div className="forecast-money-card"><span>Supported avoidable spend</span><strong>{formatOptionalCurrency(summary.potentially_avoidable_spend_supported)}</strong><small>Future exposure excluded</small></div>
      </div>
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

function MemberDetail({ member, selectedClaim, onBackToEncounters, onSelectMember, onOpenClaim, onOpenPrediction }) {
  const { defaultDateRange } = useAppData()
  const latestClaim = selectedClaim || member.latestClaim
  const memberStats = buildMemberStats(member)

  return (
    <>
      <div className="patient-header-row">
        <button className="back-link" type="button" onClick={onBackToEncounters}>
          <ArrowLeft size={16} />
          Back to Encounters
        </button>
        <div className="data-stamp">
          Data as of {formatDate(defaultDateRange.to)}
          <RefreshCw size={15} />
        </div>
      </div>

      <div className="patient-grid provider-focus">
        <div className="patient-main">
          <div className="summary-grid">
            <Card className="member-card">
              <div className="initials">{getInitials(member)}</div>
              <div className="member-info">
                <div className="member-title">
                  <h1>{member.patient}</h1>
                  <span className="status-pill success">Active Member</span>
                </div>
                <dl className="member-meta">
                  <div>
                    <dt>Member ID</dt>
                    <dd>{member.memberId}</dd>
                  </div>
                  <div>
                    <dt>DOB</dt>
                    <dd>{formatDate(member.dob)} ({calculateAge(member.dob)})</dd>
                  </div>
                  <div>
                    <dt>Gender</dt>
                    <dd>{member.gender}</dd>
                  </div>
                  <div>
                    <dt>Account #</dt>
                    <dd>{member.accountNumber}</dd>
                  </div>
                </dl>
              </div>
            </Card>

            <Card className="coverage-card">
              <SectionTitle title="Coverage Snapshot" action="View Details" />
              <dl className="coverage-grid">
                <div>
                  <dt>Payer</dt>
                  <dd>{member.payer}</dd>
                </div>
                <div>
                  <dt>Group</dt>
                  <dd>{member.groupName}</dd>
                </div>
                <div>
                  <dt>Plan</dt>
                  <dd>{latestClaim.filingIndicator}</dd>
                </div>
                <div>
                  <dt>Member Since</dt>
                  <dd>{formatDate(member.claims[member.claims.length - 1].dos)}</dd>
                </div>
                <div>
                  <dt>Subscriber ID</dt>
                  <dd>{member.subscriberId}</dd>
                </div>
                <div>
                  <dt>Relationship</dt>
                  <dd>Subscriber</dd>
                </div>
              </dl>
            </Card>
          </div>

          {selectedClaim ? <SelectedClaimDetail claim={selectedClaim} /> : null}

          <div className="member-stat-grid">
            {memberStats.map((stat) => (
              <MetricCard key={stat.label} {...stat} compact />
            ))}
          </div>

          <div className="chart-grid">
            <ProviderInformation claim={latestClaim} />
            <ProviderKpis claim={latestClaim} />
          </div>

          <RecentEncounters title="Member Encounters" claims={member.claims.slice(0, 8)} onSelectMember={onSelectMember} onOpenClaim={onOpenClaim} />
          <ClaimTimeline claim={latestClaim} />
        </div>

        <aside className="patient-aside grok-aside">
          <ProviderLlmPanel claim={latestClaim} onCasePrediction={() => onOpenPrediction(latestClaim)} />
        </aside>
      </div>
    </>
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
          <p>Why each key claim value appears on this 837 claim record.</p>
        </div>
        <span className={`claim-status ${statusClass(claim.status)}`}>{statusLabel(claim.status)}</span>
      </div>
      <div className="reason-card-grid">
        {getClaimReasonRows(claim).map((row) => (
          <div className="reason-card" key={row.field}>
            <div>
              <span>{row.field}</span>
              <strong>{row.value}</strong>
            </div>
            <p>{row.reason}</p>
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
    },
    {
      field: 'Total Charge',
      value: formatCurrency(claim.totalCharge),
      reason: `${claim.billingProvider} billed ${claim.units || 1} unit(s) for ${procedure} at ${getService(claim)}.`,
    },
    {
      field: 'Allowed',
      value: formatCurrency(claim.allowed),
      reason: `${claim.payer} adjudicated the billed charge to the allowed amount after contract and claim edits. Adjustment recorded: ${formatCurrency(claim.adjustment)}.`,
    },
    {
      field: 'Paid',
      value: formatCurrency(claim.paid),
      reason: claim.paid > 0
        ? `${claim.payer} paid this amount toward the allowed claim after adjudication and member responsibility were applied.`
        : `No payer payment is recorded for this claim, typically because the claim is denied, pending, or forwarded to another payer.`,
    },
    {
      field: 'Patient Resp.',
      value: formatCurrency(claim.patientResp),
      reason: `This is the member responsibility assigned on the claim, such as deductible, copay, coinsurance, or non-covered balance.`,
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
  const predictionReady = Boolean(
    result?.supported_money_summary
    || result?.forecast
    || result?.provider_financial_opportunity_summary
  )

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
        `/api/predictions/provider-case/${encodeURIComponent(claim.claimId || claim.number)}/llm`,
        { method: 'POST' },
      )
      setResult(payload)
    } catch (requestError) {
      setError(requestError.message || 'Provider financial prediction could not be loaded.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <Card className="provider-llm-card">
      <div className="provider-llm-header">
        <div>
          <span className="provider-llm-kicker"><Sparkles size={14} /> Groq provider assistant</span>
          <h2>Provider Financial Prediction</h2>
          <p>Predicts provider payment outcomes from de-identified workbook history and explains the supporting evidence.</p>
        </div>
        <div className="provider-llm-actions">
          <button className="llm-secondary-button" type="button" onClick={onCasePrediction}>View {claim.number || claim.claimId} forecast</button>
          <button className="llm-primary-button" type="button" onClick={generatePrediction} disabled={loading}>
            {loading ? <RefreshCw className="spin" size={16} /> : <Sparkles size={16} />}
            {loading ? 'Generating…' : 'Generate Prediction'}
          </button>
        </div>
      </div>

      {!result && !error ? (
        <div className="llm-intro">Run a concise provider-side explanation for this exact claim. Successful results are cached for faster repeat viewing.</div>
      ) : null}
      {error ? <div className="llm-config-note error">{error}</div> : null}
      {predictionReady ? <div className="llm-intro">Prediction ready for {result.claim_id}. Open it to review the forecast, financial exposure, recommended actions and supporting evidence.</div> : null}
      {predictionReady ? <button className="llm-secondary-button" type="button" onClick={() => setModalOpen(true)}>Open Provider Financial Prediction</button> : null}
      {modalOpen ? <ProviderLlmModal claim={claim} result={result} loading={loading} error={error} onClose={() => setModalOpen(false)} onRetry={generatePrediction} /> : null}
    </Card>
  )
}

function ProviderLlmModal({ claim, result, loading, error, onClose, onRetry }) {
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
      <div className="provider-llm-modal" role="dialog" aria-modal="true" aria-labelledby="provider-llm-modal-title">
        <header className="provider-llm-modal-header">
          <div>
            <span className="provider-llm-kicker"><Sparkles size={14} /> Groq provider assistant</span>
            <h2 id="provider-llm-modal-title">Provider Financial Prediction</h2>
            <p>{claim.number || claim.claimId} · Provider financial decision support</p>
          </div>
          <button className="provider-llm-close" type="button" aria-label="Close Provider Financial Prediction" onClick={onClose}><X size={20} /></button>
        </header>
        <div className="provider-llm-modal-body">
          {loading ? <div className="llm-modal-state"><RefreshCw className="spin" size={22} /> Calculating provider forecast and grounded explanation…</div> : null}
          {!loading && error ? <div className="llm-config-note error"><span>{error}</span><button className="llm-primary-button" type="button" onClick={onRetry}>Retry prediction</button></div> : null}
          {!loading && result?.supported_money_summary ? <ProviderMoneyLlmResult result={result} /> : null}
          {!loading && !error && !result?.supported_money_summary && result?.forecast ? <ProviderRenderPredictionResult result={result} /> : null}
          {!loading && !error && result && !result?.supported_money_summary && !result?.forecast ? (
            <div className="llm-modal-state provider-response-error">
              The prediction service returned an unsupported response. Please retry after the deployment finishes.
            </div>
          ) : null}
        </div>
      </div>
    </div>,
    document.body,
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
  'supported_avoidable_spend', 'future_denial_exposure', 'future_repeat_payment_exposure',
  'amount_addressed',
])
const PROBABILITY_ITEM_KEYS = new Set(['denial_probability', 'repeat_probability_30d', 'repeat_probability_60d', 'repeat_probability_90d'])

function readableLabel(value) {
  return String(value || '').replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase())
}

function ScenarioMapSection({ section }) {
  const items = section.items || {}
  if (section.step === 6) {
    return (
      <article className="scenario-path-step calculation-step">
        <header><b>{section.step}</b><strong>{section.title}</strong></header>
        <div className="scenario-calculations">
          {items.map((item) => (
            <div key={item.type || item.category}>
              <span>{item.label || CATEGORY_LABELS[item.category] || readableLabel(item.category)}</span>
              <strong>{formatOptionalCurrency(item.amount)}</strong>
              <code>{item.formula}</code>
              <small>{item.reason}</small>
            </div>
          ))}
        </div>
      </article>
    )
  }
  if (section.step === 7) {
    return (
      <article className="scenario-path-step action-step">
        <header><b>{section.step}</b><strong>{section.title}</strong></header>
        <div className="scenario-action-card">
          <span>{items.stage}</span>
          <strong>{items.action}</strong>
          <p>{items.reason}</p>
          <small>Owner: {items.owner} · Amount addressed: {formatOptionalCurrency(items.amount_addressed)} · Confidence: {formatProbability(items.confidence)}</small>
          <small>Evidence: {(items.evidence_claim_ids || []).join(', ')}</small>
        </div>
      </article>
    )
  }
  if (section.step === 8) {
    return (
      <article className="scenario-path-step trace-step">
        <header><b>{section.step}</b><strong>{section.title}</strong></header>
        <dl className="scenario-purpose-grid">
          <div><dt>Workbook location</dt><dd>{items.workbook_sheet} · row {items.workbook_row}</dd></div>
          <div><dt>Claim evidence</dt><dd>{(items.claim_ids || []).join(', ')}</dd></div>
          <div><dt>Reason codes</dt><dd>{(items.reason_codes || []).join(', ')}</dd></div>
          <div><dt>Fields used</dt><dd>{(items.fields_used || []).join(', ')}</dd></div>
          <div><dt>Workbook hash</dt><dd><code>{items.workbook_hash}</code></dd></div>
        </dl>
      </article>
    )
  }
  return (
    <article className={`scenario-path-step step-${section.step}`}>
      <header><b>{section.step}</b><strong>{section.title}</strong></header>
      <dl className="scenario-purpose-grid">
        {Object.entries(items).map(([key, value]) => (
          <div key={key}>
            <dt>{readableLabel(key)}</dt>
            <dd>{MONEY_ITEM_KEYS.has(key) ? formatOptionalCurrency(value) : PROBABILITY_ITEM_KEYS.has(key) ? formatProbability(value) : String(value ?? '')}</dd>
          </div>
        ))}
      </dl>
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
  return (
    <div className="provider-llm-workspace">
      <main className="provider-analysis-scroll">
        <div className="provider-llm-result provider-money-result">
          <section className="llm-wide-section financial-prediction-snapshot">
            <div className="llm-section-heading"><span>Financial Prediction Snapshot</span><small>{snapshot.model_version}</small></div>
            <div className="prediction-primary-grid">
              <article><span>Predicted provider payment</span><strong>{formatOptionalCurrency(snapshot.predicted_provider_payment?.value)}</strong><small>{formatPredictionRange(snapshot.predicted_provider_payment)}</small></article>
              <article><span>Predicted contractual adjustment</span><strong>{formatOptionalCurrency(snapshot.predicted_contractual_adjustment?.value)}</strong><small>{formatPredictionRange(snapshot.predicted_contractual_adjustment)}</small></article>
              <article><span>Predicted denial exposure</span><strong>{formatOptionalCurrency(snapshot.predicted_denial_revenue_exposure)}</strong><small>{formatProbability(snapshot.denial_probability)} probability</small></article>
              <article><span>Predicted repeat-payment exposure</span><strong>{formatOptionalCurrency(snapshot.predicted_repeat_payment_exposure)}</strong><small>{formatProbability(snapshot.repeat_probability_90d)} at 90 days</small></article>
            </div>
            <dl className="prediction-detail-grid">
              <div><dt>Predicted allowed</dt><dd>{formatOptionalCurrency(snapshot.predicted_allowed?.value)}</dd><small>{formatPredictionRange(snapshot.predicted_allowed)}</small></div>
              <div><dt>Predicted patient responsibility</dt><dd>{formatOptionalCurrency(snapshot.predicted_patient_responsibility?.value)}</dd><small>{formatPredictionRange(snapshot.predicted_patient_responsibility)}</small></div>
              <div><dt>30-day repeat probability</dt><dd>{formatProbability(snapshot.repeat_probability_30d)}</dd></div>
              <div><dt>60-day repeat probability</dt><dd>{formatProbability(snapshot.repeat_probability_60d)}</dd></div>
              <div><dt>90-day repeat probability</dt><dd>{formatProbability(snapshot.repeat_probability_90d)}</dd></div>
              <div><dt>Model confidence</dt><dd>{formatProbability(snapshot.confidence?.score)} · {snapshot.confidence?.level}</dd></div>
              <div><dt>Prediction method</dt><dd>{snapshot.prediction_method}</dd></div>
              <div><dt>Calculation version</dt><dd>{snapshot.calculation_version}</dd></div>
            </dl>
          </section>

          <section className="llm-wide-section provider-savings-section">
            <div className="llm-section-heading"><span>Supported Financial Opportunity</span><small>{supported.length} positive opportunity category</small></div>
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
              <article><h3>Financial Prediction</h3><dl><div><dt>Predicted allowed</dt><dd>{formatOptionalCurrency(snapshot.predicted_allowed?.value)}</dd></div><div><dt>Predicted provider payment</dt><dd>{formatOptionalCurrency(snapshot.predicted_provider_payment?.value)}</dd></div><div><dt>Predicted patient responsibility</dt><dd>{formatOptionalCurrency(snapshot.predicted_patient_responsibility?.value)}</dd></div><div><dt>Predicted adjustment</dt><dd>{formatOptionalCurrency(snapshot.predicted_contractual_adjustment?.value)}</dd></div><div><dt>Denial probability</dt><dd>{formatProbability(snapshot.denial_probability)}</dd></div><div><dt>90-day repeat probability</dt><dd>{formatProbability(snapshot.repeat_probability_90d)}</dd></div></dl></article>
            </div>
          </section>

          <section className="llm-wide-section historical-prediction-basis">
            <div className="llm-section-heading"><span>Historical Prediction Basis</span><small>Cutoff {basis.cutoff_date}</small></div>
            <dl className="llm-facts-grid"><div><dt>Match level</dt><dd>{basis.match_level}</dd></div><div><dt>Peer sample</dt><dd>{basis.sample_size}</dd></div><div><dt>Earlier member claims</dt><dd>{basis.earlier_same_member_claims}</dd></div><div><dt>Earlier same-CPT claims</dt><dd>{basis.earlier_same_cpt_claims}</dd></div><div><dt>Previous denials</dt><dd>{basis.previous_denials}</dd></div><div><dt>Consistency check</dt><dd>{result.consistency_check?.passed ? 'Passed' : ''}</dd></div></dl>
          </section>

          <section className="llm-wide-section scenario-map-section">
            <div className="llm-section-heading"><span>Provider Money Scenario Map</span><small>History → actual payment → prediction → supported action</small></div>
            <div className="scenario-pathway">{sections.map((section) => <ScenarioMapSection key={section.step} section={section} />)}</div>
          </section>

          <section className="llm-wide-section prediction-basis-section">
            <div className="llm-section-heading"><span>Prediction Explanation</span><small>RAG index {rag.index_version}</small></div>
            {explanation.sections?.length ? (
              <div className="layman-explanation">
                {explanation.sections.map((section, index) => (
                  <article key={section.title}>
                    <span>{index + 1}</span>
                    <div><strong>{section.title}</strong><p>{section.body}</p></div>
                  </article>
                ))}
              </div>
            ) : <p>{explanation.summary}</p>}
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
        <article><span>Supported avoidable spend</span><b>{formatOptionalCurrency(explanation.potentially_avoidable_spend_supported)}</b></article>
        <article><span>Predicted provider payment</span><b>{formatOptionalCurrency(explanation.predicted_provider_payment?.value)}</b><small>{formatPredictionRange(explanation.predicted_provider_payment)}</small></article>
        <article><span>Predicted contractual adjustment</span><b>{formatOptionalCurrency(explanation.predicted_contractual_adjustment?.value)}</b><small>{formatPredictionRange(explanation.predicted_contractual_adjustment)}</small></article>
        <article><span>Future denial exposure</span><b>{formatOptionalCurrency(explanation.future_denial_exposure)}</b><small>Forecast only</small></article>
        <article><span>Future repeat-payment exposure</span><b>{formatOptionalCurrency(explanation.future_repeat_payment_exposure)}</b><small>Forecast only</small></article>
        <article><span>Best action</span><b>{action.stage}</b><small>{action.action}</small></article>
      </div>
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
          <div><strong>Provider assistant</strong><small>Ask about this claim prediction</small></div>
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
        <div className="chat-results-list" ref={resultsRef} aria-live="polite">{messages.map((message, index) => <div className={`chat-message-block ${message.role}`} key={`${message.role}-${index}`}><article className={`chat-result-card ${message.role}`}><strong>{message.role === 'user' ? 'Your question' : 'Groq provider assistant'}</strong><p>{message.text}</p>{message.meta?.evidence_claim_ids?.length ? <small>Evidence: {message.meta.evidence_claim_ids.join(', ')}</small> : null}</article>{message.role === 'assistant' ? <ChatFinancialExplanation explanation={message.meta?.financial_explanation} /> : null}</div>)}{loading ? <article className="chat-result-card assistant loading"><RefreshCw className="spin" size={16} /> Reviewing the structured prediction…</article> : null}</div>
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
    { label: 'Supported Avoidable Spend', value: formatCurrency(summary.supportedAvoidableSpend), note: 'episode-deduplicated', tone: 'orange' },
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
