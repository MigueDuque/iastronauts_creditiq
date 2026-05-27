import { useState, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import type { DragEvent } from 'react'
import Dialog from '@mui/material/Dialog'
import DialogTitle from '@mui/material/DialogTitle'
import DialogContent from '@mui/material/DialogContent'
import DialogActions from '@mui/material/DialogActions'
import TextField from '@mui/material/TextField'
import Button from '@mui/material/Button'
import LinearProgress from '@mui/material/LinearProgress'
import IconButton from '@mui/material/IconButton'
import Box from '@mui/material/Box'

const UPLOAD_API = import.meta.env.VITE_UPLOAD_API_URL || ''
const API_URL    = import.meta.env.VITE_API_URL || ''

// ── filename parser ───────────────────────────────────────────────────────────
// Convention: {ORG}.{FUND}_{TYPE}_{YYYY-MM-DD}.{ext}
// e.g.  BTG_P.ACCIONES_EEFF_2025-06-30.xlsx
interface ParsedFile {
  org:  string   // BTG_P
  fund: string   // ACCIONES
  date: string   // 2025-06-30
  folder: string // BTG_P/ACCIONES/2025-06-30
}

function parseFileName(name: string): ParsedFile | null {
  // Convention: {ORG}_{FUND}_{TYPE}_{YYYY-MM-DD}.ext
  // e.g. BTG_P.ACCIONES_EEFF_2025-06-30.xlsx
  //   → parts: ['BTG', 'P.ACCIONES', 'EEFF', '2025-06-30']
  const base  = name.replace(/\.[^.]+$/, '')          // strip extension
  const parts = base.split('_')
  const dateIdx = parts.findIndex((p) => /^\d{4}-\d{2}-\d{2}$/.test(p))
  if (dateIdx < 2) return null                        // need at least org + fund + date
  const org  = parts[0]
  const fund = parts[1]
  const date = parts[dateIdx]
  if (!org || !fund || !date) return null
  return { org, fund, date, folder: `${org}/${fund}/${date}` }
}

function deriveFolder(files: FileUploadState[]): ParsedFile | null {
  for (const f of files) {
    const parsed = parseFileName(f.file.name)
    if (parsed) return parsed
  }
  return null
}

function fileType(name: string): string {
  const ext = name.split('.').pop()?.toLowerCase() ?? ''
  if (ext === 'pdf') return 'pdf'
  if (['xlsx', 'xls'].includes(ext)) return 'excel'
  if (ext === 'csv') return 'csv'
  return ext
}

// ─────────────────────────────────────────────────────────────────────────────

interface FileUploadState {
  file: File
  progress: number
  status: 'pending' | 'uploading' | 'done' | 'error'
  s3Key?: string
  error?: string
}

interface UploadDialogProps {
  open: boolean
  onClose: () => void
}

const inputSx = {
  '& .MuiOutlinedInput-root': {
    color: '#e2e1ee',
    fontFamily: 'Inter',
    fontSize: 14,
    '& fieldset': { borderColor: '#30363D' },
    '&:hover fieldset': { borderColor: '#8d90a2' },
    '&.Mui-focused fieldset': { borderColor: '#b7c4ff' },
  },
  '& .MuiInputLabel-root': { color: '#8d90a2', fontFamily: 'Inter', fontSize: 14 },
  '& .MuiInputLabel-root.Mui-focused': { color: '#b7c4ff' },
}

export default function UploadDialog({ open, onClose }: UploadDialogProps) {
  const navigate = useNavigate()

  // ── Step 1 ────────────────────────────────────────────────────
  const [files, setFiles]         = useState<FileUploadState[]>([])
  const [dragging, setDragging]   = useState(false)
  const [uploading, setUploading] = useState(false)
  const [uploadDone, setUploadDone] = useState(false)
  const fileInputRef              = useRef<HTMLInputElement>(null)

  // ── Step 2 ────────────────────────────────────────────────────
  const [step, setStep]           = useState<1 | 2>(1)
  const [companyName, setCompanyName] = useState('')
  const [launching, setLaunching] = useState(false)
  const [launchError, setLaunchError] = useState<string | null>(null)
  const [selectedYear, setSelectedYear] = useState<string>('')
  const [selectedQuarter, setSelectedQuarter] = useState<string>('')
  const [duplicateWarning, setDuplicateWarning] = useState<string | null>(null)

  function initPeriodAndYear() {
    const parsed = deriveFolder(files)
    if (parsed && parsed.date) {
      const parts = parsed.date.split('-')
      if (parts.length >= 2) {
        setSelectedYear(parts[0]) // e.g. "2025"
        setSelectedQuarter(parts[1]) // e.g. "06"
        return
      }
    }
    const currentYear = new Date().getFullYear().toString()
    setSelectedYear(currentYear)
    setSelectedQuarter('12')
  }

  function reset() {
    setFiles([]); setDragging(false); setUploading(false); setUploadDone(false)
    setStep(1); setCompanyName(''); setLaunching(false); setLaunchError(null)
    setSelectedYear(''); setSelectedQuarter(''); setDuplicateWarning(null)
  }

  function handleClose() {
    if (uploading || launching) return
    reset(); onClose()
  }

  function addFiles(incoming: FileList | null) {
    if (!incoming) return
    const next = Array.from(incoming).map<FileUploadState>((f) => ({
      file: f, progress: 0, status: 'pending',
    }))
    setFiles((prev) => {
      const existing = new Set(prev.map((f) => f.file.name))
      return [...prev, ...next.filter((f) => !existing.has(f.file.name))]
    })
  }

  function onDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault(); setDragging(false); addFiles(e.dataTransfer.files)
  }

  async function uploadFile(state: FileUploadState, folder: string): Promise<Partial<FileUploadState>> {
    try {
      const res = await fetch(`${UPLOAD_API}/upload-url`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'x-tenant-id': 'demo' },
        body: JSON.stringify({
          file_name: state.file.name,
          file_type: fileType(state.file.name),
          folder,
        }),
      })
      if (!res.ok) throw new Error(`API error ${res.status}`)
      const { upload_url: url, s3_key } = await res.json()

      await new Promise<void>((resolve, reject) => {
        const xhr = new XMLHttpRequest()
        xhr.open('PUT', url)
        xhr.setRequestHeader('Content-Type', state.file.type || 'application/octet-stream')
        xhr.upload.onprogress = (e) => {
          if (e.lengthComputable) {
            const pct = Math.round((e.loaded / e.total) * 100)
            setFiles((prev) =>
              prev.map((f) => f.file.name === state.file.name ? { ...f, progress: pct, status: 'uploading' } : f)
            )
          }
        }
        xhr.onload  = () => (xhr.status === 200 ? resolve() : reject(new Error(`S3 ${xhr.status}`)))
        xhr.onerror = () => reject(new Error('Network error'))
        xhr.send(state.file)
      })

      return { status: 'done', progress: 100, s3Key: s3_key }
    } catch (err) {
      return { status: 'error', error: (err as Error).message }
    }
  }

  async function handleUpload() {
    const parsed = deriveFolder(files)
    if (!parsed || files.length === 0) return
    setUploading(true)

    await Promise.all(
      files.map(async (f) => {
        if (f.status !== 'pending') return
        const update = await uploadFile(f, parsed.folder)
        setFiles((prev) =>
          prev.map((item) => item.file.name === f.file.name ? { ...item, ...update } : item)
        )
      })
    )

    setUploading(false)
    setUploadDone(true)
  }

  async function handleStartAnalysis(forceRerun = false) {
    const doneFiles = files.filter((f) => f.status === 'done' && f.s3Key)
    const parsed    = deriveFolder(files)
    if (doneFiles.length === 0) return

    setLaunching(true)
    setLaunchError(null)
    setDuplicateWarning(null)

    try {
      const company = companyName.trim() || (parsed ? `${parsed.org} ${parsed.fund}` : 'Unknown')
      const period  = `${selectedYear}-${selectedQuarter}`

      const body: Record<string, unknown> = {
        tenant_id: 'demo',
        business_context: {
          company_name:      company,
          reporting_period:  period,
          raw_context:       `Uploaded via CreditIQ. Folder: uploads/${parsed?.folder ?? ''}`,
        },
        files_to_process: doneFiles.map((f) => ({
          file_name:    f.file.name,
          s3_location:  f.s3Key!,
          file_type:    fileType(f.file.name),
        })),
      }
      if (forceRerun) body.force_rerun = true

      const res = await fetch(`${API_URL}/analyses`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'x-tenant-id': 'demo' },
        body: JSON.stringify(body),
      })

      if (res.status === 409) {
        const data = await res.json()
        setDuplicateWarning(data.message ?? 'Ya existe un análisis para este período.')
        setLaunching(false)
        return
      }

      if (!res.ok) throw new Error(`API error ${res.status}`)
      const data = await res.json()
      const id = data.job_id ?? data.analysis_id
      localStorage.setItem('creditiq_analysis_id', id)
      // Notify AnalysisPage in the same tab so it resets state immediately,
      // even if it's already mounted (storage event only fires in other tabs).
      window.dispatchEvent(new CustomEvent('creditiq:newjob', { detail: { jobId: id } }))
      setTimeout(() => { onClose(); reset(); navigate('/analysis') }, 600)
    } catch (err) {
      setLaunchError((err as Error).message)
      setLaunching(false)
    }
  }

  const parsed   = deriveFolder(files)
  const allDone  = files.length > 0 && files.every((f) => f.status === 'done')
  const hasError = files.some((f) => f.status === 'error')
  const canUpload = !!parsed && files.length > 0 && !uploading && !uploadDone

  const statusIcon = (status: FileUploadState['status']) => {
    if (status === 'done')      return { icon: 'check_circle', color: '#3FB950' }
    if (status === 'error')     return { icon: 'error',        color: '#F85149' }
    if (status === 'uploading') return { icon: 'autorenew',    color: '#b7c4ff' }
    return { icon: 'schedule', color: '#8d90a2' }
  }

  return (
    <Dialog
      open={open}
      onClose={handleClose}
      maxWidth="sm"
      fullWidth
      slotProps={{
        paper: {
          sx: { bgcolor: '#161B22', border: '1px solid #30363D', borderRadius: '12px', color: '#e2e1ee' },
        },
      }}
    >
      {/* ── Title ──────────────────────────────────────────────── */}
      <DialogTitle sx={{ pb: 1, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
          <span className="material-symbols-outlined" style={{ color: '#b7c4ff', fontSize: 22 }}>
            {step === 1 ? 'upload_file' : 'play_circle'}
          </span>
          <Box>
            <div style={{ fontFamily: 'Geist, sans-serif', fontSize: 18, fontWeight: 600, color: '#e2e1ee' }}>
              {step === 1 ? 'Upload Financial Documents' : 'Start Analysis'}
            </div>
            <div style={{ fontFamily: 'JetBrains Mono', fontSize: 10, color: '#8d90a2', letterSpacing: '0.05em', marginTop: 2 }}>
              STEP {step} OF 2
            </div>
          </Box>
        </Box>
        <IconButton onClick={handleClose} size="small" disabled={uploading || launching}
          sx={{ color: '#8d90a2', '&:hover': { color: '#e2e1ee' } }}>
          <span className="material-symbols-outlined" style={{ fontSize: 20 }}>close</span>
        </IconButton>
      </DialogTitle>

      <DialogContent sx={{ pt: 1 }}>

        {/* ══ STEP 1 ══════════════════════════════════════════════ */}
        {step === 1 && (
          <>
            {/* Drop zone */}
            <div
              onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
              onDragLeave={() => setDragging(false)}
              onDrop={onDrop}
              onClick={() => !uploading && !uploadDone && fileInputRef.current?.click()}
              style={{
                border: `2px dashed ${dragging ? '#b7c4ff' : '#30363D'}`,
                borderRadius: 8,
                padding: '28px 16px',
                textAlign: 'center',
                cursor: uploading || uploadDone ? 'default' : 'pointer',
                backgroundColor: dragging ? 'rgba(183,196,255,0.05)' : '#1d1f28',
                transition: 'all 0.2s',
                marginBottom: 16,
              }}
            >
              <span className="material-symbols-outlined" style={{ fontSize: 36, color: '#8d90a2', display: 'block', marginBottom: 8 }}>
                cloud_upload
              </span>
              <div style={{ fontFamily: 'Inter', fontSize: 14, color: '#c3c5d8', marginBottom: 4 }}>
                Drag &amp; drop files here, or{' '}
                <span style={{ color: '#b7c4ff', textDecoration: 'underline' }}>browse</span>
              </div>
              <div style={{ fontFamily: 'JetBrains Mono', fontSize: 10, color: '#8d90a2', letterSpacing: '0.05em' }}>
                PDF · XLSX · CSV &nbsp;·&nbsp; Naming: ORG.FUND_TYPE_YYYY-MM-DD.ext
              </div>
              <input
                ref={fileInputRef}
                type="file"
                multiple
                accept=".pdf,.xlsx,.xls,.csv"
                style={{ display: 'none' }}
                onChange={(e) => addFiles(e.target.files)}
              />
            </div>

            {/* Derived folder preview */}
            {files.length > 0 && (
              <Box sx={{ mb: 2, p: 1.5, bgcolor: '#1d1f28', border: `1px solid ${parsed ? '#30363D' : 'rgba(248,81,73,0.3)'}`, borderRadius: '8px' }}>
                {parsed ? (
                  <>
                    <div style={{ fontFamily: 'JetBrains Mono', fontSize: 9, color: '#8d90a2', letterSpacing: '0.08em', marginBottom: 8 }}>DETECTED FROM FILENAME</div>
                    <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap' }}>
                      {[
                        { label: 'ORG',  value: parsed.org },
                        { label: 'FUND', value: parsed.fund },
                        { label: 'DATE', value: parsed.date },
                      ].map(({ label, value }) => (
                        <div key={label}>
                          <div style={{ fontFamily: 'JetBrains Mono', fontSize: 9, color: '#8d90a2', letterSpacing: '0.08em' }}>{label}</div>
                          <div style={{ fontFamily: 'JetBrains Mono', fontSize: 13, color: '#b7c4ff', fontWeight: 600 }}>{value}</div>
                        </div>
                      ))}
                    </div>
                    <div style={{ fontFamily: 'JetBrains Mono', fontSize: 10, color: '#8d90a2', marginTop: 10 }}>
                      → uploads/{parsed.folder}/
                    </div>
                  </>
                ) : (
                  <div style={{ fontFamily: 'Inter', fontSize: 12, color: '#F85149', display: 'flex', alignItems: 'center', gap: 6 }}>
                    <span className="material-symbols-outlined" style={{ fontSize: 14 }}>warning</span>
                    Could not parse folder from filename. Expected: <span style={{ fontFamily: 'JetBrains Mono' }}>ORG.FUND_TYPE_YYYY-MM-DD.ext</span>
                  </div>
                )}
              </Box>
            )}

            {/* File list */}
            {files.length > 0 && (
              <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
                {files.map((f) => {
                  const { icon, color } = statusIcon(f.status)
                  return (
                    <Box key={f.file.name} sx={{ display: 'flex', alignItems: 'center', gap: 1.5, p: 1.5, bgcolor: '#1d1f28', border: '1px solid #30363D', borderRadius: '8px' }}>
                      <span className="material-symbols-outlined" style={{ fontSize: 18, color: '#8d90a2', flexShrink: 0 }}>description</span>
                      <Box sx={{ flex: 1, minWidth: 0 }}>
                        <div style={{ fontFamily: 'Inter', fontSize: 13, color: '#e2e1ee', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                          {f.file.name}
                        </div>
                        <div style={{ fontFamily: 'JetBrains Mono', fontSize: 10, color: '#8d90a2', letterSpacing: '0.05em' }}>
                          {(f.file.size / 1024).toFixed(0)} KB · {fileType(f.file.name).toUpperCase()}
                        </div>
                        {f.status === 'uploading' && (
                          <LinearProgress variant="determinate" value={f.progress}
                            sx={{ mt: 0.5, borderRadius: 1, bgcolor: '#282933', '& .MuiLinearProgress-bar': { bgcolor: '#b7c4ff' } }} />
                        )}
                        {f.status === 'error' && (
                          <div style={{ fontFamily: 'JetBrains Mono', fontSize: 10, color: '#F85149', marginTop: 2 }}>{f.error}</div>
                        )}
                      </Box>
                      <span className="material-symbols-outlined" style={{ fontSize: 18, flexShrink: 0, color }}>{icon}</span>
                    </Box>
                  )
                })}
              </Box>
            )}

            {allDone && (
              <Box sx={{ mt: 2, p: 1.5, bgcolor: 'rgba(63,185,80,0.08)', border: '1px solid rgba(63,185,80,0.2)', borderRadius: '8px', display: 'flex', alignItems: 'center', gap: 1.5 }}>
                <span className="material-symbols-outlined" style={{ color: '#3FB950', fontSize: 20 }}>check_circle</span>
                <span style={{ fontFamily: 'Inter', fontSize: 13, color: '#3FB950' }}>
                  {files.filter(f => f.status === 'done').length} file(s) uploaded. Ready to start analysis.
                </span>
              </Box>
            )}
            {hasError && !uploading && (
              <Box sx={{ mt: 2, p: 1.5, bgcolor: 'rgba(248,81,73,0.08)', border: '1px solid rgba(248,81,73,0.2)', borderRadius: '8px', display: 'flex', alignItems: 'center', gap: 1.5 }}>
                <span className="material-symbols-outlined" style={{ color: '#F85149', fontSize: 20 }}>warning</span>
                <span style={{ fontFamily: 'Inter', fontSize: 13, color: '#F85149' }}>Some files failed. Check errors above.</span>
              </Box>
            )}
          </>
        )}

        {/* ══ STEP 2 ══════════════════════════════════════════════ */}
        {step === 2 && (
          <>
            {/* Parsed metadata summary */}
            {parsed && (
              <Box sx={{ mb: 2, p: 1.5, bgcolor: '#1d1f28', border: '1px solid #30363D', borderRadius: '8px' }}>
                <div style={{ fontFamily: 'JetBrains Mono', fontSize: 9, color: '#8d90a2', letterSpacing: '0.08em', marginBottom: 8 }}>ANALYSIS TARGET</div>
                <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap', marginBottom: 10 }}>
                  {[
                    { label: 'ORGANIZATION', value: parsed.org },
                    { label: 'FUND',         value: parsed.fund },
                    { label: 'DATE',         value: parsed.date },
                  ].map(({ label, value }) => (
                    <div key={label}>
                      <div style={{ fontFamily: 'JetBrains Mono', fontSize: 9, color: '#8d90a2', letterSpacing: '0.08em' }}>{label}</div>
                      <div style={{ fontFamily: 'JetBrains Mono', fontSize: 13, color: '#b7c4ff', fontWeight: 600 }}>{value}</div>
                    </div>
                  ))}
                </div>
                {files.filter(f => f.status === 'done').map((f) => (
                  <div key={f.file.name} style={{ fontFamily: 'Inter', fontSize: 12, color: '#c3c5d8', display: 'flex', alignItems: 'center', gap: 6, marginBottom: 2 }}>
                    <span className="material-symbols-outlined" style={{ fontSize: 14, color: '#3FB950' }}>check_circle</span>
                    {f.file.name}
                  </div>
                ))}
              </Box>
            )}

            <TextField
              fullWidth
              label="Company / Fund full name (optional)"
              placeholder={parsed ? `${parsed.org} ${parsed.fund}` : 'e.g. BTG Pactual Acciones Colombia'}
              value={companyName}
              onChange={(e) => setCompanyName(e.target.value)}
              disabled={launching}
              size="small"
              sx={{ ...inputSx, mb: 2 }}
            />

            {/* Period and Year Selector */}
            <Box sx={{ display: 'flex', gap: 2, mb: 2 }}>
              {/* Year Dropdown */}
              <Box sx={{ flex: 1 }}>
                <label style={{ fontFamily: 'Inter', fontSize: 12, color: '#8d90a2', display: 'block', marginBottom: 6 }}>
                  Year of Report (Año)
                </label>
                <select
                  value={selectedYear}
                  onChange={(e) => setSelectedYear(e.target.value)}
                  disabled={launching}
                  style={{
                    width: '100%',
                    backgroundColor: '#1d1f28',
                    border: '1px solid #30363D',
                    borderRadius: '8px',
                    color: '#e2e1ee',
                    fontFamily: 'Inter',
                    fontSize: 14,
                    padding: '8.5px 12px',
                    outline: 'none',
                    cursor: 'pointer',
                    transition: 'border-color 0.2s',
                  }}
                  onFocus={(e) => (e.target.style.borderColor = '#b7c4ff')}
                  onBlur={(e) => (e.target.style.borderColor = '#30363D')}
                >
                  {Array.from({ length: 11 }, (_, i) => 2020 + i).map((y) => (
                    <option key={y} value={y.toString()} style={{ backgroundColor: '#161B22' }}>
                      {y}
                    </option>
                  ))}
                </select>
              </Box>

              {/* Period / Quarter Dropdown */}
              <Box sx={{ flex: 1 }}>
                <label style={{ fontFamily: 'Inter', fontSize: 12, color: '#8d90a2', display: 'block', marginBottom: 6 }}>
                  Reporting Quarter (Trimestre)
                </label>
                <select
                  value={selectedQuarter}
                  onChange={(e) => setSelectedQuarter(e.target.value)}
                  disabled={launching}
                  style={{
                    width: '100%',
                    backgroundColor: '#1d1f28',
                    border: '1px solid #30363D',
                    borderRadius: '8px',
                    color: '#e2e1ee',
                    fontFamily: 'Inter',
                    fontSize: 14,
                    padding: '8.5px 12px',
                    outline: 'none',
                    cursor: 'pointer',
                    transition: 'border-color 0.2s',
                  }}
                  onFocus={(e) => (e.target.style.borderColor = '#b7c4ff')}
                  onBlur={(e) => (e.target.style.borderColor = '#30363D')}
                >
                  <option value="03" style={{ backgroundColor: '#161B22' }}>Trimestre 1 (Marzo - 03)</option>
                  <option value="06" style={{ backgroundColor: '#161B22' }}>Trimestre 2 (Junio - 06)</option>
                  <option value="09" style={{ backgroundColor: '#161B22' }}>Trimestre 3 (Septiembre - 09)</option>
                  <option value="12" style={{ backgroundColor: '#161B22' }}>Trimestre 4 (Diciembre - 12)</option>
                </select>
              </Box>
            </Box>

            <div style={{ fontFamily: 'JetBrains Mono', fontSize: 10, color: '#8d90a2', letterSpacing: '0.05em', marginBottom: 16, paddingLeft: 2 }}>
              Pipeline: DocumentExtractor → FinancialAnalyzer → RiskScorer → ReportGenerator
            </div>

            {duplicateWarning && (
              <Box sx={{ mt: 1, p: 2, bgcolor: 'rgba(210,153,34,0.08)', border: '1px solid rgba(210,153,34,0.35)', borderRadius: '8px' }}>
                <Box sx={{ display: 'flex', alignItems: 'flex-start', gap: 1.5, mb: 1.5 }}>
                  <span className="material-symbols-outlined" style={{ color: '#d29922', fontSize: 20, flexShrink: 0, marginTop: 1 }}>history</span>
                  <span style={{ fontFamily: 'Inter', fontSize: 13, color: '#d29922', lineHeight: 1.5 }}>{duplicateWarning}</span>
                </Box>
                <Box sx={{ display: 'flex', gap: 1, justifyContent: 'flex-end' }}>
                  <Button
                    size="small"
                    onClick={() => setDuplicateWarning(null)}
                    sx={{ color: '#8d90a2', fontFamily: 'JetBrains Mono', fontSize: 11, textTransform: 'none', '&:hover': { color: '#e2e1ee' } }}
                  >
                    Cancel
                  </Button>
                  <Button
                    size="small"
                    onClick={() => handleStartAnalysis(true)}
                    disabled={launching}
                    sx={{
                      bgcolor: 'rgba(210,153,34,0.15)', color: '#d29922',
                      border: '1px solid rgba(210,153,34,0.4)',
                      fontFamily: 'JetBrains Mono', fontSize: 11,
                      textTransform: 'none', borderRadius: '6px', px: 2,
                      '&:hover': { bgcolor: 'rgba(210,153,34,0.25)' },
                    }}
                    startIcon={<span className="material-symbols-outlined" style={{ fontSize: 14 }}>replay</span>}
                  >
                    Re-run Analysis
                  </Button>
                </Box>
              </Box>
            )}

            {launchError && (
              <Box sx={{ mt: 1, p: 1.5, bgcolor: 'rgba(248,81,73,0.08)', border: '1px solid rgba(248,81,73,0.2)', borderRadius: '8px', display: 'flex', alignItems: 'center', gap: 1.5 }}>
                <span className="material-symbols-outlined" style={{ color: '#F85149', fontSize: 20 }}>error</span>
                <span style={{ fontFamily: 'Inter', fontSize: 13, color: '#F85149' }}>{launchError}</span>
              </Box>
            )}
          </>
        )}
      </DialogContent>

      {/* ── Actions ────────────────────────────────────────────── */}
      <DialogActions sx={{ px: 3, pb: 2.5, gap: 1 }}>
        <Button
          onClick={handleClose}
          disabled={uploading || launching}
          sx={{ color: '#8d90a2', fontFamily: 'JetBrains Mono', fontSize: 12, textTransform: 'none', '&:hover': { color: '#e2e1ee', bgcolor: '#1d1f28' } }}
        >
          Cancel
        </Button>

        {step === 1 && !uploadDone && (
          <Button
            onClick={handleUpload}
            disabled={!canUpload}
            sx={{
              bgcolor: '#2e62ff', color: '#f7f6ff',
              fontFamily: 'JetBrains Mono', fontSize: 12,
              textTransform: 'none', fontWeight: 500,
              borderRadius: '8px', px: 3,
              '&:hover': { bgcolor: '#2e62ff', opacity: 0.88 },
              '&.Mui-disabled': { bgcolor: '#282933', color: '#8d90a2' },
            }}
            startIcon={<span className="material-symbols-outlined" style={{ fontSize: 16 }}>{uploading ? 'autorenew' : 'cloud_upload'}</span>}
          >
            {uploading ? 'Uploading…' : `Upload ${files.length > 0 ? `${files.length} file${files.length > 1 ? 's' : ''}` : ''}`}
          </Button>
        )}

        {step === 1 && uploadDone && allDone && (
          <Button
            onClick={() => {
              initPeriodAndYear()
              setStep(2)
            }}
            sx={{
              bgcolor: '#2e62ff', color: '#f7f6ff',
              fontFamily: 'JetBrains Mono', fontSize: 12,
              textTransform: 'none', fontWeight: 500, borderRadius: '8px', px: 3,
              '&:hover': { bgcolor: '#2e62ff', opacity: 0.88 },
            }}
            startIcon={<span className="material-symbols-outlined" style={{ fontSize: 16 }}>arrow_forward</span>}
          >
            Next: Start Analysis
          </Button>
        )}

        {step === 2 && (
          <Button
            onClick={() => handleStartAnalysis()}
            disabled={launching}
            sx={{
              bgcolor: '#2e62ff', color: '#f7f6ff',
              fontFamily: 'JetBrains Mono', fontSize: 12,
              textTransform: 'none', fontWeight: 500, borderRadius: '8px', px: 3,
              '&:hover': { bgcolor: '#2e62ff', opacity: 0.88 },
              '&.Mui-disabled': { bgcolor: '#282933', color: '#8d90a2' },
            }}
            startIcon={<span className="material-symbols-outlined" style={{ fontSize: 16 }}>{launching ? 'autorenew' : 'play_circle'}</span>}
          >
            {launching ? 'Launching…' : 'Start Analysis'}
          </Button>
        )}
      </DialogActions>
    </Dialog>
  )
}
