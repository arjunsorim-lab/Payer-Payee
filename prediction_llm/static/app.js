const $ = (selector) => document.querySelector(selector);
const memberSelect = $('#memberSelect');
const claimSelect = $('#claimSelect');
const analyzeButton = $('#analyzeButton');
let currentData = null;

const money = (value) => new Intl.NumberFormat('en-US', {style:'currency', currency:'USD'}).format(Number(value || 0));
const pct = (value) => value == null ? 'n/a' : `${(Number(value) * 100).toFixed(1)}%`;
const escapeHtml = (value) => String(value ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));

async function fetchJson(url, options) {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `Request failed: ${response.status}`);
  return data;
}

async function boot() {
  try {
    const [health, members] = await Promise.all([fetchJson('/health'), fetchJson('/api/members')]);
    $('#statusDot').classList.toggle('ok', health.status === 'ok');
    $('#statusText').textContent = health.local_llm.available ? `MongoDB + ${health.local_llm.model} ready` : 'MongoDB ready · LLM fallback mode';
    for (const member of members.members) {
      const option = document.createElement('option');
      option.value = member.Member_ID;
      option.textContent = `${member.Member_ID} · ${member.claim_count} claims`;
      memberSelect.append(option);
    }
  } catch (error) {
    $('#statusText').textContent = error.message;
  }
}

memberSelect.addEventListener('change', async () => {
  claimSelect.innerHTML = '<option value="">Member history</option>';
  claimSelect.disabled = !memberSelect.value;
  analyzeButton.disabled = !memberSelect.value;
  if (!memberSelect.value) return;
  const data = await fetchJson(`/api/members/${encodeURIComponent(memberSelect.value)}/claims`);
  for (const claim of data.claims) {
    const option = document.createElement('option');
    option.value = claim.Claim_ID;
    option.textContent = `${claim.Claim_ID} · ${claim.Service_Date_From} · CPT ${claim.CPT_Code}`;
    claimSelect.append(option);
  }
});

analyzeButton.addEventListener('click', runAnalysis);

async function runAnalysis() {
  $('#emptyState').classList.add('hidden');
  $('#results').classList.add('hidden');
  $('#loadingState').classList.remove('hidden');
  const llm = $('#llmToggle').checked ? 'true' : 'false';
  const url = claimSelect.value
    ? `/api/claims/${encodeURIComponent(claimSelect.value)}/analysis?use_llm=${llm}`
    : `/api/members/${encodeURIComponent(memberSelect.value)}/summary?use_llm=${llm}`;
  try {
    currentData = await fetchJson(url);
    render(currentData);
  } catch (error) {
    alert(error.message);
  } finally {
    $('#loadingState').classList.add('hidden');
  }
}

function listInto(selector, items) {
  $(selector).innerHTML = (items?.length ? items : ['None identified in the available data.'])
    .map(item => `<li>${escapeHtml(typeof item === 'string' ? item : JSON.stringify(item))}</li>`).join('');
}

function render(data) {
  $('#results').classList.remove('hidden');
  $('#resultTitle').textContent = data.analysis_type === 'claim_analysis' ? `Claim ${data.claim_id}` : `Member ${data.member_id}`;
  $('#modeBadge').textContent = data.narrative.mode === 'local_llm_grounded' ? `Local LLM · ${data.narrative.model}` : 'Deterministic explanation';
  $('#overview').textContent = data.narrative.overview;
  listInto('#narrativeFacts', data.narrative.facts);
  listInto('#narrativePatterns', data.narrative.patterns);
  listInto('#narrativeLimits', data.narrative.insufficient_evidence);
  listInto('#limits', data.insufficient_evidence);

  const metrics = data.analysis_type === 'claim_analysis'
    ? Object.entries(data.financial_analysis.amounts).map(([label,value]) => ({label:label.replaceAll('_',' '),value:money(value)}))
    : [
        {label:'Claims',value:data.facts.claim_count},
        {label:'Total charged',value:money(data.facts.financial_totals.Charge_Amount)},
        {label:'Paid / allowed',value:pct(data.facts.financial_ratios.paid_to_allowed)},
        {label:'Patient / allowed',value:pct(data.facts.financial_ratios.patient_to_allowed)},
      ];
  $('#metricGrid').innerHTML = metrics.map(item => `<article class="metric"><small>${escapeHtml(item.label)}</small><strong>${escapeHtml(item.value)}</strong></article>`).join('');

  $('#patterns').innerHTML = (data.patterns.length ? data.patterns : [{type:'No configured pattern',statement:'No repeated, short-timeframe, similarity, or financial outlier pattern met the configured thresholds.',evidence_claim_ids:[]}]).map(pattern => `
    <div class="pattern"><strong>${escapeHtml(pattern.type.replaceAll('_',' '))}</strong><p>${escapeHtml(pattern.statement)}</p>
    <div>${(pattern.evidence_claim_ids || []).map(id => `<span class="claim-chip">${escapeHtml(id)}</span>`).join('')}</div></div>`).join('');

  const unique = [...new Map(data.evidence_records.map(row => [row.Claim_ID, row])).values()];
  $('#evidenceCount').textContent = `${unique.length} records`;
  $('#evidenceRows').innerHTML = unique.map(row => `<tr>
    <td>${escapeHtml(row.Claim_ID)}</td><td>${escapeHtml(row.Service_Date_From)}</td>
    <td title="${escapeHtml(row.ICD10_Diagnosis_Description)}">${escapeHtml(row.ICD10_Diagnosis_Code)}</td>
    <td title="${escapeHtml(row.CPT_Description)}">${escapeHtml(row.CPT_Code)}</td>
    <td>${escapeHtml(row.Billing_Provider_Name)}</td><td>${money(row.Charge_Amount)}</td>
    <td>${money(row.Allowed_Amount)}</td><td>${money(row.Paid_Amount)}</td><td>${money(row.Patient_Responsibility)}</td>
  </tr>`).join('');
  window.scrollTo({top: $('#results').offsetTop - 90, behavior:'smooth'});
}

$('#questionForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  const question = $('#questionInput').value.trim();
  if (!question || !currentData) return;
  const button = event.currentTarget.querySelector('button');
  button.disabled = true; button.textContent = 'Checking evidence…';
  try {
    const data = await fetchJson('/api/ask', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({member_id:currentData.member_id,claim_id:currentData.claim_id,question})});
    currentData = data; render(data);
  } catch (error) { alert(error.message); }
  finally { button.disabled = false; button.textContent = 'Ask local LLM'; }
});

boot();
