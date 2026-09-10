import { useEffect, useMemo, useRef, useState } from 'react'
import {
  ArrowLeftRight,
  CheckCircle2,
  RefreshCw,
  Scale,
  Shield,
  ShieldAlert,
  Sparkles,
  TrendingDown,
  Users,
  X,
} from 'lucide-react'

export function SamePatientBilledSavings({ memberId, diagnosisCode }) {
  const family = String(diagnosisCode || '').slice(0, 3).toUpperCase()
  const [result, setResult] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!memberId || !['N30', 'N39'].includes(family)) return undefined
    let active = true
    setLoading(true)
    setError('')
    fetch(`/api/claims/same-patient-billed-savings?member_id=${encodeURIComponent(memberId)}&diagnosis_family=${encodeURIComponent(family)}`)
      .then((response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}: Same-patient comparison failed`)
        return response.json()
      })
      .then((payload) => { if (active) setResult(payload) })
      .catch((requestError) => { if (active) setError(requestError.message || 'Could not calculate the billed scenario.') })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [family, memberId])

  if (!memberId || !['N30', 'N39'].includes(family)) return null
  if (loading) return <div className="same-patient-billed-state"><RefreshCw className="spin" size={18} /> Calculating the same-patient billed scenario…</div>
  if (error) return <div className="same-patient-billed-state error"><ShieldAlert size={18} /> {error}</div>
  if (!result?.available) return <div className="same-patient-billed-state">{result?.reason}</div>

  const calculation = result.calculation
  const fmt = (value) => new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(value || 0)

  return (
    <section className="same-patient-billed-card" aria-labelledby="same-patient-billed-heading">
      <header>
        <span>Same-patient UTI culture scenario</span>
        <h2 id="same-patient-billed-heading">Would adding the recorded culture bundle to the earlier episode have cost less?</h2>
        <p>This uses billed amounts from this patient only. The culture and specimen prices come from the later episode where those services were recorded.</p>
      </header>
      <div className="same-patient-billed-grid">
        <div><span>Earlier episode, actual</span><strong>{fmt(calculation.earlier_episode_actual_billed)}</strong></div>
        <div><span>Later episode, actual</span><strong>{fmt(calculation.later_episode_actual_billed)}</strong></div>
        <div><span>Culture and specimen add-on</span><strong>{fmt(calculation.culture_and_specimen_add_on_billed)}</strong></div>
        <div><span>Actual two-episode billed amount</span><strong>{fmt(calculation.actual_two_episode_billed)}</strong></div>
        <div><span>Proposed earlier episode with add-on</span><strong>{fmt(calculation.proposed_earlier_episode_with_add_on_billed)}</strong></div>
        <div className="difference"><span>Potential billed difference</span><strong>{fmt(calculation.potential_billed_difference)}</strong></div>
      </div>
      <p className="same-patient-billed-equation">
        {fmt(calculation.actual_two_episode_billed)} − {fmt(calculation.proposed_earlier_episode_with_add_on_billed)} = {fmt(calculation.potential_billed_difference)}
      </p>
      <details>
        <summary>Culture/specimen billed lines used</summary>
        <ul>
          {result.culture_add_on_lines.map((line) => (
            <li key={line.claim_id}>{line.procedure_description || line.cpt} ({line.cpt}) — {fmt(line.billed_amount)}</li>
          ))}
        </ul>
      </details>
      <p className="same-patient-billed-warning">{result.disclaimer}</p>
    </section>
  )
}

export function CrossPatientComparatorContent({
  onOpenClaim,
  onSelectMember,
  modalMode = false,
  onClose,
  initialMemberId = '',
  initialDiagnosisCode = '',
}) {
  const [pairsData, setPairsData] = useState(null)
  const [loadingPairs, setLoadingPairs] = useState(true)
  const [pairsError, setPairsError] = useState('')

  const [selectedOrgan, setSelectedOrgan] = useState('')
  const [selectedFamily, setSelectedFamily] = useState('')
  const [member1, setMember1] = useState('')
  const [member2, setMember2] = useState('')

  const [comparisonResult, setComparisonResult] = useState(null)
  const [loadingComparison, setLoadingComparison] = useState(false)
  const [comparisonError, setComparisonError] = useState('')
  const [activeTab, setActiveTab] = useState('divergence') // 'divergence' | 'claims'
  const comparisonRequest = useRef(0)
  useEffect(() => () => { comparisonRequest.current += 1 }, [])

  // Pre-configured showcases for quick, powerful demonstrations
  const SHOWCASE_PRESETS = [
    {
      label: 'Kidneys · UTI Culture (N39)',
      emoji: '💧',
      organ: 'Kidneys & Urinary Tract',
      family: 'N39',
      m1: 'MBR00006',
      m2: 'MBR00017',
      desc: 'Charles Moore vs Richard Jackson · High avoidable spend',
    },
    {
      label: 'Bones · RA Biologic (M06)',
      emoji: '🦴',
      organ: 'Bones & Joints',
      family: 'M06',
      m1: 'MBR00013',
      m2: 'MBR00018',
      desc: 'John Williams vs Michael Anderson · Treatment pathway divergence',
    },
    {
      label: 'Brain · Epilepsy (G40)',
      emoji: '🧠',
      organ: 'Brain & Nerves',
      family: 'G40',
      m1: 'MBR00011',
      m2: 'MBR00016',
      desc: 'John Moore vs Karen Miller · Diagnostic & maintenance variation',
    },
    {
      label: 'Neoplasms · Colon Care (C18)',
      emoji: '🎗️',
      organ: 'Cancer / Neoplasms',
      family: 'C18',
      m1: 'MBR00016',
      m2: 'MBR00017',
      desc: 'Karen Miller vs Richard Jackson · Surveillance & procedure spread',
    },
  ]

  // Load comparable pairs grouped by organ system
  useEffect(() => {
    let active = true
    setLoadingPairs(true)
    fetch('/api/claims/comparable-pairs')
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}: Failed to fetch pairs`)
        return res.json()
      })
      .then((data) => {
        if (!active) return
        setPairsData(data)
        setPairsError('')

        // Prefer the patient and diagnosis that opened this comparison. This keeps
        // the embedded view scoped to the member's selected condition while still
        // using the same eligible cross-patient comparison cohort.
        const groups = data.organ_groups || []
        if (groups.length > 0) {
          const diagnosisPrefix = String(initialDiagnosisCode || '').slice(0, 3).toUpperCase()
          const scopedGroup = groups.find((group) => group.disease_families.some((disease) => (
            disease.diagnosis_family === diagnosisPrefix
            && disease.members.some((member) => member.member_id === initialMemberId)
          )))
          if (initialMemberId && diagnosisPrefix && !scopedGroup) {
            setPairsError(`No eligible second patient was found for ${initialDiagnosisCode} for this member.`)
            setLoadingPairs(false)
            return
          }
          const defaultGroup = scopedGroup || groups.find((g) => g.organ_system.includes('Kidneys')) || groups[0]
          setSelectedOrgan(defaultGroup.organ_system)
          const firstDisease = defaultGroup.disease_families.find((disease) => (
            (!diagnosisPrefix || disease.diagnosis_family === diagnosisPrefix)
            && (!initialMemberId || disease.members.some((member) => member.member_id === initialMemberId))
          )) || defaultGroup.disease_families[0]
          if (firstDisease) {
            setSelectedFamily(firstDisease.diagnosis_family)
            if (firstDisease.members.length >= 2) {
              // Sort members by billed amount descending
              const sorted = [...firstDisease.members].sort((a, b) => b.total_billed - a.total_billed)
              const scopedMember = sorted.find((member) => member.member_id === initialMemberId)
              const firstMember = scopedMember || sorted[0]
              const secondMember = [...sorted].reverse().find((member) => member.member_id !== firstMember.member_id)
              setMember1(firstMember.member_id)
              setMember2(secondMember.member_id)
              // Execute initial comparison
              runComparisonQuery(firstMember.member_id, secondMember.member_id, firstDisease.diagnosis_family)
            }
          }
        }
      })
      .catch((err) => {
        if (!active) return
        setPairsError(err.message || 'Could not load comparable patient pairs from backend.')
      })
      .finally(() => {
        if (active) setLoadingPairs(false)
      })
    return () => {
      active = false
    }
  }, [initialDiagnosisCode, initialMemberId])

  const currentOrganGroup = useMemo(() => {
    if (!pairsData?.organ_groups) return null
    return pairsData.organ_groups.find((g) => g.organ_system === selectedOrgan) || pairsData.organ_groups[0]
  }, [pairsData, selectedOrgan])

  const availableDiseases = useMemo(() => {
    return currentOrganGroup?.disease_families || []
  }, [currentOrganGroup])

  const currentDisease = useMemo(() => {
    return availableDiseases.find((d) => d.diagnosis_family === selectedFamily) || availableDiseases[0]
  }, [availableDiseases, selectedFamily])

  const availableMembers = useMemo(() => {
    return currentDisease?.members || []
  }, [currentDisease])

  const handleOrganChange = (organName) => {
    setSelectedOrgan(organName)
    const grp = pairsData?.organ_groups?.find((g) => g.organ_system === organName)
    if (grp?.disease_families?.length > 0) {
      const d = grp.disease_families[0]
      setSelectedFamily(d.diagnosis_family)
      if (d.members.length >= 2) {
        const sorted = [...d.members].sort((a, b) => b.total_billed - a.total_billed)
        setMember1(sorted[0].member_id)
        setMember2(sorted[sorted.length - 1].member_id)
        runComparisonQuery(sorted[0].member_id, sorted[sorted.length - 1].member_id, d.diagnosis_family)
      }
    }
  }

  const handleDiseaseChange = (familyCode) => {
    setSelectedFamily(familyCode)
    const d = availableDiseases.find((item) => item.diagnosis_family === familyCode)
    if (d && d.members.length >= 2) {
      const sorted = [...d.members].sort((a, b) => b.total_billed - a.total_billed)
      setMember1(sorted[0].member_id)
      setMember2(sorted[sorted.length - 1].member_id)
      runComparisonQuery(sorted[0].member_id, sorted[sorted.length - 1].member_id, familyCode)
    }
  }

  const handleApplyPreset = (preset) => {
    setSelectedOrgan(preset.organ)
    setSelectedFamily(preset.family)
    setMember1(preset.m1)
    setMember2(preset.m2)
    runComparisonQuery(preset.m1, preset.m2, preset.family)
  }

  const handleSwapMembers = () => {
    const temp = member1
    setMember1(member2)
    setMember2(temp)
    if (member2 && temp && selectedFamily) {
      runComparisonQuery(member2, temp, selectedFamily)
    }
  }

  const runComparisonQuery = (m1, m2, fam) => {
    if (!m1 || !m2 || !fam || m1 === m2) return
    const requestId = ++comparisonRequest.current
    setComparisonResult(null)
    setLoadingComparison(true)
    setComparisonError('')
    fetch(`/api/claims/compare-patients?member_id_1=${encodeURIComponent(m1)}&member_id_2=${encodeURIComponent(m2)}&diagnosis_family=${encodeURIComponent(fam)}`)
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}: Comparison request failed`)
        return res.json()
      })
      .then((data) => {
        if (requestId !== comparisonRequest.current) return
        setComparisonResult(data)
      })
      .catch((err) => {
        if (requestId !== comparisonRequest.current) return
        setComparisonError(err.message || 'Failed to compare patients.')
        setComparisonResult(null)
      })
      .finally(() => {
        if (requestId !== comparisonRequest.current) return
        setLoadingComparison(false)
      })
  }

  const selectMember = (side, id) => {
    comparisonRequest.current += 1
    setComparisonResult(null)
    setComparisonError('')
    setLoadingComparison(false)
    const first = side === 'a' ? id : member1
    const second = side === 'b' ? id : member2
    setMember1(first)
    setMember2(second)
    if (first && second && first !== second) runComparisonQuery(first, second, selectedFamily)
  }

  const handleManualCompare = (e) => {
    e?.preventDefault?.()
    if (member1 === member2) {
      setComparisonError('Please select two different patients to compare.')
      return
    }
    runComparisonQuery(member1, member2, selectedFamily)
  }

  const fmt = (val) =>
    new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 2 }).format(val || 0)

  return (
    <div className={`cross-patient-comparator-root ${modalMode ? 'in-modal' : ''}`}>
      {/* Header */}
      <div className="comparator-header">
        <div className="comparator-title-area">
          <div className="comparator-badge">
            <Scale size={14} />
            <span>Cross-Patient Clinical & Spend Comparator</span>
          </div>
          <h2>Compare Patients With Same Organ & Disease</h2>
          <p>
            Compare recorded billed amounts and services for two patients with the same diagnosis family. Differences require review before they can be attributed to savings.
          </p>
        </div>
        {modalMode && onClose && (
          <button className="comparator-close-btn" type="button" onClick={onClose} aria-label="Close modal">
            <X size={20} />
          </button>
        )}
      </div>

      {/* Showcase Presets */}
      <div className="comparator-presets-bar">
        <span className="presets-label">
          <Sparkles size={14} /> Quick Demonstrations:
        </span>
        <div className="presets-chips">
          {SHOWCASE_PRESETS.map((preset) => (
            <button
              key={preset.family}
              type="button"
              className={`preset-chip ${selectedFamily === preset.family && member1 === preset.m1 ? 'active' : ''}`}
              onClick={() => handleApplyPreset(preset)}
              title={preset.desc}
            >
              <span className="preset-emoji">{preset.emoji}</span>
              <span className="preset-name">{preset.label}</span>
            </button>
          ))}
        </div>
      </div>

      {/* Main Filter / Selection Card */}
      <div className="comparator-filter-card">
        {loadingPairs ? (
          <div className="comparator-loading-inline">
            <RefreshCw className="spin" size={18} />
            <span>Scanning patient episodes across all organ systems...</span>
          </div>
        ) : pairsError ? (
          <div className="comparator-error-banner">
            <ShieldAlert size={18} />
            <span>{pairsError}</span>
          </div>
        ) : (
          <>
            {/* Step 1: Organ System Pills */}
            <div className="filter-step">
              <label className="step-label">
                <strong>1. Select Organ System:</strong>
                <span className="step-hint">Categorised by anatomical chapter</span>
              </label>
              <div className="organ-pills-row">
                {pairsData?.organ_groups?.map((group) => {
                  const isSelected = selectedOrgan === group.organ_system
                  return (
                    <button
                      key={group.organ_system}
                      type="button"
                      className={`organ-pill ${isSelected ? 'active' : ''}`}
                      onClick={() => handleOrganChange(group.organ_system)}
                    >
                      <span className="organ-emoji">{group.organ_emoji}</span>
                      <span className="organ-name">{group.organ_system}</span>
                      <span className="organ-count">({group.total_diseases})</span>
                    </button>
                  )
                })}
              </div>
            </div>

            {/* Step 2 & 3: Disease & Patient Pair */}
            <form className="comparator-selection-grid" onSubmit={handleManualCompare}>
              <div className="selection-item">
                <label>
                  <span>2. Disease Family</span>
                  <select
                    value={selectedFamily}
                    onChange={(e) => handleDiseaseChange(e.target.value)}
                    disabled={!availableDiseases.length}
                  >
                    {availableDiseases.map((d) => (
                      <option key={d.diagnosis_family} value={d.diagnosis_family}>
                        {d.diagnosis_family} – {d.diagnosis_description || 'Unspecified'} ({d.member_count} patients)
                      </option>
                    ))}
                  </select>
                </label>
              </div>

              <div className="selection-item">
                <label>
                  <span>3. Patient A</span>
                  <select
                    value={member1}
                    onChange={(e) => selectMember('a', e.target.value)}
                    disabled={!availableMembers.length}
                  >
                    <option value="">Select Patient A</option>
                    {availableMembers.map((m) => (
                      <option key={`m1-${m.member_id}`} value={m.member_id}>
                        {m.patient_name} ({m.member_id}) · billed {fmt(m.total_billed)} · {m.claim_count} claims
                      </option>
                    ))}
                  </select>
                </label>
              </div>

              <div className="swap-btn-container">
                <button
                  type="button"
                  className="comparator-swap-button"
                  onClick={handleSwapMembers}
                  title="Swap Patient A and Patient B"
                >
                  <ArrowLeftRight size={18} />
                </button>
              </div>

              <div className="selection-item">
                <label>
                  <span>4. Patient B</span>
                  <select
                    value={member2}
                    onChange={(e) => selectMember('b', e.target.value)}
                    disabled={!availableMembers.length}
                  >
                    <option value="">Select Patient B</option>
                    {availableMembers.map((m) => (
                      <option key={`m2-${m.member_id}`} value={m.member_id}>
                        {m.patient_name} ({m.member_id}) · billed {fmt(m.total_billed)} · {m.claim_count} claims
                      </option>
                    ))}
                  </select>
                </label>
              </div>

              <div className="selection-actions">
                <button
                  type="submit"
                  className="comparator-compare-btn"
                  disabled={loadingComparison || !member1 || !member2 || member1 === member2}
                >
                  {loadingComparison ? (
                    <>
                      <RefreshCw className="spin" size={16} />
                      <span>Comparing...</span>
                    </>
                  ) : (
                    <>
                      <Sparkles size={16} />
                      <span>Compare Claims</span>
                    </>
                  )}
                </button>
              </div>
            </form>
            <details className="corresponding-members">
              <summary>All {availableMembers.length} corresponding members · {selectedFamily}</summary>
              <p>Both patient dropdowns contain every member listed here. Billed totals below cover all recorded episodes for this diagnosis family; the comparison uses each patient's highest-billed episode.</p>
              <div className="comparison-evidence-table-wrap">
                <table className="comparison-evidence-table">
                  <thead><tr><th>Member</th><th>Claims</th><th>Episodes</th><th>Total billed</th><th>Select patient</th></tr></thead>
                  <tbody>{availableMembers.map((member) => (
                    <tr key={member.member_id}>
                      <td>{member.patient_name} ({member.member_id})</td>
                      <td>{member.claim_count}</td><td>{member.episode_count}</td><td>{fmt(member.total_billed)}</td>
                      <td>
                        <button type="button" onClick={() => selectMember('a', member.member_id)}>Select A</button>{' '}
                        <button type="button" onClick={() => selectMember('b', member.member_id)}>Select B</button>
                      </td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
            </details>
            {member1 && member1 === member2 ? <p role="status">Select a different member for Patient B to compare two patients.</p> : null}
          </>
        )}
      </div>

      {/* Error message */}
      {comparisonError && (
        <div className="comparator-error-banner">
          <ShieldAlert size={18} />
          <span>{comparisonError}</span>
        </div>
      )}

      {/* Results Section */}
      {loadingComparison && !comparisonResult && (
        <div className="comparator-loading-state">
          <RefreshCw className="spin" size={28} />
          <p>Evaluating claim histories, matching service lines, and computing savings difference...</p>
        </div>
      )}

      {comparisonResult && (
        <div className="comparator-results-section">
          {/* Executive Savings Banner */}
          <div className="comparator-savings-banner">
            <div className="savings-left">
              <span className="savings-kicker">Cross-Patient Billed-Amount Variance</span>
              <div className="savings-amount-row">
                <span className="savings-big-number">
                  {fmt(comparisonResult.savings_summary.total_savings)}
                </span>
                <span className="savings-pct-pill">
                  <TrendingDown size={18} />
                  {comparisonResult.savings_summary.savings_percentage}% Lower Billed
                </span>
              </div>
              <p className="savings-subtext">
                Recorded billed totals for {comparisonResult.higher_cost_patient.patient_name} and {comparisonResult.lower_cost_patient.patient_name} in diagnosis family {comparisonResult.diagnosis_family} are {fmt(comparisonResult.savings_summary.higher_total)} and {fmt(comparisonResult.savings_summary.lower_total)} respectively. Each patient's highest-billed episode is shown; this does not establish avoidable spending.
              </p>
            </div>
            <div className="savings-right">
              <div className="savings-meta-box">
                <span className="meta-label">Organ System</span>
                <strong>{comparisonResult.organ_emoji} {comparisonResult.organ_system}</strong>
              </div>
              <div className="savings-meta-box">
                <span className="meta-label">Disease Family</span>
                <strong>{comparisonResult.diagnosis_family} · {comparisonResult.diagnosis_description}</strong>
              </div>
              <div className="savings-meta-box">
                <span className="meta-label">Claim Volume Difference</span>
                <strong>
                  {Math.abs(comparisonResult.savings_summary.claim_count_difference)} claim{Math.abs(comparisonResult.savings_summary.claim_count_difference) === 1 ? '' : 's'} difference ({comparisonResult.higher_cost_patient.episode.claim_count} vs {comparisonResult.lower_cost_patient.episode.claim_count})
                </strong>
              </div>
            </div>
          </div>

          {/* Side-by-Side Patient Cards */}
          <div className="patient-comparison-grid">
            {/* Higher Cost Patient */}
            <div className="comparator-patient-card higher-cost-card">
              <div className="patient-card-top">
                <div className="patient-card-badge danger">
                  <span>Higher Spend Patient Episode</span>
                </div>
                <h3>{comparisonResult.higher_cost_patient.patient_name}</h3>
                <span className="member-id-tag">
                  Member ID: {comparisonResult.higher_cost_patient.member_id}
                </span>
              </div>
              <div className="patient-card-metrics">
                <div className="metric-cell highlight">
                  <span>Total Billed</span>
                  <strong>{fmt(comparisonResult.higher_cost_patient.episode.total_billed)}</strong>
                </div>
                <div className="metric-cell">
                  <span>Total Allowed</span>
                  <strong>{fmt(comparisonResult.higher_cost_patient.episode.total_allowed)}</strong>
                </div>
                <div className="metric-cell">
                  <span>Episode Claims</span>
                  <strong>{comparisonResult.higher_cost_patient.episode.claim_count} claims</strong>
                </div>
                <div className="metric-cell">
                  <span>Timeline</span>
                  <strong>
                    {comparisonResult.higher_cost_patient.episode.start_date} – {comparisonResult.higher_cost_patient.episode.end_date}
                  </strong>
                </div>
              </div>
              <div className="patient-card-services">
                <h4>Top Services Billed in Episode:</h4>
                <div className="service-tags">
                  {comparisonResult.higher_cost_patient.episode.cpt_breakdown?.slice(0, 4).map((cpt) => (
                    <div key={cpt.cpt} className="cpt-chip">
                      <code>{cpt.cpt}</code>
                      <span className="cpt-desc">{cpt.description || 'Service'}</span>
                      <span className="cpt-amt">{fmt(cpt.total_billed)} ({cpt.count}x)</span>
                    </div>
                  ))}
                </div>
              </div>
              {onSelectMember && (
                <button
                  type="button"
                  className="view-patient-profile-btn"
                  onClick={() => onSelectMember(comparisonResult.higher_cost_patient.member_id)}
                >
                  <Users size={14} />
                  <span>View Patient 360 ({comparisonResult.higher_cost_patient.member_id})</span>
                </button>
              )}
            </div>

            {/* Lower Cost Patient */}
            <div className="comparator-patient-card lower-cost-card">
              <div className="patient-card-top">
                <div className="patient-card-badge success">
                  <CheckCircle2 size={13} />
                  <span>Value-Based Benchmark Episode</span>
                </div>
                <h3>{comparisonResult.lower_cost_patient.patient_name}</h3>
                <span className="member-id-tag">
                  Member ID: {comparisonResult.lower_cost_patient.member_id}
                </span>
              </div>
              <div className="patient-card-metrics">
                <div className="metric-cell highlight success">
                  <span>Total Billed</span>
                  <strong>{fmt(comparisonResult.lower_cost_patient.episode.total_billed)}</strong>
                </div>
                <div className="metric-cell">
                  <span>Total Allowed</span>
                  <strong>{fmt(comparisonResult.lower_cost_patient.episode.total_allowed)}</strong>
                </div>
                <div className="metric-cell">
                  <span>Episode Claims</span>
                  <strong>{comparisonResult.lower_cost_patient.episode.claim_count} claims</strong>
                </div>
                <div className="metric-cell">
                  <span>Timeline</span>
                  <strong>
                    {comparisonResult.lower_cost_patient.episode.start_date} – {comparisonResult.lower_cost_patient.episode.end_date}
                  </strong>
                </div>
              </div>
              <div className="patient-card-services">
                <h4>Top Services Billed in Episode:</h4>
                <div className="service-tags">
                  {comparisonResult.lower_cost_patient.episode.cpt_breakdown?.slice(0, 4).map((cpt) => (
                    <div key={cpt.cpt} className="cpt-chip success">
                      <code>{cpt.cpt}</code>
                      <span className="cpt-desc">{cpt.description || 'Service'}</span>
                      <span className="cpt-amt">{fmt(cpt.total_billed)} ({cpt.count}x)</span>
                    </div>
                  ))}
                </div>
              </div>
              {onSelectMember && (
                <button
                  type="button"
                  className="view-patient-profile-btn"
                  onClick={() => onSelectMember(comparisonResult.lower_cost_patient.member_id)}
                >
                  <Users size={14} />
                  <span>View Patient 360 ({comparisonResult.lower_cost_patient.member_id})</span>
                </button>
              )}
            </div>
          </div>

          {/* Navigation Tabs for Divergence vs Claims */}
          <div className="comparator-tabs-bar">
            <button
              type="button"
              className={`comparator-tab ${activeTab === 'divergence' ? 'active' : ''}`}
              onClick={() => setActiveTab('divergence')}
            >
              <span>Service-by-Service Variance Breakdown</span>
              <span className="tab-badge">{comparisonResult.service_divergence?.length || 0}</span>
            </button>
            <button
              type="button"
              className={`comparator-tab ${activeTab === 'claims' ? 'active' : ''}`}
              onClick={() => setActiveTab('claims')}
            >
              <span>Underlying Claims Audit Trail</span>
              <span className="tab-badge">
                {(comparisonResult.higher_cost_patient.episode.claims?.length || 0) +
                  (comparisonResult.lower_cost_patient.episode.claims?.length || 0)}
              </span>
            </button>
          </div>

          {/* Tab 1: Service Divergence Breakdown */}
          {activeTab === 'divergence' && (
            <div className="divergence-table-wrap">
              <div className="divergence-table-header">
                <h4>Cost Divergence by Procedure / CPT Code</h4>
                <p>
                  Identifies specific clinical services that caused the {fmt(comparisonResult.savings_summary.total_savings)} spend difference.
                </p>
              </div>
              <div className="table-responsive">
                <table className="comparator-data-table">
                  <thead>
                    <tr>
                      <th>CPT Code</th>
                      <th>Clinical Description</th>
                      <th>Variance Driver</th>
                      <th>{comparisonResult.higher_cost_patient.patient_name}</th>
                      <th>{comparisonResult.lower_cost_patient.patient_name}</th>
                      <th>Billed Amount Difference</th>
                    </tr>
                  </thead>
                  <tbody>
                    {comparisonResult.service_divergence?.map((row) => {
                      const isPositive = row.difference > 0
                      return (
                        <tr key={row.cpt}>
                          <td>
                            <span className="cpt-code-pill">{row.cpt}</span>
                          </td>
                          <td>
                            <strong>{row.description || 'Medical service'}</strong>
                          </td>
                          <td>
                            <span className={`divergence-badge ${row.category}`}>
                              {row.category === 'additional'
                                ? 'Additional Service'
                                : row.category === 'excess'
                                ? 'Excess Utilisation'
                                : 'Price Variation'}
                            </span>
                          </td>
                          <td>
                            <span className="patient-spend-val">
                              {fmt(row.higher_cost_patient.total_billed)}
                            </span>
                            <span className="count-sub">({row.higher_cost_patient.count}x)</span>
                          </td>
                          <td>
                            <span className="patient-spend-val">
                              {fmt(row.lower_cost_patient.total_billed)}
                            </span>
                            <span className="count-sub">({row.lower_cost_patient.count}x)</span>
                          </td>
                          <td>
                            <strong className={`diff-amount ${isPositive ? 'savings' : 'negative'}`}>
                              {isPositive ? `+${fmt(row.difference)}` : fmt(row.difference)}
                            </strong>
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* Tab 2: Underlying Claims Audit Trail */}
          {activeTab === 'claims' && (
            <div className="claims-audit-split">
              <div className="claims-split-col">
                <div className="split-col-header">
                  <h4>{comparisonResult.higher_cost_patient.patient_name} Claims ({comparisonResult.higher_cost_patient.episode.claims?.length})</h4>
                  <span className="badge-count danger">{fmt(comparisonResult.higher_cost_patient.episode.total_billed)}</span>
                </div>
                <div className="table-responsive">
                  <table className="comparator-data-table mini">
                    <thead>
                      <tr>
                        <th>Claim ID</th>
                        <th>DOS</th>
                        <th>Diagnosis</th>
                        <th>CPT</th>
                        <th>Paid</th>
                      </tr>
                    </thead>
                    <tbody>
                      {comparisonResult.higher_cost_patient.episode.claims?.map((c) => (
                        <tr key={c.claim_id}>
                          <td>
                            {onOpenClaim ? (
                              <button
                                type="button"
                                className="claim-link-button"
                                onClick={() => onOpenClaim({ number: c.claim_id, claimId: c.claim_id })}
                              >
                                {c.claim_id}
                              </button>
                            ) : (
                              <strong>{c.claim_id}</strong>
                            )}
                          </td>
                          <td>{c.dos}</td>
                          <td><code>{c.diagnosis}</code></td>
                          <td><code>{c.cpt}</code></td>
                          <td><strong>{fmt(c.paid)}</strong></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>

              <div className="claims-split-col">
                <div className="split-col-header">
                  <h4>{comparisonResult.lower_cost_patient.patient_name} Claims ({comparisonResult.lower_cost_patient.episode.claims?.length})</h4>
                  <span className="badge-count success">{fmt(comparisonResult.lower_cost_patient.episode.total_billed)}</span>
                </div>
                <div className="table-responsive">
                  <table className="comparator-data-table mini">
                    <thead>
                      <tr>
                        <th>Claim ID</th>
                        <th>DOS</th>
                        <th>Diagnosis</th>
                        <th>CPT</th>
                        <th>Paid</th>
                      </tr>
                    </thead>
                    <tbody>
                      {comparisonResult.lower_cost_patient.episode.claims?.map((c) => (
                        <tr key={c.claim_id}>
                          <td>
                            {onOpenClaim ? (
                              <button
                                type="button"
                                className="claim-link-button"
                                onClick={() => onOpenClaim({ number: c.claim_id, claimId: c.claim_id })}
                              >
                                {c.claim_id}
                              </button>
                            ) : (
                              <strong>{c.claim_id}</strong>
                            )}
                          </td>
                          <td>{c.dos}</td>
                          <td><code>{c.diagnosis}</code></td>
                          <td><code>{c.cpt}</code></td>
                          <td><strong>{fmt(c.paid)}</strong></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          )}

          {/* Clinical Governance & Disclaimer */}
          <div className="comparator-governance-card">
            <div className="governance-icon">
              <Shield size={20} />
            </div>
            <div className="governance-content">
              <strong>Clinical Governance & Value-Based Review Notice</strong>
              <p>{comparisonResult.disclaimer}</p>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
