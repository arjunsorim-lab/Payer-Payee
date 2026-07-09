import { useEffect, useRef, useState } from 'react'
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
  Stethoscope,
  TrendingDown,
  TrendingUp,
  UserRound,
  Users,
} from 'lucide-react'
import './App.css'
import { claimsData } from './data/claimsData'
import { buildPredictionSummary, predictClaim } from '../../shared/predictionEngine'

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

const defaultDateRange = getDateRange(claimsData)
const recentClaims = claimsData.slice(0, 10)
const recentEncounters = claimsData.slice(0, 14)
const payerOptions = ['All Payers', ...uniqueValues(claimsData, 'payer')]
const planOptions = ['All Plans', ...uniqueValues(claimsData, 'filingIndicator')]
const providerOptions = ['All Groups', ...uniqueValues(claimsData, 'billingProvider')]
const members = buildMembers(claimsData)
const membersById = new Map(members.map((member) => [member.memberId, member]))

function uniqueValues(rows, key) {
  return [...new Set(rows.map((row) => row[key]).filter(Boolean))].sort((a, b) => a.localeCompare(b))
}

function getDateRange(rows) {
  const dates = rows.map((row) => row.dos).filter(Boolean).sort()
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
    { label: 'Total Allowed', value: formatCurrency(member.totalAllowed), delta: '+ 8.4%', dir: 'up' },
    { label: 'Total Paid', value: formatCurrency(member.totalPaid), delta: '+ 9.1%', dir: 'up' },
    { label: 'Patient Responsibility', value: formatCurrency(member.totalPatientResp), delta: '+ 6.7%', dir: 'up' },
    { label: 'Open Balance', value: formatCurrency(member.totalPatientResp), delta: '- 12.3%', dir: 'down' },
    { label: 'Active Claims', value: claimCount.toLocaleString(), note: `${claimCount - member.deniedCount} active, ${member.deniedCount} denied` },
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

function buildProviderKpis(claim) {
  const providerClaims = claimsData.filter((row) => row.billingProvider === claim.billingProvider)
  const totalAllowed = sum(providerClaims, 'allowed')
  const totalPaid = sum(providerClaims, 'paid')
  const totalPatientResp = sum(providerClaims, 'patientResp')
  const denied = providerClaims.filter((row) => row.status === 'Denied').length
  const approvalRate = providerClaims.length ? ((providerClaims.length - denied) / providerClaims.length) * 100 : 0
  const denialRate = providerClaims.length ? (denied / providerClaims.length) * 100 : 0
  const reimbursementRate = totalAllowed ? (totalPaid / totalAllowed) * 100 : 0

  return [
    { label: 'Total Allowed', value: formatCompactCurrency(totalAllowed), delta: '+ 7.6%' },
    { label: 'Total Paid', value: formatCompactCurrency(totalPaid), delta: '+ 8.3%' },
    { label: 'Patient Responsibility', value: formatCompactCurrency(totalPatientResp), delta: '+ 6.1%' },
    { label: 'Claims Submitted', value: providerClaims.length.toLocaleString() },
    { label: 'Approval Rate', value: formatPercent(approvalRate), delta: '+ 3.2 pts' },
    { label: 'Denial Rate', value: formatPercent(denialRate), delta: '- 1.8 pts', dir: 'down' },
    { label: 'Average Reimbursement %', value: formatPercent(reimbursementRate), delta: '+ 1.4 pts' },
    { label: 'Average Days to Pay', value: '24.3', delta: '- 2.6 days', dir: 'down' },
    { label: 'Open AR', value: formatCompactCurrency(totalPatientResp) },
  ]
}

function App() {
  const [activeView, setActiveView] = useState('home')
  const [activeNav, setActiveNav] = useState('home')
  const [selectedMemberId, setSelectedMemberId] = useState(null)
  const [selectedClaim, setSelectedClaim] = useState(null)
  const [selectedPredictionClaim, setSelectedPredictionClaim] = useState(null)
  const [searchQuery, setSearchQuery] = useState('')

  const openClaimDetail = (claim) => {
    setSelectedPredictionClaim(null)
    setSelectedMemberId(null)
    setSelectedClaim(claim)
    setActiveView('claims')
    setActiveNav('Claims')
  }

  const openAllClaims = () => {
    setSelectedPredictionClaim(null)
    setSelectedMemberId(null)
    setSelectedClaim(null)
    setActiveView('claims')
    setActiveNav('Claims')
  }

  const openMemberDetail = (memberId) => {
    setSelectedPredictionClaim(null)
    setSelectedMemberId(memberId)
    setSelectedClaim(null)
    setActiveView('member')
    setActiveNav('Patient 360')
  }

  const backToEncounters = () => {
    setSelectedPredictionClaim(null)
    setSelectedMemberId(null)
    setSelectedClaim(null)
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
        setActiveView('member')
        setActiveNav('Patient 360')
      }
    }
  }

  const openPredictionDetail = (claim) => {
    setSelectedPredictionClaim(claim)
    setSelectedMemberId(null)
    setSelectedClaim(null)
    setActiveView('predictions')
    setActiveNav('Predictions')
  }

  const backToPredictions = () => {
    setSelectedPredictionClaim(null)
  }

  const navigate = (view, navKey = view) => {
    setActiveView(view)
    setActiveNav(navKey)
    setSelectedPredictionClaim(null)
    if (view === 'member') {
      backToEncounters()
    } else if (view === 'claims') {
      setSelectedClaim(null)
      setSelectedMemberId(null)
    }
  }

  return (
    <div className="app-shell">
      <Sidebar activeNav={activeNav} onNavigate={navigate} />
      <main className="workspace">
        {activeView === 'home' ? (
          <ExecutiveDashboard
            searchQuery={searchQuery}
            onSearchChange={setSearchQuery}
            onSearchSubmit={(value) => {
              const query = value.trim()
              if (query) {
                setSearchQuery(query)
                setSelectedMemberId(null)
                setSelectedClaim(null)
                setActiveView('member')
                setActiveNav('Patient 360')
              }
            }}
            onOpenClaim={openClaimDetail}
            onViewAllClaims={openAllClaims}
          />
        ) : activeView === 'predictions' ? (
          <PredictionsWorkspace
            selectedClaim={selectedPredictionClaim}
            searchQuery={searchQuery}
            onSearchChange={updateSearchQuery}
            onOpenPrediction={openPredictionDetail}
            onBackToPredictions={backToPredictions}
          />
        ) : activeView === 'claims' ? (
          <ClaimsWorkspace
            selectedClaim={selectedClaim}
            searchQuery={searchQuery}
            onSearchChange={updateSearchQuery}
            onOpenClaim={openClaimDetail}
            onViewAllClaims={openAllClaims}
          />
        ) : (
          <PatientWorkspace
            selectedClaim={selectedClaim}
            selectedMemberId={selectedMemberId}
            searchQuery={searchQuery}
            onSearchChange={updateSearchQuery}
            onSelectMember={openMemberDetail}
            onOpenClaim={openClaimDetail}
            onBackToEncounters={backToEncounters}
          />
        )}
      </main>
    </div>
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

            return (
              <button
                className={`nav-link ${active ? 'active' : ''}`}
                type="button"
                onClick={() => onNavigate(item.view, item.label)}
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

function TopBar({ searchQuery, onSearchChange }) {
  const inputRef = useRef(null)

  useEffect(() => {
    const handleKeyDown = (event) => {
      if (event.key === '/' && document.activeElement !== inputRef.current) {
        event.preventDefault()
        inputRef.current?.focus()
      }
    }

    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [])

  return (
    <header className="topbar">
      <label className="search-field">
        <Search size={18} />
        <input
          ref={inputRef}
          type="search"
          value={searchQuery}
          onChange={(event) => onSearchChange(event.target.value)}
          placeholder="Search patients by name or member ID"
          aria-label="Search patients by name or member ID"
        />
        <kbd>/</kbd>
      </label>
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

function PatientWorkspace({ selectedClaim, selectedMemberId, searchQuery, onSearchChange, onSelectMember, onOpenClaim, onBackToEncounters }) {
  const selectedMember = selectedMemberId ? membersById.get(selectedMemberId) : null

  return (
    <>
      <TopBar searchQuery={searchQuery} onSearchChange={onSearchChange} />
      <section className="patient-page">
        {selectedMember ? (
          <MemberDetail
            member={selectedMember}
            selectedClaim={selectedClaim}
            onBackToEncounters={onBackToEncounters}
            onSelectMember={onSelectMember}
            onOpenClaim={onOpenClaim}
          />
        ) : (
          <EncounterSearch searchQuery={searchQuery} onSelectMember={onSelectMember} onOpenClaim={onOpenClaim} />
        )}
      </section>
    </>
  )
}

function ClaimsWorkspace({ selectedClaim, searchQuery, onSearchChange, onOpenClaim, onViewAllClaims }) {
  const [timeFilter, setTimeFilter] = useState('All Time')
  const [currentPage, setCurrentPage] = useState(1)
  const pageSize = 10
  const normalizedQuery = searchQuery.trim().toLowerCase()
  const searchedClaims = normalizedQuery
    ? claimsData.filter((claim) => (
      claim.patient.toLowerCase().includes(normalizedQuery) ||
      claim.memberId.toLowerCase().includes(normalizedQuery)
    ))
    : claimsData
  const filteredClaims = filterClaimsByTime(searchedClaims, timeFilter)
  const pageCount = Math.max(1, Math.ceil(filteredClaims.length / pageSize))
  const safePage = Math.min(currentPage, pageCount)
  const pagedClaims = filteredClaims.slice((safePage - 1) * pageSize, safePage * pageSize)

  useEffect(() => {
    setCurrentPage(1)
  }, [searchQuery, timeFilter])

  return (
    <>
      <TopBar searchQuery={searchQuery} onSearchChange={onSearchChange} />
      <section className="claims-page">
        {selectedClaim ? (
          <ClaimDetailPage claim={selectedClaim} onBackToClaims={onViewAllClaims} />
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

function PredictionsWorkspace({ selectedClaim, searchQuery, onSearchChange, onOpenPrediction, onBackToPredictions }) {
  const [riskFilter, setRiskFilter] = useState('At Risk')
  const [payerFilter, setPayerFilter] = useState('All Payers')
  const [sortBy, setSortBy] = useState('Highest Risk')
  const normalizedQuery = searchQuery.trim().toLowerCase()
  const predictionRows = claimsData.map((claim) => ({ claim, prediction: predictClaim(claim, claimsData) }))

  const filteredRows = predictionRows
    .filter(({ claim, prediction }) => {
      const matchesSearch = !normalizedQuery || [
        claim.number,
        claim.patient,
        claim.memberId,
        claim.payer,
        claim.billingProvider,
        claim.cptCode,
        claim.cptDescription,
        claim.diagnosisCode,
        claim.diagnosisDescription,
      ].some((value) => value?.toString().toLowerCase().includes(normalizedQuery))
      const matchesPayer = payerFilter === 'All Payers' || claim.payer === payerFilter
      const matchesRisk = riskFilter === 'All' ||
        (riskFilter === 'At Risk' && prediction.risks.overall.level !== 'Low') ||
        prediction.risks.overall.level === riskFilter

      return matchesSearch && matchesPayer && matchesRisk
    })
    .sort((a, b) => {
      if (sortBy === 'Predicted Paid') {
        return b.prediction.money.predictedPaid - a.prediction.money.predictedPaid
      }
      if (sortBy === 'Newest DOS') {
        return b.claim.dos.localeCompare(a.claim.dos)
      }
      return b.prediction.risks.overall.score - a.prediction.risks.overall.score
    })

  const summary = buildPredictionSummary(filteredRows.map(({ claim }) => claim), claimsData)
  const displayedRows = filteredRows.slice(0, 50)

  return (
    <>
      <TopBar searchQuery={searchQuery} onSearchChange={onSearchChange} />
      <section className="predictions-page">
        {selectedClaim ? (
          <PredictionDetailPage claim={selectedClaim} onBackToPredictions={onBackToPredictions} />
        ) : (
          <>
            <div className="predictions-header">
              <div>
                <h1>Predictions</h1>
                <p>Payment forecasts and at-risk claim worklists generated from the current 837 claim data.</p>
              </div>
              <div className="prediction-controls">
                <label className="prediction-select">
                  <span>Risk</span>
                  <select value={riskFilter} onChange={(event) => setRiskFilter(event.target.value)}>
                    <option>At Risk</option>
                    <option>High</option>
                    <option>Medium</option>
                    <option>Low</option>
                    <option>All</option>
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
                    <option>Highest Risk</option>
                    <option>Predicted Paid</option>
                    <option>Newest DOS</option>
                  </select>
                  <ChevronDown size={16} />
                </label>
              </div>
            </div>

            <PredictionSummary summary={summary} />
            <AtRiskClaimsQueue
              title="Prediction Queue"
              subtitle={`${filteredRows.length.toLocaleString()} matching claim${filteredRows.length === 1 ? '' : 's'} from the current database`}
              items={displayedRows}
              onOpenClaim={onOpenPrediction}
              emptyMessage="No claims match the current prediction filters."
            />
          </>
        )}
      </section>
    </>
  )
}

function PredictionDetailPage({ claim, onBackToPredictions }) {
  const prediction = predictClaim(claim, claimsData)
  const topDrivers = prediction.riskDrivers.slice(0, 6)

  return (
    <>
      <div className="patient-header-row prediction-detail-nav">
        <button className="back-link" type="button" onClick={onBackToPredictions}>
          <ArrowLeft size={16} />
          Back to Predictions
        </button>
        <div className="data-stamp">
          Forecast generated from {prediction.peerCount.toLocaleString()} peer claim{prediction.peerCount === 1 ? '' : 's'}
          <RefreshCw size={15} />
        </div>
      </div>

      <Card className="prediction-detail-hero">
        <div>
          <span className={`claim-status ${statusClass(claim.status)}`}>{statusLabel(claim.status)}</span>
          <h1>Prediction Detail - {claim.number}</h1>
          <p>{claim.patient} · {claim.memberId} · {claim.payer} · {getService(claim)}</p>
        </div>
        <RiskBadge level={prediction.risks.overall.level} score={prediction.risks.overall.score} />
      </Card>

      <div className="prediction-detail-grid">
        <PaymentForecastCard prediction={prediction} />
        <Card className="prediction-driver-card">
          <div className="prediction-card-header">
            <div>
              <h2>Driver Breakdown</h2>
              <p>Dominant risk signals ranked for this claim.</p>
            </div>
            <span>{prediction.confidence} confidence</span>
          </div>
          <div className="prediction-driver-grid">
            {topDrivers.map((driver) => (
              <div className="prediction-driver-item" key={driver.label}>
                <div>
                  <strong>{driver.label}</strong>
                  <RiskBadge level={riskLevelForScore(driver.score)} score={driver.score} />
                </div>
                <p>{driver.reason}</p>
              </div>
            ))}
          </div>
        </Card>
      </div>

      <Card className="prediction-action-card">
        <div className="prediction-card-header">
          <div>
            <h2>Recommended Actions</h2>
            <p>Situation-specific fixes based on the highest current risk drivers.</p>
          </div>
          <span>{prediction.fixes.length} action{prediction.fixes.length === 1 ? '' : 's'}</span>
        </div>
        <ol>
          {prediction.fixes.map((fix) => (
            <li key={fix}>{fix}</li>
          ))}
        </ol>
      </Card>

      <SelectedClaimDetail claim={claim} />
    </>
  )
}

function riskLevelForScore(score) {
  if (score >= 50) return 'High'
  if (score >= 35) return 'Medium'
  return 'Low'
}

function filterClaimsByTime(claims, timeFilter) {
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

function ClaimDetailPage({ claim, onBackToClaims }) {
  const prediction = predictClaim(claim, claimsData)

  return (
    <>
      <div className="patient-header-row">
        <button className="back-link" type="button" onClick={onBackToClaims}>
          <ArrowLeft size={16} />
          Back to Claims
        </button>
        <div className="data-stamp">
          Claim submitted {formatDate(claim.submissionDate)}
          <RefreshCw size={15} />
        </div>
      </div>

      <div className="claim-detail-layout">
        <SelectedClaimDetail claim={claim} />
        <PaymentForecastCard claim={claim} prediction={prediction} />
        <ClaimReasonCard claim={claim} />
      </div>
    </>
  )
}

function EncounterSearch({ searchQuery, onSelectMember, onOpenClaim }) {
  const normalizedQuery = searchQuery.trim().toLowerCase()
  const filteredEncounters = normalizedQuery
    ? claimsData
      .filter((claim) => (
        claim.patient.toLowerCase().includes(normalizedQuery) ||
        claim.memberId.toLowerCase().includes(normalizedQuery)
      ))
      .slice(0, 50)
    : recentEncounters

  return (
    <>
      <div className="patient-header-row search-results-header">
        <div>
          <h1>Patient 360</h1>
          <p>Recent encounters from the current 837 claims database</p>
        </div>
        <div className="data-stamp">
          Data as of {formatDate(defaultDateRange.to)}
          <RefreshCw size={15} />
        </div>
      </div>

      <RecentEncounters claims={filteredEncounters} onSelectMember={onSelectMember} onOpenClaim={onOpenClaim} />
    </>
  )
}

function MemberDetail({ member, selectedClaim, onBackToEncounters, onSelectMember, onOpenClaim }) {
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

      <div className="patient-grid">
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
            <PaymentOverview member={member} />
            <TrendCard member={member} />
          </div>

          <RecentEncounters claims={member.claims.slice(0, 8)} onSelectMember={onSelectMember} onOpenClaim={onOpenClaim} />
          <ClaimTimeline claim={latestClaim} />
        </div>

        <aside className="patient-aside">
          <ProviderInformation claim={latestClaim} />
          <ProviderKpis claim={latestClaim} />
        </aside>
      </div>
    </>
  )
}

function ExecutiveDashboard({ searchQuery, onSearchChange, onSearchSubmit, onOpenClaim, onViewAllClaims }) {
  const [openMenu, setOpenMenu] = useState(null)
  const [dateRange, setDateRange] = useState(defaultDateRange)
  const [payer, setPayer] = useState('All Payers')
  const [plan, setPlan] = useState('All Plans')
  const [providerGroup, setProviderGroup] = useState('All Groups')
  const [filters, setFilters] = useState({ deniedOnly: false, highValue: false })

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
        <form
          className="dashboard-search-panel home-topbar-search"
          onSubmit={(event) => {
            event.preventDefault()
            const formData = new FormData(event.currentTarget)
            onSearchSubmit?.(formData.get('patientSearch')?.toString() ?? '')
          }}
        >
          <Search size={22} />
          <input
            name="patientSearch"
            type="search"
            value={searchQuery}
            onChange={(event) => onSearchChange(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                event.preventDefault()
                onSearchSubmit?.(event.currentTarget.value)
              }
            }}
            placeholder="Search patients by name or member ID"
            aria-label="Search patients by name or member ID"
          />
          <kbd>/</kbd>
        </form>
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
          <span className={`claim-status ${statusClass(claim.status)}`} title={claim.status}>{statusLabel(claim.status)}</span>
          <h1>{claim.number}</h1>
          <p>{claim.patient} · {claim.memberId} · {claim.payer}</p>
        </div>
        <div className="claim-hero-meta">
          <span>DOS</span>
          <strong>{formatDate(claim.dos)}</strong>
          <span>Submitted</span>
          <strong>{formatDate(claim.submissionDate)}</strong>
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

function PaymentOverview({ member }) {
  const allowed = member.totalAllowed
  const paid = member.totalPaid
  const patientResp = member.totalPatientResp
  const adjustments = member.totalAdjustment
  const paidPercent = allowed ? (paid / allowed) * 100 : 0
  const patientPercent = allowed ? (patientResp / allowed) * 100 : 0

  return (
    <Card className="payment-overview">
      <SectionTitle title="Payments Overview (YTD)" />
      <div className="payment-body">
        <div className="donut" style={{ '--paid-share': `${paidPercent}%` }}>
          <div>
            <strong>{formatCompactCurrency(allowed)}</strong>
            <span>Total Allowed</span>
          </div>
        </div>
        <ul className="legend-list">
          <li>
            <span className="dot green"></span>
            <div>
              <strong>Paid by Payer</strong>
              <span>{formatCurrency(paid)} ({paidPercent.toFixed(1)}%)</span>
            </div>
          </li>
          <li>
            <span className="dot blue"></span>
            <div>
              <strong>Patient Responsibility</strong>
              <span>{formatCurrency(patientResp)} ({patientPercent.toFixed(1)}%)</span>
            </div>
          </li>
          <li>
            <span className="dot gray"></span>
            <div>
              <strong>Adjustments</strong>
              <span>{formatCurrency(adjustments)}</span>
            </div>
          </li>
        </ul>
      </div>
    </Card>
  )
}

function TrendCard({ member }) {
  const latestClaims = member.claims.slice(0, 6).reverse()
  const maxValue = Math.max(...latestClaims.map((claim) => Math.max(claim.paid, claim.patientResp)), 1)
  const points = latestClaims.map((claim, index) => {
    const x = 90 + index * 100
    const paidY = 170 - (claim.paid / maxValue) * 122
    const respY = 170 - (claim.patientResp / maxValue) * 122
    return { claim, x, paidY, respY }
  })
  const paidPath = points.map((point, index) => `${index === 0 ? 'M' : 'L'}${point.x} ${point.paidY}`).join(' ')
  const respPath = points.map((point, index) => `${index === 0 ? 'M' : 'L'}${point.x} ${point.respY}`).join(' ')

  return (
    <Card className="trend-card">
      <SectionTitle title="Paid vs Patient Responsibility (Trend)" />
      <div className="chart-legend">
        <span><span className="line-key green"></span>Paid by Payer</span>
        <span><span className="line-key blue"></span>Patient Responsibility</span>
      </div>
      <svg className="trend-svg" viewBox="0 0 640 220" role="img" aria-label="Paid and patient responsibility trend">
        {[42, 82, 122, 162].map((y) => (
          <line key={y} x1="58" x2="608" y1={y} y2={y} className="grid-line" />
        ))}
        <line x1="58" x2="608" y1="182" y2="182" className="axis-line" />
        <path d={paidPath} className="trend-line green-line" />
        <path d={respPath} className="trend-line blue-line" />
        {points.map((point) => (
          <g key={`${point.claim.number}-paid`}>
            <circle cx={point.x} cy={point.paidY} r="4" className="green-point" />
            <text x={point.x} y={point.paidY - 10} textAnchor="middle">{formatCompactCurrency(point.claim.paid)}</text>
          </g>
        ))}
        {points.map((point) => (
          <g key={`${point.claim.number}-resp`}>
            <circle cx={point.x} cy={point.respY} r="4" className="blue-point" />
          </g>
        ))}
        {points.map((point) => (
          <text key={point.claim.number} x={point.x} y="206" textAnchor="middle" className="x-label">{formatDate(point.claim.dos).replace(', 2026', '')}</text>
        ))}
        {['$6K', '$4K', '$2K', '$0'].map((label, index) => (
          <text key={label} x="34" y={46 + index * 40} textAnchor="middle" className="y-label">{label}</text>
        ))}
      </svg>
    </Card>
  )
}

function RecentEncounters({ claims, onSelectMember, onOpenClaim }) {
  return (
    <Card className="encounters-card">
      <SectionTitle title="Recent Encounters" action="View All Encounters" />
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
                <td className="empty-table-cell" colSpan="10">No patients match that name or member ID.</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
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
      <SectionTitle title="Provider Information" action="View Provider 360" />
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
  const providerKpis = buildProviderKpis(claim)

  return (
    <Card className="provider-kpis">
      <SectionTitle title="Provider KPIs (YTD)" action="View Full Performance" />
      <div className="provider-kpi-grid">
        {providerKpis.map((kpi) => (
          <div className="provider-kpi" key={kpi.label}>
            <span>{kpi.label}</span>
            <strong>{kpi.value}</strong>
            {kpi.delta ? (
              <small className={kpi.dir === 'down' ? 'down' : 'up'}>{kpi.delta}</small>
            ) : null}
            <em>vs prior 12 months</em>
          </div>
        ))}
      </div>
    </Card>
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
  const latestMonthStart = `${defaultDateRange.to.slice(0, 8)}01`
  const latestYearStart = `${defaultDateRange.to.slice(0, 4)}-01-01`
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
    { label: 'Predicted Paid', value: formatCurrency(summary.totalPredictedPaid), note: 'historical peer forecast', tone: 'green' },
    { label: 'Predicted Adjustment', value: formatCurrency(summary.totalPredictedAdjustment), note: 'expected write-off', tone: 'orange' },
    { label: 'At-Risk Claims', value: summary.atRiskCount.toLocaleString(), note: `${summary.highRiskCount} high, ${summary.denialQueueCount} denial queue`, tone: 'violet' },
    { label: 'Average Risk', value: `${summary.averageOverallRisk}%`, note: 'overall claim risk score', tone: 'blue' },
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

function AtRiskClaimsQueue({
  items,
  onOpenClaim,
  title = 'At-Risk Claims Queue',
  subtitle,
  emptyMessage = 'No at-risk claims match the current filters.',
}) {
  return (
    <Card className="risk-queue-card">
      <div className="risk-queue-heading">
        <div>
          <h2>{title}</h2>
          {subtitle ? <p>{subtitle}</p> : null}
        </div>
      </div>
      <div className="risk-queue-table-wrap">
        <table className="data-table risk-queue-table">
          <thead>
            <tr>
              <th>Claim</th>
              <th>Patient</th>
              <th>Overall Risk</th>
              <th>Forecast</th>
              <th>Prediction Reasons</th>
              <th>Recommended Fix</th>
            </tr>
          </thead>
          <tbody>
            {items.length ? items.map(({ claim, prediction }) => (
              <tr key={claim.number}>
                <td>
                  <button className="claim-link-button" type="button" onClick={() => onOpenClaim?.(claim)}>
                    {claim.number}
                  </button>
                  <span>{claim.payer}</span>
                </td>
                <td>{claim.patient}</td>
                <td>
                  <RiskBadge level={prediction.risks.overall.level} score={prediction.risks.overall.score} />
                </td>
                <td className="risk-forecast-cell">
                  <strong>{formatCurrency(prediction.money.predictedPaid)}</strong>
                  <span>{prediction.outcome.likely} · {prediction.confidence}</span>
                </td>
                <td>
                  <ul className="queue-reason-list">
                    {prediction.reasons.slice(0, 2).map((reason) => (
                      <li key={reason}>{reason}</li>
                    ))}
                  </ul>
                </td>
                <td>
                  <ul className="queue-fix-list">
                    {prediction.fixes.slice(0, 2).map((fix) => (
                      <li key={fix}>{fix}</li>
                    ))}
                  </ul>
                </td>
              </tr>
            )) : (
              <tr>
                <td className="empty-table-cell" colSpan={6}>{emptyMessage}</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
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
  claims = recentClaims,
  title = 'Recent Claims',
  featured = false,
  compact = false,
  onOpenClaim,
  onViewAllClaims,
  emptyMessage = 'No claims match the current filters.',
  footer,
}) {
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
            {claims.length ? claims.map((claim) => (
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
