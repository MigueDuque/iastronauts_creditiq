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

interface JobSummary {
  job_id: string
  date: string
  status: string
  company_name: string | null
  periods: string[]
}

interface AgentProgressEntry {
  index: number
  label: string
  title: string
  detail: string
  state: 'done' | 'running' | 'pending' | 'failed'
  step: string | null
}

interface PipelineProgress {
  current_agent: number | null
  current_step: string | null
  agents: AgentProgressEntry[]
}

interface JobStatus {
  analysis_id?: string
  status: 'pending' | 'processing' | 'extraction_complete' | 'analysis_complete' | 'completed' | 'failed'
  error?: string | null
  progress?: PipelineProgress
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

interface DashboardMetric {
  key: string
  label: string
  value: string
  signal: 'positive' | 'neutral' | 'negative'
}

interface InsightTier1 {
  signal: string
  so_what: string
  category: string
}

interface InsightTier2 {
  account_id: string
  signal: string
  so_what: string
}

interface AnalyzerOutput {
  job_id: string
  company_name: string
  currency: string
  periods: string[]
  overall_financial_health: string
  executive_narrative: string
  portfolio_thesis?: string
  insight_tiers?: {
    tier1_critical?: InsightTier1[]
    tier2_material?: InsightTier2[]
  }
  narrative_layers?: {
    executive?: string
    tactical?: string
    technical?: string
  }
  executive_kpis?: {
    dashboard_metrics?: DashboardMetric[]
    profitability?: { roe_pct: number; net_margin_pct: number; ebitda_margin_pct: number }
    earnings_quality?: { quality_score: number; quality_label: string; unrealized_gain_dependency_pct: number }
    concentration?: { top1_concentration_pct: number; top3_concentration_pct: number }
    fund?: { aum_growth_pct?: number; net_investor_flow_cop_mm?: number; redemption_ratio_pct?: number }
  }
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
  const [showJobPicker, setShowJobPicker] = useState(false)
  const [previousJobs, setPreviousJobs] = useState<JobSummary[]>([])
  const [loadingJobs, setLoadingJobs] = useState(false)
  const [showRestartDialog, setShowRestartDialog] = useState(false)
  const [restartIntent, setRestartIntent] = useState<'agent2' | 'final' | null>(null)
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
        if (data.status === 'analysis_complete' || data.status === 'completed') {
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

  async function openJobPicker() {
    setShowJobPicker(true)
    setLoadingJobs(true)
    try {
      const res = await fetch(`${API}/jobs`, { headers: HEADERS })
      if (res.ok) {
        const data = await res.json()
        setPreviousJobs(data.jobs ?? [])
      }
    } catch (e) {
      console.error('[jobs]', e)
    } finally {
      setLoadingJobs(false)
    }
  }

  async function selectJob(job: JobSummary) {
    setShowJobPicker(false)
    ;[STORAGE_KEY, STATUS_KEY, REPORT_KEY, ANALYZER_KEY, PHASE_KEY].forEach(k => localStorage.removeItem(k))
    setJobStatus(null); setReport(null); setAnalyzerData(null); setPhase(null); setElapsed(0)
    localStorage.setItem(STORAGE_KEY, job.job_id)

    // Eagerly restore existing S3 data so the UI renders immediately without a blank flash.
    // /report returns load_first([financial_analyzer, extractor]) — the most complete available.
    const s = job.status
    if (s === 'extraction_complete' || s === 'analysis_complete' || s === 'completed') {
      try {
        const rep = await fetch(`${API}/analyses/${job.job_id}/report`, { headers: HEADERS })
        if (rep.ok) {
          const data = await rep.json()
          if (s === 'extraction_complete') setReport(data)
          else setAnalyzerData(data)
        }
      } catch { /* poll will retry */ }
    }

    setJobId(job.job_id)
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

  function handleRunAgent2Click() {
    if (!jobId) return
    // If Agent 2 results already exist, ask the user before overwriting
    if (analyzerData) { setRestartIntent('agent2'); setShowRestartDialog(true); return }
    _doRunAgent2()
  }

  async function _doRunAgent2() {
    if (!jobId) return
    setPhase('agent2'); setReport(null); setAnalyzerData(null); setElapsed(0)
    await fetch(`${API}/analyses/${jobId}/continue`, { method: 'POST', headers: HEADERS })
    _startPolling(async (data) => {
      if (data.status === 'analysis_complete') {
        const rep = await fetch(`${API}/analyses/${jobId}/report`, { headers: HEADERS })
        if (rep.ok) setAnalyzerData(await rep.json())
      }
    })
  }

  function handleRunFinalClick() {
    if (!jobId) return
    // For completed jobs, always confirm before re-running
    if (jobStatus?.status === 'completed') { setRestartIntent('final'); setShowRestartDialog(true); return }
    _doRunFinal()
  }

  async function _doRunFinal() {
    if (!jobId) return
    setPhase('final'); setAnalyzerData(null); setElapsed(0)
    await fetch(`${API}/analyses/${jobId}/continue`, { method: 'POST', headers: HEADERS })
    _startPolling(() => {})
  }

  async function confirmRestart() {
    setShowRestartDialog(false)
    if (restartIntent === 'agent2') await _doRunAgent2()
    else if (restartIntent === 'final') await _doRunFinal()
    setRestartIntent(null)
  }

  function skipToFinal() {
    setShowRestartDialog(false)
    setRestartIntent(null)
    _doRunFinal()
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

  // Show spinner only when genuinely running — not while loading historical data into state
  const isProcessing = jobStatus?.status === 'processing' || jobStatus?.status === 'pending' ||
    (jobStatus == null && !report && !analyzerData)

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
      <AiReasoningPipeline status={jobStatus?.status ?? null} jobId={jobId ?? undefined} progress={jobStatus?.progress} />

      {/* Right: Main canvas */}
      <div className="flex-1 flex flex-col gap-5 overflow-hidden min-w-0">

        {/* No active job */}
        {!jobId && (
          <div className="bg-surface border border-border rounded flex flex-col items-center justify-center gap-6 py-20 text-center">
            <span className="material-symbols-outlined text-outline text-[48px]">analytics</span>
            <div>
              <p className="text-body-md font-body-md text-on-surface mb-1">No active analysis</p>
              <p className="text-label-sm font-label-sm text-outline">Upload financial documents and start an analysis to see results here.</p>
            </div>
            <button
              onClick={openJobPicker}
              className="flex items-center gap-2 px-4 py-2 rounded border border-border text-outline hover:text-on-surface hover:border-on-surface-variant transition-colors text-[12px] font-mono"
            >
              <span className="material-symbols-outlined text-[16px]">history</span>
              Load previous job
            </button>
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
                  onClick={openJobPicker}
                  title="Load a different previous job"
                  className="flex items-center gap-1 px-2.5 py-1 rounded border border-border text-outline hover:text-on-surface hover:border-on-surface-variant transition-colors text-[11px] font-mono"
                >
                  <span className="material-symbols-outlined text-[14px]">history</span>
                  Jobs
                </button>
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
                    onClick={handleRunAgent2Click}
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

            {/* ── AGENT 2 COMPLETE: executive intelligence dashboard ── */}
            {analyzerData && (jobStatus?.status === 'analysis_complete' || jobStatus?.status === 'completed') && (
              <>
                {/* ── Row 1: Financial health + smart KPI cards ── */}
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                  {/* Health — always first */}
                  <MetricCard
                    label="Financial Health"
                    value={analyzerData.overall_financial_health}
                    color={HEALTH_COLOR[analyzerData.overall_financial_health] ?? '#b7c4ff'}
                    icon="monitor_heart"
                  />
                  {/* Smart KPI cards from executive_kpis.dashboard_metrics */}
                  {(analyzerData.executive_kpis?.dashboard_metrics ?? []).slice(0, 3).map(m => (
                    <MetricCard
                      key={m.key}
                      label={m.label}
                      value={m.value}
                      color={m.signal === 'positive' ? '#3FB950' : m.signal === 'negative' ? '#F85149' : '#D29922'}
                      icon={
                        m.key === 'aum_growth'      ? 'trending_up'    :
                        m.key === 'net_flow'         ? 'swap_vert'      :
                        m.key === 'roe'              ? 'percent'        :
                        m.key === 'net_margin'       ? 'payments'       :
                        m.key === 'ebitda_margin'    ? 'bar_chart'      :
                        m.key === 'earnings_quality' ? 'verified'       :
                        m.key === 'concentration'    ? 'hub'            :
                        m.key === 'current_ratio'    ? 'account_balance': 'analytics'
                      }
                    />
                  ))}
                </div>

                {/* ── Row 2: Secondary KPI cards + stat counters ── */}
                <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
                  {(analyzerData.executive_kpis?.dashboard_metrics ?? []).slice(3, 5).map(m => (
                    <MetricCard
                      key={m.key}
                      label={m.label}
                      value={m.value}
                      color={m.signal === 'positive' ? '#3FB950' : m.signal === 'negative' ? '#F85149' : '#D29922'}
                      icon={
                        m.key === 'concentration' ? 'hub' :
                        m.key === 'current_ratio' ? 'account_balance' : 'analytics'
                      }
                    />
                  ))}
                  <StatCounter label="High Materiality" value={analyzerData.high_materiality_accounts.length} unit="accounts" />
                  <StatCounter label="Anomalies" value={anomalyCount} unit="detected" color={anomalyCount > 0 ? '#D29922' : '#3FB950'} />
                  <StatCounter
                    label="NIIF 18"
                    value={analyzerData.financial_ratios.niif18?.compliance?.compliance_score ?? 0}
                    unit="/100"
                    color="#b7c4ff"
                  />
                </div>

                {/* ── Tier 1: Critical executive signals ── */}
                {(analyzerData.insight_tiers?.tier1_critical ?? []).length > 0 && (
                  <div className="bg-surface border rounded overflow-hidden"
                       style={{ borderColor: 'rgba(248,81,73,0.35)' }}>
                    <div className="px-5 py-3 border-b flex items-center gap-2"
                         style={{ borderColor: 'rgba(248,81,73,0.2)', background: 'rgba(248,81,73,0.05)' }}>
                      <span className="material-symbols-outlined text-[15px]" style={{ color: '#F85149' }}>priority_high</span>
                      <span className="text-[10px] font-mono uppercase tracking-widest" style={{ color: '#F85149' }}>
                        Critical Executive Signals
                      </span>
                      <span className="ml-auto text-[9px] font-mono text-outline">
                        {analyzerData.insight_tiers!.tier1_critical!.length} signal{analyzerData.insight_tiers!.tier1_critical!.length !== 1 ? 's' : ''}
                      </span>
                    </div>
                    <div className="divide-y divide-border">
                      {analyzerData.insight_tiers!.tier1_critical!.map((t, i) => (
                        <div key={i} className="px-5 py-3 flex gap-4 items-start">
                          <span className="text-[9px] font-mono px-1.5 py-0.5 rounded shrink-0 mt-0.5"
                                style={{ color: '#F85149', background: 'rgba(248,81,73,0.12)', border: '1px solid rgba(248,81,73,0.25)' }}>
                            {t.category || 'SIGNAL'}
                          </span>
                          <div className="flex-1 min-w-0">
                            <p className="text-body-sm font-body-sm font-semibold text-on-surface">{t.signal}</p>
                            {t.so_what && (
                              <p className="text-[11px] text-outline mt-0.5 leading-snug">{t.so_what}</p>
                            )}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* ── Portfolio thesis ── */}
                {analyzerData.portfolio_thesis && (
                  <div className="bg-surface border border-border rounded p-5">
                    <div className="text-label-sm font-label-sm text-on-surface-variant uppercase tracking-widest mb-3 flex items-center gap-2">
                      <span className="material-symbols-outlined text-[14px]">strategy</span>
                      Portfolio Thesis
                    </div>
                    <p className="text-body-sm font-body-sm text-on-surface leading-relaxed">
                      {analyzerData.portfolio_thesis}
                    </p>
                  </div>
                )}

                {/* ── Narrative layers ── */}
                {analyzerData.narrative_layers && (
                  analyzerData.narrative_layers.executive ||
                  analyzerData.narrative_layers.tactical ||
                  analyzerData.narrative_layers.technical
                ) ? (
                  <NarrativeLayers layers={analyzerData.narrative_layers} />
                ) : analyzerData.executive_narrative ? (
                  <div className="bg-surface border border-border rounded p-5">
                    <div className="text-label-sm font-label-sm text-on-surface-variant uppercase tracking-widest mb-3 flex items-center gap-2">
                      <span className="material-symbols-outlined text-[14px]">description</span>
                      Executive Narrative
                    </div>
                    <p className="text-body-sm font-body-sm text-on-surface leading-relaxed">
                      {analyzerData.executive_narrative}
                    </p>
                  </div>
                ) : null}

                {/* ── Tier 2: Material account findings ── */}
                {(analyzerData.insight_tiers?.tier2_material ?? []).length > 0 && (
                  <div className="bg-surface border border-border rounded overflow-hidden">
                    <div className="px-5 py-3 border-b border-border bg-surface-container-low flex items-center gap-2">
                      <span className="material-symbols-outlined text-[14px] text-outline">insights</span>
                      <span className="text-[10px] font-mono text-on-surface-variant uppercase tracking-widest">
                        Material Account Findings
                      </span>
                    </div>
                    <div className="divide-y divide-border">
                      {analyzerData.insight_tiers!.tier2_material!.map((t, i) => (
                        <div key={i} className="px-5 py-3 flex gap-4 items-start">
                          <span className="text-[9px] font-mono text-outline shrink-0 mt-0.5 w-16 truncate">{t.account_id}</span>
                          <div className="flex-1 min-w-0">
                            <p className="text-body-sm font-body-sm text-on-surface">{t.signal}</p>
                            {t.so_what && (
                              <p className="text-[11px] text-outline mt-0.5">{t.so_what}</p>
                            )}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* ── Top accounts table ── */}
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

                {/* ── NIIF 18 compliance flags ── */}
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

                {/* Agent 2 continue / completed banner */}
                {jobStatus?.status === 'analysis_complete' && (
                  <div className="bg-surface border rounded p-5 flex flex-col md:flex-row items-center justify-between gap-4"
                       style={{ borderColor: '#b7c4ff', background: 'rgba(183,196,255,0.06)' }}>
                    <div className="flex items-start gap-3">
                      <span className="material-symbols-outlined text-[22px] mt-0.5" style={{ color: '#b7c4ff' }}>analytics</span>
                      <div>
                        <p className="text-body-md font-body-md font-semibold text-on-surface mb-0.5">
                          Agent 2 complete — review financial analysis
                        </p>
                        <p className="text-label-sm font-label-sm text-outline">
                          Analysis looks correct? Continue to risk scoring and report generation.
                        </p>
                      </div>
                    </div>
                    <div className="flex gap-2 shrink-0">
                      <button
                        onClick={handleRunAgent2Click}
                        className="flex items-center gap-1 px-3 py-2 rounded border border-border text-outline hover:text-on-surface font-mono text-[11px] transition-colors"
                      >
                        <span className="material-symbols-outlined text-[13px]">replay</span>
                        Re-run Agent 2
                      </button>
                      <button
                        onClick={handleRunFinalClick}
                        className="flex items-center gap-2 px-5 py-2.5 rounded font-mono text-[13px] font-semibold whitespace-nowrap transition-all hover:opacity-90 active:scale-95"
                        style={{ background: '#b7c4ff', color: '#0d1117' }}
                      >
                        <span className="material-symbols-outlined text-[16px]">play_arrow</span>
                        Run Agents 3–4
                      </button>
                    </div>
                  </div>
                )}
                {jobStatus?.status === 'completed' && (
                  <div className="bg-surface border rounded p-5 flex flex-col md:flex-row items-center justify-between gap-4"
                       style={{ borderColor: '#3FB950', background: 'rgba(63,185,80,0.04)' }}>
                    <div className="flex items-center gap-3">
                      <span className="material-symbols-outlined text-[22px]" style={{ color: '#3FB950' }}>check_circle</span>
                      <div>
                        <p className="text-body-md font-body-md font-semibold text-on-surface mb-0.5">Pipeline complete</p>
                        <p className="text-label-sm font-label-sm text-outline">All agents finished. Report saved to S3.</p>
                      </div>
                    </div>
                    <div className="flex gap-2 shrink-0">
                      <button
                        onClick={handleRunAgent2Click}
                        className="flex items-center gap-1 px-3 py-2 rounded border border-border text-outline hover:text-on-surface font-mono text-[11px] transition-colors"
                      >
                        <span className="material-symbols-outlined text-[13px]">replay</span>
                        Re-run Agent 2
                      </button>
                      <button
                        onClick={handleRunFinalClick}
                        className="flex items-center gap-1 px-3 py-2 rounded border border-border text-outline hover:text-on-surface font-mono text-[11px] transition-colors"
                      >
                        <span className="material-symbols-outlined text-[13px]">replay</span>
                        Re-run Agents 3–4
                      </button>
                    </div>
                  </div>
                )}
              </>
            )}

            {/* ── COMPLETED with no analyzer data (edge case: Agent 2 never ran) ── */}
            {jobStatus?.status === 'completed' && !analyzerData && (
              <div className="bg-surface border rounded p-6 flex items-center gap-3"
                   style={{ borderColor: '#3FB950', background: 'rgba(63,185,80,0.04)' }}>
                <span className="material-symbols-outlined text-[22px]" style={{ color: '#3FB950' }}>check_circle</span>
                <p className="text-body-sm font-body-sm text-on-surface">Pipeline complete — report saved to S3.</p>
              </div>
            )}

          </>
        )}
      </div>

      <style>{`
        @keyframes spin { from { transform: rotate(0deg) } to { transform: rotate(360deg) } }
      `}</style>

      {/* Restart confirmation dialog */}
      {showRestartDialog && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center"
          style={{ background: 'rgba(0,0,0,0.65)' }}
          onClick={() => setShowRestartDialog(false)}
        >
          <div
            className="bg-surface border border-border rounded-lg p-6 max-w-sm w-full mx-4 shadow-2xl"
            onClick={e => e.stopPropagation()}
          >
            <div className="flex items-start gap-3 mb-5">
              <span className="material-symbols-outlined text-[22px] mt-0.5" style={{ color: '#D29922' }}>replay</span>
              <div>
                <p className="text-body-md font-body-md font-semibold text-on-surface mb-1">
                  {restartIntent === 'agent2' ? 'Agent 2 already has results' : 'Pipeline already completed'}
                </p>
                <p className="text-label-sm font-label-sm text-outline">
                  {restartIntent === 'agent2'
                    ? 'Re-running Agent 2 will overwrite the existing financial analysis.'
                    : 'Re-running Agents 3–4 will overwrite the existing report.'}
                </p>
              </div>
            </div>
            <div className="flex flex-col gap-2">
              {restartIntent === 'agent2' && (
                <button
                  onClick={skipToFinal}
                  className="w-full px-4 py-2.5 rounded border font-mono text-[12px] text-left flex items-center gap-2 hover:bg-surface-container transition-colors"
                  style={{ borderColor: '#b7c4ff', color: '#b7c4ff' }}
                >
                  <span className="material-symbols-outlined text-[15px]">skip_next</span>
                  Skip Agent 2 — continue to Agents 3–4
                </button>
              )}
              <button
                onClick={confirmRestart}
                className="w-full px-4 py-2.5 rounded border border-border text-outline font-mono text-[12px] text-left flex items-center gap-2 hover:bg-surface-container transition-colors"
              >
                <span className="material-symbols-outlined text-[15px]">replay</span>
                {restartIntent === 'agent2' ? 'Re-run Agent 2 (overwrite analysis)' : 'Re-run Agents 3–4 (overwrite report)'}
              </button>
              <button
                onClick={() => setShowRestartDialog(false)}
                className="w-full px-4 py-2 rounded text-outline font-mono text-[11px] hover:text-on-surface transition-colors text-center"
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Job picker modal */}
      {showJobPicker && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center"
          style={{ background: 'rgba(0,0,0,0.65)' }}
          onClick={() => setShowJobPicker(false)}
        >
          <div
            className="bg-surface border border-border rounded-lg p-6 max-w-lg w-full mx-4 shadow-2xl flex flex-col gap-4"
            onClick={e => e.stopPropagation()}
          >
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="material-symbols-outlined text-[18px] text-outline">history</span>
                <span className="text-body-md font-body-md font-semibold text-on-surface">Previous Jobs</span>
              </div>
              <button
                onClick={() => setShowJobPicker(false)}
                className="text-outline hover:text-on-surface transition-colors"
              >
                <span className="material-symbols-outlined text-[18px]">close</span>
              </button>
            </div>

            {loadingJobs && (
              <div className="flex items-center justify-center py-8 gap-3 text-outline">
                <span className="material-symbols-outlined text-[18px] animate-spin">autorenew</span>
                <span className="text-label-sm font-label-sm font-mono">Fetching from S3…</span>
              </div>
            )}

            {!loadingJobs && previousJobs.length === 0 && (
              <p className="text-label-sm font-label-sm text-outline text-center py-8">
                No previous jobs found in S3.
              </p>
            )}

            {!loadingJobs && previousJobs.length > 0 && (
              <div className="flex flex-col gap-2 max-h-80 overflow-y-auto pr-1">
                {previousJobs.map(job => (
                  <button
                    key={job.job_id}
                    onClick={() => selectJob(job)}
                    className="w-full text-left bg-surface-container-low hover:bg-surface-container rounded border border-border hover:border-on-surface-variant transition-all p-3 flex flex-col gap-1"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-body-sm font-body-sm font-semibold text-on-surface truncate">
                        {job.company_name ?? job.job_id}
                      </span>
                      <StatusBadge status={job.status} />
                    </div>
                    <div className="flex items-center gap-3 text-[10px] font-mono text-outline">
                      <span>{job.date}</span>
                      {job.periods.length > 0 && <span>· {job.periods.join(' → ')}</span>}
                      <span className="truncate opacity-60">{job.job_id}</span>
                    </div>
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

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

function StatusBadge({ status }: { status: string }) {
  const color =
    status === 'completed'          ? '#3FB950' :
    status === 'analysis_complete'  ? '#b7c4ff' :
    status === 'extraction_complete'? '#D29922' :
    status === 'failed'             ? '#F85149' :
    status === 'cancelled'          ? '#F85149' : '#8d90a2'
  return (
    <span
      className="text-[9px] font-mono px-2 py-0.5 rounded shrink-0"
      style={{ color, background: `${color}18`, border: `1px solid ${color}40` }}
    >
      {status.replace(/_/g, ' ').toUpperCase()}
    </span>
  )
}

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

function StatCounter({ label, value, unit, color = '#e2e1ee' }: { label: string; value: number; unit: string; color?: string }) {
  return (
    <div className="bg-surface border border-border rounded p-3 text-center">
      <div className="text-[10px] font-mono text-outline uppercase mb-1 tracking-wide">{label}</div>
      <div className="text-[22px] font-mono font-bold" style={{ color }}>{value}</div>
      <div className="text-[9px] font-mono text-outline">{unit}</div>
    </div>
  )
}

function NarrativeLayers({ layers }: { layers: { executive?: string; tactical?: string; technical?: string } }) {
  const tabs = [
    { key: 'executive', label: 'Executive',  icon: 'person',       text: layers.executive },
    { key: 'tactical',  label: 'Tactical',   icon: 'swap_horiz',   text: layers.tactical  },
    { key: 'technical', label: 'Technical',  icon: 'data_object',  text: layers.technical },
  ].filter(t => t.text)

  const [active, setActive] = useState(tabs[0]?.key ?? 'executive')
  const current = tabs.find(t => t.key === active)

  if (tabs.length === 0) return null

  return (
    <div className="bg-surface border border-border rounded overflow-hidden">
      <div className="flex border-b border-border bg-surface-container-low">
        <span className="flex items-center gap-1 px-4 py-3 text-[10px] font-mono text-outline uppercase tracking-widest border-r border-border">
          <span className="material-symbols-outlined text-[13px]">layers</span>
          Narrative
        </span>
        {tabs.map(t => (
          <button
            key={t.key}
            onClick={() => setActive(t.key)}
            className={`flex items-center gap-1.5 px-4 py-3 text-[11px] font-mono transition-colors border-r border-border
              ${active === t.key ? 'text-on-surface bg-surface' : 'text-outline hover:text-on-surface-variant'}`}
          >
            <span className="material-symbols-outlined text-[12px]">{t.icon}</span>
            {t.label}
          </button>
        ))}
      </div>
      {current && (
        <div className="p-5">
          <p className="text-body-sm font-body-sm text-on-surface leading-relaxed">
            {current.text}
          </p>
        </div>
      )}
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
