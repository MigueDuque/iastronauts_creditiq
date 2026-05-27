import { useEffect, useState } from 'react'
import { useParams, Link } from 'react-router-dom'

const API = import.meta.env.VITE_API_URL || ''

interface Account {
  account_id: string
  normalized_account_name: string
  category: string
  subcategory: string
  current_value: number
  previous_value: number | null
  confidence_score: number
  source_file: string
}

interface JobStatus {
  job_id: string
  status: 'pending' | 'processing' | 'completed' | 'failed'
  started_at: string
  finished_at: string | null
  error: string | null
  accounts_extracted: number | null
}

interface ExtractorOutput {
  job_id: string
  company_name: string
  statement_type: string
  currency: string
  periods: string[]
  extraction_confidence: number
  extraction_warnings: string[]
  accounts: Account[]
}

const CATEGORY_COLOR: Record<string, string> = {
  assets:      '#b7c4ff',
  liabilities: '#F85149',
  equity:      '#3FB950',
  revenue:     '#D29922',
  expense:     '#ff7b72',
  other:       '#8d90a2',
}

export default function JobResultPage() {
  const { jobId } = useParams<{ jobId: string }>()
  const [status, setStatus]   = useState<JobStatus | null>(null)
  const [report, setReport]   = useState<ExtractorOutput | null>(null)
  const [elapsed, setElapsed] = useState(0)
  const [catFilter, setCatFilter] = useState<string>('all')

  // poll status
  useEffect(() => {
    if (!jobId) return
    const headers = { 'x-tenant-id': 'demo' }
    const interval = setInterval(async () => {
      try {
        const res = await fetch(`${API}/analyses/${jobId}`, { headers })
        if (!res.ok) {
          console.error(`[poll] status ${res.status}:`, await res.text())
          return
        }
        const data: JobStatus = await res.json()
        setStatus(data)
        if (data.status === 'completed' || data.status === 'failed') {
          clearInterval(interval)
          if (data.status === 'completed') {
            const rep = await fetch(`${API}/analyses/${jobId}/report`, { headers })
            if (rep.ok) setReport(await rep.json())
            else console.error(`[report] status ${rep.status}:`, await rep.text())
          }
        }
      } catch (e) {
        console.error('[poll] network error:', e)
      }
    }, 8000)  // SAM local cold-start ~6s — poll every 8s to avoid container cascade
    setElapsed(0)
    return () => clearInterval(interval)
  }, [jobId])

  // elapsed timer
  useEffect(() => {
    if (status?.status === 'completed' || status?.status === 'failed') return
    const t = setInterval(() => setElapsed(e => e + 1), 1000)
    return () => clearInterval(t)
  }, [status?.status])

  const accounts  = report?.accounts ?? []
  const categories = ['all', ...Array.from(new Set(accounts.map(a => a.category))).sort()]
  const filtered  = catFilter === 'all' ? accounts : accounts.filter(a => a.category === catFilter)

  const statusColor = {
    pending:    '#8d90a2',
    processing: '#b7c4ff',
    completed:  '#3FB950',
    failed:     '#F85149',
  }[status?.status ?? 'pending']

  const statusIcon = {
    pending:    'schedule',
    processing: 'autorenew',
    completed:  'check_circle',
    failed:     'error',
  }[status?.status ?? 'pending']

  return (
    <div style={{ padding: '28px 32px', maxWidth: 1200 }}>

      {/* ── Header ─────────────────────────────────────────────── */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 24 }}>
        <Link to="/" style={{ color: '#8d90a2', textDecoration: 'none', display: 'flex', alignItems: 'center', gap: 4, fontSize: 13, fontFamily: 'JetBrains Mono' }}>
          <span className="material-symbols-outlined" style={{ fontSize: 16 }}>arrow_back</span>
          Dashboard
        </Link>
        <span style={{ color: '#30363D' }}>/</span>
        <span style={{ fontFamily: 'JetBrains Mono', fontSize: 13, color: '#8d90a2' }}>Agent 1 Output</span>
      </div>

      {/* ── Status card ────────────────────────────────────────── */}
      <div style={{ background: '#161B22', border: '1px solid #30363D', borderRadius: 12, padding: '20px 24px', marginBottom: 24 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 16 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <span
              className="material-symbols-outlined"
              style={{
                fontSize: 28, color: statusColor,
                animation: status?.status === 'processing' ? 'spin 1s linear infinite' : 'none',
              }}
            >
              {statusIcon}
            </span>
            <div>
              <div style={{ fontFamily: 'Geist, sans-serif', fontSize: 20, fontWeight: 600, color: '#e2e1ee' }}>
                DocumentExtractor — Agent 1
              </div>
              <div style={{ fontFamily: 'JetBrains Mono', fontSize: 11, color: '#8d90a2', marginTop: 2 }}>
                JOB: {jobId}
              </div>
            </div>
          </div>
          <div style={{ display: 'flex', gap: 24 }}>
            <Kpi label="STATUS"    value={(status?.status ?? '—').toUpperCase()} color={statusColor} />
            <Kpi label="ELAPSED"   value={`${elapsed}s`}                         color="#8d90a2" />
            {report && <Kpi label="ACCOUNTS" value={String(report.accounts.length)} color="#b7c4ff" />}
            {report && <Kpi label="CONFIDENCE" value={`${(report.extraction_confidence * 100).toFixed(1)}%`} color="#3FB950" />}
          </div>
        </div>

        {/* meta row */}
        {report && (
          <div style={{ marginTop: 16, paddingTop: 16, borderTop: '1px solid #30363D', display: 'flex', gap: 32, flexWrap: 'wrap' }}>
            {[
              { label: 'COMPANY',    value: report.company_name },
              { label: 'TYPE',       value: report.statement_type },
              { label: 'CURRENCY',   value: report.currency },
              { label: 'PERIODS',    value: report.periods.join(' · ') || '—' },
            ].map(({ label, value }) => (
              <div key={label}>
                <div style={{ fontFamily: 'JetBrains Mono', fontSize: 9, color: '#8d90a2', letterSpacing: '0.08em', marginBottom: 2 }}>{label}</div>
                <div style={{ fontFamily: 'Inter', fontSize: 13, color: '#e2e1ee' }}>{value || '—'}</div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* ── Processing state ───────────────────────────────────── */}
      {(status?.status === 'pending' || status?.status === 'processing') && (
        <div style={{ background: '#161B22', border: '1px solid #30363D', borderRadius: 12, padding: 32, textAlign: 'center' }}>
          <div style={{ fontFamily: 'JetBrains Mono', fontSize: 11, color: '#8d90a2', letterSpacing: '0.08em', marginBottom: 16 }}>
            {status?.status === 'pending' ? 'QUEUED — STARTING...' : 'RUNNING EXTRACTION PIPELINE'}
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12, maxWidth: 400, margin: '0 auto', textAlign: 'left' }}>
            {[
              { label: 'S3 file download', done: status?.status !== 'pending' },
              { label: 'PDF → AWS Textract (table extraction)', done: false },
              { label: 'Excel → pandas parsing', done: false },
              { label: 'Claude Haiku → NIIF normalization', done: false },
            ].map((s, i) => (
              <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <span className="material-symbols-outlined" style={{ fontSize: 16, color: s.done ? '#3FB950' : '#8d90a2' }}>
                  {s.done ? 'check_circle' : 'radio_button_unchecked'}
                </span>
                <span style={{ fontFamily: 'Inter', fontSize: 13, color: s.done ? '#e2e1ee' : '#8d90a2' }}>{s.label}</span>
              </div>
            ))}
          </div>
          <div style={{ fontFamily: 'JetBrains Mono', fontSize: 10, color: '#8d90a2', marginTop: 24 }}>
            Polling every 2s · Textract PDF jobs take 30–120s
          </div>
        </div>
      )}

      {/* ── Error ──────────────────────────────────────────────── */}
      {status?.status === 'failed' && (
        <div style={{ background: 'rgba(248,81,73,0.08)', border: '1px solid rgba(248,81,73,0.2)', borderRadius: 12, padding: 24 }}>
          <div style={{ fontFamily: 'JetBrains Mono', fontSize: 11, color: '#F85149', marginBottom: 8 }}>EXTRACTION FAILED</div>
          <pre style={{ fontFamily: 'JetBrains Mono', fontSize: 12, color: '#F85149', whiteSpace: 'pre-wrap', margin: 0 }}>
            {status.error}
          </pre>
        </div>
      )}

      {/* ── Accounts table ─────────────────────────────────────── */}
      {report && report.accounts.length > 0 && (
        <div style={{ background: '#161B22', border: '1px solid #30363D', borderRadius: 12, overflow: 'hidden' }}>
          {/* table header */}
          <div style={{ padding: '16px 20px', borderBottom: '1px solid #30363D', display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
            <div style={{ fontFamily: 'Inter', fontSize: 15, fontWeight: 600, color: '#e2e1ee' }}>
              Extracted Accounts
            </div>
            {/* category filter */}
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
              {categories.map(cat => (
                <button
                  key={cat}
                  onClick={() => setCatFilter(cat)}
                  style={{
                    padding: '3px 10px',
                    borderRadius: 6,
                    border: `1px solid ${catFilter === cat ? (CATEGORY_COLOR[cat] ?? '#b7c4ff') : '#30363D'}`,
                    background: catFilter === cat ? `${CATEGORY_COLOR[cat] ?? '#b7c4ff'}18` : 'transparent',
                    color: catFilter === cat ? (CATEGORY_COLOR[cat] ?? '#b7c4ff') : '#8d90a2',
                    fontFamily: 'JetBrains Mono',
                    fontSize: 10,
                    cursor: 'pointer',
                    textTransform: 'uppercase',
                    letterSpacing: '0.05em',
                  }}
                >
                  {cat === 'all' ? `All (${accounts.length})` : `${cat} (${accounts.filter(a => a.category === cat).length})`}
                </button>
              ))}
            </div>
          </div>

          {/* table */}
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ background: '#0c0e16' }}>
                  {['ID', 'Category', 'Normalized Account Name', 'Subcategory', 'Current (MM)', 'Prior (MM)', 'Δ%', 'Conf', 'Source'].map(h => (
                    <th key={h} style={{ padding: '10px 14px', textAlign: h === 'Current (MM)' || h === 'Prior (MM)' || h === 'Δ%' || h === 'Conf' ? 'right' : 'left', fontFamily: 'JetBrains Mono', fontSize: 10, color: '#8d90a2', letterSpacing: '0.05em', fontWeight: 500, whiteSpace: 'nowrap', borderBottom: '1px solid #30363D' }}>
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {filtered.map((a, i) => {
                  const delta = a.previous_value != null && a.previous_value !== 0
                    ? ((a.current_value - a.previous_value) / Math.abs(a.previous_value)) * 100
                    : null
                  const catColor = CATEGORY_COLOR[a.category] ?? '#8d90a2'
                  return (
                    <tr key={a.account_id} style={{ background: i % 2 === 0 ? 'transparent' : '#0c0e1640', borderBottom: '1px solid #30363D22' }}>
                      <td style={{ padding: '9px 14px', fontFamily: 'JetBrains Mono', fontSize: 11, color: '#8d90a2', whiteSpace: 'nowrap' }}>{a.account_id}</td>
                      <td style={{ padding: '9px 14px', whiteSpace: 'nowrap' }}>
                        <span style={{ fontFamily: 'JetBrains Mono', fontSize: 10, color: catColor, background: `${catColor}18`, border: `1px solid ${catColor}30`, padding: '2px 7px', borderRadius: 4 }}>
                          {a.category}
                        </span>
                      </td>
                      <td style={{ padding: '9px 14px', fontFamily: 'Inter', fontSize: 13, color: '#e2e1ee', minWidth: 240 }}>{a.normalized_account_name}</td>
                      <td style={{ padding: '9px 14px', fontFamily: 'Inter', fontSize: 11, color: '#8d90a2' }}>{a.subcategory}</td>
                      <td style={{ padding: '9px 14px', fontFamily: 'JetBrains Mono', fontSize: 12, color: '#e2e1ee', textAlign: 'right', whiteSpace: 'nowrap' }}>
                        {a.current_value.toLocaleString('en-US', { minimumFractionDigits: 1, maximumFractionDigits: 1 })}
                      </td>
                      <td style={{ padding: '9px 14px', fontFamily: 'JetBrains Mono', fontSize: 12, color: '#8d90a2', textAlign: 'right', whiteSpace: 'nowrap' }}>
                        {a.previous_value != null ? a.previous_value.toLocaleString('en-US', { minimumFractionDigits: 1, maximumFractionDigits: 1 }) : '—'}
                      </td>
                      <td style={{ padding: '9px 14px', fontFamily: 'JetBrains Mono', fontSize: 11, textAlign: 'right', whiteSpace: 'nowrap', color: delta == null ? '#8d90a2' : delta > 0 ? '#3FB950' : '#F85149' }}>
                        {delta != null ? `${delta > 0 ? '+' : ''}${delta.toFixed(1)}%` : '—'}
                      </td>
                      <td style={{ padding: '9px 14px', fontFamily: 'JetBrains Mono', fontSize: 11, textAlign: 'right', color: a.confidence_score >= 0.8 ? '#3FB950' : a.confidence_score >= 0.5 ? '#D29922' : '#F85149' }}>
                        {(a.confidence_score * 100).toFixed(0)}%
                      </td>
                      <td style={{ padding: '9px 14px', fontFamily: 'JetBrains Mono', fontSize: 10, color: '#8d90a2', maxWidth: 160, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={a.source_file}>
                        {a.source_file}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* ── Warnings ───────────────────────────────────────────── */}
      {report && report.extraction_warnings.length > 0 && (
        <div style={{ marginTop: 16, background: 'rgba(210,153,34,0.08)', border: '1px solid rgba(210,153,34,0.2)', borderRadius: 12, padding: '16px 20px' }}>
          <div style={{ fontFamily: 'JetBrains Mono', fontSize: 10, color: '#D29922', letterSpacing: '0.08em', marginBottom: 8 }}>
            EXTRACTION WARNINGS ({report.extraction_warnings.length})
          </div>
          {report.extraction_warnings.map((w, i) => (
            <div key={i} style={{ fontFamily: 'Inter', fontSize: 12, color: '#D29922', marginBottom: 4 }}>· {w}</div>
          ))}
        </div>
      )}

      <style>{`
        @keyframes spin { from { transform: rotate(0deg) } to { transform: rotate(360deg) } }
      `}</style>
    </div>
  )
}

function Kpi({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div style={{ textAlign: 'right' }}>
      <div style={{ fontFamily: 'JetBrains Mono', fontSize: 9, color: '#8d90a2', letterSpacing: '0.08em', marginBottom: 2 }}>{label}</div>
      <div style={{ fontFamily: 'JetBrains Mono', fontSize: 16, fontWeight: 700, color }}>{value}</div>
    </div>
  )
}
