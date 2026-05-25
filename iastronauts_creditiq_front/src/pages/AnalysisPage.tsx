import { useState } from 'react'
import Header from '../components/Header'
import Footer from '../components/Footer'

const steps = [
  {
    id: 1,
    label: 'Step 1',
    title: 'Document Ingestion & OCR',
    detail: 'Processed 45 pages in 1.2s',
    status: 'done',
  },
  {
    id: 2,
    label: 'Step 2',
    title: 'NIIF Policy Extraction',
    detail: 'Identified 12 key accounting policies',
    status: 'done',
  },
  {
    id: 3,
    label: 'Step 3 (Active)',
    title: 'Materiality Analysis',
    detail: null,
    status: 'active',
  },
  {
    id: 4,
    label: 'Step 4',
    title: 'Final Risk Synthesis',
    detail: null,
    status: 'pending',
  },
]

const tableRows = [
  {
    account: 'Cash and Cash Equivalents',
    current: '124.5',
    prior: '110.2',
    variance: '+13.0%',
    varClass: 'text-risk-low',
    note: 'Note 3',
    statusLabel: 'Verified',
    statusClass: 'bg-risk-low/10 text-risk-low border-risk-low/20',
    rowClass: '',
  },
  {
    account: 'Accounts Receivable',
    current: '342.1',
    prior: '280.4',
    variance: '+22.0%',
    varClass: 'text-risk-high',
    note: 'Note 4',
    statusLabel: 'Anomaly',
    statusClass: 'bg-risk-high/10 text-risk-high border-risk-high/20',
    rowClass: 'bg-risk-high/5 border-l-2 border-l-risk-high',
  },
  {
    account: 'Inventory',
    current: '89.4',
    prior: '92.1',
    variance: '-2.9%',
    varClass: 'text-outline',
    note: 'Note 5',
    statusLabel: 'Verified',
    statusClass: 'bg-risk-low/10 text-risk-low border-risk-low/20',
    rowClass: '',
  },
  {
    account: 'Property, Plant & Equipment',
    current: '850.0',
    prior: '790.0',
    variance: '+7.6%',
    varClass: 'text-risk-medium',
    note: 'Note 7',
    statusLabel: 'Review',
    statusClass: 'bg-risk-medium/10 text-risk-medium border-risk-medium/20',
    rowClass: 'bg-risk-medium/5 border-l-2 border-l-risk-medium',
  },
]

export default function AnalysisPage() {
  const [chatInput, setChatInput] = useState('')

  return (
    <div className="bg-background text-on-surface font-body-md text-body-md antialiased min-h-screen flex flex-col selection:bg-primary-container selection:text-on-primary-container">
      <Header />

      <main className="flex-grow w-full px-margin-mobile md:px-margin-desktop py-6 max-w-container-max-width mx-auto flex flex-col md:flex-row gap-gutter">

        {/* ── Left: AI Reasoning Panel ──────────────────────────────── */}
        <aside className="w-full md:w-[320px] flex-shrink-0 flex flex-col gap-4">
          <div className="surface surface-container-low border border-border rounded p-4 h-full glass-panel ai-glow">
            <div className="flex items-center justify-between mb-6 pb-4 border-b border-border">
              <h2 className="text-label-md font-label-md text-primary uppercase tracking-wider">AI Reasoning Pipeline</h2>
              <span className="material-symbols-outlined text-primary text-[18px]">memory</span>
            </div>

            <div className="relative">
              {/* Vertical connector line */}
              <div className="absolute left-3 top-2 bottom-2 w-[1px] bg-border"></div>

              <div className="flex flex-col gap-6 relative z-10">
                {steps.map((step) => (
                  <div key={step.id} className={`flex gap-4 items-start ${step.status === 'pending' ? 'opacity-50' : ''}`}>
                    {/* Step indicator */}
                    {step.status === 'done' && (
                      <div className="w-6 h-6 rounded-full bg-surface border border-border flex items-center justify-center shrink-0 mt-0.5">
                        <span className="material-symbols-outlined text-[12px] text-risk-low">check</span>
                      </div>
                    )}
                    {step.status === 'active' && (
                      <div className="w-6 h-6 rounded-full bg-primary-container border border-primary flex items-center justify-center shrink-0 mt-0.5 shadow-[0_0_8px_rgba(46,98,255,0.4)]">
                        <div className="w-2 h-2 rounded-full bg-on-primary-container animate-pulse"></div>
                      </div>
                    )}
                    {step.status === 'pending' && (
                      <div className="w-6 h-6 rounded-full bg-surface border border-border flex items-center justify-center shrink-0 mt-0.5">
                        <div className="w-1.5 h-1.5 rounded-full bg-outline"></div>
                      </div>
                    )}

                    <div>
                      <p className={`text-label-sm font-label-sm uppercase mb-1 ${step.status === 'active' ? 'text-primary' : 'text-on-surface-variant'}`}>
                        {step.label}
                      </p>
                      <p className={`text-body-sm font-body-sm text-on-surface ${step.status === 'active' ? 'font-medium' : ''}`}>
                        {step.title}
                      </p>
                      {step.detail && (
                        <p className="text-label-sm font-label-sm text-outline mt-1">{step.detail}</p>
                      )}
                      {step.status === 'active' && (
                        <div className="mt-2 bg-surface p-2 rounded border border-border text-label-sm font-label-sm font-mono text-secondary-fixed">
                          &gt; Analyzing variance in Note 4...<br />
                          &gt; Cross-referencing prior year...<br />
                          &gt; Anomaly detected.
                        </div>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </aside>

        {/* ── Right: High-Density Canvas ────────────────────────────── */}
        <div className="flex-1 flex flex-col gap-6 overflow-hidden">

          {/* KPI Row */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {/* KPI: Data Confidence */}
            <div className="bg-surface border border-border rounded p-4 relative overflow-hidden group">
              <div className="absolute inset-0 bg-gradient-to-br from-primary/5 to-transparent opacity-0 group-hover:opacity-100 transition-opacity"></div>
              <div className="flex justify-between items-start mb-2">
                <span className="text-label-sm font-label-sm text-on-surface-variant uppercase">Overall Data Confidence</span>
                <span className="material-symbols-outlined text-[16px] text-primary">verified_user</span>
              </div>
              <div className="flex items-baseline gap-2">
                <span className="text-headline-lg font-headline-lg text-on-surface">98.4%</span>
                <span className="text-label-sm font-label-sm text-risk-low flex items-center">
                  <span className="material-symbols-outlined text-[14px]">arrow_upward</span> 1.2%
                </span>
              </div>
            </div>

            {/* KPI: Anomalies */}
            <div className="bg-surface border border-risk-high/30 rounded p-4 relative overflow-hidden bg-gradient-to-r from-risk-high/10 to-transparent">
              <div className="flex justify-between items-start mb-2">
                <span className="text-label-sm font-label-sm text-risk-high uppercase">High Risk Anomalies</span>
                <span className="material-symbols-outlined text-[16px] text-risk-high">warning</span>
              </div>
              <div className="flex items-baseline gap-2">
                <span className="text-headline-lg font-headline-lg text-on-surface">2</span>
                <span className="text-label-sm font-label-sm text-on-surface-variant">Requires Review</span>
              </div>
            </div>

            {/* KPI: Materiality */}
            <div className="bg-surface border border-border rounded p-4">
              <div className="flex justify-between items-start mb-2">
                <span className="text-label-sm font-label-sm text-on-surface-variant uppercase">Materiality Threshold</span>
                <span className="material-symbols-outlined text-[16px] text-outline">tune</span>
              </div>
              <div className="flex items-baseline gap-2">
                <span className="text-headline-lg font-headline-lg text-on-surface">$5.2M</span>
                <span className="text-label-sm font-label-sm text-outline">Base: Total Assets</span>
              </div>
            </div>
          </div>

          {/* Financial Comparison Table */}
          <div className="bg-surface border border-border rounded flex flex-col flex-1 min-h-[300px]">
            <div className="p-4 border-b border-border flex justify-between items-center bg-surface-container-low rounded-t">
              <h3 className="text-body-md font-body-md font-semibold text-on-surface">Financial Comparison &amp; NIIF Variance</h3>
              <div className="flex gap-2">
                <button className="px-3 py-1 text-label-sm font-label-sm border border-border rounded hover:bg-surface-container text-on-surface-variant transition-colors flex items-center gap-1">
                  <span className="material-symbols-outlined text-[14px]">filter_list</span> Filter
                </button>
                <button className="px-3 py-1 text-label-sm font-label-sm bg-primary text-on-primary rounded hover:opacity-90 transition-opacity">
                  Export CSV
                </button>
              </div>
            </div>
            <div className="overflow-x-auto flex-1">
              <table className="w-full text-left whitespace-nowrap">
                <thead className="bg-surface-container-lowest border-b border-border text-label-sm font-label-sm text-on-surface-variant uppercase">
                  <tr>
                    <th className="py-3 px-4 font-medium">Account Description</th>
                    <th className="py-3 px-4 font-medium text-right">Current Period (M)</th>
                    <th className="py-3 px-4 font-medium text-right">Prior Year (M)</th>
                    <th className="py-3 px-4 font-medium text-right">Variance</th>
                    <th className="py-3 px-4 font-medium">NIIF Note</th>
                    <th className="py-3 px-4 font-medium text-center">AI Status</th>
                  </tr>
                </thead>
                <tbody className="text-body-sm font-body-sm font-mono text-on-surface divide-y divide-border">
                  {tableRows.map((row) => (
                    <tr key={row.account} className={`hover:bg-surface-container-lowest/50 transition-colors ${row.rowClass}`}>
                      <td className="py-3 px-4 font-sans">{row.account}</td>
                      <td className="py-3 px-4 text-right">{row.current}</td>
                      <td className="py-3 px-4 text-right">{row.prior}</td>
                      <td className={`py-3 px-4 text-right ${row.varClass}`}>{row.variance}</td>
                      <td className="py-3 px-4 text-outline">{row.note}</td>
                      <td className="py-3 px-4 text-center">
                        <span className={`inline-flex items-center px-2 py-0.5 rounded text-[10px] font-medium border ${row.statusClass}`}>
                          {row.statusLabel}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {/* AI Analysis Chat */}
          <div className="bg-surface border border-border rounded flex flex-col h-[400px] shadow-sm overflow-hidden">
            {/* Chat header */}
            <div className="px-4 py-3 border-b border-border bg-surface-container-low flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="material-symbols-outlined text-primary text-[18px]">smart_toy</span>
                <h4 className="text-label-md font-label-md text-on-surface uppercase tracking-wider">AI Analysis Assistant</h4>
              </div>
              <span className="text-[10px] font-medium px-2 py-0.5 rounded bg-primary/10 text-primary border border-primary/20 uppercase tracking-tighter">NIIF-LLM Active</span>
            </div>

            {/* Messages */}
            <div className="flex-1 overflow-y-auto p-4 space-y-4 bg-background/50">
              <div className="flex flex-col gap-1 max-w-[80%]">
                <p className="text-label-sm font-label-sm text-outline px-1">CreditIQ AI</p>
                <div className="bg-surface-container-highest p-3 rounded-xl rounded-tl-none border border-border">
                  <p className="text-body-sm text-on-surface">
                    I've completed the initial materiality analysis. The 22% variance in Accounts Receivable (Note 4) appears anomalous compared to historical trends. Would you like me to drill down into the specific transactions?
                  </p>
                </div>
              </div>
              <div className="flex flex-col gap-1 items-end ml-auto max-w-[80%]">
                <p className="text-label-sm font-label-sm text-outline px-1">Analyst</p>
                <div className="bg-primary-container/20 p-3 rounded-xl rounded-tr-none border border-primary/30">
                  <p className="text-body-sm text-on-surface">
                    Yes, please summarize the top 5 transactions contributing to this variance.
                  </p>
                </div>
              </div>
            </div>

            {/* Chat input */}
            <div className="p-4 border-t border-border bg-surface">
              <div className="relative flex items-center">
                <span className="material-symbols-outlined absolute left-3 text-primary text-[20px]">auto_awesome</span>
                <input
                  type="text"
                  value={chatInput}
                  onChange={(e) => setChatInput(e.target.value)}
                  className="w-full bg-surface-container-lowest border border-border rounded-full py-3 pl-10 pr-12 text-body-sm focus:border-primary focus:ring-1 focus:ring-primary transition-all outline-none placeholder:text-outline/50"
                  placeholder="Ask about the analysis or request specific data deep-dives..."
                />
                <button className="absolute right-2 p-2 text-primary hover:bg-primary/10 rounded-full transition-colors">
                  <span className="material-symbols-outlined">send</span>
                </button>
              </div>
              <div className="mt-2 flex gap-3 px-1">
                <button className="text-[10px] text-outline-variant hover:text-primary transition-colors"># VarianceDetails</button>
                <button className="text-[10px] text-outline-variant hover:text-primary transition-colors"># Note4Review</button>
                <button className="text-[10px] text-outline-variant hover:text-primary transition-colors"># PolicyCheck</button>
              </div>
            </div>
          </div>

        </div>
      </main>

      <Footer />
    </div>
  )
}
