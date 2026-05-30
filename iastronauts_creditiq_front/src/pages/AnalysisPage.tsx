import { useState, useEffect, useRef } from 'react'
import AiReasoningPipeline from '../components/AiReasoningPipeline'

const API          = import.meta.env.VITE_API_URL || ''
const STORAGE_KEY  = 'creditiq_analysis_id'
const STATUS_KEY   = 'creditiq_status'
const REPORT_KEY   = 'creditiq_report'
const ANALYZER_KEY = 'creditiq_analyzer_data'
const SCORER_KEY   = 'creditiq_scorer_data'
const PHASE_KEY    = 'creditiq_phase'
const HEADERS      = { 'x-tenant-id': 'demo' }

// ── Processing step timings ────────────────────────────────────────────────

const EXTRACTION_STEPS = [
  { label: 'Connecting & downloading from S3',          start: 0  },
  { label: 'Parsing document (Textract / pandas)',       start: 4  },
  { label: 'LLM classification & NIIF normalization',   start: 55 },
] as const

const ANALYZER_STEPS = [
  { label: 'Loading historical reports & enriching accounts',  start: 0  },
  { label: 'Computing ratios, materiality & anomaly flags',    start: 5  },
  { label: 'LLM sub-agents: Movement · Causality · Thesis',   start: 15 },
  { label: 'Executive narrative & portfolio synthesis',        start: 50 },
] as const

function stepState(idx: number, starts: readonly number[], elapsed: number): 'pending' | 'active' | 'done' {
  const nextStart = starts[idx + 1] ?? Infinity
  if (elapsed < starts[idx]) return 'pending'
  if (elapsed >= nextStart) return 'done'
  return 'active'
}

// ── Color maps ────────────────────────────────────────────────────────────

const CATEGORY_COLOR: Record<string, string> = {
  assets:      '#5BA4FF',
  liabilities: '#FF6B9D',
  equity:      '#6DD4FF',
  revenue:     '#FFB84D',
  expense:     '#7FD0FF',
  other:       '#A8B8D8',
}

const HEALTH_COLOR: Record<string, string> = {
  STABLE:   '#6DD4FF',
  GROWING:  '#5BA4FF',
  DECLINING:'#FFB84D',
  CRITICAL: '#FF6B9D',
}

const RISK_COLOR: Record<string, string> = {
  LOW:    '#56F2C1',
  MEDIUM: '#FFB020',
  HIGH:   '#FF6B9D',
}

// Accent color per agent view
const AGENT_COLOR: Record<string, string> = {
  agent1: '#FFB020',
  agent2: '#56CCF2',
  agent3: '#A78BFA',
  agent4: '#56F2C1',
}

// ── Types ──────────────────────────────────────────────────────────────────

type ActiveView      = 'agent1' | 'agent2' | 'agent3' | 'agent4'
type ProcessingPhase = 'agent1' | 'agent2' | 'agent3' | 'agent4' | null

interface JobSummary {
  job_id: string
  date: string
  status: string
  company_name: string | null
  periods: string[]
}

interface AgentProgressEntry {
  index: number; label: string; title: string; detail: string
  state: 'done' | 'running' | 'pending' | 'failed'; step: string | null
}

interface PipelineProgress {
  current_agent: number | null; current_step: string | null
  agents: AgentProgressEntry[]
}

interface JobStatus {
  analysis_id?: string
  status: 'pending' | 'processing' | 'extraction_complete' | 'analysis_complete' | 'scoring_complete' | 'completed' | 'failed' | 'cancelled'
  error?: string | null
  progress?: PipelineProgress
}

interface Account {
  account_id: string; normalized_account_name: string; category: string
  current_value: number; previous_value: number | null
  confidence_score: number; source_file: string
}

interface ExtractorOutput {
  job_id: string; company_name: string; currency: string; periods: string[]
  extraction_confidence: number; extraction_warnings: string[]; accounts: Account[]
}

interface DashboardMetric {
  key: string; label: string; value: string; signal: 'positive' | 'neutral' | 'negative'
}

interface InsightTier1 { signal: string; so_what: string; category: string }
interface InsightTier2 { account_id: string; signal: string; so_what: string }

interface AnalyzerOutput {
  job_id: string; company_name: string; currency: string; periods: string[]
  overall_financial_health: string; executive_narrative: string; portfolio_thesis?: string
  insight_tiers?: { tier1_critical?: InsightTier1[]; tier2_material?: InsightTier2[] }
  narrative_layers?: { executive?: string; tactical?: string; technical?: string }
  executive_kpis?: {
    dashboard_metrics?: DashboardMetric[]
    profitability?: { roe_pct: number; net_margin_pct: number; ebitda_margin_pct: number }
    earnings_quality?: { quality_score: number; quality_label: string; unrealized_gain_dependency_pct: number }
    concentration?: { top1_concentration_pct: number; top3_concentration_pct: number }
    fund?: { aum_growth_pct?: number; net_investor_flow_cop_mm?: number; redemption_ratio_pct?: number }
  }
  portfolio_concentration?: {
    top_account_name: string; top_account_pct: number; top3_concentration_pct: number
    concentration_label: string; insight: string; hhi: number; effective_positions: number
    category_concentration: Record<string, number>
    top_accounts: Array<{ name: string; value_cop_mm: number; pct_of_total: number; category: string }>
  }
  fund_analysis?: {
    is_investment_fund: boolean; fund_type: string
    nav_reconciliation?: {
      opening_nav: number | null; contributions: number | null; redemptions: number | null
      investment_return: number | null; closing_nav: number; net_investor_flow: number | null
      reconciles: boolean; gap_cop_mm: number; narrative: string
    }
    top_positions?: Array<{
      account_id: string; account_name: string; asset_class: string
      current_value: number; pct_of_portfolio: number; status: string
    }>
    asset_breakdown_pct?: Record<string, number>
    cash_ratio?: number; top1_position_pct?: number; top3_concentration_pct?: number; insights?: string[]
  }
  high_materiality_accounts: string[]; niif_notes_required: string[]
  sheet_concentration?: {
    asset_total: number; asset_breakdown: Array<{ name: string; value: number; pct: number }>; asset_available: boolean
    instrument_total: number; instrument_breakdown: Array<{
      instrument_type: string; total: number; pct: number
      emisores: Array<{ name: string; value: number; pct: number }>
    }>; instrument_available: boolean
    bank_total: number; bank_breakdown: Array<{ name: string; value: number; pct: number }>; bank_available: boolean
  }
  financial_ratios: {
    totals: { total_assets: number; total_liabilities: number; total_equity: number; total_revenue: number; net_income: number; ebitda: number }
    ratios: { razon_corriente: number; endeudamiento_global: number; margen_neto_pct: number; margen_ebitda_pct: number; roe_pct: number; deuda_patrimonio: number }
    niif18?: { compliance?: { compliance_score: number; flags: string[] }; subtotals?: { resultado_operativo: number; resultado_neto: number; ebitda_niif18: number } }
  }
  analysis_results: { account_id: string; account_name: string; variation_pct: number; materiality: string; risk_level: string; anomaly_detected: boolean }[]
}

interface RiskDimension {
  score: number; level: string; weight: number; key_findings: string[]; risk_drivers: string[]
}

interface ScorerOutput {
  job_id: string; company_name: string; overall_risk_score: string
  validation_score: number; analysis_confidence: number
  anti_hallucination_passed: boolean; requires_human_review: boolean
  issues_found: string[]; compliance_flags: string[]; fund_context_adjusted: boolean
  risk_dimensions: {
    liquidity?: RiskDimension; credit?: RiskDimension; solvency?: RiskDimension
    market?: RiskDimension; operational?: RiskDimension
    composite_score?: number; weights_used?: string
  }
  risk_summary: {
    risk_headline?: string
    risk_narrative_paragraph1?: string; risk_narrative_paragraph2?: string; risk_narrative_paragraph3?: string
    risk_recommendations?: string[]
  }
}

// ── Helpers ────────────────────────────────────────────────────────────────

function initialView(status: string | null): ActiveView {
  if (status === 'completed') return 'agent4'
  if (status === 'scoring_complete') return 'agent3'
  if (status === 'analysis_complete') return 'agent2'
  return 'agent1'
}

// ── Component ──────────────────────────────────────────────────────────────

export default function AnalysisPage() {
  const [jobId, setJobId] = useState<string | null>(() => localStorage.getItem(STORAGE_KEY))
  const [jobStatus, setJobStatus] = useState<JobStatus | null>(() => {
    try { return JSON.parse(localStorage.getItem(STATUS_KEY) ?? 'null') } catch { return null }
  })
  const [report, setReport] = useState<ExtractorOutput | null>(() => {
    try { return JSON.parse(localStorage.getItem(REPORT_KEY) ?? 'null') } catch { return null }
  })
  const [analyzerData, setAnalyzerData] = useState<AnalyzerOutput | null>(() => {
    try { return JSON.parse(localStorage.getItem(ANALYZER_KEY) ?? 'null') } catch { return null }
  })
  const [scorerData, setScorerData] = useState<ScorerOutput | null>(() => {
    try { return JSON.parse(localStorage.getItem(SCORER_KEY) ?? 'null') } catch { return null }
  })
  const [phase, setPhase] = useState<ProcessingPhase>(
    () => (localStorage.getItem(PHASE_KEY) as ProcessingPhase) ?? null
  )
  const [activeView, setActiveView] = useState<ActiveView>(() =>
    initialView(JSON.parse(localStorage.getItem(STATUS_KEY) ?? 'null')?.status ?? null)
  )
  const [elapsed, setElapsed] = useState(0)
  const [catFilter, setCatFilter] = useState('all')
  const [showCancelDialog, setShowCancelDialog] = useState(false)
  const [showJobPicker, setShowJobPicker] = useState(false)
  const [previousJobs, setPreviousJobs] = useState<JobSummary[]>([])
  const [loadingJobs, setLoadingJobs] = useState(false)
  const [jobFilter, setJobFilter] = useState('')
  const [showRestartDialog, setShowRestartDialog] = useState(false)
  const [restartIntent, setRestartIntent] = useState<'agent2' | 'agent3' | 'agent4' | null>(null)
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // track previous data to detect new arrivals for auto-switching
  const prevReport   = useRef(report)
  const prevAnalyzer = useRef(analyzerData)
  const prevScorer   = useRef(scorerData)
  const prevStatus   = useRef(jobStatus?.status)
  const userNavigatedRef = useRef(false)
  const [newData, setNewData] = useState<Set<ActiveView>>(new Set())

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
    if (scorerData) localStorage.setItem(SCORER_KEY, JSON.stringify(scorerData))
    else localStorage.removeItem(SCORER_KEY)
  }, [scorerData])

  useEffect(() => {
    if (phase) localStorage.setItem(PHASE_KEY, phase)
    else localStorage.removeItem(PHASE_KEY)
  }, [phase])

  // ── New job created in UploadDialog ────────────────────────────────────
  // UploadDialog dispatches 'creditiq:newjob' after POST /analyses. Without this
  // listener the page keeps showing the previous job until a manual refresh,
  // because jobId is only read from localStorage at mount.
  useEffect(() => {
    function onNewJob(e: Event) {
      const id = (e as CustomEvent<{ jobId?: string }>).detail?.jobId
      if (!id) return
      if (intervalRef.current) { clearInterval(intervalRef.current); intervalRef.current = null }
      ;[STATUS_KEY, REPORT_KEY, ANALYZER_KEY, SCORER_KEY, PHASE_KEY].forEach(k => localStorage.removeItem(k))
      setReport(null); setAnalyzerData(null); setScorerData(null); setJobStatus(null); setPhase(null)
      setElapsed(0); setCatFilter('all'); setActiveView('agent1'); setNewData(new Set())
      userNavigatedRef.current = false
      localStorage.setItem(STORAGE_KEY, id)
      setJobId(id)
    }
    window.addEventListener('creditiq:newjob', onNewJob as EventListener)
    return () => window.removeEventListener('creditiq:newjob', onNewJob as EventListener)
  }, [])

  // ── Auto-switch view when new data arrives ─────────────────────────────

  useEffect(() => {
    if (report && !prevReport.current) {
      if (!userNavigatedRef.current) setActiveView('agent1')
      else setNewData(prev => { const s = new Set(prev); s.add('agent1'); return s })
    }
    prevReport.current = report
  }, [report])

  useEffect(() => {
    if (analyzerData && !prevAnalyzer.current) {
      if (!userNavigatedRef.current) setActiveView('agent2')
      else setNewData(prev => { const s = new Set(prev); s.add('agent2'); return s })
    }
    prevAnalyzer.current = analyzerData
  }, [analyzerData])

  useEffect(() => {
    if (scorerData && !prevScorer.current) {
      if (!userNavigatedRef.current) setActiveView('agent3')
      else setNewData(prev => { const s = new Set(prev); s.add('agent3'); return s })
    }
    prevScorer.current = scorerData
  }, [scorerData])

  useEffect(() => {
    if (jobStatus?.status === 'completed' && prevStatus.current !== 'completed') {
      if (!userNavigatedRef.current) setActiveView('agent4')
      else setNewData(prev => { const s = new Set(prev); s.add('agent4'); return s })
    }
    prevStatus.current = jobStatus?.status
  }, [jobStatus?.status])

  // ── Main polling effect ────────────────────────────────────────────────

  const TERMINAL = new Set(['completed', 'failed', 'extraction_complete', 'analysis_complete', 'scoring_complete'])

  useEffect(() => {
    if (intervalRef.current) clearInterval(intervalRef.current)
    if (!jobId) {
      setJobStatus(null); setReport(null); setAnalyzerData(null); setScorerData(null)
      setElapsed(0); setCatFilter('all'); return
    }

    let skipPolling = false
    try {
      const cached = JSON.parse(localStorage.getItem(STATUS_KEY) ?? 'null') as JobStatus | null
      if (cached?.analysis_id !== jobId) {
        setJobStatus(null); setReport(null); setAnalyzerData(null); setScorerData(null)
        setElapsed(0); setCatFilter('all')
      } else if (TERMINAL.has(cached.status ?? '')) {
        // We already have a verified terminal status for this job (set by selectJob or a
        // previous poll). Restore it from cache and skip polling — no agent is running.
        setJobStatus(cached)
        skipPolling = true
      }
    } catch {
      setJobStatus(null); setReport(null); setAnalyzerData(null); setScorerData(null)
    }

    if (skipPolling) return

    let alive = true
    const poll = async () => {
      try {
        const res = await fetch(`${API}/analyses/${jobId}`, { headers: HEADERS })
        if (!res.ok || !alive) return
        const data: JobStatus = await res.json()
        if (!alive) return
        setJobStatus(data)
        if (TERMINAL.has(data.status)) {
          if (intervalRef.current) { clearInterval(intervalRef.current); intervalRef.current = null }
        }
        if (data.status === 'extraction_complete' || data.status === 'analysis_complete' ||
            data.status === 'scoring_complete' || data.status === 'completed') {
          if (!report) {
            const r = await fetch(`${API}/analyses/${jobId}/extractor`, { headers: HEADERS })
            if (r.ok && alive) setReport(await r.json())
          }
        }
        if (data.status === 'analysis_complete' || data.status === 'scoring_complete' || data.status === 'completed') {
          if (!analyzerData) {
            const r = await fetch(`${API}/analyses/${jobId}/analyzer`, { headers: HEADERS })
            if (r.ok && alive) {
              const j = await r.json()
              if (j?.analysis_results) setAnalyzerData(j)
            }
          }
        }
        if (data.status === 'scoring_complete' || data.status === 'completed') {
          if (!scorerData) {
            const r = await fetch(`${API}/analyses/${jobId}/scorer`, { headers: HEADERS })
            if (r.ok && alive) setScorerData(await r.json())
          }
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
    if (active && jobId) { setShowCancelDialog(true) } else { _doClean() }
  }

  function _doClean() {
    if (intervalRef.current) { clearInterval(intervalRef.current); intervalRef.current = null }
    ;[STORAGE_KEY, STATUS_KEY, REPORT_KEY, ANALYZER_KEY, SCORER_KEY, PHASE_KEY].forEach(k => localStorage.removeItem(k))
    setJobId(null); setJobStatus(null); setReport(null); setAnalyzerData(null); setScorerData(null)
    setPhase(null); setActiveView('agent1'); setElapsed(0); setShowCancelDialog(false)
    userNavigatedRef.current = false; setNewData(new Set())
  }

  function handleViewSelect(v: ActiveView) {
    userNavigatedRef.current = true
    setActiveView(v)
    setNewData(prev => { const s = new Set(prev); s.delete(v); return s })
  }

  async function confirmClear() {
    if (jobId) { try { await fetch(`${API}/analyses/${jobId}`, { method: 'DELETE', headers: HEADERS }) } catch {} }
    _doClean()
  }

  async function openJobPicker() {
    setShowJobPicker(true); setJobFilter(''); setLoadingJobs(true)
    try {
      const res = await fetch(`${API}/jobs`, { headers: HEADERS })
      if (res.ok) setPreviousJobs((await res.json()).jobs ?? [])
    } catch (e) { console.error('[jobs]', e) }
    finally { setLoadingJobs(false) }
  }

  async function selectJob(job: JobSummary) {
    setShowJobPicker(false)
    ;[STORAGE_KEY, STATUS_KEY, REPORT_KEY, ANALYZER_KEY, SCORER_KEY, PHASE_KEY].forEach(k => localStorage.removeItem(k))
    setJobStatus(null); setReport(null); setAnalyzerData(null); setScorerData(null); setPhase(null); setElapsed(0)
    userNavigatedRef.current = false; setNewData(new Set())
    localStorage.setItem(STORAGE_KEY, job.job_id)

    const jid = job.job_id

    // Always try all three endpoints — don't trust job.status which may be stale
    const [extRes, anlRes, scoRes] = await Promise.allSettled([
      fetch(`${API}/analyses/${jid}/extractor`, { headers: HEADERS }).then(r => r.ok ? r.json() : null),
      fetch(`${API}/analyses/${jid}/analyzer`,  { headers: HEADERS }).then(r => r.ok ? r.json() : null),
      fetch(`${API}/analyses/${jid}/scorer`,    { headers: HEADERS }).then(r => r.ok ? r.json() : null),
    ])

    const extData = extRes.status === 'fulfilled' && extRes.value?.accounts            ? extRes.value : null
    const anlData = anlRes.status === 'fulfilled' && anlRes.value?.analysis_results    ? anlRes.value : null
    const scoData = scoRes.status === 'fulfilled' && scoRes.value?.overall_risk_score  ? scoRes.value : null

    if (extData) setReport(extData)
    if (anlData) setAnalyzerData(anlData)
    if (scoData) setScorerData(scoData)

    // Derive true status from what actually loaded; trust 'completed'/'failed'/'cancelled' from reported
    const reported = job.status as JobStatus['status']
    const trueStatus: JobStatus['status'] =
      reported === 'completed' || reported === 'failed' || reported === 'cancelled' ? reported :
      scoData  ? 'scoring_complete' :
      anlData  ? 'analysis_complete' :
      extData  ? 'extraction_complete' :
      reported

    const syntheticStatus: JobStatus = { analysis_id: jid, status: trueStatus }
    localStorage.setItem(STATUS_KEY, JSON.stringify(syntheticStatus))
    setJobStatus(syntheticStatus)
    setActiveView(initialView(trueStatus))
    setJobId(jid)
  }

  function _startPolling(onStatus: (d: JobStatus) => void) {
    if (intervalRef.current) { clearInterval(intervalRef.current); intervalRef.current = null }
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
          setPhase(null)
        }
      } catch (e) { console.error('[poll]', e) }
    }
    poll()
    intervalRef.current = setInterval(poll, 4000)
    return () => { alive = false; if (intervalRef.current) { clearInterval(intervalRef.current); intervalRef.current = null } }
  }

  function handleRunAgent2Click() {
    if (!jobId) return
    if (analyzerData) { setRestartIntent('agent2'); setShowRestartDialog(true); return }
    _doRunAgent2()
  }

  async function _doRunAgent2() {
    if (!jobId) return
    userNavigatedRef.current = false; setNewData(new Set())
    setPhase('agent2'); setAnalyzerData(null); setScorerData(null); setElapsed(0)
    setActiveView('agent2')
    await fetch(`${API}/analyses/${jobId}/reanalyze`, { method: 'POST', headers: HEADERS })
    _startPolling(async (data) => {
      if (data.status === 'analysis_complete') {
        const r = await fetch(`${API}/analyses/${jobId}/analyzer`, { headers: HEADERS })
        if (r.ok) {
          const j = await r.json()
          if (j?.analysis_results) setAnalyzerData(j)
        }
      }
    })
  }

  function handleRunAgent3Click() {
    if (!jobId) return
    if (scorerData) { setRestartIntent('agent3'); setShowRestartDialog(true); return }
    _doRunAgent3()
  }

  async function _doRunAgent3() {
    if (!jobId) return
    userNavigatedRef.current = false; setNewData(new Set())
    setPhase('agent3'); setScorerData(null); setElapsed(0)
    setActiveView('agent3')
    await fetch(`${API}/analyses/${jobId}/continue`, { method: 'POST', headers: HEADERS })
    _startPolling(async (data) => {
      if (data.status === 'scoring_complete') {
        const r = await fetch(`${API}/analyses/${jobId}/scorer`, { headers: HEADERS })
        if (r.ok) setScorerData(await r.json())
      }
    })
  }

  async function _doRunAgent3Forced() {
    if (!jobId) return
    userNavigatedRef.current = false; setNewData(new Set())
    setPhase('agent3'); setScorerData(null); setElapsed(0)
    setActiveView('agent3')
    await fetch(`${API}/analyses/${jobId}/reanalyze-scorer`, { method: 'POST', headers: HEADERS })
    _startPolling(async (data) => {
      if (data.status === 'scoring_complete') {
        const r = await fetch(`${API}/analyses/${jobId}/scorer`, { headers: HEADERS })
        if (r.ok) setScorerData(await r.json())
      }
    })
  }

  function handleRunAgent4Click() {
    if (!jobId) return
    if (jobStatus?.status === 'completed') { setRestartIntent('agent4'); setShowRestartDialog(true); return }
    _doRunAgent4()
  }

  async function _doRunAgent4() {
    if (!jobId) return
    userNavigatedRef.current = false; setNewData(new Set())
    setPhase('agent4'); setElapsed(0)
    setActiveView('agent4')
    await fetch(`${API}/analyses/${jobId}/continue`, { method: 'POST', headers: HEADERS })
    _startPolling(() => {})
  }

  async function confirmRestart() {
    setShowRestartDialog(false)
    if (restartIntent === 'agent2') await _doRunAgent2()
    else if (restartIntent === 'agent3') await _doRunAgent3Forced()
    else if (restartIntent === 'agent4') await _doRunAgent4()
    setRestartIntent(null)
  }

  function skipToAgent3() {
    setShowRestartDialog(false); setRestartIntent(null)
    _doRunAgent3Forced()
  }

  // ── Derived state ──────────────────────────────────────────────────────

  const st = jobStatus?.status ?? null

  // Which agent is currently running (for background-banner logic)
  const runningView: ActiveView | null =
    phase === 'agent2' ? 'agent2' :
    phase === 'agent3' ? 'agent3' :
    phase === 'agent4' ? 'agent4' :
    (st === 'processing' || st === 'pending') ? 'agent1' : null

  const isProcessing = st === 'processing' || st === 'pending'

  // View state helper: what to show in each tab
  type ViewState = 'running' | 'waiting' | 'done' | 'locked'
  function getViewState(v: ActiveView): ViewState {
    if (v === 'agent1') {
      if (report) return st === 'extraction_complete' ? 'waiting' : 'done'
      return (st === 'processing' || st === 'pending') && (phase === null || phase === 'agent1') ? 'running' : 'locked'
    }
    if (v === 'agent2') {
      if (analyzerData) return st === 'analysis_complete' ? 'waiting' : 'done'
      return phase === 'agent2' ? 'running' : 'locked'
    }
    if (v === 'agent3') {
      if (scorerData) return st === 'scoring_complete' ? 'waiting' : 'done'
      return phase === 'agent3' ? 'running' : 'locked'
    }
    // agent4
    if (st === 'completed') return 'done'
    return phase === 'agent4' ? 'running' : 'locked'
  }

  const accounts   = report?.accounts ?? []
  const categories = ['all', ...Array.from(new Set(accounts.map(a => a.category))).sort()]
  const filtered   = catFilter === 'all' ? accounts : accounts.filter(a => a.category === catFilter)
  const anomalyCount = analyzerData?.analysis_results?.filter(r => r.anomaly_detected).length ?? 0

  const dashboardMetrics = (analyzerData?.executive_kpis?.dashboard_metrics ?? [])
    .filter(m => m.key !== 'aum' && m.key !== 'earnings_quality')
  const isFundWithNav = !!(analyzerData?.fund_analysis?.is_investment_fund &&
    analyzerData.fund_analysis.nav_reconciliation?.closing_nav != null)
  const row1ColCount = (isFundWithNav ? 2 : 1) + Math.min(dashboardMetrics.length, 3)
  const row1GridCols =
    row1ColCount <= 3 ? 'md:grid-cols-3' :
    row1ColCount === 4 ? 'md:grid-cols-4' : 'md:grid-cols-5'
  const metricIcon = (key: string) =>
    key === 'aum_growth' ? 'trending_up' : key === 'net_flow' ? 'swap_vert' :
    key === 'roe' ? 'percent' : key === 'net_margin' ? 'payments' :
    key === 'ebitda_margin' ? 'bar_chart' : key === 'concentration' ? 'hub' : 'analytics'

  const processingLabel =
    phase === 'agent2' ? 'Agent 2 — Financial Analysis' :
    phase === 'agent3' ? 'Agent 3 — Risk Scoring' :
    phase === 'agent4' ? 'Agent 4 — Report Generation' :
    st === 'pending'   ? 'Queued — starting pipeline…' :
                         'Agent 1 — Document Extraction'

  const companyName = report?.company_name ?? analyzerData?.company_name ?? scorerData?.company_name ?? null

  // ── Render ─────────────────────────────────────────────────────────────

  return (
    <div className="p-margin-mobile md:p-margin-desktop flex flex-col md:flex-row gap-gutter min-h-0">

      {/* Left: AI Reasoning Pipeline */}
      <AiReasoningPipeline
        status={st}
        jobId={jobId ?? undefined}
        progress={jobStatus?.progress}
        phase={phase}
      />

      {/* Right: Main canvas */}
      <div className="flex-1 flex flex-col gap-4 overflow-hidden min-w-0">

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
            {/* ── Status bar ── */}
            <div className="bg-surface border border-border rounded px-5 py-3.5 flex flex-wrap items-center justify-between gap-3">
              <div className="flex items-center gap-3">
                <span
                  className="material-symbols-outlined text-[22px]"
                  style={{
                    color: isProcessing ? '#56CCF2' : st === 'completed' ? '#56F2C1' : st === 'failed' ? '#FF4D6D' : '#FFB020',
                    animation: isProcessing ? 'spin 1.2s linear infinite' : 'none',
                  }}
                >
                  {st === 'completed' ? 'check_circle' : st === 'failed' ? 'error' :
                   isProcessing ? 'autorenew' : 'pause_circle'}
                </span>
                <div>
                  <div className="text-body-md font-body-md font-semibold text-on-surface">
                    {companyName ?? 'Financial Analysis'}
                  </div>
                  <div className="text-[10px] font-mono text-outline">{jobId}</div>
                </div>
              </div>

              <div className="flex items-center gap-5">
                {isProcessing && (
                  <div className="flex items-center gap-2">
                    <span className="text-[10px] font-mono text-outline">{processingLabel}</span>
                    <span className="text-[10px] font-mono text-outline">· {elapsed}s</span>
                  </div>
                )}
                {report && <Kpi label="ACCOUNTS" value={String(report.accounts.length)} color="#5BA4FF" />}
                {analyzerData && <Kpi label="HEALTH" value={analyzerData.overall_financial_health} color={HEALTH_COLOR[analyzerData.overall_financial_health] ?? '#8d90a2'} />}
                {scorerData && <Kpi label="RISK" value={scorerData.overall_risk_score} color={RISK_COLOR[scorerData.overall_risk_score] ?? '#8d90a2'} />}
                <button onClick={openJobPicker} className="flex items-center gap-1 px-2.5 py-1 rounded border border-border text-outline hover:text-on-surface transition-colors text-[11px] font-mono">
                  <span className="material-symbols-outlined text-[13px]">history</span>
                  Jobs
                </button>
                <button onClick={handleClearClick} className="flex items-center gap-1 px-2.5 py-1 rounded border border-border text-outline hover:text-on-surface transition-colors text-[11px] font-mono">
                  <span className="material-symbols-outlined text-[13px]">close</span>
                  Clear
                </button>
              </div>
            </div>

            {/* ── View tab bar ── */}
            <ViewTabBar
              activeView={activeView}
              onSelectView={handleViewSelect}
              report={report}
              analyzerData={analyzerData}
              scorerData={scorerData}
              status={st}
              phase={phase}
              newData={newData}
            />

            {/* ── Background running banner ── */}
            {isProcessing && runningView && runningView !== activeView && (
              <div className="bg-surface border border-border rounded px-4 py-2.5 flex items-center gap-3"
                   style={{ borderColor: 'rgba(86,204,242,0.3)', background: 'rgba(86,204,242,0.04)' }}>
                <span className="material-symbols-outlined text-[15px] animate-spin" style={{ color: '#56CCF2' }}>autorenew</span>
                <span className="text-[11px] font-mono text-outline flex-1">
                  {processingLabel} — running in background
                </span>
                <button
                  onClick={() => setActiveView(runningView)}
                  className="text-[11px] font-mono px-2.5 py-1 rounded border border-border text-outline hover:text-on-surface transition-colors flex items-center gap-1"
                >
                  <span className="material-symbols-outlined text-[12px]">open_in_new</span>
                  View progress
                </button>
              </div>
            )}

            {/* ── Error state ── */}
            {st === 'failed' && (
              <div className="bg-surface border border-risk-high/30 rounded p-5"
                   style={{ background: 'rgba(255,77,109,0.05)' }}>
                <div className="text-label-sm font-label-sm text-risk-high uppercase mb-2 font-mono">Pipeline failed</div>
                <pre className="text-body-sm font-body-sm text-risk-high whitespace-pre-wrap font-mono">
                  {jobStatus?.error ?? 'See logs for details.'}
                </pre>
              </div>
            )}

            {/* ══ AGENT 1 VIEW ════════════════════════════════════════════ */}
            {activeView === 'agent1' && (() => {
              const vs = getViewState('agent1')
              return (
                <>
                  {vs === 'running' && (
                    <StepSpinner
                      label="Agent 1 — Document Extraction"
                      elapsed={elapsed}
                      steps={EXTRACTION_STEPS}
                      hint="PDF Textract jobs take 30–120s · polling every 8s"
                    />
                  )}

                  {(vs === 'done' || vs === 'waiting') && report && (
                    <>
                      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                        <MetricCard label="Data Confidence" value={`${(report.extraction_confidence * 100).toFixed(1)}%`} color="#FFB020" icon="verified_user" />
                        <MetricCard label="Reporting Periods" value={report.periods.join(' · ') || '—'} color="#FFB020" icon="calendar_month" />
                      </div>

                      {vs === 'waiting' && (
                        <ContinueBanner
                          color="#FFB020"
                          icon="checklist"
                          title="Agent 1 complete — review extracted accounts"
                          subtitle="Verify the table below, then run the financial analysis engine."
                        >
                          <button
                            onClick={handleRunAgent2Click}
                            className="flex items-center gap-2 px-5 py-2.5 rounded font-mono text-[13px] font-semibold whitespace-nowrap transition-all hover:opacity-90 active:scale-95 shrink-0"
                            style={{ background: '#FFB020', color: '#050816' }}
                          >
                            <span className="material-symbols-outlined text-[16px]">play_arrow</span>
                            Run Agent 2 — Financial Analysis
                          </button>
                        </ContinueBanner>
                      )}

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

                  {vs === 'locked' && (
                    <LockedView
                      icon="description"
                      message="Agent 1 hasn't run yet"
                      hint="Start a new analysis or load a previous job."
                    />
                  )}
                </>
              )
            })()}

            {/* ══ AGENT 2 VIEW ════════════════════════════════════════════ */}
            {activeView === 'agent2' && (() => {
              const vs = getViewState('agent2')
              return (
                <>
                  {vs === 'running' && (
                    <StepSpinner
                      label="Agent 2 — Financial Analysis"
                      elapsed={elapsed}
                      steps={ANALYZER_STEPS}
                      hint="LLM analysis takes 30–90s depending on portfolio size · polling every 4s"
                    />
                  )}

                  {(vs === 'done' || vs === 'waiting') && analyzerData && (
                    <>
                      {/* Row 1: Health + KPIs */}
                      <div className={`grid grid-cols-2 gap-3 ${row1GridCols}`}>
                        {isFundWithNav && (
                          <MetricCard
                            label="AUM — Patrimonio Neto"
                            value={`${analyzerData.fund_analysis!.nav_reconciliation!.closing_nav!.toLocaleString('en-US', { maximumFractionDigits: 0 })} MM`}
                            color="var(--color-brand-accent)"
                            icon="account_balance_wallet"
                          />
                        )}
                        <MetricCard label="Financial Health" value={analyzerData.overall_financial_health} color={HEALTH_COLOR[analyzerData.overall_financial_health] ?? '#2F80FF'} icon="monitor_heart" />
                        {dashboardMetrics.slice(0, 3).map(m => (
                          <MetricCard key={m.key} label={m.label} value={m.value}
                            color={m.signal === 'positive' ? 'var(--color-success-low)' : m.signal === 'negative' ? 'var(--color-danger-soft)' : 'var(--color-warning-soft)'}
                            icon={metricIcon(m.key)} />
                        ))}
                      </div>

                      {/* Row 2 */}
                      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                        {dashboardMetrics.slice(3, 5).map(m => (
                          <MetricCard key={m.key} label={m.label} value={m.value}
                            color={m.signal === 'positive' ? 'var(--color-success-low)' : m.signal === 'negative' ? 'var(--color-danger-soft)' : 'var(--color-warning-soft)'}
                            icon={metricIcon(m.key)} />
                        ))}
                        <StatCounter label="High Materiality" value={analyzerData.high_materiality_accounts.length} unit="accounts" />
                        <StatCounter label="Anomalies" value={anomalyCount} unit="detected" color={anomalyCount > 0 ? '#FFB020' : '#56F2C1'} />
                      </div>

                      {/* Tier 1 signals */}
                      {(analyzerData.insight_tiers?.tier1_critical ?? []).length > 0 && (
                        <div className="bg-surface border rounded overflow-hidden" style={{ borderColor: 'rgba(255,77,109,0.35)' }}>
                          <div className="px-5 py-3 border-b flex items-center gap-2" style={{ borderColor: 'rgba(255,77,109,0.20)', background: 'rgba(255,77,109,0.05)' }}>
                            <span className="material-symbols-outlined text-[15px]" style={{ color: '#FF4D6D' }}>priority_high</span>
                            <span className="text-[10px] font-mono uppercase tracking-widest" style={{ color: '#FF4D6D' }}>Critical Executive Signals</span>
                            <span className="ml-auto text-[9px] font-mono text-outline">{analyzerData.insight_tiers!.tier1_critical!.length} signal{analyzerData.insight_tiers!.tier1_critical!.length !== 1 ? 's' : ''}</span>
                          </div>
                          <div className="divide-y divide-border">
                            {analyzerData.insight_tiers!.tier1_critical!.map((t, i) => (
                              <div key={i} className="px-5 py-3 flex gap-4 items-start">
                                <span className="text-[9px] font-mono px-1.5 py-0.5 rounded shrink-0 mt-0.5" style={{ color: '#FF4D6D', background: 'rgba(255,77,109,0.12)', border: '1px solid rgba(255,77,109,0.25)' }}>{t.category || 'SIGNAL'}</span>
                                <div className="flex-1 min-w-0">
                                  <p className="text-body-sm font-body-sm font-semibold text-on-surface">{t.signal}</p>
                                  {t.so_what && <p className="text-[11px] text-outline mt-0.5 leading-snug">{t.so_what}</p>}
                                </div>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}

                      {/* Portfolio thesis */}
                      {analyzerData.portfolio_thesis && (
                        <div className="bg-surface border border-border rounded p-5">
                          <div className="text-label-sm font-label-sm text-on-surface-variant uppercase tracking-widest mb-3 flex items-center gap-2">
                            <span className="material-symbols-outlined text-[14px]">strategy</span>Portfolio Thesis
                          </div>
                          <p className="text-body-sm font-body-sm text-on-surface leading-relaxed">{analyzerData.portfolio_thesis}</p>
                        </div>
                      )}

                      {/* Narrative layers */}
                      {analyzerData.narrative_layers && (analyzerData.narrative_layers.executive || analyzerData.narrative_layers.tactical || analyzerData.narrative_layers.technical)
                        ? <NarrativeLayers layers={analyzerData.narrative_layers} />
                        : analyzerData.executive_narrative
                        ? (
                          <div className="bg-surface border border-border rounded p-5">
                            <div className="text-label-sm font-label-sm text-on-surface-variant uppercase tracking-widest mb-3 flex items-center gap-2">
                              <span className="material-symbols-outlined text-[14px]">description</span>Executive Narrative
                            </div>
                            <p className="text-body-sm font-body-sm text-on-surface leading-relaxed">{analyzerData.executive_narrative}</p>
                          </div>
                        ) : null}

                      {/* Tier 2 findings */}
                      {(analyzerData.insight_tiers?.tier2_material ?? []).length > 0 && (
                        <div className="bg-surface border border-border rounded overflow-hidden">
                          <div className="px-5 py-3 border-b border-border bg-surface-container-low flex items-center gap-2">
                            <span className="material-symbols-outlined text-[14px] text-outline">insights</span>
                            <span className="text-[10px] font-mono text-on-surface-variant uppercase tracking-widest">Material Account Findings</span>
                          </div>
                          <div className="divide-y divide-border">
                            {analyzerData.insight_tiers!.tier2_material!.map((t, i) => (
                              <div key={i} className="px-5 py-3 flex gap-4 items-start">
                                <span className="text-[9px] font-mono text-outline shrink-0 mt-0.5 w-16 truncate">{t.account_id}</span>
                                <div className="flex-1 min-w-0">
                                  <p className="text-body-sm font-body-sm text-on-surface">{t.signal}</p>
                                  {t.so_what && <p className="text-[11px] text-outline mt-0.5">{t.so_what}</p>}
                                </div>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}

                      {/* Top accounts table */}
                      {analyzerData.analysis_results.length > 0 && (
                        <div className="bg-surface border border-border rounded overflow-hidden">
                          <div className="p-4 border-b border-border bg-surface-container-low flex items-center justify-between">
                            <h3 className="text-body-md font-body-md font-semibold text-on-surface">Top Accounts — Variation & Risk</h3>
                            <span className="text-[10px] font-mono text-outline">sorted by |Δ%|</span>
                          </div>
                          <div className="overflow-x-auto">
                            <table className="w-full text-left whitespace-nowrap">
                              <thead className="bg-surface-container-lowest border-b border-border text-label-sm font-label-sm text-on-surface-variant uppercase">
                                <tr>{['Account', 'Δ%', 'Materiality', 'Risk', 'Anomaly'].map(h => (
                                  <th key={h} className="py-2.5 px-3 font-medium" style={{ textAlign: h === 'Δ%' ? 'right' : 'left' }}>{h}</th>
                                ))}</tr>
                              </thead>
                              <tbody className="text-body-sm font-body-sm text-on-surface divide-y divide-border">
                                {[...analyzerData.analysis_results].sort((a, b) => Math.abs(b.variation_pct) - Math.abs(a.variation_pct)).slice(0, 10).map((r, i) => (
                                  <tr key={r.account_id} className={`hover:bg-surface-container-lowest/50 ${i % 2 ? 'bg-surface-container-lowest/20' : ''}`}>
                                    <td className="py-2 px-3 text-on-surface min-w-[200px]">{r.account_name}</td>
                                    <td className="py-2 px-3 text-right font-mono text-[11px]" style={{ color: r.variation_pct > 0 ? '#56F2C1' : r.variation_pct < 0 ? '#FF4D6D' : '#94A3B8' }}>
                                      {r.variation_pct !== 0 ? `${r.variation_pct > 0 ? '+' : ''}${r.variation_pct.toFixed(1)}%` : '—'}
                                    </td>
                                    <td className="py-2 px-3">
                                      <span className="text-[10px] font-mono px-2 py-0.5 rounded" style={{ color: r.materiality === 'HIGH' ? '#FF4D6D' : r.materiality === 'MEDIUM' ? '#FFB020' : '#56F2C1', background: r.materiality === 'HIGH' ? '#FF4D6D20' : r.materiality === 'MEDIUM' ? '#FFB02020' : '#56F2C120', border: `1px solid ${r.materiality === 'HIGH' ? '#FF4D6D40' : r.materiality === 'MEDIUM' ? '#FFB02040' : '#56F2C140'}` }}>{r.materiality}</span>
                                    </td>
                                    <td className="py-2 px-3"><span className="text-[10px] font-mono" style={{ color: RISK_COLOR[r.risk_level] ?? '#8d90a2' }}>{r.risk_level}</span></td>
                                    <td className="py-2 px-3">{r.anomaly_detected && <span className="material-symbols-outlined text-[14px]" style={{ color: '#FFB020' }}>warning</span>}</td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        </div>
                      )}

                      {/* Sheet concentration */}
                      {analyzerData.sheet_concentration && (analyzerData.sheet_concentration.asset_available || analyzerData.sheet_concentration.instrument_available || analyzerData.sheet_concentration.bank_available) && (
                        <SheetConcentrationSection sc={analyzerData.sheet_concentration} />
                      )}

                      {/* NIIF 18 */}
                      {(analyzerData.financial_ratios.niif18?.compliance?.flags?.length ?? 0) > 0 && (
                        <div className="bg-surface border border-border rounded p-4">
                          <div className="text-label-sm font-label-sm text-on-surface-variant uppercase tracking-widest mb-3 flex items-center gap-2">
                            <span className="material-symbols-outlined text-[14px]">policy</span>NIIF 18 Compliance Flags
                          </div>
                          {analyzerData.financial_ratios.niif18!.compliance!.flags.map((f, i) => (
                            <div key={i} className="text-label-sm font-label-sm text-risk-medium mb-1">· {f}</div>
                          ))}
                        </div>
                      )}

                      {/* Continue banner */}
                      {vs === 'waiting' && (
                        <ContinueBanner
                          color="#56CCF2"
                          icon="analytics"
                          title="Agent 2 complete — review financial analysis"
                          subtitle="Analysis looks correct? Continue to risk scoring."
                        >
                          <button
                            onClick={handleRunAgent2Click}
                            className="flex items-center gap-1 px-3 py-2 rounded border border-border text-outline hover:text-on-surface font-mono text-[11px] transition-colors"
                          >
                            <span className="material-symbols-outlined text-[13px]">replay</span>
                            Re-run Agent 2
                          </button>
                          <button
                            onClick={handleRunAgent3Click}
                            className="flex items-center gap-2 px-5 py-2.5 rounded font-mono text-[13px] font-semibold whitespace-nowrap transition-all hover:opacity-90 active:scale-95"
                            style={{ background: '#56CCF2', color: '#050816' }}
                          >
                            <span className="material-symbols-outlined text-[16px]">play_arrow</span>
                            Run Agent 3 — Risk Scoring
                          </button>
                        </ContinueBanner>
                      )}
                      {vs === 'done' && st !== 'analysis_complete' && (
                        <div className="flex justify-end gap-2">
                          <button onClick={handleRunAgent2Click} className="flex items-center gap-1 px-3 py-2 rounded border border-border text-outline hover:text-on-surface font-mono text-[11px] transition-colors">
                            <span className="material-symbols-outlined text-[13px]">replay</span>Re-run Agent 2
                          </button>
                        </div>
                      )}
                    </>
                  )}

                  {vs === 'locked' && (
                    <LockedView icon="analytics" message="Agent 2 hasn't run yet" hint="Complete Agent 1 extraction first, then run the financial analysis." />
                  )}
                </>
              )
            })()}

            {/* ══ AGENT 3 VIEW ════════════════════════════════════════════ */}
            {activeView === 'agent3' && (() => {
              const vs = getViewState('agent3')
              return (
                <>
                  {vs === 'running' && (
                    <PulseSpinner
                      label="Agent 3 — Risk Scoring"
                      elapsed={elapsed}
                      message="Running risk scoring engines…"
                      hint="Risk scoring takes 15–45s · polling every 4s"
                    />
                  )}

                  {(vs === 'done' || vs === 'waiting') && scorerData && (
                    <>
                      {/* Overview cards */}
                      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                        <MetricCard label="Overall Risk" value={scorerData.overall_risk_score} color={RISK_COLOR[scorerData.overall_risk_score] ?? '#8d90a2'} icon="security" />
                        <MetricCard label="Validation Score" value={`${scorerData.validation_score}/100`}
                          color={scorerData.validation_score >= 75 ? '#56F2C1' : scorerData.validation_score >= 50 ? '#FFB020' : '#FF4D6D'} icon="verified" />
                        <MetricCard label="Confidence" value={`${(scorerData.analysis_confidence * 100).toFixed(0)}%`}
                          color={scorerData.analysis_confidence >= 0.8 ? '#56F2C1' : scorerData.analysis_confidence >= 0.6 ? '#FFB020' : '#FF4D6D'} icon="query_stats" />
                        <div className="bg-surface border border-border rounded p-4 flex flex-col gap-2">
                          <span className="text-label-sm font-label-sm text-on-surface-variant uppercase text-[10px]">Flags</span>
                          <span className={`text-[11px] font-mono ${scorerData.anti_hallucination_passed ? 'text-[#56F2C1]' : 'text-[#FF4D6D]'}`}>
                            {scorerData.anti_hallucination_passed ? '✓ Anti-hallucination' : '✗ Anti-hallucination'}
                          </span>
                          <span className={`text-[11px] font-mono ${!scorerData.requires_human_review ? 'text-[#56F2C1]' : 'text-[#FFB020]'}`}>
                            {scorerData.requires_human_review ? '⚠ Human review required' : '✓ Auto-approved'}
                          </span>
                          {scorerData.fund_context_adjusted && <span className="text-[11px] font-mono text-[#56CCF2]">⊕ Fund weights applied</span>}
                        </div>
                      </div>

                      {/* Risk dimension grid */}
                      <div className="bg-surface border border-border rounded overflow-hidden">
                        <div className="px-5 py-3 border-b border-border bg-surface-container-low flex items-center gap-2">
                          <span className="material-symbols-outlined text-[14px] text-outline">assessment</span>
                          <span className="text-[10px] font-mono text-on-surface-variant uppercase tracking-widest">Risk Dimensions</span>
                          {scorerData.risk_dimensions.composite_score != null && (
                            <span className="ml-auto text-[9px] font-mono text-outline">
                              composite {scorerData.risk_dimensions.composite_score}/100
                              {scorerData.risk_dimensions.weights_used ? ` · ${scorerData.risk_dimensions.weights_used} weights` : ''}
                            </span>
                          )}
                        </div>
                        <div className="grid grid-cols-1 md:grid-cols-5 divide-y md:divide-y-0 md:divide-x divide-border">
                          {(['liquidity', 'credit', 'solvency', 'market', 'operational'] as const).map(dim => {
                            const d = scorerData.risk_dimensions[dim]
                            if (!d) return null
                            const c = RISK_COLOR[d.level] ?? '#8d90a2'
                            const names: Record<string, string> = { liquidity: 'Liquidity', credit: 'Credit', solvency: 'Solvency', market: 'Market', operational: 'Operational' }
                            return (
                              <div key={dim} className="p-4 flex flex-col gap-2">
                                <div className="flex items-center justify-between">
                                  <span className="text-[10px] font-mono text-outline uppercase">{names[dim]}</span>
                                  <span className="text-[9px] font-mono px-1.5 py-0.5 rounded" style={{ color: c, background: `${c}15`, border: `1px solid ${c}30` }}>{d.level}</span>
                                </div>
                                <div className="flex items-end gap-2">
                                  <span className="text-[20px] font-mono font-bold" style={{ color: c }}>{d.score}</span>
                                  <span className="text-[10px] font-mono text-outline mb-0.5">/100</span>
                                </div>
                                <div className="h-1 bg-surface-container rounded overflow-hidden">
                                  <div className="h-full rounded" style={{ width: `${d.score}%`, background: c, opacity: 0.7 }} />
                                </div>
                                {d.key_findings.slice(0, 2).map((f, i) => (
                                  <p key={i} className="text-[10px] text-outline leading-tight">· {f}</p>
                                ))}
                              </div>
                            )
                          })}
                        </div>
                      </div>

                      {/* LLM narrative */}
                      {scorerData.risk_summary?.risk_headline && (
                        <div className="bg-surface border border-border rounded p-5">
                          <div className="text-label-sm font-label-sm text-on-surface-variant uppercase tracking-widest mb-3 flex items-center gap-2">
                            <span className="material-symbols-outlined text-[14px]">shield</span>Risk Assessment
                          </div>
                          <p className="text-body-md font-body-md font-semibold text-on-surface mb-4">{scorerData.risk_summary.risk_headline}</p>
                          <div className="flex flex-col gap-3">
                            {[scorerData.risk_summary.risk_narrative_paragraph1, scorerData.risk_summary.risk_narrative_paragraph2, scorerData.risk_summary.risk_narrative_paragraph3]
                              .filter(Boolean).map((p, i) => <p key={i} className="text-body-sm font-body-sm text-on-surface leading-relaxed">{p}</p>)}
                          </div>
                          {(scorerData.risk_summary.risk_recommendations ?? []).length > 0 && (
                            <div className="mt-4 pt-4 border-t border-border">
                              <p className="text-[10px] font-mono text-outline uppercase tracking-widest mb-2">Recommendations</p>
                              {scorerData.risk_summary.risk_recommendations!.map((r, i) => (
                                <div key={i} className="text-label-sm font-label-sm text-on-surface mb-1.5">· {r}</div>
                              ))}
                            </div>
                          )}
                        </div>
                      )}

                      {/* Issues */}
                      {scorerData.issues_found.length > 0 && (
                        <div className="bg-surface border border-border rounded p-4">
                          <div className="text-label-sm font-label-sm text-on-surface-variant uppercase tracking-widest mb-3 flex items-center gap-2">
                            <span className="material-symbols-outlined text-[14px]" style={{ color: '#FFB020' }}>warning</span>
                            Issues Found ({scorerData.issues_found.length})
                          </div>
                          {scorerData.issues_found.map((issue, i) => <div key={i} className="text-label-sm font-label-sm text-risk-medium mb-1">· {issue}</div>)}
                        </div>
                      )}

                      {/* Compliance flags */}
                      {scorerData.compliance_flags.length > 0 && (
                        <div className="bg-surface border border-border rounded p-4">
                          <div className="text-label-sm font-label-sm text-on-surface-variant uppercase tracking-widest mb-3 flex items-center gap-2">
                            <span className="material-symbols-outlined text-[14px]">policy</span>
                            Compliance Flags ({scorerData.compliance_flags.length})
                          </div>
                          {scorerData.compliance_flags.map((f, i) => <div key={i} className="text-label-sm font-label-sm text-outline mb-1">· {f}</div>)}
                        </div>
                      )}

                      {/* Continue / re-run */}
                      {vs === 'waiting' && (
                        <ContinueBanner
                          color="#A78BFA"
                          icon="security"
                          title="Agent 3 complete — review risk scoring"
                          subtitle="Risk assessment looks correct? Continue to report generation."
                        >
                          <button
                            onClick={() => { setRestartIntent('agent3'); setShowRestartDialog(true) }}
                            className="flex items-center gap-1 px-3 py-2 rounded border border-border text-outline hover:text-on-surface font-mono text-[11px] transition-colors"
                          >
                            <span className="material-symbols-outlined text-[13px]">replay</span>
                            Re-run Agent 3
                          </button>
                          <button
                            onClick={handleRunAgent4Click}
                            className="flex items-center gap-2 px-5 py-2.5 rounded font-mono text-[13px] font-semibold whitespace-nowrap transition-all hover:opacity-90 active:scale-95"
                            style={{ background: '#A78BFA', color: '#050816' }}
                          >
                            <span className="material-symbols-outlined text-[16px]">play_arrow</span>
                            Run Agent 4 — Report
                          </button>
                        </ContinueBanner>
                      )}
                      {vs === 'done' && (
                        <div className="flex justify-end gap-2">
                          <button onClick={() => { setRestartIntent('agent3'); setShowRestartDialog(true) }} className="flex items-center gap-1 px-3 py-2 rounded border border-border text-outline hover:text-on-surface font-mono text-[11px] transition-colors">
                            <span className="material-symbols-outlined text-[13px]">replay</span>Re-run Agent 3
                          </button>
                        </div>
                      )}
                    </>
                  )}

                  {vs === 'locked' && (
                    <LockedView icon="security" message="Agent 3 hasn't run yet" hint="Complete Agent 2 financial analysis first, then run risk scoring." />
                  )}
                </>
              )
            })()}

            {/* ══ AGENT 4 VIEW ════════════════════════════════════════════ */}
            {activeView === 'agent4' && (() => {
              const vs = getViewState('agent4')
              return (
                <>
                  {vs === 'running' && (
                    <PulseSpinner
                      label="Agent 4 — Report Generation"
                      elapsed={elapsed}
                      message="Generating report narrative…"
                      hint="Report generation takes 15–60s · polling every 4s"
                    />
                  )}

                  {vs === 'done' && (
                    <>
                      <div className="bg-surface border rounded p-8 flex flex-col items-center gap-4 text-center"
                           style={{ borderColor: '#56F2C1', background: 'rgba(86,242,193,0.04)' }}>
                        <span className="material-symbols-outlined text-[48px]" style={{ color: '#56F2C1' }}>task_alt</span>
                        <div>
                          <p className="text-body-md font-body-md font-semibold text-on-surface mb-1">Pipeline complete</p>
                          <p className="text-label-sm font-label-sm text-outline">Report saved to S3. All agents finished successfully.</p>
                        </div>
                        <div className="flex gap-2 mt-2">
                          <button onClick={() => { setRestartIntent('agent3'); setShowRestartDialog(true) }} className="flex items-center gap-1 px-3 py-2 rounded border border-border text-outline hover:text-on-surface font-mono text-[11px] transition-colors">
                            <span className="material-symbols-outlined text-[13px]">replay</span>Re-run Agent 3
                          </button>
                          <button onClick={handleRunAgent4Click} className="flex items-center gap-1 px-3 py-2 rounded border border-border text-outline hover:text-on-surface font-mono text-[11px] transition-colors">
                            <span className="material-symbols-outlined text-[13px]">replay</span>Re-run Agent 4
                          </button>
                        </div>
                      </div>
                    </>
                  )}

                  {vs === 'locked' && (
                    <LockedView icon="article" message="Agent 4 hasn't run yet" hint="Complete risk scoring first, then generate the report." />
                  )}
                </>
              )
            })()}
          </>
        )}
      </div>

      <style>{`
        @keyframes spin { from { transform: rotate(0deg) } to { transform: rotate(360deg) } }
      `}</style>

      {/* Restart dialog */}
      {showRestartDialog && (
        <Modal onClose={() => setShowRestartDialog(false)}>
          <div className="flex items-start gap-3 mb-5">
            <span className="material-symbols-outlined text-[22px] mt-0.5" style={{ color: '#D29922' }}>replay</span>
            <div>
              <p className="text-body-md font-body-md font-semibold text-on-surface mb-1">
                {restartIntent === 'agent2' ? 'Agent 2 already has results' :
                 restartIntent === 'agent3' ? 'Agent 3 already has results' : 'Pipeline already completed'}
              </p>
              <p className="text-label-sm font-label-sm text-outline">
                {restartIntent === 'agent2' ? 'Re-running Agent 2 will overwrite the existing financial analysis.' :
                 restartIntent === 'agent3' ? 'Re-running Agent 3 will overwrite the existing risk scoring.' :
                 'Re-running Agent 4 will overwrite the existing report.'}
              </p>
            </div>
          </div>
          <div className="flex flex-col gap-2">
            {restartIntent === 'agent2' && (
              <button onClick={skipToAgent3} className="w-full px-4 py-2.5 rounded border font-mono text-[12px] text-left flex items-center gap-2 hover:bg-surface-container transition-colors" style={{ borderColor: '#56CCF2', color: '#56CCF2' }}>
                <span className="material-symbols-outlined text-[15px]">skip_next</span>
                Skip Agent 2 — continue to Agent 3
              </button>
            )}
            <button onClick={confirmRestart} className="w-full px-4 py-2.5 rounded border border-border text-outline font-mono text-[12px] text-left flex items-center gap-2 hover:bg-surface-container transition-colors">
              <span className="material-symbols-outlined text-[15px]">replay</span>
              {restartIntent === 'agent2' ? 'Re-run Agent 2 (overwrite analysis)' :
               restartIntent === 'agent3' ? 'Re-run Agent 3 (overwrite risk scoring)' : 'Re-run Agent 4 (overwrite report)'}
            </button>
            <button onClick={() => setShowRestartDialog(false)} className="w-full px-4 py-2 rounded text-outline font-mono text-[11px] hover:text-on-surface transition-colors text-center">
              Cancel
            </button>
          </div>
        </Modal>
      )}

      {/* Job picker */}
      {showJobPicker && (
        <Modal onClose={() => setShowJobPicker(false)} wide>
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-2">
              <span className="material-symbols-outlined text-[18px] text-outline">history</span>
              <span className="text-body-md font-body-md font-semibold text-on-surface">Previous Jobs</span>
            </div>
            <button onClick={() => setShowJobPicker(false)} className="text-outline hover:text-on-surface transition-colors">
              <span className="material-symbols-outlined text-[18px]">close</span>
            </button>
          </div>

          {/* Search */}
          <div className="relative mb-3">
            <span className="material-symbols-outlined absolute left-3 top-1/2 -translate-y-1/2 text-[14px] text-outline pointer-events-none">search</span>
            <input
              type="text"
              placeholder="Filter by company or job ID…"
              value={jobFilter}
              onChange={e => setJobFilter(e.target.value)}
              className="w-full bg-surface-container-low border border-border rounded pl-8 pr-3 py-2 text-[12px] font-mono text-on-surface placeholder-outline focus:outline-none focus:border-on-surface-variant"
            />
          </div>

          {loadingJobs && (
            <div className="flex items-center justify-center py-10 gap-3 text-outline">
              <span className="material-symbols-outlined text-[18px] animate-spin">autorenew</span>
              <span className="text-label-sm font-label-sm font-mono">Fetching from S3…</span>
            </div>
          )}

          {!loadingJobs && previousJobs.length === 0 && (
            <p className="text-label-sm font-label-sm text-outline text-center py-10">No previous jobs found in S3.</p>
          )}

          {!loadingJobs && previousJobs.length > 0 && (() => {
            const visible = previousJobs.filter(j =>
              !jobFilter ||
              (j.company_name ?? '').toLowerCase().includes(jobFilter.toLowerCase()) ||
              j.job_id.toLowerCase().includes(jobFilter.toLowerCase())
            )
            return (
              <>
                {visible.length === 0 && <p className="text-label-sm font-label-sm text-outline text-center py-6">No jobs match "{jobFilter}"</p>}
                <div className="flex flex-col gap-1.5 max-h-96 overflow-y-auto pr-1">
                  {visible.map(job => (
                    <button
                      key={job.job_id}
                      onClick={() => selectJob(job)}
                      className="w-full text-left bg-surface-container-low hover:bg-surface-container rounded border border-border hover:border-on-surface-variant transition-all p-3.5 flex flex-col gap-2"
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-body-sm font-body-sm font-semibold text-on-surface truncate flex-1">
                          {job.company_name ?? <span className="text-outline font-mono text-[11px]">{job.job_id}</span>}
                        </span>
                        <StatusBadge status={job.status} />
                      </div>
                      <div className="flex items-center gap-3">
                        <PipelineDots status={job.status} />
                        <span className="text-[10px] font-mono text-outline">{job.date}</span>
                        {job.periods.length > 0 && <span className="text-[10px] font-mono text-outline">· {job.periods.join(' → ')}</span>}
                        <span className="text-[9px] font-mono text-outline opacity-50 truncate">{job.job_id}</span>
                      </div>
                    </button>
                  ))}
                </div>
              </>
            )
          })()}
        </Modal>
      )}

      {/* Cancel dialog */}
      {showCancelDialog && (
        <Modal onClose={() => setShowCancelDialog(false)}>
          <div className="flex items-start gap-3 mb-5">
            <span className="material-symbols-outlined text-[22px] mt-0.5" style={{ color: '#FFB020' }}>warning</span>
            <div>
              <p className="text-body-md font-body-md font-semibold text-on-surface mb-1">Cancel this analysis?</p>
              <p className="text-label-sm font-label-sm text-outline">The running pipeline will be stopped. Partial results already saved to S3 will remain, but this job will be marked as cancelled and cannot be resumed.</p>
            </div>
          </div>
          <div className="flex justify-end gap-2">
            <button onClick={() => setShowCancelDialog(false)} className="px-4 py-2 rounded border border-border text-outline hover:text-on-surface text-[12px] font-mono transition-colors">Keep running</button>
            <button onClick={confirmClear} className="px-4 py-2 rounded text-[12px] font-mono font-semibold transition-all hover:opacity-90 active:scale-95" style={{ background: '#FF4D6D', color: '#fff' }}>Stop &amp; clear</button>
          </div>
        </Modal>
      )}
    </div>
  )
}

// ── Sub-components ────────────────────────────────────────────────────────

function ViewTabBar({ activeView, onSelectView, report, analyzerData, scorerData, status, phase, newData }: {
  activeView: ActiveView
  onSelectView: (v: ActiveView) => void
  report: ExtractorOutput | null
  analyzerData: AnalyzerOutput | null
  scorerData: ScorerOutput | null
  status: string | null
  phase: ProcessingPhase
  newData?: Set<ActiveView>
}) {
  const isRunning1 = (status === 'processing' || status === 'pending') && (phase === null || phase === 'agent1')
  const isRunning2 = phase === 'agent2'
  const isRunning3 = phase === 'agent3'
  const isRunning4 = phase === 'agent4'

  const tabs = [
    {
      key: 'agent1' as const,
      label: 'Agent 1',
      name: 'Extraction',
      icon: 'description',
      available: !!report || isRunning1,
      running: isRunning1,
      done: !!report,
      color: AGENT_COLOR.agent1,
      summary: report ? `${report.accounts.length} accounts · ${(report.extraction_confidence * 100).toFixed(0)}% conf` : null,
    },
    {
      key: 'agent2' as const,
      label: 'Agent 2',
      name: 'Analysis',
      icon: 'analytics',
      available: !!analyzerData || isRunning2,
      running: isRunning2,
      done: !!analyzerData,
      color: AGENT_COLOR.agent2,
      summary: analyzerData ? analyzerData.overall_financial_health : null,
      summaryColor: HEALTH_COLOR[analyzerData?.overall_financial_health ?? ''],
    },
    {
      key: 'agent3' as const,
      label: 'Agent 3',
      name: 'Risk',
      icon: 'security',
      available: !!scorerData || isRunning3,
      running: isRunning3,
      done: !!scorerData,
      color: AGENT_COLOR.agent3,
      summary: scorerData ? scorerData.overall_risk_score : null,
      summaryColor: RISK_COLOR[scorerData?.overall_risk_score ?? ''],
    },
    {
      key: 'agent4' as const,
      label: 'Agent 4',
      name: 'Report',
      icon: 'article',
      available: status === 'completed' || isRunning4,
      running: isRunning4,
      done: status === 'completed',
      color: AGENT_COLOR.agent4,
      summary: status === 'completed' ? 'Complete' : null,
    },
  ]

  return (
    <div className="grid grid-cols-4 bg-surface border border-border rounded overflow-hidden">
      {tabs.map(tab => {
        const isActive = activeView === tab.key
        const hasNew = newData?.has(tab.key) && !tab.running
        return (
          <button
            key={tab.key}
            onClick={() => onSelectView(tab.key)}
            className={`relative flex flex-col gap-0.5 px-4 py-3 text-left transition-all border-r border-border last:border-r-0 cursor-pointer
              ${isActive ? 'bg-surface-container' : tab.available ? 'hover:bg-surface-container-low' : 'opacity-40 hover:opacity-70'}`}
          >
            {/* Active top bar */}
            {isActive && <div className="absolute top-0 left-0 right-0 h-[2px]" style={{ background: tab.color }} />}

            {/* New data dot */}
            {hasNew && (
              <span className="absolute top-1.5 right-1.5 w-2 h-2 rounded-full animate-pulse" style={{ background: tab.color }} />
            )}

            <div className="flex items-center gap-1.5 w-full">
              <span className="material-symbols-outlined text-[13px]"
                    style={{ color: tab.running ? '#56CCF2' : tab.done ? tab.color : '#475569' }}>
                {tab.icon}
              </span>
              <span className="text-[9px] font-mono uppercase tracking-wider text-outline">{tab.label}</span>
              {tab.running && (
                <span className="material-symbols-outlined text-[11px] ml-auto animate-spin" style={{ color: '#56CCF2' }}>autorenew</span>
              )}
              {tab.done && !tab.running && !hasNew && (
                <span className="material-symbols-outlined text-[11px] ml-auto" style={{ color: tab.color }}>check_circle</span>
              )}
              {hasNew && (
                <span className="text-[9px] font-mono ml-auto animate-pulse" style={{ color: tab.color }}>NEW</span>
              )}
            </div>
            <span className={`text-[13px] font-semibold ${isActive ? 'text-on-surface' : 'text-on-surface-variant'}`}>
              {tab.name}
            </span>
            {tab.running && <span className="text-[10px] font-mono animate-pulse" style={{ color: '#56CCF2' }}>Running…</span>}
            {tab.summary && !tab.running && (
              <span className="text-[10px] font-mono truncate" style={{ color: (tab as any).summaryColor ?? tab.color }}>
                {tab.summary}
              </span>
            )}
            {!tab.available && !tab.running && (
              <span className="text-[10px] font-mono text-outline">Not started</span>
            )}
          </button>
        )
      })}
    </div>
  )
}

function StepSpinner({ label, elapsed, steps, hint }: {
  label: string; elapsed: number
  steps: readonly { label: string; start: number }[]
  hint: string
}) {
  return (
    <div className="bg-surface border border-border rounded p-8 flex flex-col items-center gap-6">
      <div className="text-center">
        <p className="text-label-sm font-label-sm text-outline uppercase tracking-widest mb-1">{label}</p>
        <p className="text-[11px] font-mono text-outline">{elapsed}s elapsed</p>
      </div>
      <div className="flex flex-col gap-5 w-full max-w-sm relative">
        <div className="absolute left-[11px] top-3 bottom-3 w-px bg-border" />
        {steps.map((step, i) => {
          const starts = steps.map(s => s.start) as number[]
          const s = stepState(i, starts, elapsed)
          return (
            <div key={i} className={`flex items-start gap-4 relative z-10 transition-opacity duration-500 ${s === 'pending' ? 'opacity-35' : ''}`}>
              {s === 'done' && <div className="w-6 h-6 rounded-full flex items-center justify-center shrink-0 bg-surface border border-[#56F2C1]"><span className="material-symbols-outlined text-[13px]" style={{ color: '#56F2C1' }}>check</span></div>}
              {s === 'active' && <div className="w-6 h-6 rounded-full flex items-center justify-center shrink-0 border border-primary bg-primary-container shadow-[0_0_8px_rgba(46,98,255,0.35)]"><div className="w-2 h-2 rounded-full bg-on-primary-container animate-pulse" /></div>}
              {s === 'pending' && <div className="w-6 h-6 rounded-full flex items-center justify-center shrink-0 bg-surface border border-border"><div className="w-1.5 h-1.5 rounded-full bg-outline" /></div>}
              <div className="pt-0.5">
                <p className={`text-body-sm font-body-sm ${s === 'active' ? 'text-on-surface font-semibold' : s === 'done' ? 'text-[#56F2C1]' : 'text-outline'}`}>{step.label}</p>
                {s === 'active' && <p className="text-[10px] font-mono text-outline mt-1 animate-pulse">Running…</p>}
                {s === 'done'   && <p className="text-[10px] font-mono mt-1" style={{ color: '#56F2C1' }}>✓ Complete</p>}
              </div>
            </div>
          )
        })}
      </div>
      <p className="text-[10px] font-mono text-outline">{hint}</p>
    </div>
  )
}

function PulseSpinner({ label, elapsed, message, hint }: {
  label: string; elapsed: number; message: string; hint: string
}) {
  return (
    <div className="bg-surface border border-border rounded p-8 flex flex-col items-center gap-6">
      <div className="text-center">
        <p className="text-label-sm font-label-sm text-outline uppercase tracking-widest mb-1">{label}</p>
        <p className="text-[11px] font-mono text-outline">{elapsed}s elapsed</p>
      </div>
      <div className="flex items-center gap-4">
        <div className="w-6 h-6 rounded-full flex items-center justify-center shrink-0 border border-primary bg-primary-container shadow-[0_0_8px_rgba(46,98,255,0.35)]">
          <div className="w-2 h-2 rounded-full bg-on-primary-container animate-pulse" />
        </div>
        <p className="text-body-sm font-body-sm text-on-surface font-semibold animate-pulse">{message}</p>
      </div>
      <p className="text-[10px] font-mono text-outline">{hint}</p>
    </div>
  )
}

function ContinueBanner({ color, icon, title, subtitle, children }: {
  color: string; icon: string; title: string; subtitle: string; children: React.ReactNode
}) {
  return (
    <div className="bg-surface border rounded p-5 flex flex-col md:flex-row items-center justify-between gap-4"
         style={{ borderColor: color, background: `${color}0f` }}>
      <div className="flex items-start gap-3">
        <span className="material-symbols-outlined text-[22px] mt-0.5" style={{ color }}>{icon}</span>
        <div>
          <p className="text-body-md font-body-md font-semibold text-on-surface mb-0.5">{title}</p>
          <p className="text-label-sm font-label-sm text-outline">{subtitle}</p>
        </div>
      </div>
      <div className="flex gap-2 shrink-0">{children}</div>
    </div>
  )
}

function LockedView({ icon, message, hint }: { icon: string; message: string; hint: string }) {
  return (
    <div className="bg-surface border border-border rounded p-12 flex flex-col items-center gap-3 text-center">
      <span className="material-symbols-outlined text-outline text-[40px]">{icon}</span>
      <p className="text-body-sm font-body-sm text-on-surface-variant font-semibold">{message}</p>
      <p className="text-[11px] font-mono text-outline">{hint}</p>
    </div>
  )
}

function Modal({ onClose, wide, children }: { onClose: () => void; wide?: boolean; children: React.ReactNode }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center" style={{ background: 'rgba(0,0,0,0.65)' }} onClick={onClose}>
      <div className={`bg-surface border border-border rounded-lg p-6 ${wide ? 'max-w-lg' : 'max-w-sm'} w-full mx-4 shadow-2xl`} onClick={e => e.stopPropagation()}>
        {children}
      </div>
    </div>
  )
}

function PipelineDots({ status }: { status: string }) {
  const done = status === 'completed' ? 4 : status === 'scoring_complete' ? 3 : status === 'analysis_complete' ? 2 : status === 'extraction_complete' ? 1 : 0
  const running = status === 'processing' || status === 'pending'
  const colors = [AGENT_COLOR.agent1, AGENT_COLOR.agent2, AGENT_COLOR.agent3, AGENT_COLOR.agent4]
  return (
    <div className="flex items-center gap-1">
      {[0, 1, 2, 3].map(i => (
        <div key={i} className={`w-2 h-2 rounded-full ${i === done && running ? 'animate-pulse' : ''}`}
             style={{ background: i < done ? colors[i] : i === done && running ? '#56CCF2' : '#1e2030' }} />
      ))}
    </div>
  )
}

function StatusBadge({ status }: { status: string }) {
  const color =
    status === 'completed'          ? '#56F2C1' :
    status === 'scoring_complete'   ? '#A78BFA' :
    status === 'analysis_complete'  ? '#56CCF2' :
    status === 'extraction_complete'? '#FFB020' :
    status === 'failed'             ? '#FF4D6D' :
    status === 'cancelled'          ? '#FF4D6D' : '#94A3B8'
  return (
    <span className="text-[9px] font-mono px-2 py-0.5 rounded shrink-0"
          style={{ color, background: `${color}18`, border: `1px solid ${color}40` }}>
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
  // Compact two-line card: label + icon on line 1, value on line 2.
  return (
    <div className="bg-surface border border-border rounded px-3 py-2">
      <div className="flex items-center justify-between gap-2">
        <span className="text-label-sm font-label-sm text-on-surface-variant uppercase text-[9px] truncate">{label}</span>
        <span className="material-symbols-outlined text-[13px] text-outline shrink-0">{icon}</span>
      </div>
      <div className="text-[15px] leading-tight font-mono font-bold truncate" style={{ color }} title={value}>{value}</div>
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
    { key: 'executive', label: 'Executive', icon: 'person',      text: layers.executive },
    { key: 'tactical',  label: 'Tactical',  icon: 'swap_horiz',  text: layers.tactical  },
    { key: 'technical', label: 'Technical', icon: 'data_object', text: layers.technical },
  ].filter(t => t.text)
  const [active, setActive] = useState(tabs[0]?.key ?? 'executive')
  const current = tabs.find(t => t.key === active)
  if (tabs.length === 0) return null
  return (
    <div className="bg-surface border border-border rounded overflow-hidden">
      <div className="flex border-b border-border bg-surface-container-low">
        <span className="flex items-center gap-1 px-4 py-3 text-[10px] font-mono text-outline uppercase tracking-widest border-r border-border">
          <span className="material-symbols-outlined text-[13px]">layers</span>Narrative
        </span>
        {tabs.map(t => (
          <button key={t.key} onClick={() => setActive(t.key)}
            className={`flex items-center gap-1.5 px-4 py-3 text-[11px] font-mono transition-colors border-r border-border ${active === t.key ? 'text-on-surface bg-surface' : 'text-outline hover:text-on-surface-variant'}`}>
            <span className="material-symbols-outlined text-[12px]">{t.icon}</span>{t.label}
          </button>
        ))}
      </div>
      {current && <div className="p-5"><p className="text-body-sm font-body-sm text-on-surface leading-relaxed">{current.text}</p></div>}
    </div>
  )
}

type SheetConc = NonNullable<AnalyzerOutput['sheet_concentration']>

function SheetConcentrationSection({ sc }: { sc: SheetConc }) {
  const tabs = [
    { key: 'asset',      label: 'Activos',     icon: 'account_balance',   available: sc.asset_available },
    { key: 'instrument', label: 'Instrumentos', icon: 'candlestick_chart', available: sc.instrument_available },
    { key: 'bank',       label: 'Bancos',       icon: 'corporate_fare',    available: sc.bank_available },
  ].filter(t => t.available)
  const [active, setActive] = useState(tabs[0]?.key ?? 'asset')
  if (tabs.length === 0) return null
  const barColor = 'var(--color-brand-accent)'

  function BarRow({ name, value, pct, maxPct, indent = false }: { name: string; value: number; pct: number; maxPct: number; indent?: boolean }) {
    return (
      <div className={`flex items-center gap-3 py-2 px-5 border-b border-border last:border-b-0 ${indent ? 'pl-10' : ''}`}>
        {indent && <span className="text-[10px] font-mono text-outline shrink-0">└─</span>}
        <span className="text-[12px] text-on-surface flex-1 min-w-0 truncate" title={name}>{name}</span>
        <div className="w-24 h-1.5 bg-surface-container rounded overflow-hidden shrink-0">
          <div className="h-full rounded" style={{ width: `${(pct / maxPct) * 100}%`, background: barColor, opacity: 0.65 }} />
        </div>
        <span className="text-[11px] font-mono w-10 text-right shrink-0" style={{ color: barColor }}>{pct.toFixed(1)}%</span>
        <span className="text-[10px] font-mono text-outline w-32 text-right shrink-0">{value.toLocaleString('en-US', { maximumFractionDigits: 0 })} MM</span>
      </div>
    )
  }

  return (
    <div className="bg-surface border border-border rounded overflow-hidden">
      <div className="border-b border-border bg-surface-container-low">
        <div className="px-5 py-3 flex items-center gap-2">
          <span className="material-symbols-outlined text-[14px] text-outline">donut_small</span>
          <span className="text-[10px] font-mono text-on-surface-variant uppercase tracking-widest">Concentration / Sheet</span>
        </div>
        <div className="flex border-t border-border">
          {tabs.map(t => (
            <button key={t.key} onClick={() => setActive(t.key)}
              className={`flex items-center gap-1.5 px-4 py-2.5 text-[11px] font-mono transition-colors border-r border-border ${active === t.key ? 'text-on-surface bg-surface' : 'text-outline hover:text-on-surface-variant'}`}>
              <span className="material-symbols-outlined text-[12px]">{t.icon}</span>{t.label}
            </button>
          ))}
        </div>
      </div>

      {active === 'asset' && sc.asset_available && (
        <div>
          <div className="px-5 py-2 border-b border-border flex items-center justify-between">
            <span className="text-[10px] font-mono text-outline">Activos del Balance General</span>
            <span className="text-[10px] font-mono text-outline">Total: {sc.asset_total.toLocaleString('en-US', { maximumFractionDigits: 0 })} MM</span>
          </div>
          {sc.asset_breakdown.map(r => <BarRow key={r.name} name={r.name} value={r.value} pct={r.pct} maxPct={sc.asset_breakdown[0]?.pct || 1} />)}
        </div>
      )}
      {active === 'instrument' && sc.instrument_available && (
        <div>
          <div className="px-5 py-2 border-b border-border flex items-center justify-between">
            <span className="text-[10px] font-mono text-outline">Tipos de instrumento · Emisores</span>
            <span className="text-[10px] font-mono text-outline">Total: {sc.instrument_total.toLocaleString('en-US', { maximumFractionDigits: 0 })} MM</span>
          </div>
          {sc.instrument_breakdown.map(ib => (
            <div key={ib.instrument_type}>
              <div className="flex items-center gap-3 px-5 py-2 border-b border-border bg-surface-container-low/50">
                <span className="text-[10px] font-mono px-1.5 py-0.5 rounded shrink-0" style={{ color: barColor, background: `${barColor}18`, border: `1px solid ${barColor}30` }}>{ib.pct.toFixed(1)}%</span>
                <span className="text-[12px] font-semibold text-on-surface flex-1">{ib.instrument_type}</span>
                <span className="text-[10px] font-mono text-outline">{ib.total.toLocaleString('en-US', { maximumFractionDigits: 0 })} MM</span>
              </div>
              {ib.emisores.map(e => <BarRow key={e.name} name={e.name} value={e.value} pct={e.pct} maxPct={ib.emisores[0]?.pct || 1} indent />)}
            </div>
          ))}
        </div>
      )}
      {active === 'bank' && sc.bank_available && (
        <div>
          <div className="px-5 py-2 border-b border-border flex items-center justify-between">
            <span className="text-[10px] font-mono text-outline">Efectivo por entidad bancaria</span>
            <span className="text-[10px] font-mono text-outline">Total: {sc.bank_total.toLocaleString('en-US', { maximumFractionDigits: 0 })} MM</span>
          </div>
          {sc.bank_breakdown.map(r => <BarRow key={r.name} name={r.name} value={r.value} pct={r.pct} maxPct={sc.bank_breakdown[0]?.pct || 1} />)}
        </div>
      )}
    </div>
  )
}

function TablePagination({ page, totalPages, pageSize, totalRows, onPage }: {
  page: number; totalPages: number; pageSize: number; totalRows: number; onPage: (p: number) => void
}) {
  if (totalRows <= pageSize) return null
  const from = (page - 1) * pageSize + 1
  const to = Math.min(page * pageSize, totalRows)
  const btn = 'flex items-center justify-center w-7 h-7 rounded border border-border text-outline hover:text-on-surface hover:border-on-surface-variant disabled:opacity-30 disabled:hover:text-outline disabled:hover:border-border transition-colors'
  return (
    <div className="flex items-center justify-between gap-3 px-4 py-2.5 border-t border-border bg-surface-container-low">
      <span className="text-[10px] font-mono text-outline">
        {from}–{to} of {totalRows}
      </span>
      <div className="flex items-center gap-1.5">
        <button className={btn} onClick={() => onPage(1)} disabled={page <= 1} title="First page">
          <span className="material-symbols-outlined text-[15px]">first_page</span>
        </button>
        <button className={btn} onClick={() => onPage(page - 1)} disabled={page <= 1} title="Previous page">
          <span className="material-symbols-outlined text-[15px]">chevron_left</span>
        </button>
        <span className="text-[10px] font-mono text-on-surface-variant px-2 tabular-nums">
          {page} / {totalPages}
        </span>
        <button className={btn} onClick={() => onPage(page + 1)} disabled={page >= totalPages} title="Next page">
          <span className="material-symbols-outlined text-[15px]">chevron_right</span>
        </button>
        <button className={btn} onClick={() => onPage(totalPages)} disabled={page >= totalPages} title="Last page">
          <span className="material-symbols-outlined text-[15px]">last_page</span>
        </button>
      </div>
    </div>
  )
}

const ACCOUNTS_PAGE_SIZE = 15

function AccountsTable({ report, filtered, accounts, categories, catFilter, setCatFilter }: {
  report: { company_name: string; currency: string; extraction_warnings: string[] }
  filtered: { account_id: string; normalized_account_name: string; category: string; current_value: number; previous_value: number | null; confidence_score: number; source_file: string }[]
  accounts: { category: string }[]
  categories: string[]
  catFilter: string
  setCatFilter: (c: string) => void
}) {
  const [page, setPage] = useState(1)
  // Reset to the first page whenever the category filter changes the row set.
  useEffect(() => { setPage(1) }, [catFilter])
  const totalPages = Math.max(1, Math.ceil(filtered.length / ACCOUNTS_PAGE_SIZE))
  const pageSafe = Math.min(page, totalPages)
  const pageRows = filtered.slice((pageSafe - 1) * ACCOUNTS_PAGE_SIZE, pageSafe * ACCOUNTS_PAGE_SIZE)
  return (
    <div className="bg-surface border border-border rounded overflow-hidden">
      <div className="p-4 border-b border-border flex flex-wrap items-center justify-between gap-3 bg-surface-container-low">
        <div>
          <h3 className="text-body-md font-body-md font-semibold text-on-surface">Extracted Accounts</h3>
          <p className="text-label-sm font-label-sm text-outline font-mono mt-0.5">{report.company_name} · {report.currency} MM</p>
        </div>
        <div className="flex gap-1.5 flex-wrap">
          {categories.map(cat => (
            <button key={cat} onClick={() => setCatFilter(cat)}
              className="px-2.5 py-0.5 rounded text-[10px] font-mono uppercase transition-all"
              style={{
                border: `1px solid ${catFilter === cat ? (CATEGORY_COLOR[cat] ?? '#2F80FF') : 'rgba(47,128,255,0.14)'}`,
                background: catFilter === cat ? `${CATEGORY_COLOR[cat] ?? '#2F80FF'}18` : 'transparent',
                color: catFilter === cat ? (CATEGORY_COLOR[cat] ?? '#2F80FF') : '#94A3B8',
              }}>
              {cat === 'all' ? `All (${accounts.length})` : `${cat} (${accounts.filter(a => a.category === cat).length})`}
            </button>
          ))}
        </div>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-left whitespace-nowrap">
          <thead className="bg-surface-container-lowest border-b border-border text-label-sm font-label-sm text-on-surface-variant uppercase">
            <tr>{['ID', 'Category', 'Account Name', 'Current (MM)', 'Prior (MM)', 'Δ%', 'Conf', 'Source'].map(h => (
              <th key={h} className="py-2.5 px-3 font-medium" style={{ textAlign: ['Current (MM)', 'Prior (MM)', 'Δ%', 'Conf'].includes(h) ? 'right' : 'left' }}>{h}</th>
            ))}</tr>
          </thead>
          <tbody className="text-body-sm font-body-sm text-on-surface divide-y divide-border">
            {pageRows.map((a, i) => {
              const delta = a.previous_value != null && a.previous_value !== 0
                ? ((a.current_value - a.previous_value) / Math.abs(a.previous_value)) * 100 : null
              const catColor = CATEGORY_COLOR[a.category] ?? '#8d90a2'
              return (
                <tr key={a.account_id} className={`hover:bg-surface-container-lowest/50 ${i % 2 ? 'bg-surface-container-lowest/20' : ''}`}>
                  <td className="py-2 px-3 font-mono text-[11px] text-outline">{a.account_id}</td>
                  <td className="py-2 px-3">
                    <span className="text-[10px] font-mono px-2 py-0.5 rounded" style={{ color: catColor, background: `${catColor}18`, border: `1px solid ${catColor}30` }}>{a.category}</span>
                  </td>
                  <td className="py-2 px-3 text-on-surface min-w-[200px]">{a.normalized_account_name}</td>
                  <td className="py-2 px-3 text-right font-mono">{a.current_value.toLocaleString('en-US', { minimumFractionDigits: 1, maximumFractionDigits: 1 })}</td>
                  <td className="py-2 px-3 text-right font-mono text-outline">{a.previous_value != null ? a.previous_value.toLocaleString('en-US', { minimumFractionDigits: 1, maximumFractionDigits: 1 }) : '—'}</td>
                  <td className="py-2 px-3 text-right font-mono text-[11px]" style={{ color: delta == null ? '#94A3B8' : delta > 0 ? '#56F2C1' : '#FF4D6D' }}>
                    {delta != null ? `${delta > 0 ? '+' : ''}${delta.toFixed(1)}%` : '—'}
                  </td>
                  <td className="py-2 px-3 text-right font-mono text-[11px]" style={{ color: a.confidence_score >= 0.8 ? '#56F2C1' : a.confidence_score >= 0.5 ? '#FFB020' : '#FF4D6D' }}>
                    {(a.confidence_score * 100).toFixed(0)}%
                  </td>
                  <td className="py-2 px-3 font-mono text-[10px] text-outline max-w-[120px] overflow-hidden text-ellipsis" title={a.source_file}>{a.source_file}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      <TablePagination
        page={pageSafe}
        totalPages={totalPages}
        pageSize={ACCOUNTS_PAGE_SIZE}
        totalRows={filtered.length}
        onPage={setPage}
      />
      {report.extraction_warnings.length > 0 && (
        <div className="p-4 border-t border-border bg-surface-container-low">
          <div className="text-[10px] font-mono text-risk-medium uppercase tracking-widest mb-2">Warnings ({report.extraction_warnings.length})</div>
          {report.extraction_warnings.map((w, i) => <div key={i} className="text-label-sm font-label-sm text-risk-medium mb-1">· {w}</div>)}
        </div>
      )}
    </div>
  )
}
