import { Link } from 'react-router-dom'
import Header from '../components/Header'
import Footer from '../components/Footer'

export default function DashboardPage() {
  return (
    <div className="bg-background text-on-surface min-h-screen flex flex-col overflow-x-hidden selection:bg-primary-container selection:text-on-primary-container">
      <Header />

      <div className="flex flex-1 max-w-container-max-width mx-auto w-full">
        {/* ── Sidebar ──────────────────────────────────────────────── */}
        <aside className="hidden lg:flex flex-col w-[240px] fixed h-[calc(100vh-72px)] bg-background border-r border-border py-6 px-4">
          <div className="flex flex-col gap-2">
            <span className="text-label-sm font-label-sm text-outline mb-2 px-2 uppercase">Core Modules</span>
            <Link
              to="/"
              className="flex items-center gap-3 px-3 py-2 rounded-lg bg-surface-container-high text-primary ai-glow"
            >
              <span className="material-symbols-outlined" style={{ fontVariationSettings: "'FILL' 1" }}>dashboard</span>
              <span className="text-label-md font-label-md">Executive Overview</span>
            </Link>
            <Link
              to="/analysis"
              className="flex items-center gap-3 px-3 py-2 rounded-lg text-on-surface-variant hover:bg-surface-container hover:text-on-surface transition-colors"
            >
              <span className="material-symbols-outlined">psychology</span>
              <span className="text-label-md font-label-md">AI Analysis</span>
            </Link>
          </div>

          <div className="mt-auto pt-6 border-t border-border flex flex-col gap-4">
            <button className="w-full flex items-center justify-center gap-2 bg-primary-container text-on-primary-container py-2.5 px-4 rounded-lg text-label-md font-label-md hover:opacity-90 transition-opacity">
              <span className="material-symbols-outlined">upload_file</span>
              Upload Document
            </button>
            <div className="bg-surface-container-low p-3 rounded-lg border border-border">
              <div className="flex items-center justify-between mb-2">
                <span className="text-label-sm font-label-sm text-outline">System Status</span>
                <div className="flex items-center gap-1 text-risk-low text-label-sm font-label-sm">
                  <span className="w-2 h-2 rounded-full bg-risk-low animate-pulse"></span>
                  Online
                </div>
              </div>
              <div className="text-label-sm font-label-sm text-on-surface-variant">Last model sync: 2m ago</div>
            </div>
          </div>
        </aside>

        {/* ── Main Content ─────────────────────────────────────────── */}
        <main className="flex-1 w-full lg:ml-[240px] p-margin-mobile md:p-margin-desktop space-y-gutter">
          {/* Mobile quick actions */}
          <div className="flex lg:hidden gap-2 overflow-x-auto pb-2">
            <button className="flex-shrink-0 flex items-center gap-2 bg-primary-container text-on-primary-container py-2 px-4 rounded-lg text-label-md font-label-md">
              <span className="material-symbols-outlined">upload_file</span>
              Upload
            </button>
            <button className="flex-shrink-0 flex items-center gap-2 border border-border bg-surface text-on-surface py-2 px-4 rounded-lg text-label-md font-label-md">
              <span className="material-symbols-outlined">summarize</span>
              Report
            </button>
          </div>

          {/* Page header */}
          <div className="flex flex-col md:flex-row justify-between items-start md:items-end gap-4">
            <div>
              <span className="text-primary font-label-md uppercase tracking-widest mb-1 block">Welcome, Alex</span>
              <h2 className="text-display-lg font-display-lg text-on-surface">Executive Dashboard</h2>
              <p className="text-body-lg font-body-lg text-on-surface-variant mt-1">Real-time intelligence and portfolio risk analysis.</p>
            </div>
            <div className="hidden md:flex gap-3">
              <button className="flex items-center gap-2 border border-border bg-surface text-on-surface py-2 px-4 rounded-lg text-label-md font-label-md hover:bg-surface-container transition-colors">
                <span className="material-symbols-outlined">assessment</span>
                Risk Assessment
              </button>
              <button className="flex items-center gap-2 border border-border bg-surface text-on-surface py-2 px-4 rounded-lg text-label-md font-label-md hover:bg-surface-container transition-colors">
                <span className="material-symbols-outlined">summarize</span>
                Generate Report
              </button>
            </div>
          </div>

          {/* Bento Grid */}
          <div className="grid grid-cols-1 md:grid-cols-12 gap-gutter">

            {/* AI Executive Summary — 8 cols */}
            <div className="md:col-span-8 glass-panel rounded-xl p-6 ai-gradient-border relative overflow-hidden group">
              <div className="absolute top-0 right-0 w-64 h-64 opacity-5 group-hover:opacity-10 transition-opacity pointer-events-none">
                <div className="w-full h-full rounded-full bg-gradient-to-br from-primary to-secondary blur-3xl" />
              </div>

              <div className="flex items-center justify-between mb-4">
                <div className="flex items-center gap-2">
                  <span className="material-symbols-outlined text-primary" style={{ fontVariationSettings: "'FILL' 1" }}>auto_awesome</span>
                  <span className="text-label-md font-label-md text-primary uppercase tracking-wider">AI Executive Summary</span>
                </div>
                <span className="text-[10px] font-medium px-2 py-0.5 rounded bg-primary/10 text-primary border border-primary/20 uppercase tracking-tighter font-label-sm">Live Analysis</span>
              </div>

              <p className="text-body-md font-body-md text-on-surface leading-relaxed mb-6">
                Portfolio analysis indicates <span className="text-primary font-medium">moderate financial risk</span> across the current period. Accounts Receivable shows a <span className="text-risk-high font-medium">+22.0% variance</span> above materiality threshold (Note 4), requiring immediate review. Overall liquidity position remains stable with cash equivalents up <span className="text-risk-low font-medium">+13.0%</span> YoY. Three NIIF compliance flags detected in PP&amp;E disclosure.
              </p>

              <div className="grid grid-cols-3 gap-4 border-t border-border pt-4">
                <div>
                  <span className="text-label-sm font-label-sm text-outline block mb-1 uppercase">Accounts Analyzed</span>
                  <span className="text-headline-md font-headline-md text-on-surface">48</span>
                  <span className="sparkline sparkline-positive ml-2 align-middle"></span>
                </div>
                <div>
                  <span className="text-label-sm font-label-sm text-outline block mb-1 uppercase">Anomalies Found</span>
                  <span className="text-headline-md font-headline-md text-risk-high">2</span>
                  <span className="sparkline sparkline-negative ml-2 align-middle"></span>
                </div>
                <div>
                  <span className="text-label-sm font-label-sm text-outline block mb-1 uppercase">NIIF Flags</span>
                  <span className="text-headline-md font-headline-md text-risk-medium">3</span>
                  <span className="sparkline ml-2 align-middle"></span>
                </div>
              </div>
            </div>

            {/* Enterprise Risk Score — 4 cols */}
            <div className="md:col-span-4 bg-surface border border-border rounded-xl p-6 flex flex-col items-center justify-center relative">
              <h3 className="text-label-md font-label-md text-outline absolute top-4 left-4 uppercase">Enterprise Risk Score</h3>
              <button className="absolute top-4 right-4 text-outline hover:text-on-surface transition-colors">
                <span className="material-symbols-outlined text-[18px]">more_vert</span>
              </button>

              <div className="relative w-48 h-48 mt-8 flex items-center justify-center">
                <svg className="w-full h-full -rotate-90" viewBox="0 0 100 100">
                  <circle cx="50" cy="50" fill="none" r="45" stroke="#282933" strokeWidth="8" />
                  <circle
                    cx="50" cy="50" fill="none" r="45"
                    stroke="#D29922"
                    strokeDasharray="283"
                    strokeDashoffset="100"
                    strokeWidth="8"
                    className="transition-all duration-1000 ease-out"
                  />
                </svg>
                <div className="absolute flex flex-col items-center justify-center text-center">
                  <span className="text-display-lg font-display-lg text-risk-medium leading-none">64</span>
                  <span className="text-label-md font-label-md text-on-surface-variant mt-1">/100</span>
                  <span className="text-label-sm font-label-sm text-risk-medium bg-risk-medium/10 px-2 py-0.5 rounded mt-2 border border-risk-medium/20">MODERATE</span>
                </div>
              </div>

              <div className="w-full mt-6 grid grid-cols-2 gap-4 border-t border-border pt-4">
                <div className="text-center">
                  <span className="block text-label-sm font-label-sm text-outline mb-1">vs Last Week</span>
                  <span className="text-body-md font-body-md text-risk-high flex items-center justify-center gap-1">
                    <span className="material-symbols-outlined text-[16px]">trending_up</span> +2 Pts
                  </span>
                </div>
                <div className="text-center border-l border-border">
                  <span className="block text-label-sm font-label-sm text-outline mb-1">Confidence</span>
                  <span className="text-body-md font-body-md text-primary flex items-center justify-center gap-1">
                    94%
                  </span>
                </div>
              </div>
            </div>

            {/* Recent Analyses — 8 cols */}
            <div className="md:col-span-8 bg-surface border border-border rounded-xl overflow-hidden">
              <div className="p-4 border-b border-border flex justify-between items-center bg-surface-container-low">
                <h3 className="text-body-md font-body-md font-semibold text-on-surface">Recent Analyses</h3>
                <Link to="/analysis" className="text-label-sm font-label-sm text-primary hover:text-primary/80 transition-colors flex items-center gap-1">
                  View All <span className="material-symbols-outlined text-[14px]">arrow_forward</span>
                </Link>
              </div>
              <div className="divide-y divide-border">
                {[
                  { name: 'BTG Pactual EEFF June 2025', status: 'completed', risk: 'MEDIUM', time: '2h ago', score: 64 },
                  { name: 'Rendición Cuentas Colombia Q2', status: 'processing', risk: 'LOW', time: '45m ago', score: null },
                  { name: 'Portfolio Risk Assessment Q1', status: 'completed', risk: 'LOW', time: '1d ago', score: 28 },
                ].map((item) => (
                  <div key={item.name} className="flex items-center justify-between px-4 py-3 hover:bg-surface-container-lowest/50 transition-colors">
                    <div className="flex items-center gap-3">
                      <span className={`material-symbols-outlined text-[18px] ${item.status === 'completed' ? 'text-risk-low' : 'text-primary animate-pulse'}`}>
                        {item.status === 'completed' ? 'check_circle' : 'autorenew'}
                      </span>
                      <div>
                        <p className="text-body-sm font-body-sm text-on-surface">{item.name}</p>
                        <p className="text-label-sm font-label-sm text-outline">{item.time}</p>
                      </div>
                    </div>
                    <div className="flex items-center gap-3">
                      {item.score !== null && (
                        <span className={`text-label-sm font-label-sm px-2 py-0.5 rounded border ${
                          item.risk === 'HIGH' ? 'text-risk-high bg-risk-high/10 border-risk-high/20' :
                          item.risk === 'MEDIUM' ? 'text-risk-medium bg-risk-medium/10 border-risk-medium/20' :
                          'text-risk-low bg-risk-low/10 border-risk-low/20'
                        }`}>{item.risk}</span>
                      )}
                      {item.status === 'processing' && (
                        <span className="text-label-sm font-label-sm text-primary bg-primary/10 border border-primary/20 px-2 py-0.5 rounded">Processing...</span>
                      )}
                      <button className="text-outline hover:text-on-surface transition-colors">
                        <span className="material-symbols-outlined text-[18px]">chevron_right</span>
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </div>

            {/* Financial Health — 4 cols */}
            <div className="md:col-span-4 bg-surface border border-border rounded-xl p-6">
              <h3 className="text-label-md font-label-md text-outline uppercase mb-4">Financial Health Indicators</h3>
              <div className="flex flex-col gap-4">
                {[
                  { label: 'Liquidity Ratio', value: '1.84', status: 'risk-low', trend: '+0.12' },
                  { label: 'Debt / Equity', value: '0.67', status: 'risk-low', trend: '-0.03' },
                  { label: 'Operating Margin', value: '18.2%', status: 'risk-medium', trend: '-1.4%' },
                  { label: 'AR Turnover', value: '4.1x', status: 'risk-high', trend: '-0.8x' },
                ].map((ind) => (
                  <div key={ind.label} className="flex items-center justify-between">
                    <span className="text-body-sm font-body-sm text-on-surface-variant">{ind.label}</span>
                    <div className="flex items-center gap-2">
                      <span className={`text-body-sm font-body-sm font-medium text-${ind.status}`}>{ind.value}</span>
                      <span className={`text-label-sm font-label-sm text-${ind.status}`}>{ind.trend}</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>

          </div>
        </main>
      </div>

      <Footer />
    </div>
  )
}
