import { useState, useEffect, useRef } from 'react'
import AiReasoningPipeline from '../components/AiReasoningPipeline'

const API          = import.meta.env.VITE_API_URL || ''
const STORAGE_KEY  = 'creditiq_analysis_id'
const STATUS_KEY   = 'creditiq_status'
const REPORT_KEY   = 'creditiq_report'
const ANALYZER_KEY = 'creditiq_analyzer_data'
const PHASE_KEY    = 'creditiq_phase'
const HEADERS      = { 'x-tenant-id': 'demo' }

// ── Processing step animations ─────────────────────────────────────────────

const EXTRACTION_STEPS = [
  { label: 'Connecting & downloading from S3',          start: 0  },
  { label: 'Parsing document (Textract / pandas)',       start: 4  },
  { label: 'LLM classification & NIIF normalization',   start: 55 },
] as const

const ANALYZER_STEPS = [
  { label: 'Loading historical reports & enriching accounts',       start: 0  },
  { label: 'Computing ratios, NIIF 18 subtotals & anomaly flags',   start: 4  },
  { label: 'LLM qualitative analysis & risk classification',        start: 10 },
] as const

function stepState(idx: number, starts: readonly number[], elapsed: number): 'pending' | 'active' | 'done' {
  const nextStart = starts[idx + 1] ?? Infinity
  if (elapsed < starts[idx]) return 'pending'
  if (elapsed >= nextStart) return 'done'
  return 'active'
}

// ── Color maps ────────────────────────────────────────────────────────────

const CATEGORY_COLOR: Record<string, string> = {
  assets:      '#b7c4ff',
  liabilities: '#F85149',
  equity:      '#3FB950',
  revenue:     '#D29922',
  expense:     '#ff7b72',
  other:       '#8d90a2',
}

const HEALTH_COLOR: Record<string, string> = {
  STABLE:   '#3FB950',
  GROWING:  '#b7c4ff',
  DECLINING:'#D29922',
  CRITICAL: '#F85149',
}

const RISK_COLOR: Record<string, string> = {
  LOW:    '#3FB950',
  MEDIUM: '#D29922',
  HIGH:   '#F85149',
}

// ── Types ──────────────────────────────────────────────────────────────────

type ProcessingPhase = 'agent1' | 'agent2' | 'final' | null

interface JobStatus {
  analysis_id?: string
  status: 'pending' | 'processing' | 'extraction_complete' | 'analysis_complete' | 'completed' | 'failed'
  error?: string | null
}

interface Account {
  account_id: string
  normalized_account_name: string
  category: string
  current_value: number
  previous_value: number | null
  confidence_score: number
  source_file: string
}

interface ExtractorOutput {
  job_id: string
  company_name: string
  currency: string
  periods: string[]
  extraction_confidence: number
  extraction_warnings: string[]
  accounts: Account[]
}

interface AnalyzerOutput {
  job_id: string
  company_name: string
  currency: string
  periods: string[]
  overall_financial_health: 'STABLE' | 'DECLINING' | 'GROWING' | 'CRITICAL'
  executive_narrative: string
  high_materiality_accounts: string[]
  niif_notes_required: string[]
  financial_ratios: {
    totals: {
      total_assets: number
      total_liabilities: number
      total_equity: number
      total_revenue: number
      net_income: number
      ebitda: number
    }
    ratios: {
      razon_corriente: number
      endeudamiento_global: number
      margen_neto_pct: number
      margen_ebitda_pct: number
      roe_pct: number
      deuda_patrimonio: number
    }
    niif18?: {
      compliance?: { compliance_score: number; flags: string[] }
      subtotals?: { resultado_operativo: number; resultado_neto: number; ebitda_niif18: number }
    }
  }
  analysis_results: {
    account_id: string
    account_name: string
    variation_pct: number
    materiality: string
    risk_level: string
    anomaly_detected: boolean
  }[]
}

// ── Component ──────────────────────────────────────────────────────────────

export default function AnalysisPage() {
  const [jobId, setJobId]   = useState<string | null>(() => localStorage.getItem(STORAGE_KEY))
  const [jobStatus, setJobStatus] = useState<JobStatus | null>(() => {
    try { return JSON.parse(localStorage.getItem(STATUS_KEY) ?? 'null') } catch { return null }
  })
  const [report, setReport] = useState<ExtractorOutput | null>(() => {
    try { return JSON.parse(localStorage.getItem(REPORT_KEY) ?? 'null') } catch { return null }
  })
  const [analyzerData, setAnalyzerData] = useState<AnalyzerOutput | null>(() => {
    try { return JSON.parse(localStorage.getItem(ANALYZER_KEY) ?? 'null') } catch { return null }
  })
  const [phase, setPhase] = useState<ProcessingPhase>(
    () => (localStorage.getItem(PHASE_KEY) as ProcessingPhase) ?? null
  )
  const [elapsed, setElapsed] = useState(0)
  const [catFilter, setCatFilter] = useState('all')
  const [showCancelDialog, setShowCancelDialog] = useState(false)
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // ── localStorage sync ──────────────────────────────────────────────────

  useEffect(() => {
    if (jobStatus) localStorage.setItem(STATUS_KEY, JSON.stringify(jobStatus))
    else localStorage.removeItem(STATUS_KEY)
  }, [jobStatus])

  useEffect(() => {
    if (report) localStorage.setItem(REPORT_KEY, JSON.stringify(report))
    else localStorage.removeItem(REPORT_KEY)
  }, [report])

  useEffect(() => {
    if (analyzerData) localStorage.setItem(ANALYZER_KEY, JSON.stringify(analyzerData))
    else localStorage.removeItem(ANALYZER_KEY)
  }, [analyzerData])

  useEffect(() => {
    if (phase) localStorage.setItem(PHASE_KEY, phase)
    else localStorage.removeItem(PHASE_KEY)
  }, [phase])

  // ── Main polling effect ────────────────────────────────────────────────

  const TERMINAL = new Set(['completed', 'failed', 'extraction_complete', 'analysis_complete'])

  useEffect(() => {
    if (intervalRef.current) clearInterval(intervalRef.current)
    if (!jobId) {
      setJobStatus(null); setReport(null); setAnalyzerData(null); setElapsed(0); setCatFilter('all')
      return
    }

    try {
      const cached = JSON.parse(localStorage.getItem(STATUS_KEY) ?? 'null') as JobStatus | null
      if (cached?.analysis_id !== jobId) {
        setJobStatus(null); setReport(null); setAnalyzerData(null); setElapsed(0); setCatFilter('all')
      }
    } catch {
      setJobStatus(null); setReport(null); setAnalyzerData(null)
    }

    let alive = true

    const poll = async () => {
      try {
        const res = await fetch(`${API}/analyses/${jobId}`, { headers: HEADERS })
        if (!res.ok || !alive) { console.error('[poll]', res.status, await res.text()); return }
        const data: JobStatus = await res.json()
        if (!alive) return
        setJobStatus(data)

        if (TERMINAL.has(data.status)) {
          if (intervalRef.current) { clearInterval(intervalRef.current); intervalRef.current = null }
        }
        if (data.status === 'extraction_complete') {
          const rep = await fetch(`${API}/analyses/${jobId}/report`, { headers: HEADERS })
          if (rep.ok && alive) setReport(await rep.json())
        }
        if (data.status === 'analysis_complete') {
          const rep = await fetch(`${API}/analyses/${jobId}/report`, { headers: HEADERS })
          if (rep.ok && alive) setAnalyzerData(await rep.json())
        }
      } catch (e) { console.error('[poll]', e) }
    }

    poll()
    intervalRef.current = setInterval(poll, 8000)
    return () => { alive = false; if (intervalRef.current) clearInterval(intervalRef.current) }
  }, [jobId])

  // ── Elapsed timer ──────────────────────────────────────────────────────

  useEffect(() => {
    if (!jobId || TERMINAL.has(jobStatus?.status ?? '')) return
    const t = setInterval(() => setElapsed(e => e + 1), 1000)
    return () => clearInterval(t)
  }, [jobId, jobStatus?.status])

  // ── Actions ────────────────────────────────────────────────────────────

  function handleClearClick() {
    const active = jobStatus?.status === 'processing' || jobStatus?.status === 'pending'
    if (active && jobId) {
      setShowCancelDialog(true)
    } else {
      _doClean()
    }
  }

  function _doClean() {
    if (intervalRef.current) { clearInterval(intervalRef.current); intervalRef.current = null }
    ;[STORAGE_KEY, STATUS_KEY, REPORT_KEY, ANALYZER_KEY, PHASE_KEY].forEach(k => localStorage.removeItem(k))
    setJobId(null); setJobStatus(null); setReport(null); setAnalyzerData(null); setPhase(null)
    setElapsed(0); setShowCancelDialog(false)
  }

  async function confirmClear() {
    if (jobId) {
      try {
        await fetch(`${API}/analyses/${jobId}`, { method: 'DELETE', headers: HEADERS })
      } catch { /* best-effort */ }
    }
    _doClean()
  }

  function _startPolling(onStatus: (d: JobStatus) => void) {
    let alive = true
    const poll = async () => {
      try {
        const res = await fetch(`${API}/analyses/${jobId}`, { headers: HEADERS })
        if (!res.ok || !alive) return
        const data: JobStatus = await res.json()
        if (!alive) return
        setJobStatus(data)
        onStatus(data)
        if (TERMINAL.has(data.status)) {
          if (intervalRef.current) { clearInterval(intervalRef.current); intervalRef.current = null }
        }
      } catch (e) { console.error('[poll]', e) }
    }
    poll()
    intervalRef.current = setInterval(poll, 4000)
    return () => { alive = false }
  }

  async function handleRunAgent2() {
    if (!jobId) return
    setPhase('agent2')
    setReport(null)       // clear accounts table — Agent 2 is now the focus
    setElapsed(0)
    await fetch(`${API}/analyses/${jobId}/continue`, { method: 'POST', headers: HEADERS })
    _startPolling(async (data) => {
      if (data.status === 'analysis_complete') {
        const rep = await fetch(`${API}/analyses/${jobId}/report`, { headers: HEADERS })
        if (rep.ok) setAnalyzerData(await rep.json())
      }
    })
  }

  async function handleRunFinal() {
    if (!jobId) return
    setPhase('final')
    setAnalyzerData(null)  // clear analysis panel — Agents 3-4 are now the focus
    setElapsed(0)
    await fetch(`${API}/analyses/${jobId}/continue`, { method: 'POST', headers: HEADERS })
    _startPolling(() => {})
  }

  // ── Derived state ──────────────────────────────────────────────────────

  const accounts   = report?.accounts ?? []
  const categories = ['all', ...Array.from(new Set(accounts.map(a => a.category))).sort()]
  const filtered   = catFilter === 'all' ? accounts : accounts.filter(a => a.category === catFilter)
  const anomalyCount = analyzerData?.analysis_results.filter(r => r.anomaly_detected).length ?? 0

  const statusColor = {
    pending:              '#D29922',
    processing:           '#b7c4ff',
    extraction_complete:  '#D29922',
    analysis_complete:    '#b7c4ff',
    completed:            '#3FB950',
    failed:               '#F85149',
  }[jobStatus?.status ?? 'pending']

  const isProcessing = jobStatus?.status === 'processing' || jobStatus?.status === 'pending' || jobStatus == null

  const currentSteps = phase === 'agent2' ? ANALYZER_STEPS : EXTRACTION_STEPS
  const processingLabel =
    phase === 'agent2'   ? 'Agent 2 — Financial Analysis' :
    phase === 'final'    ? 'Agents 3–4 — Risk Scoring & Report' :
    jobStatus?.status === 'pending' ? 'Queued — starting pipeline…' :
                           'Agent 1 — Document Extraction'

  // ── Render ─────────────────────────────────────────────────────────────

  return (
    <div className="p-margin-mobile md:p-margin-desktop flex flex-col md:flex-row gap-gutter min-h-0">

      {/* Left: AI Reasoning Pipeline */}
      <AiReasoningPipeline status={jobStatus?.status ?? null} jobId={jobId ?? undefined} />

      {/* Right: Main canvas */}
      <div className="flex-1 flex flex-col gap-5 overflow-hidden min-w-0">

        {/* No active job */}
        {!jobId && (
          <div className="bg-surface border border-border rounded flex flex-col items-center justify-center gap-4 py-20 text-center">
            <span className="material-symbols-outlined text-outline text-[48px]">analytics</span>
            <div>
              <p className="text-body-md font-body-md text-on-surface mb-1">No active analysis</p>
              <p className="text-label-sm font-label-sm text-outline">Upload financial documents and start an analysis to see results here.</p>
            </div>
          </div>
        )}

        {jobId && (
          <>
            {/* Status bar */}
            <div className="bg-surface border border-border rounded px-5 py-4 flex flex-wrap items-center justify-between gap-4">
              <div className="flex items-center gap-3">
                <span
                  className="material-symbols-outlined text-[24px]"
                  style={{
                    color: statusColor,
                    animation: jobStatus?.status === 'processing' ? 'spin 1.2s linear infinite' : 'none',
                  }}
                >
                  {jobStatus?.status === 'completed'         ? 'check_circle' :
                   jobStatus?.status === 'failed'            ? 'error'        :
                   jobStatus?.status === 'extraction_complete'? 'pause_circle' :
                   jobStatus?.status === 'analysis_complete' ? 'pause_circle' :
                   jobStatus?.status === 'processing'        ? 'autorenew'    : 'schedule'}
                </span>
                <div>
                  <div className="text-body-md font-body-md font-semibold text-on-surface">
                    {report?.company_name ?? analyzerData?.company_name ?? 'Financial Analysis'}
                  </div>
                  <div className="text-label-sm font-label-sm text-outline font-mono">{jobId}</div>
                </div>
              </div>

              <div className="flex items-center gap-6">
                <Kpi label="STATUS" value={(jobStatus?.status ?? '—').toUpperCase()} color={statusColor} />
                {isProcessing && <Kpi label="ELAPSED" value={`${elapsed}s`} color="#8d90a2" />}
                {report && (
                  <>
                    <Kpi label="ACCOUNTS"   value={String(report.accounts.length)} color="#b7c4ff" />
                    <Kpi label="CONFIDENCE" value={`${(report.extraction_confidence * 100).toFixed(1)}%`} color="#3FB950" />
                  </>
                )}
                {analyzerData && !report && (
                  <Kpi
                    label="HEALTH"
                    value={analyzerData.overall_financial_health}
                    color={HEALTH_COLOR[analyzerData.overall_financial_health] ?? '#8d90a2'}
                  />
                )}
                <button
                  onClick={handleClearClick}
                  title="Clear current analysis and start a new one"
                  className="flex items-center gap-1 px-2.5 py-1 rounded border border-border text-outline hover:text-on-surface hover:border-on-surface-variant transition-colors text-[11px] font-mono"
                >
                  <span className="material-symbols-outlined text-[14px]">close</span>
                  Clear
                </button>
              </div>
            </div>

            {/* Processing spinner */}
            {isProcessing && (
              <div className="bg-surface border border-border rounded p-8 flex flex-col items-center gap-6">
                <div className="text-center">
                  <p className="text-label-sm font-label-sm text-outline uppercase tracking-widest mb-1">
                    {processingLabel}
                  </p>
                  <p className="text-[11px] font-mono text-outline">{elapsed}s elapsed</p>
                </div>

                {phase === 'final' ? (
                  <div className="flex items-center gap-4">
                    <div className="w-6 h-6 rounded-full flex items-center justify-center shrink-0 border border-primary bg-primary-container shadow-[0_0_8px_rgba(46,98,255,0.35)]">
                      <div className="w-2 h-2 rounded-full bg-on-primary-container animate-pulse" />
                    </div>
                    <p className="text-body-sm font-body-sm text-on-surface font-semibold animate-pulse">
                      Running risk scoring & report generation…
                    </p>
                  </div>
                ) : (
                  <div className="flex flex-col gap-5 w-full max-w-sm relative">
                    <div className="absolute left-[11px] top-3 bottom-3 w-px bg-border" />
                    {currentSteps.map((step, i) => {
                      const starts = currentSteps.map(s => s.start) as number[]
                      const s = stepState(i, starts, elapsed)
                      return (
                        <div key={i} className={`flex items-start gap-4 relative z-10 transition-opacity duration-500 ${s === 'pending' ? 'opacity-35' : ''}`}>
                          {s === 'done' && (
                            <div className="w-6 h-6 rounded-full flex items-center justify-center shrink-0 bg-surface border border-[#3FB950]">
                              <span className="material-symbols-outlined text-[13px]" style={{ color: '#3FB950' }}>check</span>
                            </div>
                          )}
                          {s === 'active' && (
                            <div className="w-6 h-6 rounded-full flex items-center justify-center shrink-0 border border-primary bg-primary-container shadow-[0_0_8px_rgba(46,98,255,0.35)]">
                              <div className="w-2 h-2 rounded-full bg-on-primary-container animate-pulse" />
                            </div>
                          )}
                          {s === 'pending' && (
                            <div className="w-6 h-6 rounded-full flex items-center justify-center shrink-0 bg-surface border border-border">
                              <div className="w-1.5 h-1.5 rounded-full bg-outline" />
                            </div>
                          )}
                          <div className="pt-0.5">
                            <p className={`text-body-sm font-body-sm ${s === 'active' ? 'text-on-surface font-semibold' : s === 'done' ? 'text-[#3FB950]' : 'text-outline'}`}>
                              {step.label}
                            </p>
                            {s === 'active' && <p className="text-[10px] font-mono text-outline mt-1 animate-pulse">Running…</p>}
                            {s === 'done'   && <p className="text-[10px] font-mono mt-1" style={{ color: '#3FB950' }}>✓ Complete</p>}
                          </div>
                        </div>
                      )
                    })}
                  </div>
                )}

                <p className="text-[10px] font-mono text-outline">
                  {phase === 'agent2'
                    ? 'LLM analysis takes 10–30s · polling every 4s'
                    : phase === 'final'
                    ? 'Report generation takes 15–60s · polling every 4s'
                    : 'PDF Textract jobs take 30–120s · polling every 8s'}
                </p>
              </div>
            )}

            {/* Error state */}
            {jobStatus?.status === 'failed' && (
              <div className="bg-surface border border-risk-high/30 rounded p-6"
                   style={{ background: 'rgba(248,81,73,0.05)' }}>
                <div className="text-label-sm font-label-sm text-risk-high uppercase mb-2 font-mono">Pipeline failed</div>
                <pre className="text-body-sm font-body-sm text-risk-high whitespace-pre-wrap font-mono">
                  {jobStatus.error ?? 'See logs for details.'}
                </pre>
              </div>
            )}

            {/* ── AGENT 1 COMPLETE: accounts table + continue to Agent 2 ── */}
            {report && jobStatus?.status === 'extraction_complete' && (
              <>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  <div className="bg-surface border border-border rounded p-4">
                    <div className="flex justify-between items-start mb-2">
                      <span className="text-label-sm font-label-sm text-on-surface-variant uppercase">Data Confidence</span>
                      <span className="material-symbols-outlined text-[16px] text-primary">verified_user</span>
                    </div>
                    <div className="text-headline-lg font-headline-lg text-on-surface">
                      {(report.extraction_confidence * 100).toFixed(1)}%
                    </div>
                  </div>
                  <div className="bg-surface border border-border rounded p-4">
                    <div className="flex justify-between items-start mb-2">
                      <span className="text-label-sm font-label-sm text-on-surface-variant uppercase">Reporting Periods</span>
                      <span className="material-symbols-outlined text-[16px] text-outline">calendar_month</span>
                    </div>
                    <div className="text-headline-lg font-headline-lg text-on-surface">
                      {report.periods.join(' · ') || '—'}
                    </div>
                  </div>
                </div>

                {/* Agent 1 continue banner */}
                <div className="bg-surface border rounded p-5 flex flex-col md:flex-row items-center justify-between gap-4"
                     style={{ borderColor: '#D29922', background: 'rgba(210,153,34,0.06)' }}>
                  <div className="flex items-start gap-3">
                    <span className="material-symbols-outlined text-[22px] mt-0.5" style={{ color: '#D29922' }}>checklist</span>
                    <div>
                      <p className="text-body-md font-body-md font-semibold text-on-surface mb-0.5">
                        Agent 1 complete — review extracted accounts
                      </p>
                      <p className="text-label-sm font-label-sm text-outline">
                        Verify the table below, then run the financial analysis engine.
                      </p>
                    </div>
                  </div>
                  <button
                    onClick={handleRunAgent2}
                    className="flex items-center gap-2 px-5 py-2.5 rounded font-mono text-[13px] font-semibold whitespace-nowrap transition-all hover:opacity-90 active:scale-95 shrink-0"
                    style={{ background: '#D29922', color: '#0d1117' }}
                  >
                    <span className="material-symbols-outlined text-[16px]">play_arrow</span>
                    Run Agent 2 — Financial Analysis
                  </button>
                </div>

                {/* Accounts table */}
                <AccountsTable
                  report={report}
                  filtered={filtered}
                  accounts={accounts}
                  categories={categories}
                  catFilter={catFilter}
                  setCatFilter={setCatFilter}
                />
              </>
            )}

            {/* ── AGENT 2 COMPLETE: analysis summary + continue to Agents 3-4 ── */}
            {analyzerData && jobStatus?.status === 'analysis_complete' && (
              <>
                {/* Key metrics grid */}
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                  <MetricCard
                    label="Financial Health"
                    value={analyzerData.overall_financial_health}
                    color={HEALTH_COLOR[analyzerData.overall_financial_health] ?? '#8d90a2'}
                    icon="monitor_heart"
                  />
                  <MetricCard
                    label="Net Income (MM)"
                    value={analyzerData.financial_ratios.totals.net_income.toLocaleString('en-US', { minimumFractionDigits: 1, maximumFractionDigits: 1 })}
                    color={analyzerData.financial_ratios.totals.net_income >= 0 ? '#3FB950' : '#F85149'}
                    icon="payments"
                  />
                  <MetricCard
                    label="Razón Corriente"
                    value={analyzerData.financial_ratios.ratios.razon_corriente.toFixed(2)}
                    color={analyzerData.financial_ratios.ratios.razon_corriente >= 1 ? '#3FB950' : '#F85149'}
                    icon="account_balance"
                  />
                  <MetricCard
                    label="NIIF 18 Score"
                    value={`${analyzerData.financial_ratios.niif18?.compliance?.compliance_score ?? '—'}/100`}
                    color="#b7c4ff"
                    icon="gavel"
                  />
                </div>

                {/* Summary stats row */}
                <div className="grid grid-cols-3 gap-3">
                  <div className="bg-surface border border-border rounded p-3 text-center">
                    <div className="text-[11px] font-mono text-outline uppercase mb-1">High Materiality</div>
                    <div className="text-[22px] font-mono font-bold text-on-surface">
                      {analyzerData.high_materiality_accounts.length}
                    </div>
                    <div className="text-[10px] font-mono text-outline">accounts</div>
                  </div>
                  <div className="bg-surface border border-border rounded p-3 text-center">
                    <div className="text-[11px] font-mono text-outline uppercase mb-1">Anomalies</div>
                    <div className="text-[22px] font-mono font-bold" style={{ color: anomalyCount > 0 ? '#D29922' : '#3FB950' }}>
                      {anomalyCount}
                    </div>
                    <div className="text-[10px] font-mono text-outline">detected</div>
                  </div>
                  <div className="bg-surface border border-border rounded p-3 text-center">
                    <div className="text-[11px] font-mono text-outline uppercase mb-1">NIIF Notes</div>
                    <div className="text-[22px] font-mono font-bold text-on-surface">
                      {analyzerData.niif_notes_required.length}
                    </div>
                    <div className="text-[10px] font-mono text-outline">required</div>
                  </div>
                </div>

                {/* Executive narrative */}
                {analyzerData.executive_narrative && (
                  <div className="bg-surface border border-border rounded p-5">
                    <div className="text-label-sm font-label-sm text-on-surface-variant uppercase tracking-widest mb-3 flex items-center gap-2">
                      <span className="material-symbols-outlined text-[14px]">description</span>
                      Executive Narrative
                    </div>
                    <p className="text-body-sm font-body-sm text-on-surface leading-relaxed">
                      {analyzerData.executive_narrative.slice(0, 500)}
                      {analyzerData.executive_narrative.length > 500 && (
                        <span className="text-outline"> …</span>
                      )}
                    </p>
                  </div>
                )}

                {/* Top accounts by variation */}
                {analyzerData.analysis_results.length > 0 && (
                  <div className="bg-surface border border-border rounded overflow-hidden">
                    <div className="p-4 border-b border-border bg-surface-container-low flex items-center justify-between">
                      <h3 className="text-body-md font-body-md font-semibold text-on-surface">
                        Top Accounts — Variation & Risk
                      </h3>
                      <span className="text-[10px] font-mono text-outline">sorted by |Δ%|</span>
                    </div>
                    <div className="overflow-x-auto">
                      <table className="w-full text-left whitespace-nowrap">
                        <thead className="bg-surface-container-lowest border-b border-border text-label-sm font-label-sm text-on-surface-variant uppercase">
                          <tr>
                            {['Account', 'Δ%', 'Materiality', 'Risk', 'Anomaly'].map(h => (
                              <th key={h} className="py-2.5 px-3 font-medium"
                                  style={{ textAlign: ['Δ%'].includes(h) ? 'right' : 'left' }}>
                                {h}
                              </th>
                            ))}
                          </tr>
                        </thead>
                        <tbody className="text-body-sm font-body-sm text-on-surface divide-y divide-border">
                          {[...analyzerData.analysis_results]
                            .sort((a, b) => Math.abs(b.variation_pct) - Math.abs(a.variation_pct))
                            .slice(0, 10)
                            .map((r, i) => (
                              <tr key={r.account_id} className={`hover:bg-surface-container-lowest/50 ${i % 2 ? 'bg-surface-container-lowest/20' : ''}`}>
                                <td className="py-2 px-3 text-on-surface min-w-[200px]">{r.account_name}</td>
                                <td className="py-2 px-3 text-right font-mono text-[11px]"
                                    style={{ color: r.variation_pct > 0 ? '#3FB950' : r.variation_pct < 0 ? '#F85149' : '#8d90a2' }}>
                                  {r.variation_pct !== 0 ? `${r.variation_pct > 0 ? '+' : ''}${r.variation_pct.toFixed(1)}%` : '—'}
                                </td>
                                <td className="py-2 px-3">
                                  <span className="text-[10px] font-mono px-2 py-0.5 rounded"
                                        style={{
                                          color: r.materiality === 'HIGH' ? '#F85149' : r.materiality === 'MEDIUM' ? '#D29922' : '#3FB950',
                                          background: r.materiality === 'HIGH' ? '#F8514920' : r.materiality === 'MEDIUM' ? '#D2992220' : '#3FB95020',
                                          border: `1px solid ${r.materiality === 'HIGH' ? '#F8514940' : r.materiality === 'MEDIUM' ? '#D2992240' : '#3FB95040'}`,
                                        }}>
                                    {r.materiality}
                                  </span>
                                </td>
                                <td className="py-2 px-3">
                                  <span className="text-[10px] font-mono" style={{ color: RISK_COLOR[r.risk_level] ?? '#8d90a2' }}>
                                    {r.risk_level}
                                  </span>
                                </td>
                                <td className="py-2 px-3">
                                  {r.anomaly_detected && (
                                    <span className="material-symbols-outlined text-[14px]" style={{ color: '#D29922' }}>warning</span>
                                  )}
                                </td>
                              </tr>
                            ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}

                {/* NIIF 18 compliance flags */}
                {(analyzerData.financial_ratios.niif18?.compliance?.flags?.length ?? 0) > 0 && (
                  <div className="bg-surface border border-border rounded p-4">
                    <div className="text-label-sm font-label-sm text-on-surface-variant uppercase tracking-widest mb-3 flex items-center gap-2">
                      <span className="material-symbols-outlined text-[14px]">policy</span>
                      NIIF 18 Compliance Flags
                    </div>
                    {analyzerData.financial_ratios.niif18!.compliance!.flags.map((flag, i) => (
                      <div key={i} className="text-label-sm font-label-sm text-risk-medium mb-1">· {flag}</div>
                    ))}
                  </div>
                )}

                {/* Agent 2 continue banner */}
                <div className="bg-surface border rounded p-5 flex flex-col md:flex-row items-center justify-between gap-4"
                     style={{ borderColor: '#b7c4ff', background: 'rgba(183,196,255,0.06)' }}>
                  <div className="flex items-start gap-3">
                    <span className="material-symbols-outlined text-[22px] mt-0.5" style={{ color: '#b7c4ff' }}>
                      analytics
                    </span>
                    <div>
                      <p className="text-body-md font-body-md font-semibold text-on-surface mb-0.5">
                        Agent 2 complete — review financial analysis
                      </p>
                      <p className="text-label-sm font-label-sm text-outline">
                        Analysis looks correct? Continue to risk scoring and report generation.
                      </p>
                    </div>
                  </div>
                  <button
                    onClick={handleRunFinal}
                    className="flex items-center gap-2 px-5 py-2.5 rounded font-mono text-[13px] font-semibold whitespace-nowrap transition-all hover:opacity-90 active:scale-95 shrink-0"
                    style={{ background: '#b7c4ff', color: '#0d1117' }}
                  >
                    <span className="material-symbols-outlined text-[16px]">play_arrow</span>
                    Run Agents 3–4
                  </button>
                </div>
              </>
            )}

            {/* ── COMPLETED ── */}
            {jobStatus?.status === 'completed' && (
              <div className="bg-surface border border-border rounded p-8 flex flex-col items-center gap-4 text-center"
                   style={{ borderColor: '#3FB950', background: 'rgba(63,185,80,0.04)' }}>
                <span className="material-symbols-outlined text-[40px]" style={{ color: '#3FB950' }}>check_circle</span>
                <div>
                  <p className="text-body-md font-body-md font-semibold text-on-surface mb-1">Pipeline complete</p>
                  <p className="text-label-sm font-label-sm text-outline">
                    All agents finished. Report saved to S3.
                  </p>
                </div>
              </div>
            )}

          </>
        )}
      </div>

      <style>{`
        @keyframes spin { from { transform: rotate(0deg) } to { transform: rotate(360deg) } }
      `}</style>

      {/* Cancel confirmation dialog */}
      {showCancelDialog && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center"
          style={{ background: 'rgba(0,0,0,0.65)' }}
          onClick={() => setShowCancelDialog(false)}
        >
          <div
            className="bg-surface border border-border rounded-lg p-6 max-w-sm w-full mx-4 shadow-2xl"
            onClick={e => e.stopPropagation()}
          >
            <div className="flex items-start gap-3 mb-5">
              <span className="material-symbols-outlined text-[22px] mt-0.5" style={{ color: '#D29922' }}>
                warning
              </span>
              <div>
                <p className="text-body-md font-body-md font-semibold text-on-surface mb-1">
                  Cancel this analysis?
                </p>
                <p className="text-label-sm font-label-sm text-outline">
                  The running pipeline will be stopped. Partial results already saved to S3 will remain,
                  but this job will be marked as cancelled and cannot be resumed.
                </p>
              </div>
            </div>
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setShowCancelDialog(false)}
                className="px-4 py-2 rounded border border-border text-outline hover:text-on-surface text-[12px] font-mono transition-colors"
              >
                Keep running
              </button>
              <button
                onClick={confirmClear}
                className="px-4 py-2 rounded text-[12px] font-mono font-semibold transition-all hover:opacity-90 active:scale-95"
                style={{ background: '#F85149', color: '#fff' }}
              >
                Stop &amp; clear
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

// ── Sub-components ────────────────────────────────────────────────────────

function Kpi({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div className="text-right">
      <div className="text-[9px] font-mono text-outline uppercase tracking-widest mb-0.5">{label}</div>
      <div className="text-[15px] font-mono font-bold" style={{ color }}>{value}</div>
    </div>
  )
}

function MetricCard({ label, value, color, icon }: { label: string; value: string; color: string; icon: string }) {
  return (
    <div className="bg-surface border border-border rounded p-4">
      <div className="flex justify-between items-start mb-2">
        <span className="text-label-sm font-label-sm text-on-surface-variant uppercase text-[10px]">{label}</span>
        <span className="material-symbols-outlined text-[15px] text-outline">{icon}</span>
      </div>
      <div className="text-[20px] font-mono font-bold" style={{ color }}>{value}</div>
    </div>
  )
}

function AccountsTable({
  report, filtered, accounts, categories, catFilter, setCatFilter,
}: {
  report: { company_name: string; currency: string; extraction_warnings: string[] }
  filtered: { account_id: string; normalized_account_name: string; category: string; current_value: number; previous_value: number | null; confidence_score: number; source_file: string }[]
  accounts: { category: string }[]
  categories: string[]
  catFilter: string
  setCatFilter: (c: string) => void
}) {
  return (
    <div className="bg-surface border border-border rounded overflow-hidden">
      <div className="p-4 border-b border-border flex flex-wrap items-center justify-between gap-3 bg-surface-container-low">
        <div>
          <h3 className="text-body-md font-body-md font-semibold text-on-surface">Extracted Accounts</h3>
          <p className="text-label-sm font-label-sm text-outline font-mono mt-0.5">
            {report.company_name} · {report.currency} MM
          </p>
        </div>
        <div className="flex gap-1.5 flex-wrap">
          {categories.map(cat => (
            <button
              key={cat}
              onClick={() => setCatFilter(cat)}
              className="px-2.5 py-0.5 rounded text-[10px] font-mono uppercase transition-all"
              style={{
                border: `1px solid ${catFilter === cat ? (CATEGORY_COLOR[cat] ?? '#b7c4ff') : '#30363D'}`,
                background: catFilter === cat ? `${CATEGORY_COLOR[cat] ?? '#b7c4ff'}18` : 'transparent',
                color: catFilter === cat ? (CATEGORY_COLOR[cat] ?? '#b7c4ff') : '#8d90a2',
              }}
            >
              {cat === 'all'
                ? `All (${accounts.length})`
                : `${cat} (${accounts.filter(a => a.category === cat).length})`}
            </button>
          ))}
        </div>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-left whitespace-nowrap">
          <thead className="bg-surface-container-lowest border-b border-border text-label-sm font-label-sm text-on-surface-variant uppercase">
            <tr>
              {['ID', 'Category', 'Account Name', 'Current (MM)', 'Prior (MM)', 'Δ%', 'Conf', 'Source'].map(h => (
                <th key={h} className="py-2.5 px-3 font-medium"
                    style={{ textAlign: ['Current (MM)', 'Prior (MM)', 'Δ%', 'Conf'].includes(h) ? 'right' : 'left' }}>
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="text-body-sm font-body-sm text-on-surface divide-y divide-border">
            {filtered.map((a, i) => {
              const delta = a.previous_value != null && a.previous_value !== 0
                ? ((a.current_value - a.previous_value) / Math.abs(a.previous_value)) * 100
                : null
              const catColor = CATEGORY_COLOR[a.category] ?? '#8d90a2'
              return (
                <tr key={a.account_id} className={`hover:bg-surface-container-lowest/50 ${i % 2 ? 'bg-surface-container-lowest/20' : ''}`}>
                  <td className="py-2 px-3 font-mono text-[11px] text-outline">{a.account_id}</td>
                  <td className="py-2 px-3">
                    <span className="text-[10px] font-mono px-2 py-0.5 rounded"
                          style={{ color: catColor, background: `${catColor}18`, border: `1px solid ${catColor}30` }}>
                      {a.category}
                    </span>
                  </td>
                  <td className="py-2 px-3 text-on-surface min-w-[200px]">{a.normalized_account_name}</td>
                  <td className="py-2 px-3 text-right font-mono">
                    {a.current_value.toLocaleString('en-US', { minimumFractionDigits: 1, maximumFractionDigits: 1 })}
                  </td>
                  <td className="py-2 px-3 text-right font-mono text-outline">
                    {a.previous_value != null
                      ? a.previous_value.toLocaleString('en-US', { minimumFractionDigits: 1, maximumFractionDigits: 1 })
                      : '—'}
                  </td>
                  <td className="py-2 px-3 text-right font-mono text-[11px]"
                      style={{ color: delta == null ? '#8d90a2' : delta > 0 ? '#3FB950' : '#F85149' }}>
                    {delta != null ? `${delta > 0 ? '+' : ''}${delta.toFixed(1)}%` : '—'}
                  </td>
                  <td className="py-2 px-3 text-right font-mono text-[11px]"
                      style={{ color: a.confidence_score >= 0.8 ? '#3FB950' : a.confidence_score >= 0.5 ? '#D29922' : '#F85149' }}>
                    {(a.confidence_score * 100).toFixed(0)}%
                  </td>
                  <td className="py-2 px-3 font-mono text-[10px] text-outline max-w-[120px] overflow-hidden text-ellipsis"
                      title={a.source_file}>
                    {a.source_file}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {report.extraction_warnings.length > 0 && (
        <div className="p-4 border-t border-border bg-surface-container-low">
          <div className="text-[10px] font-mono text-risk-medium uppercase tracking-widest mb-2">
            Warnings ({report.extraction_warnings.length})
          </div>
          {report.extraction_warnings.map((w, i) => (
            <div key={i} className="text-label-sm font-label-sm text-risk-medium mb-1">· {w}</div>
          ))}
        </div>
      )}
    </div>
  )
}
