type Status = 'pending' | 'processing' | 'extraction_complete' | 'analysis_complete' | 'completed' | 'failed' | null

interface Props {
  status: Status
  jobId?: string
}

const STEPS = [
  { label: 'Agent 1', title: 'Document Ingestion & OCR',    detail: 'Textract PDF · pandas Excel' },
  { label: 'Agent 2', title: 'Financial Analysis & NIIF',   detail: 'Account classification · variance' },
  { label: 'Agent 3', title: 'Risk Scoring',                detail: 'Materiality · anomaly detection' },
  { label: 'Agent 4', title: 'Report Generation',           detail: 'LLM narrative · NIIF notes draft' },
]

function stepStatus(index: number, status: Status): 'done' | 'active' | 'pending' | 'failed' {
  if (status === 'failed')              return index === 0 ? 'failed' : 'pending'
  if (status === 'completed')           return 'done'
  if (status === 'analysis_complete')   return index <= 1 ? 'done' : index === 2 ? 'active' : 'pending'
  if (status === 'extraction_complete') return index === 0 ? 'done' : index === 1 ? 'active' : 'pending'
  if (status === 'processing')          return index === 0 ? 'active' : 'pending'
  return 'pending'
}

export default function AiReasoningPipeline({ status, jobId }: Props) {
  const statusLabel =
    status === 'completed'           ? { text: 'COMPLETED', color: '#3FB950' } :
    status === 'failed'              ? { text: 'FAILED',    color: '#F85149' } :
    status === 'analysis_complete'   ? { text: 'ANALYZED',  color: '#D29922' } :
    status === 'extraction_complete' ? { text: 'REVIEW',    color: '#D29922' } :
    status === 'processing'          ? { text: 'RUNNING',   color: '#b7c4ff' } :
    status === 'pending'             ? { text: 'QUEUED',    color: '#D29922' } :
                                       { text: 'IDLE',      color: '#8d90a2' }

  return (
    <aside className="w-full md:w-[300px] flex-shrink-0 flex flex-col gap-4">
      <div className="surface surface-container-low border border-border rounded p-4 h-full glass-panel ai-glow">

        {/* Header */}
        <div className="flex items-center justify-between mb-5 pb-4 border-b border-border">
          <h2 className="text-label-md font-label-md text-primary uppercase tracking-wider">AI Pipeline</h2>
          <div className="flex items-center gap-2">
            {status && status !== null && (
              <span
                className="text-[9px] font-mono px-2 py-0.5 rounded border"
                style={{
                  color: statusLabel.color,
                  borderColor: `${statusLabel.color}40`,
                  background: `${statusLabel.color}12`,
                }}
              >
                {statusLabel.text}
              </span>
            )}
            <span className="material-symbols-outlined text-primary text-[18px]">memory</span>
          </div>
        </div>

        {/* Job ID */}
        {jobId && (
          <div className="mb-4 px-2 py-1.5 rounded bg-surface-container-lowest border border-border">
            <div className="text-[9px] font-mono text-outline uppercase tracking-widest mb-1">JOB</div>
            <div className="text-[10px] font-mono text-on-surface-variant truncate">{jobId}</div>
          </div>
        )}

        {/* Steps */}
        <div className="relative">
          <div className="absolute left-3 top-2 bottom-2 w-[1px] bg-border" />
          <div className="flex flex-col gap-5 relative z-10">
            {STEPS.map((step, i) => {
              const s = stepStatus(i, status)
              return (
                <div key={i} className={`flex gap-4 items-start ${s === 'pending' ? 'opacity-40' : ''}`}>
                  {/* Indicator */}
                  {s === 'done' && (
                    <div className="w-6 h-6 rounded-full bg-surface border border-risk-low flex items-center justify-center shrink-0 mt-0.5">
                      <span className="material-symbols-outlined text-[12px] text-risk-low">check</span>
                    </div>
                  )}
                  {s === 'active' && (
                    <div className="w-6 h-6 rounded-full bg-primary-container border border-primary flex items-center justify-center shrink-0 mt-0.5 shadow-[0_0_8px_rgba(46,98,255,0.4)]">
                      <div className="w-2 h-2 rounded-full bg-on-primary-container animate-pulse" />
                    </div>
                  )}
                  {s === 'failed' && (
                    <div className="w-6 h-6 rounded-full bg-surface border border-risk-high flex items-center justify-center shrink-0 mt-0.5">
                      <span className="material-symbols-outlined text-[12px] text-risk-high">close</span>
                    </div>
                  )}
                  {s === 'pending' && (
                    <div className="w-6 h-6 rounded-full bg-surface border border-border flex items-center justify-center shrink-0 mt-0.5">
                      <div className="w-1.5 h-1.5 rounded-full bg-outline" />
                    </div>
                  )}

                  {/* Text */}
                  <div className="min-w-0">
                    <p className={`text-label-sm font-label-sm uppercase mb-0.5 ${s === 'active' ? 'text-primary' : 'text-on-surface-variant'}`}>
                      {step.label}
                    </p>
                    <p className={`text-body-sm font-body-sm text-on-surface ${s === 'active' ? 'font-semibold' : ''}`}>
                      {step.title}
                    </p>
                    <p className="text-[10px] font-mono text-outline mt-0.5">{step.detail}</p>
                    {s === 'active' && (
                      <div className="mt-2 bg-surface p-2 rounded border border-border text-[10px] font-mono text-secondary-fixed leading-relaxed">
                        <span className="animate-pulse">▊</span> Running…
                      </div>
                    )}
                    {s === 'done' && i === 0 && (
                      <div className="mt-1 text-[10px] font-mono text-risk-low">
                        ✓ Accounts extracted
                      </div>
                    )}
                  </div>
                </div>
              )
            })}
          </div>
        </div>

        {/* No job state */}
        {!jobId && (
          <div className="mt-6 text-center">
            <span className="material-symbols-outlined text-outline text-[28px] block mb-2">upload_file</span>
            <p className="text-label-sm font-label-sm text-outline">Upload documents to start</p>
          </div>
        )}
      </div>
    </aside>
  )
}
