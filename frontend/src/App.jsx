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
import { predictClaim } from '../../shared/predictionEngine'

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
const CLAIMS_CACHE_KEY = 'payerpayee.claims.v1'
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
  let lastError = new Error('Backend API is unavailable')

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

function readClaimsCache() {
  try {
    const cached = JSON.parse(window.localStorage.getItem(CLAIMS_CACHE_KEY) || 'null')
    return Array.isArray(cached?.items) ? cached.items : []
  } catch {
    return []
  }
}

function writeClaimsCache(items) {
  try {
    window.localStorage.setItem(CLAIMS_CACHE_KEY, JSON.stringify({ items, savedAt: Date.now() }))
  } catch {
    // Browsers may disable storage; the bundled snapshot remains available.
  }
}

async function loadBundledClaims() {
  const response = await fetch('/data/claims-fallback.json', { headers: { Accept: 'application/json' } })
  if (!response.ok) throw new Error(`Bundled data request failed: ${response.status}`)
  return response.json()
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
      }
    })
    .sort((a, b) => b.latestClaim.dos.localeCompare(a.latestClaim.dos))
}

function buildMemberStats(member) {
  const claimCount = member.claims.length
  return [
    { label: 'Total Allowed', value: formatCurrency(member.totalAllowed), note: `Across ${claimCount.toLocaleString()} claims` },
    { label: 'Total Paid', value: formatCurrency(member.totalPaid), note: 'Payer payments in the database' },
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
  const [claimsData, setClaimsData] = useState(() => readClaimsCache())
  const [dataLoading, setDataLoading] = useState(true)
  const [dataError, setDataError] = useState('')
  const dataModel = useMemo(() => buildDataModel(claimsData), [claimsData])
  const routeInitializedRef = useRef(false)
  const initialClaimsRef = useRef(claimsData)

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

    const bundledClaims = initialClaimsRef.current.length
      ? Promise.resolve(initialClaimsRef.current)
      : loadBundledClaims().then((items) => {
          if (active) {
            setClaimsData(items)
            writeClaimsCache(items)
            setDataError('')
            setDataLoading(false)
          }
          return items
        })

    fetchJson('/api/claims?limit=2000')
      .then((payload) => {
        if (!active) return
        const items = payload.items || []
        setClaimsData(items)
        writeClaimsCache(items)
        setDataError('')
      })
      .catch(async (apiError) => {
        if (!active) return
        try {
          const items = await bundledClaims
          if (!active) return
          if (items.length) setDataError('')
        } catch {
          if (active && !initialClaimsRef.current.length) setDataError(apiError.message)
        }
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
                <Card className="state-card">Loading MongoDB claim data...</Card>
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
  const [scenarioLoading, setScenarioLoading] = useState(true)
  const [scenarioError, setScenarioError] = useState('')
  const [riskFilter, setRiskFilter] = useState('All Scenarios')
  const [payerFilter, setPayerFilter] = useState('All Payers')
  const [sortBy, setSortBy] = useState('Highest Repeat Risk')
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
        setScenarioError('')
      })
      .catch(() => {
        if (cancelled) return
        setScenarios([])
        setScenarioError('The Python prediction service is unavailable. Start the Flask backend and refresh this page.')
      })
      .finally(() => {
        if (!cancelled) setScenarioLoading(false)
      })
    return () => { cancelled = true }
  }, [claimsData.length])

  const filteredScenarios = useMemo(() => scenarios
    .filter((scenario) => {
      const matchesSearch = !normalizedQuery || [
        scenario.anchor.number,
        scenario.patient,
        scenario.memberId,
        scenario.payer,
        scenario.provider,
        scenario.condition,
        scenario.diagnosisCode,
      ].some((value) => value?.toString().toLowerCase().includes(normalizedQuery))
      const matchesPayer = payerFilter === 'All Payers' || scenario.payer === payerFilter
      const matchesRisk = riskFilter === 'All Scenarios' || scenario.risk.level === riskFilter

      return matchesSearch && matchesPayer && matchesRisk
    })
    .sort((a, b) => {
      if (sortBy === 'Avoidable Spend') return b.avoidableSpend - a.avoidableSpend
      if (sortBy === 'Predicted Paid') return b.forecast.paid - a.forecast.paid
      if (sortBy === 'Most Visits') return b.totalVisitCount - a.totalVisitCount
      if (sortBy === 'Newest Episode') return b.episodeEnd.localeCompare(a.episodeEnd)
      return b.risk.score - a.risk.score
    }), [scenarios, normalizedQuery, payerFilter, riskFilter, sortBy])

  const summary = useMemo(() => ({
    totalScenarios: filteredScenarios.length,
    highRiskCount: filteredScenarios.filter((scenario) => scenario.risk.level === 'High').length,
    predictedPaid: filteredScenarios.reduce((total, scenario) => total + scenario.forecast.paid, 0),
    avoidableSpend: filteredScenarios.reduce((total, scenario) => total + scenario.avoidableSpend, 0),
  }), [filteredScenarios])
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
                    <option>High</option>
                    <option>Medium</option>
                    <option>Low</option>
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
                    <option>Highest Repeat Risk</option>
                    <option>Avoidable Spend</option>
                    <option>Predicted Paid</option>
                    <option>Most Visits</option>
                    <option>Newest Episode</option>
                  </select>
                  <ChevronDown size={16} />
                </label>
              </div>
            </div>

            {scenarioLoading ? <Card className="scenario-service-state"><RefreshCw className="spin" size={22} /> Building predictions from the claims database…</Card> : null}
            {scenarioError ? <Card className="scenario-service-state error"><Info size={22} /> {scenarioError}</Card> : null}
            {!scenarioLoading && !scenarioError ? (
              <>
                <PredictionSummary summary={summary} />
                <PredictionScenarioDirectory
                  scenarios={displayedScenarios}
                  totalCount={filteredScenarios.length}
                  onOpenScenario={(scenario) => onOpenPrediction(scenario.anchor)}
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
    fetchJson(`/api/predictions/provider-case/${encodeURIComponent(claim.number || claim.claimId)}`)
      .then((payload) => {
        if (!cancelled) setScenario(payload.item || null)
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
          {scenario.totalVisitCount} related claims · {scenario.peerCount.toLocaleString()} financial peers · {scenario.confidence} confidence
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
            <article className={`scenario-card scenario-${scenario.category}`} key={scenario.id}>
              <header className="scenario-card-header">
                <div className="scenario-condition-icon"><ShieldCheck size={24} /></div>
                <div>
                  <span>{scenario.pathway.label} · {scenario.diagnosisCode}</span>
                  <h3>{scenario.condition}</h3>
                  <p>{scenario.patient} · {scenario.memberId}</p>
                </div>
                <RiskBadge level={scenario.risk.level} score={scenario.risk.score} />
              </header>

              <div className="scenario-card-views">
                <div>
                  <span><Hospital size={15} /> Provider</span>
                  <strong>{scenario.provider}</strong>
                  <small>{scenario.payer}</small>
                </div>
                <div>
                  <span><Banknote size={15} /> Financial forecast</span>
                  <strong>{formatCurrency(scenario.forecast.paid)} predicted paid</strong>
                  <small>{formatCurrency(scenario.forecast.adjustment)} predicted adjustment</small>
                </div>
                <div>
                  <span><Target size={15} /> Provider opportunity</span>
                  <strong>{scenario.risk.level} · {scenario.risk.score}% repeat risk</strong>
                  <small>{scenario.bestSavingsPhase}</small>
                </div>
              </div>

              <div className="scenario-card-footer">
                <div>
                  <span>Likely outcome</span>
                  <strong>{scenario.likelyOutcome}</strong>
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
  const selectedClaim = scenario.selectedClaim || scenario.anchor
  const topMetrics = [
    ['Repeat risk', `${scenario.repeatRisk.score}%`, scenario.repeatRisk.level],
    ['Denial risk', `${scenario.denialRisk.score}%`, scenario.denialRisk.level],
    ['Expected payment', formatCurrency(scenario.forecast.paid), `${formatCurrency(scenario.forecast.paidRange.low)}–${formatCurrency(scenario.forecast.paidRange.high)}`],
    ['Potentially avoidable spend', formatCurrency(scenario.avoidableSpend), scenario.avoidableSpendSupported ? 'Evidence supported' : 'Insufficient evidence'],
    ['Model confidence', `${scenario.confidenceScore}%`, `${scenario.confidence} · ${scenario.peerCount} peer episodes`],
  ]

  return (
    <Card className="provider-forecast-detail">
      <header className="provider-forecast-heading">
        <div>
          <span>Provider case forecast · {scenario.episodeId}</span>
          <h1>{scenario.condition}</h1>
          <p>{scenario.provider} · {scenario.payer} · claim {selectedClaim.number || selectedClaim.claimId}</p>
        </div>
        <span className="priority-chip">Priority {scenario.priorityScore}/100</span>
      </header>

      <div className="provider-forecast-metrics">
        {topMetrics.map(([label, value, note]) => (
          <div key={label}><span>{label}</span><strong>{value}</strong><small>{note}</small></div>
        ))}
      </div>

      <div className="provider-forecast-sections">
        <section>
          <h2>Episode timeline</h2>
          <p>{formatDate(scenario.episodeStart)}–{formatDate(scenario.episodeEnd)} · {scenario.totalVisitCount} claims · diagnosis family {scenario.diagnosisFamily}</p>
          <div className="episode-claim-list">
            {scenario.claims.map((claim) => (
              <div className={claim.claimId === selectedClaim.claimId ? 'selected' : ''} key={claim.claimId}>
                <strong>{claim.number || claim.claimId}</strong><span>{formatDate(claim.dos)}</span><span>{claim.cptCode} · {claim.cptDescription}</span><span>{claim.status}</span>
              </div>
            ))}
          </div>
        </section>
        <section>
          <h2>Financial forecast</h2>
          <p>Allowed {formatCurrency(scenario.forecast.allowedRange.low)}–{formatCurrency(scenario.forecast.allowedRange.high)} · patient responsibility {formatCurrency(scenario.forecast.patientResp)} · adjustment {formatCurrency(scenario.forecast.adjustment)}</p>
          <small>{scenario.forecast.peerHierarchy}; only earlier adjudicated peer episodes are used.</small>
        </section>
        <section>
          <h2>Risk and evidence</h2>
          <ul>{scenario.riskDrivers.map((reason) => <li key={reason}>{reason}</li>)}</ul>
          <small>Repeat probability: 30 days {Math.round(scenario.repeatRisk.probabilities['30'] * 100)}%, 60 days {Math.round(scenario.repeatRisk.probabilities['60'] * 100)}%, 90 days {Math.round(scenario.repeatRisk.probabilities['90'] * 100)}%.</small>
        </section>
        <section>
          <h2>Recommended provider actions</h2>
          <ol>{scenario.recommendedActions.map((action) => <li key={action}>{action}</li>)}</ol>
          <small>Recommended phase: {scenario.bestSavingsPhase}. Priority indicates administrative review order, not clinical acuity.</small>
        </section>
      </div>

      <footer className="provider-forecast-note">{scenario.method} · Decision support only. No determination of medical necessity.</footer>
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
  const { claimsData } = useAppData()
  const prediction = predictClaim(claim, claimsData)

  return (
    <>
      <div className="claim-detail-layout">
        <SelectedClaimDetail claim={claim} />
        <PaymentForecastCard claim={claim} prediction={prediction} />
        <ClaimReasonCard claim={claim} />
      </div>
    </>
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

function PaymentForecastCard({ prediction }) {
  const moneyCards = [
    { label: 'Expected Allowed', value: formatCurrency(prediction.money.predictedAllowed), note: `${prediction.money.allowedRate}% allowed rate` },
    { label: 'Expected Paid', value: formatCurrency(prediction.money.predictedPaid), note: `Range ${formatRange(prediction.money.paidRange)}` },
    { label: 'Patient Balance', value: formatCurrency(prediction.money.predictedPatientResp), note: `${prediction.money.patientToAllowedRate}% of allowed` },
    { label: 'Expected Adjustment', value: formatCurrency(prediction.money.predictedAdjustment), note: `${prediction.money.adjustmentRate}% write-off` },
  ]
  const riskRows = [
    ['Denial Risk', prediction.risks.denial],
    ['Adjustment Risk', prediction.risks.adjustment],
    ['Collection Risk', prediction.risks.collection],
    ['COB / Forwarded Risk', prediction.risks.cob],
    ['Repeat Claim Risk', prediction.risks.repeat],
    ['Provider Risk', prediction.risks.provider],
  ]

  return (
    <Card className="payment-forecast-card">
      <div className="forecast-header">
        <div>
          <span>Prediction MVP</span>
          <h2>Payment Forecast & Claim Risk</h2>
          <p>{prediction.outcome.explanation} Confidence: {prediction.confidence}.</p>
        </div>
        <RiskBadge level={prediction.risks.overall.level} score={prediction.risks.overall.score} />
      </div>

      <div className="forecast-money-grid">
        {moneyCards.map((item) => (
          <div className="forecast-money-card" key={item.label}>
            <span>{item.label}</span>
            <strong>{item.value}</strong>
            <small>{item.note}</small>
          </div>
        ))}
      </div>

      <div className="forecast-detail-grid">
        <div className="forecast-risk-panel">
          <h3>Risk Signals</h3>
          <div className="forecast-risk-list">
            {riskRows.map(([label, risk]) => (
              <div key={label}>
                <span>{label}</span>
                <RiskBadge level={risk.level} score={risk.score} />
              </div>
            ))}
          </div>
        </div>

        <div className="forecast-risk-panel">
          <h3>Likely Outcome</h3>
          <strong>{prediction.outcome.likely}</strong>
          <p>{prediction.risks.denial.reason}</p>
          {prediction.resubmissionSuccess ? (
            <p>Resubmission success estimate: {prediction.resubmissionSuccess.score}%.</p>
          ) : null}
        </div>

        <div className="forecast-risk-panel">
          <h3>Fix Before Submit</h3>
          <ul>
            {prediction.fixes.map((fix) => (
              <li key={fix}>{fix}</li>
            ))}
          </ul>
        </div>
      </div>

      <div className="forecast-reasons-block">
        <h3>Why This Prediction</h3>
        <ul>
          {prediction.reasons.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      </div>
    </Card>
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

  useEffect(() => {
    setResult(null)
    setError('')
    setModalOpen(false)
  }, [claim.number, claim.claimId])

  const runAnalysis = async () => {
    setLoading(true)
    setError('')
    setModalOpen(true)
    try {
      const payload = await fetchJson(
        `/api/predictions/provider-case/${encodeURIComponent(claim.number || claim.claimId)}/llm`,
        { method: 'POST' },
      )
      setResult(payload)
    } catch (requestError) {
      setError(requestError.message || 'Provider LLM analysis is unavailable.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <Card className="provider-llm-card">
      <div className="provider-llm-header">
        <div>
          <span className="provider-llm-kicker"><Sparkles size={14} /> Groq provider assistant</span>
          <h2>Provider LLM Analysis</h2>
          <p>Analyses de-identified provider, payer, procedure, utilisation and payment facts for this claim episode.</p>
        </div>
        <div className="provider-llm-actions">
          <button className="llm-secondary-button" type="button" onClick={onCasePrediction}>View {claim.number || claim.claimId} forecast</button>
          <button className="llm-primary-button" type="button" onClick={runAnalysis} disabled={loading}>
            {loading ? <RefreshCw className="spin" size={16} /> : <Sparkles size={16} />}
            {loading ? 'Analysing…' : 'Run LLM analysis'}
          </button>
        </div>
      </div>

      {!result && !error ? (
        <div className="llm-intro">Run a concise provider-side explanation for this exact claim. Successful results are cached for faster repeat viewing.</div>
      ) : null}
      {error ? <div className="llm-config-note error">{error}</div> : null}
      {result && !result.configured ? (
        <div className="llm-config-note">
          <strong>LLM setup required</strong>
          <span>{result.message}</span>
        </div>
      ) : null}
      {result?.forecast ? <div className="llm-intro">Analysis ready for {result.claim_id}. Open the financial decision-support view to review predictions, backtest and evidence.</div> : null}
      {result?.forecast ? <button className="llm-secondary-button" type="button" onClick={() => setModalOpen(true)}>Open Provider LLM Analysis</button> : null}
      {modalOpen ? <ProviderLlmModal claim={claim} result={result} loading={loading} error={error} onClose={() => setModalOpen(false)} onRetry={runAnalysis} /> : null}
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
            <h2 id="provider-llm-modal-title">Provider LLM Analysis</h2>
            <p>{claim.number || claim.claimId} · Provider financial decision support</p>
          </div>
          <button className="provider-llm-close" type="button" aria-label="Close Provider LLM Analysis" onClick={onClose}><X size={20} /></button>
        </header>
        <div className="provider-llm-modal-body">
          {loading ? <div className="llm-modal-state"><RefreshCw className="spin" size={22} /> Calculating provider forecast and grounded explanation…</div> : null}
          {!loading && error ? <div className="llm-config-note error"><span>{error}</span><button className="llm-primary-button" type="button" onClick={onRetry}>Retry analysis</button></div> : null}
          {!loading && result?.forecast ? <ProviderMoneyLlmResult result={result} /> : null}
        </div>
      </div>
    </div>,
    document.body,
  )
}

function MetricBasisDetails({ basis, label }) {
  if (!basis) return null
  return (
    <details className="llm-how-calculated">
      <summary>How calculated</summary>
      <span>{label || 'Metric'} basis</span>
      <small>{basis.local_sample_size || 0} earlier member claim(s)</small>
      <small>{basis.external_sample_size || 0} external peer claim(s)</small>
      <small>Blend: {formatProbability(basis.blend_weights?.local)} local / {formatProbability(basis.blend_weights?.external)} external</small>
    </details>
  )
}

function ScenarioMapData({ content }) {
  if (Array.isArray(content)) return <ul>{content.map((item) => <li key={String(item)}>{String(item)}</li>)}</ul>
  return <dl>{Object.entries(content || {}).map(([key, value]) => {
    const label = key.replaceAll('_', ' ')
    if (value && typeof value === 'object' && !Array.isArray(value)) return <div className="scenario-map-nested" key={key}><dt>{label}</dt><dd><ScenarioMapData content={value} /></dd></div>
    const currency = typeof value === 'number' && /payment|paid|allowed|responsibility|adjustment|exposure|risk|spend|amount|charge|opportunity/.test(key) && !/rate|probability/.test(key)
    return <div key={key}><dt>{label}</dt><dd>{currency ? formatOptionalCurrency(value) : Array.isArray(value) ? value.join(', ') || 'None' : value === null ? 'Not enough evidence to estimate reliably.' : String(value)}</dd></div>
  })}</dl>
}

function ProviderMoneyLlmResult({ result }) {
  const forecast = result.forecast || {}
  const facts = result.actual_claim_facts || {}
  const opportunity = result.provider_financial_opportunity_summary || {}
  const money = result.provider_financial_metrics || {}
  const reconciliation = result.financial_reconciliation || {}
  const backtest = result.backtest_against_actual || {}
  const scenario = result.provider_money_scenario_map || {}
  const basis = result.prediction_basis || {}
  const denial = forecast.denial_risk || {}
  const repeat = forecast.repeat_service_risk || {}
  const confidence = forecast.confidence || {}
  const metricBasis = basis.metric_basis || {}
  const actions = Array.isArray(result.recommended_actions) ? result.recommended_actions : []
  const drivers = Array.isArray(result.risk_drivers) ? result.risk_drivers : []
  const evidence = Array.isArray(result.evidence_used) ? result.evidence_used : []
  const savings = result.where_provider_money_can_be_saved || {}
  const currentOpportunity = savings.current_claim_opportunity || {}
  const futureExposure = savings.future_exposure || {}
  const avoidableSpend = savings.avoidable_spend || {}
  const bestAction = savings.best_action || {}
  const savingsComparison = savings.historical_comparison || {}
  const recurrenceEvidence = savings.recurrence_evidence || {}
  const dataRequired = Array.isArray(savings.data_required_for_stronger_estimate) ? savings.data_required_for_stronger_estimate : []
  const reconciliationDifference = savings.forecast_reconciliation_difference || {}
  const unavailable = 'Not enough evidence to estimate reliably.'
  const snapshotCards = [
    { label: 'Predicted claim outcome', value: forecast.predicted_claim_outcome?.display_value || 'Unavailable', note: `Probability ${formatProbability(forecast.predicted_claim_outcome?.probability)}` },
    { label: 'Denial probability', value: formatProbability(denial.probability), note: `${denial.level || 'unknown'} risk`, basis: { local_sample_size: denial.basis?.member_sample_size, external_sample_size: denial.basis?.external_sample_size, blend_weights: denial.basis?.blend_weights } },
    { label: '30-day repeat probability', value: formatProbability(repeat.probability_30d), basis: { local_sample_size: repeat.basis?.['30']?.member_trials, external_sample_size: repeat.basis?.['30']?.peer_trials, blend_weights: repeat.basis?.['30']?.blend_weights } },
    { label: '60-day repeat probability', value: formatProbability(repeat.probability_60d), basis: { local_sample_size: repeat.basis?.['60']?.member_trials, external_sample_size: repeat.basis?.['60']?.peer_trials, blend_weights: repeat.basis?.['60']?.blend_weights } },
    { label: '90-day repeat probability', value: formatProbability(repeat.probability_90d), note: `${repeat.level || 'unknown'} risk`, basis: { local_sample_size: repeat.basis?.['90']?.member_trials, external_sample_size: repeat.basis?.['90']?.peer_trials, blend_weights: repeat.basis?.['90']?.blend_weights } },
    { label: 'Predicted allowed', value: formatOptionalCurrency(forecast.predicted_allowed?.value), note: formatPredictionRange(forecast.predicted_allowed), basis: metricBasis.predicted_allowed },
    { label: 'Predicted paid', value: formatOptionalCurrency(forecast.predicted_paid?.value), note: formatPredictionRange(forecast.predicted_paid), basis: metricBasis.predicted_paid },
    { label: 'Predicted patient responsibility', value: formatOptionalCurrency(forecast.predicted_patient_responsibility?.value), note: formatPredictionRange(forecast.predicted_patient_responsibility), basis: metricBasis.predicted_patient_responsibility },
    { label: 'Predicted adjustment', value: formatOptionalCurrency(forecast.predicted_adjustment?.value), note: formatPredictionRange(forecast.predicted_adjustment), basis: metricBasis.predicted_adjustment },
    { label: 'Provider expected net reimbursement', value: formatOptionalCurrency(money.provider_expected_net_reimbursement), note: 'Equals predicted provider payment' },
    { label: 'Provider denial revenue exposure', value: formatOptionalCurrency(futureExposure.expected_denial_revenue_exposure), note: futureExposure.label || 'Forecast exposure — not confirmed savings' },
    { label: 'Estimated financial opportunity', value: currentOpportunity.status === 'validated' ? formatOptionalCurrency(currentOpportunity.amount) : 'No validated opportunity', note: bestAction.stage || 'No immediate validated savings action' },
    { label: 'Potentially avoidable repeat spend', value: Number.isFinite(money.potentially_avoidable_repeat_spend) ? formatOptionalCurrency(money.potentially_avoidable_repeat_spend) : unavailable, note: forecast.potentially_avoidable_spend?.reason || '' },
    { label: 'Model confidence', value: formatProbability(confidence.score), note: confidence.level || 'unknown' },
    { label: 'Prediction method', value: (confidence.prediction_method || 'Unavailable').replaceAll('_', ' '), note: confidence.model_version || '' },
  ]
  const factRows = [
    ['Claim ID', facts.claim_id], ['Service date', formatDate(facts.service_date)], ['Member-safe reference', facts.member_safe_reference], ['Payer', facts.payer],
    ['Billing provider', facts.billing_provider], ['Rendering provider', facts.rendering_provider], ['CPT', [facts.cpt_code, facts.cpt_description].filter(Boolean).join(' — ')],
    ['ICD-10 / diagnosis family', [facts.diagnosis_code, facts.diagnosis_family, facts.diagnosis_description].filter(Boolean).join(' — ')], ['Units', facts.units],
    ['Place of service', [facts.place_of_service_code, facts.place_of_service_description].filter(Boolean).join(' — ')], ['Actual charge', formatOptionalCurrency(facts.charge_amount)],
    ['Actual allowed', formatOptionalCurrency(facts.allowed_amount)], ['Actual paid', formatOptionalCurrency(facts.paid_amount)], ['Actual patient responsibility', formatOptionalCurrency(facts.patient_responsibility)],
    ['Actual adjustment', formatOptionalCurrency(facts.adjustment_amount)], ['Actual status', facts.claim_status], ['Actual denial reason', facts.denial_reason || 'None recorded'],
    ['Prior authorization status', facts.has_prior_auth ? 'Present' : 'Missing — requirement unknown'], ['Referral status', facts.has_referral ? 'Present' : 'Missing — requirement unknown'],
  ]
  const backtestRows = ['allowed', 'paid', 'patient_responsibility', 'adjustment'].map((key) => [key.replaceAll('_', ' '), backtest[key] || {}])
  const mapSections = [
    ['Member claim-history view', scenario.member_claim_history], ['Encounter and coding view', scenario.encounter_and_coding],
    ['Provider claim/payment prediction', scenario.provider_claim_payment_prediction, 'provider-focus'], ['Where provider money may be saved', scenario.where_provider_money_may_be_saved],
    ['Cost-leakage risks', scenario.cost_leakage_risks], ['Provider money comparison', scenario.provider_money_comparison],
  ]

  return (
    <div className="provider-llm-result provider-money-result">
      <section className="llm-wide-section financial-opportunity-summary">
        <div className="llm-section-heading"><span>Provider Financial Opportunity Summary</span><small>{opportunity.best_savings_phase || 'insufficient evidence'}</small></div>
        <div className="financial-opportunity-grid">
          <article><span>Expected provider payment</span><strong>{formatOptionalCurrency(opportunity.expected_provider_payment)}</strong></article>
          <article><span>Expected denial revenue exposure</span><strong>{formatOptionalCurrency(futureExposure.expected_denial_revenue_exposure)}</strong></article>
          <article><span>Validated current-claim opportunity</span><strong>{opportunity.opportunity_available ? formatOptionalCurrency(opportunity.provider_opportunity_amount) : 'No validated opportunity'}</strong></article>
        </div>
        <p>{opportunity.supporting_reason}</p><small>Owner: {opportunity.responsible_operational_team} · Evidence: {(opportunity.affected_claim_ids || []).join(', ')}</small>
      </section>

      <section className="llm-wide-section prediction-snapshot">
        <div className="llm-section-heading"><span>Prediction Snapshot</span><small>{forecast.forecast_label}</small></div>
        <div className="llm-snapshot-grid money-snapshot-grid">{snapshotCards.map((card) => <article className="llm-snapshot-card" key={card.label}><span>{card.label}</span><strong>{card.value}</strong>{card.note ? <small>{card.note}</small> : null}<MetricBasisDetails basis={card.basis} label={card.label} /></article>)}</div>
      </section>

      <section className="llm-wide-section provider-savings-section">
        <div className="llm-section-heading"><span>Where Provider Money Can Be Saved</span><small>Validated recovery and forecast exposure kept separate</small></div>

        <article className={`savings-status-banner ${currentOpportunity.status || 'insufficient'}`}>
          <span>A. Validated current-claim opportunity</span>
          <strong>{currentOpportunity.status === 'validated' ? `${currentOpportunity.type?.replaceAll('_', ' ')} · ${formatOptionalCurrency(currentOpportunity.amount)}` : 'No validated current-claim savings opportunity identified'}</strong>
          <p>{currentOpportunity.status === 'validated' ? 'The amount is supported by the matched historical comparison.' : 'The claim can still have forecast financial exposure, but exposure is not confirmed savings.'}</p>
        </article>

        <div className="savings-opportunity-columns">
          <article>
            <header><strong>Current-claim evidence</strong><small>{currentOpportunity.sample_size || 0} matched claim(s)</small></header>
            <dl>
              {(currentOpportunity.calculation_basis || []).map((item) => <div key={item.metric}><dt>{item.metric.replaceAll('_', ' ')}</dt><dd>{Number.isFinite(item.value) ? formatOptionalCurrency(item.value) : 'Not calculated'}<small>{item.formula}</small></dd></div>)}
              <div><dt>Matching level</dt><dd>{currentOpportunity.peer_match_level || 'Insufficient evidence'}<small>Minimum sample: {currentOpportunity.minimum_sample_size || 0}</small></dd></div>
              <div><dt>Patient-balance opportunity</dt><dd>{currentOpportunity.patient_balance_opportunity_available ? 'Confirmed' : 'Unavailable'}<small>{currentOpportunity.patient_balance_reason}</small></dd></div>
            </dl>
          </article>
          <article>
            <header><strong>B. Future financial exposure</strong><small>{futureExposure.label}</small></header>
            <dl>
              <div><dt>Denial revenue exposure</dt><dd>{formatOptionalCurrency(futureExposure.expected_denial_revenue_exposure)}<small>{formatProbability(futureExposure.denial_probability)} × predicted paid</small></dd></div>
              <div><dt>Repeat allowed exposure</dt><dd>{formatOptionalCurrency(futureExposure.expected_repeat_allowed_exposure)}<small>{formatProbability(futureExposure.repeat_probability_90d)} × predicted allowed</small></dd></div>
              <div><dt>Repeat provider-payment exposure</dt><dd>{formatOptionalCurrency(futureExposure.expected_repeat_provider_payment_exposure)}<small>{formatProbability(futureExposure.repeat_probability_90d)} × predicted paid</small></dd></div>
              <div><dt>Forecast reconciliation difference</dt><dd>{formatOptionalCurrency(reconciliationDifference.value)}<small>This is not savings or recoverable revenue.</small></dd></div>
              <div><dt>Potentially avoidable spend</dt><dd>{avoidableSpend.available ? formatOptionalCurrency(avoidableSpend.amount) : 'Not validated'}<small>{avoidableSpend.reason}</small></dd></div>
            </dl>
          </article>
        </div>

        <div className="savings-action-callout">
          <span>C. Best next provider action</span>
          <strong>{bestAction.stage || 'No immediate validated savings action'}</strong>
          <p>{bestAction.action} {bestAction.reason}</p>
          <small>Amount addressed: {Number.isFinite(bestAction.amount_addressed) ? formatOptionalCurrency(bestAction.amount_addressed) : 'None'} ({bestAction.amount_type || 'none'}) · Owner: {bestAction.owner || 'Provider operations'} · Confidence: {formatProbability(bestAction.confidence)}</small>
        </div>

        <article className="savings-comparison-card">
          <header><strong>D. Historical comparison</strong><small>{savingsComparison.peer_count || 0} matched claim(s) · {savingsComparison.match_level || 'Insufficient evidence'}</small></header>
          <div className="savings-rate-grid">
            {[
              ['Allowed rate', savingsComparison.actual_allowed_rate, savingsComparison.peer_allowed_rate, savingsComparison.indicators?.allowed_rate],
              ['Paid-to-allowed rate', savingsComparison.actual_paid_to_allowed_rate, savingsComparison.peer_paid_to_allowed_rate, savingsComparison.indicators?.paid_to_allowed_rate],
              ['Adjustment rate', savingsComparison.actual_adjustment_rate, savingsComparison.peer_adjustment_rate, savingsComparison.indicators?.adjustment_rate],
              ['Patient-share rate', savingsComparison.actual_patient_share_rate, savingsComparison.peer_patient_share_rate, savingsComparison.indicators?.patient_share_rate],
            ].map(([label, actualRate, peerRate, indicator]) => <div key={label}><span>{label}</span><strong>{formatProbability(actualRate)} actual</strong><small>{formatProbability(peerRate)} historical median</small><b className={indicator}>{indicator || 'Unavailable'}</b></div>)}
          </div>
          <p>{savingsComparison.conclusion}</p>
          <small>Matched claims: {(savingsComparison.affected_claim_ids || []).join(', ') || 'None'} · Cutoff: {savingsComparison.prediction_cutoff_date || basis.prediction_cutoff_date}</small>
        </article>

        <div className="savings-evidence-grid">
          <article><span>Recurrence evidence</span>{['30', '60', '90'].map((horizon) => { const item = recurrenceEvidence[horizon] || {}; return <p key={horizon}><strong>{horizon} days:</strong> {item.local_numerator || 0}/{item.local_denominator || 0} local · {item.external_numerator || 0}/{item.external_denominator || 0} external · {formatProbability(item.final_blended_rate)} blended</p> })}<small>{(recurrenceEvidence['90']?.filters_used || []).join(' · ')}</small></article>
          <article><span>E. Data required for stronger savings estimate</span><ul>{dataRequired.map((item) => <li key={item}>{item}</li>)}</ul></article>
        </div>
      </section>

      <section className="llm-wide-section actual-facts-section">
        <div className="llm-section-heading"><span>Actual Claim Facts</span><small>Actual adjudicated result — separate from predictions</small></div>
        <dl className="llm-facts-grid money-facts-grid">{factRows.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value ?? 'Unavailable'}</dd></div>)}</dl>
      </section>

      <section className="llm-wide-section backtest-section">
        <div className="llm-section-heading"><span>Backtest Against Actual Result</span><small>Temporal holdout at {backtest.prediction_cutoff_date}</small></div>
        <div className="backtest-outcome"><strong>Claim outcome</strong><span>Predicted: {backtest.claim_outcome?.predicted || 'Unavailable'} ({formatProbability(backtest.claim_outcome?.probability)})</span><span>Actual: {backtest.claim_outcome?.actual || 'Unavailable'}</span><b>{backtest.claim_outcome?.correct ? 'Matched' : 'Did not match'}</b></div>
        <div className="backtest-grid">{backtestRows.map(([label, item]) => <article key={label}><span>{label}</span><strong>{formatOptionalCurrency(item.predicted)} predicted</strong><small>{formatOptionalCurrency(item.actual)} actual</small><small>Error {formatOptionalCurrency(item.absolute_error)} · {Number.isFinite(item.percentage_error) ? `${item.percentage_error.toFixed(1)}%` : 'N/A'}</small><small>{formatPredictionRange(item.range)} · {item.actual_in_range === null ? 'Range unavailable' : item.actual_in_range ? 'Actual inside range' : 'Actual outside range'}</small></article>)}</div>
      </section>

      <section className="llm-wide-section scenario-map-section">
        <div className="llm-section-heading"><span>Provider Money Scenario Map</span><small>Generated from this claim and earlier history</small></div>
        <div className="provider-money-map">{mapSections.map(([title, content, className]) => <article className={className || ''} key={title}><strong>{title}</strong><ScenarioMapData content={content} /></article>)}</div>
      </section>

      <section className="llm-wide-section">
        <div className="llm-section-heading"><span>Financial Risk Drivers</span></div>
        <div className="llm-driver-list">{drivers.map((driver) => <article key={driver.title}><header><strong>{driver.title}</strong><b>{driver.value}</b></header><p>{driver.reason}</p><small>{driver.source_type?.replaceAll('_', ' ')} · {driver.risk_direction}</small></article>)}</div>
        {reconciliation.warnings?.map((warning) => <p className="financial-warning" key={warning}>{warning}</p>)}
      </section>

      <section className="llm-wide-section">
        <div className="llm-section-heading"><span>Ranked Provider Actions</span><small>Evidence-driven administrative actions</small></div>
        <div className="llm-action-list ranked-action-list">{actions.map((action) => <article key={action.code}><header><b>#{action.rank}</b><strong>{action.title}</strong><span>{action.urgency}</span></header><p>{action.reason}</p><small>Impact: {Number.isFinite(action.expected_financial_impact) ? formatOptionalCurrency(action.expected_financial_impact) : 'Not estimated'} · Owner: {action.operational_owner} · Claims: {(action.affected_claim_ids || []).join(', ')}</small></article>)}</div>
      </section>

      <section className="llm-wide-section prediction-basis-section">
        <div className="llm-section-heading"><span>Prediction Basis and Peer Evidence</span><small>Cutoff {basis.prediction_cutoff_date}</small></div>
        <dl className="llm-facts-grid"><div><dt>Earlier member claims</dt><dd>{basis.member_prior_claims_used || 0}</dd></div><div><dt>Earlier same-CPT claims</dt><dd>{basis.member_prior_same_cpt_claims || 0}</dd></div><div><dt>External financial peers</dt><dd>{basis.peer_claims_used || 0}</dd></div><div><dt>Peer episodes</dt><dd>{basis.peer_episodes_used || 0}</dd></div><div><dt>Matching level</dt><dd>{basis.matching_level}</dd></div><div><dt>Fallback level</dt><dd>{basis.fallback_level}</dd></div><div><dt>Model version</dt><dd>{basis.model_version}</dd></div><div><dt>Confidence</dt><dd>{formatProbability(confidence.score)} · {confidence.level}</dd></div></dl>
        <p className="confidence-explanation"><strong>Confidence drivers:</strong> {(confidence.drivers || []).join(', ') || 'None recorded'}. <strong>Penalties:</strong> {(confidence.penalties || []).join(', ') || 'None recorded'}.</p>
        <div className="llm-evidence-list">{evidence.map((item) => <article key={item.claim_id}><strong>Claim {item.claim_id}</strong><small>{formatDate(item.service_date)} · CPT {item.cpt_code} · {item.claim_status}</small><small>Actual allowed {formatOptionalCurrency(item.actual_allowed)} · Actual paid {formatOptionalCurrency(item.actual_paid)}</small></article>)}</div>
      </section>

      <ProviderPredictionChat result={result} />
    </div>
  )
}

function ProviderPredictionChat({ result }) {
  const claimId = result.claim_id
  const episodeId = result.episode_id
  const storageKey = `payerpayee.provider-chat.${claimId}.${episodeId}`
  const conversationId = useMemo(() => `${claimId}-${episodeId}-${Date.now().toString(36)}`, [claimId, episodeId])
  const [messages, setMessages] = useState(() => {
    try {
      const cached = JSON.parse(window.localStorage.getItem(storageKey) || 'null')
      if (Array.isArray(cached)) return cached
    } catch { /* use welcome message */ }
    return [{ role: 'assistant', text: 'Ask me to explain any backend-calculated prediction, financial exposure, sample basis, backtest result or provider action. I cannot change the calculated values.' }]
  })
  const [draft, setDraft] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [lastQuestion, setLastQuestion] = useState('')
  const resultsRef = useRef(null)
  const suggested = result.suggested_questions || ['How was the predicted allowed amount calculated?', 'How much provider revenue is at risk?', 'Which historical claims were used?', 'How confident is the model and why?']

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
      window.requestAnimationFrame(() => resultsRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }))
    } catch (requestError) {
      setError(requestError.message || 'Chat response is unavailable.')
    } finally { setLoading(false) }
  }
  const clear = () => { setMessages([{ role: 'assistant', text: 'Chat cleared. Ask a question about this prediction.' }]); setError('') }

  return (
    <>
      <section className="llm-wide-section provider-chat-results" ref={resultsRef} aria-live="polite">
        <div className="llm-section-heading"><span>Prediction Assistant Results</span><button type="button" onClick={clear}>Clear chat</button></div>
        <div className="chat-results-list">{messages.map((message, index) => <article className={`chat-result-card ${message.role}`} key={`${message.role}-${index}`}><strong>{message.role === 'user' ? 'Your question' : 'Grok provider assistant'}</strong><p>{message.text}</p>{message.meta?.evidence_claim_ids?.length ? <small>Evidence: {message.meta.evidence_claim_ids.join(', ')}</small> : null}</article>)}{loading ? <article className="chat-result-card assistant loading"><RefreshCw className="spin" size={16} /> Reviewing the structured prediction…</article> : null}</div>
      </section>
      <aside className="provider-chat-prompt" aria-label="Ask About This Prediction">
        <div className="provider-chat-prompt-title"><Sparkles size={17} /><span>Ask Grok about this prediction</span></div>
        <div className="chat-suggestions">{suggested.slice(0, 5).map((question) => <button type="button" key={question} onClick={() => submit(question)}>{question}</button>)}</div>
        {error ? <div className="chat-error">{error}<button type="button" onClick={() => submit(lastQuestion)}>Retry</button></div> : null}
        <div className="chatgpt-composer"><textarea value={draft} onChange={(event) => setDraft(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); submit() } }} placeholder="Ask about this claim prediction…" rows="1" /><button type="button" aria-label="Send chat question" onClick={() => submit()} disabled={loading || !draft.trim()}><Send size={18} /></button></div>
        <small>Enter to send · Shift+Enter for a new line</small>
      </aside>
    </>
  )
}

export function ProviderLlmResult({ result }) {
  const forecast = result.forecast || {}
  const facts = result.actual_claim_facts || {}
  const analysis = result.llm_analysis || result.analysis || {}
  const outcome = forecast.predicted_claim_outcome || {}
  const denial = forecast.denial_risk || {}
  const repeat = forecast.repeat_service_risk || {}
  const confidence = forecast.confidence || {}
  const avoidable = forecast.potentially_avoidable_spend || {}
  const basis = result.prediction_basis || {}
  const riskDrivers = Array.isArray(result.risk_drivers) ? result.risk_drivers : []
  const actions = Array.isArray(result.recommended_actions) ? result.recommended_actions : []
  const evidence = Array.isArray(result.evidence_used) ? result.evidence_used : []
  const limitations = Array.isArray(result.limitations) ? result.limitations : []
  const snapshotCards = [
    { label: 'Predicted claim outcome', value: outcome.display_value || 'Unavailable', note: `Probability: ${formatProbability(outcome.probability)}`, lines: [`Peer sample: ${confidence.peer_sample_size || 0}`] },
    { label: 'Denial risk', value: formatProbability(denial.probability), note: `${denial.level || 'unknown'} risk`, lines: [`Peer sample: ${confidence.peer_sample_size || 0}`] },
    { label: 'Repeat-service forecast', value: `90 days: ${formatProbability(repeat.probability_90d)}`, lines: [`30 days: ${formatProbability(repeat.probability_30d)}`, `60 days: ${formatProbability(repeat.probability_60d)}`, `Risk level: ${repeat.level || 'unknown'}`, `Peer sample: ${confidence.peer_sample_size || 0}`] },
    { label: 'Predicted allowed', value: formatOptionalCurrency(forecast.predicted_allowed?.value), note: formatPredictionRange(forecast.predicted_allowed), lines: [`Peer sample: ${confidence.peer_sample_size || 0}`] },
    { label: 'Predicted paid', value: formatOptionalCurrency(forecast.predicted_paid?.value), note: formatPredictionRange(forecast.predicted_paid), lines: [`Peer sample: ${confidence.peer_sample_size || 0}`] },
    { label: 'Predicted patient responsibility', value: formatOptionalCurrency(forecast.predicted_patient_responsibility?.value), note: formatPredictionRange(forecast.predicted_patient_responsibility), lines: [`Peer sample: ${confidence.peer_sample_size || 0}`] },
    { label: 'Predicted adjustment', value: formatOptionalCurrency(forecast.predicted_adjustment?.value), note: formatPredictionRange(forecast.predicted_adjustment), lines: [`Peer sample: ${confidence.peer_sample_size || 0}`] },
    { label: 'Potentially avoidable spend', value: avoidable.available ? formatOptionalCurrency(avoidable.value) : 'Not enough evidence', note: avoidable.available ? avoidable.savings_phase : (avoidable.reason || 'Estimate unavailable'), lines: [`Peer sample: ${confidence.peer_sample_size || 0}`] },
    { label: 'Model confidence', value: Number.isFinite(confidence.score) ? formatProbability(confidence.score) : 'Unavailable', note: confidence.level || 'unknown', lines: [`Peer sample: ${confidence.peer_sample_size || 0}`] },
    { label: 'Peer sample size', value: Number.isFinite(confidence.peer_sample_size) ? confidence.peer_sample_size.toLocaleString() : 'Unavailable', note: `${confidence.peer_episode_count || 0} peer episodes` },
    { label: 'Prediction method', value: (confidence.prediction_method || 'Unavailable').replaceAll('_', ' '), note: confidence.model_version || '' },
  ]

  const factRows = [
    ['Claim ID', facts.claim_id], ['Actual claim status', facts.claim_status], ['CPT', [facts.cpt_code, facts.cpt_description].filter(Boolean).join(' — ')],
    ['ICD-10 diagnosis family', [facts.diagnosis_family, facts.diagnosis_description].filter(Boolean).join(' — ')],
    ['Place of service', [facts.place_of_service_code, facts.place_of_service_description].filter(Boolean).join(' — ')],
    ['Actual charge', formatOptionalCurrency(facts.charge_amount)], ['Actual allowed', formatOptionalCurrency(facts.allowed_amount)],
    ['Actual paid', formatOptionalCurrency(facts.paid_amount)], ['Actual patient responsibility', formatOptionalCurrency(facts.patient_responsibility)],
    ['Actual adjustment', formatOptionalCurrency(facts.adjustment_amount)], ['Actual denial reason', facts.denial_reason || 'None recorded'],
    ['Prior authorization', facts.has_prior_auth ? 'Present' : 'Missing — requirement unknown'], ['Referral', facts.has_referral ? 'Present' : 'Missing — requirement unknown'],
  ]

  const forecastRows = [
    ['Predicted allowed', forecast.predicted_allowed], ['Predicted paid', forecast.predicted_paid],
    ['Predicted patient responsibility', forecast.predicted_patient_responsibility], ['Predicted adjustment', forecast.predicted_adjustment],
  ]

  return (
    <div className="provider-llm-result">
      <section className="llm-wide-section prediction-snapshot">
        <div className="llm-section-heading"><span>Prediction snapshot</span><small>All values below are model estimates</small></div>
        <div className="llm-snapshot-grid">
          {snapshotCards.map((card) => (
            <article className="llm-snapshot-card" key={card.label}>
              <span>{card.label}</span><strong>{card.value}</strong>
              {card.note ? <small>{card.note}</small> : null}
              {card.lines?.map((line) => <small key={line}>{line}</small>)}
            </article>
          ))}
        </div>
      </section>

      <section className="llm-wide-section actual-facts-section">
        <div className="llm-section-heading"><span>Actual claim facts</span><small>{facts.adjudicated ? 'Actual adjudicated result' : 'Historical claim record'}</small></div>
        <dl className="llm-facts-grid">{factRows.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value || 'Unavailable'}</dd></div>)}</dl>
      </section>

      <section className="llm-wide-section forecast-detail-section">
        <div className="llm-section-heading"><span>{forecast.forecast_label || 'Forecast'}</span><small>Predictions are separate from actual adjudication</small></div>
        <div className="llm-financial-grid">
          {forecastRows.map(([label, item]) => (
            <article key={label}><span>{label}</span><strong>{formatOptionalCurrency(item?.value)}</strong><small>Low {formatOptionalCurrency(item?.low)} · High {formatOptionalCurrency(item?.high)}</small><em>{confidence.peer_sample_size || 0} peer claims</em></article>
          ))}
        </div>
      </section>

      <section className="llm-wide-section">
        <div className="llm-section-heading"><span>Provider summary</span></div>
        <p>{analysis.provider_summary || 'A provider explanation is unavailable; the deterministic prediction snapshot remains available above.'}</p>
      </section>

      <section className="llm-wide-section prediction-basis-section">
        <div className="llm-section-heading"><span>Prediction basis</span></div>
        <dl className="llm-facts-grid">
          <div><dt>Peer claims used</dt><dd>{basis.peer_claims_used?.toLocaleString?.() || 0}</dd></div>
          <div><dt>Earlier member claims used</dt><dd>{basis.member_prior_claims_used?.toLocaleString?.() || 0}</dd></div>
          <div><dt>Earlier same-CPT claims</dt><dd>{basis.member_prior_same_cpt_claims?.toLocaleString?.() || 0}</dd></div>
          <div><dt>Member financial claims used</dt><dd>{basis.member_financial_claims_used?.toLocaleString?.() || 0}</dd></div>
          <div><dt>Matching level</dt><dd>{basis.matching_level || 'Unavailable'}</dd></div>
          <div><dt>Member financial match</dt><dd>{basis.member_financial_match_level || 'No matching history'}</dd></div>
          <div><dt>Fallback</dt><dd>{basis.fallback_explanation || 'Unavailable'}</dd></div>
          <div><dt>Historical peer denial rate</dt><dd>{formatProbability(basis.historical_peer_denial_rate)}</dd></div>
          <div><dt>Median allowed rate</dt><dd>{formatProbability(basis.median_allowed_rate)}</dd></div>
          <div><dt>Median paid-to-allowed rate</dt><dd>{formatProbability(basis.median_paid_to_allowed_rate)}</dd></div>
        </dl>
        <p className="confidence-explanation"><strong>{confidence.level || 'Unknown'} confidence.</strong> {confidence.explanation || 'Confidence explanation unavailable.'}</p>
      </section>

      <section className="llm-wide-section">
        <div className="llm-section-heading"><span>Risk drivers</span></div>
        <div className="llm-driver-list">{riskDrivers.length ? riskDrivers.map((driver) => <article key={driver.title}><header><strong>{driver.title}</strong><b>{driver.value}</b></header><p>{driver.reason}</p><small>{driver.source_type?.replaceAll('_', ' ')} · {driver.risk_direction} · Evidence: {(driver.evidence_ids || []).join(', ')}</small></article>) : <p>No risk drivers are available.</p>}</div>
      </section>

      <section className="llm-wide-section">
        <div className="llm-section-heading"><span>Recommended provider actions</span></div>
        <div className="llm-action-list">{actions.length ? actions.map((action) => <article key={action.code}><strong>{action.title}</strong><p>{action.reason}</p></article>) : <p>No claim-specific administrative action is supported.</p>}</div>
      </section>

      <section className="llm-wide-section">
        <div className="llm-section-heading"><span>Evidence used</span></div>
        <div className="llm-evidence-list">{evidence.map((item) => <article key={item.claim_id}><strong>Claim {item.claim_id}</strong><dl><div><dt>Service date</dt><dd>{formatDate(item.service_date)}</dd></div><div><dt>CPT</dt><dd>{item.cpt_code} — {item.cpt_description}</dd></div><div><dt>Diagnosis family</dt><dd>{item.diagnosis_family} — {item.diagnosis_description}</dd></div><div><dt>Place of service</dt><dd>{item.place_of_service_code} — {item.place_of_service_description}</dd></div><div><dt>Status</dt><dd>{item.claim_status}</dd></div><div><dt>Actual allowed</dt><dd>{formatOptionalCurrency(item.actual_allowed)}</dd></div><div><dt>Actual paid</dt><dd>{formatOptionalCurrency(item.actual_paid)}</dd></div><div><dt>Actual patient responsibility</dt><dd>{formatOptionalCurrency(item.actual_patient_responsibility)}</dd></div><div><dt>Actual adjustment</dt><dd>{formatOptionalCurrency(item.actual_adjustment)}</dd></div></dl><small>Prediction fields used: {(item.prediction_fields_used || []).join(', ')}</small></article>)}</div>
      </section>

      <section className="llm-wide-section">
        <div className="llm-section-heading"><span>Limitations</span></div>
        <ul>{limitations.map((item) => <li key={item}>{item}</li>)}</ul>
      </section>

      <details className="llm-exact-output">
        <summary>Exact model output</summary>
        <pre>{JSON.stringify(result.exact_model_output || {}, null, 2)}</pre>
      </details>

      <small className="llm-result-meta">Model: {result.model} · {result.promptVersion}{result.cached ? ' · cached' : ` · ${result.latencyMs || 0} ms`}{result.fallback ? ' · deterministic fallback' : ''}. Decision support only.</small>
    </div>
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
  const cards = [
    { label: 'Provider Cases', value: summary.totalScenarios.toLocaleString(), note: 'claim episodes available for provider review', tone: 'blue' },
    { label: 'High Repeat Risk', value: summary.highRiskCount.toLocaleString(), note: 'episodes needing pathway review', tone: 'violet' },
    { label: 'Predicted Paid', value: formatCurrency(summary.predictedPaid), note: 'episode forecast from financial peers', tone: 'green' },
    { label: 'Avoidable Spend', value: formatCurrency(summary.avoidableSpend), note: 'modeled opportunity after the first visit', tone: 'orange' },
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
