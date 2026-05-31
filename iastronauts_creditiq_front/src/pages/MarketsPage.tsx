import { useState, useEffect, useCallback } from 'react'

const API = import.meta.env.VITE_API_URL || ''

// ── Types ─────────────────────────────────────────────────────────────────────────
interface MarketItem {
  key: string
  label: string
  ticker?: string
  base?: string
  currency?: string
  unit?: string
  value: number
  prev_close: number
  change_pct: number
  history: number[]
  ok: boolean
}

interface MarketData {
  as_of: string
  stocks: MarketItem[]
  fx: MarketItem[]
  indices: MarketItem[]
  commodities: MarketItem[]
}

// ── Sparkline ─────────────────────────────────────────────────────────────────────
function Sparkline({ points, color, width = 72, height = 28 }: { points: number[]; color: string; width?: number; height?: number }) {
  if (points.length < 2) return null
  const min = Math.min(...points)
  const max = Math.max(...points)
  const range = max - min || 1
  const step = width / (points.length - 1)
  const pts = points.map((v, i) => `${i * step},${height - ((v - min) / range) * height}`).join(' ')
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} style={{ display: 'block' }}>
      <polyline points={pts} fill="none" stroke={color} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

// ── Helpers ───────────────────────────────────────────────────────────────────────
function pctColor(chg: number) {
  if (chg > 0) return '#4ade80'
  if (chg < 0) return '#f87171'
  return '#94a3b8'
}
function pctArrow(chg: number) {
  if (chg > 0) return '▲'
  if (chg < 0) return '▼'
  return '—'
}
function fmtPrice(value: number, decimals = 2) {
  if (value >= 1000) return value.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 })
  return value.toLocaleString('en-US', { minimumFractionDigits: decimals, maximumFractionDigits: decimals })
}
function fmtPct(chg: number) {
  return `${chg >= 0 ? '+' : ''}${chg.toFixed(2)}%`
}
function relativeTime(iso: string) {
  const diff = Math.max(0, Date.now() - new Date(iso).getTime())
  const min = Math.round(diff / 60000)
  if (min < 1) return 'just now'
  if (min < 60) return `${min} min ago`
  return `${Math.round(min / 60)} h ago`
}

// ── Section header ────────────────────────────────────────────────────────────────
function SectionLabel({ children }: { children: string }) {
  return (
    <p style={{ margin: '0 0 14px', fontSize: 11, fontWeight: 700, letterSpacing: '0.1em', textTransform: 'uppercase', color: '#64748b' }}>
      {children}
    </p>
  )
}

// ── FX strip card ─────────────────────────────────────────────────────────────────
function FxCard({ item }: { item: MarketItem }) {
  const color = pctColor(item.change_pct)
  return (
    <div style={{
      flex: '1 1 160px',
      background: 'rgba(255,255,255,0.03)',
      border: '1px solid rgba(59,130,246,0.1)',
      borderRadius: 12,
      padding: '14px 18px',
      display: 'flex',
      flexDirection: 'column',
      gap: 6,
    }}>
      <p style={{ margin: 0, fontSize: 11, color: '#64748b', letterSpacing: '0.06em', textTransform: 'uppercase' }}>
        {item.label}
      </p>
      <p style={{ margin: 0, fontSize: 20, fontWeight: 700, color: '#f1f5f9', letterSpacing: '-0.02em' }}>
        {fmtPrice(item.value)}
      </p>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <p style={{ margin: 0, fontSize: 12, fontWeight: 600, color }}>
          {pctArrow(item.change_pct)} {fmtPct(item.change_pct)}
        </p>
        <Sparkline points={item.history} color={color} width={60} height={22} />
      </div>
    </div>
  )
}

// ── Stock card ────────────────────────────────────────────────────────────────────
function StockCard({ item }: { item: MarketItem }) {
  const color = pctColor(item.change_pct)
  return (
    <div style={{
      background: 'rgba(255,255,255,0.03)',
      border: '1px solid rgba(59,130,246,0.1)',
      borderRadius: 12,
      padding: '16px 18px',
      display: 'flex',
      flexDirection: 'column',
      gap: 8,
      transition: 'border-color 0.15s, background 0.15s',
      cursor: 'default',
    }}
      onMouseEnter={e => {
        const el = e.currentTarget as HTMLDivElement
        el.style.borderColor = 'rgba(59,130,246,0.22)'
        el.style.background = 'rgba(255,255,255,0.05)'
      }}
      onMouseLeave={e => {
        const el = e.currentTarget as HTMLDivElement
        el.style.borderColor = 'rgba(59,130,246,0.1)'
        el.style.background = 'rgba(255,255,255,0.03)'
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div>
          <p style={{ margin: 0, fontSize: 13, fontWeight: 600, color: '#e2e8f0' }}>{item.label}</p>
          <p style={{ margin: '2px 0 0', fontSize: 10, color: '#475569', letterSpacing: '0.04em' }}>{item.ticker}</p>
        </div>
        <span style={{
          fontSize: 11, fontWeight: 600, color,
          background: item.change_pct > 0 ? 'rgba(74,222,128,0.1)' : item.change_pct < 0 ? 'rgba(248,113,113,0.1)' : 'rgba(148,163,184,0.1)',
          padding: '2px 7px', borderRadius: 6,
        }}>
          {pctArrow(item.change_pct)} {fmtPct(item.change_pct)}
        </span>
      </div>
      <p style={{ margin: 0, fontSize: 22, fontWeight: 700, color: '#f1f5f9', letterSpacing: '-0.02em' }}>
        {fmtPrice(item.value)}
        <span style={{ fontSize: 11, fontWeight: 500, color: '#475569', marginLeft: 4 }}>COP</span>
      </p>
      <Sparkline points={item.history} color={color} width={80} height={28} />
    </div>
  )
}

// ── Compact row (indices + commodities) ───────────────────────────────────────────
function CompactRow({ item, suffix }: { item: MarketItem; suffix?: string }) {
  const color = pctColor(item.change_pct)
  return (
    <div style={{
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
      padding: '12px 18px',
      borderBottom: '1px solid rgba(59,130,246,0.06)',
      gap: 12,
    }}>
      <div style={{ flex: 1, minWidth: 0 }}>
        <p style={{ margin: 0, fontSize: 13, fontWeight: 600, color: '#e2e8f0' }}>{item.label}</p>
        {suffix && <p style={{ margin: '1px 0 0', fontSize: 10, color: '#475569' }}>{suffix}</p>}
      </div>
      <div style={{ textAlign: 'right', flexShrink: 0 }}>
        <p style={{ margin: 0, fontSize: 14, fontWeight: 700, color: '#f1f5f9' }}>{fmtPrice(item.value)}</p>
        <p style={{ margin: '2px 0 0', fontSize: 11, fontWeight: 600, color }}>{pctArrow(item.change_pct)} {fmtPct(item.change_pct)}</p>
      </div>
      <Sparkline points={item.history} color={color} width={56} height={22} />
    </div>
  )
}

function CompactCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{
      background: 'rgba(255,255,255,0.03)',
      border: '1px solid rgba(59,130,246,0.1)',
      borderRadius: 14,
      overflow: 'hidden',
    }}>
      <div style={{ padding: '14px 18px 10px', borderBottom: '1px solid rgba(59,130,246,0.07)' }}>
        <SectionLabel>{title}</SectionLabel>
      </div>
      {children}
    </div>
  )
}

// ── Skeleton loader ───────────────────────────────────────────────────────────────
function Skeleton({ width = '100%', height = 20, radius = 6 }: { width?: string | number; height?: number; radius?: number }) {
  return (
    <div style={{
      width, height, borderRadius: radius,
      background: 'linear-gradient(90deg, rgba(255,255,255,0.04) 25%, rgba(255,255,255,0.08) 50%, rgba(255,255,255,0.04) 75%)',
      backgroundSize: '200% 100%',
      animation: 'shimmer 1.4s infinite',
    }} />
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────────
export default function MarketsPage() {
  const [data, setData] = useState<MarketData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [lastUpdated, setLastUpdated] = useState<string>('')

  const load = useCallback(async (force = false) => {
    try {
      const url = API ? `${API}/market/data${force ? '?force=true' : ''}` : null
      if (!url) { setLoading(false); return }
      const res = await fetch(url)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const json: MarketData = await res.json()
      setData(json)
      setLastUpdated(json.as_of)
      setError(null)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
    const t = setInterval(() => load(), 300_000)
    return () => clearInterval(t)
  }, [load])

  const cardStyle: React.CSSProperties = {
    minHeight: '100vh',
    backgroundColor: '#06091a',
    color: '#e2e8f0',
    fontFamily: "'Inter', sans-serif",
    padding: '24px 32px 48px',
  }

  return (
    <div style={cardStyle}>
      <style>{`@keyframes shimmer { 0%{background-position:200% 0} 100%{background-position:-200% 0} }`}</style>

      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end', marginBottom: 24 }}>
        <div>
          <p style={{ margin: 0, fontSize: 22, fontWeight: 700, color: '#f1f5f9' }}>Markets</p>
          <p style={{ margin: '4px 0 0', fontSize: 13, color: '#64748b' }}>
            Live market data via Yahoo Finance
          </p>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          {lastUpdated && (
            <p style={{ margin: 0, fontSize: 12, color: '#475569' }}>
              Updated {relativeTime(lastUpdated)}
            </p>
          )}
          <button
            onClick={() => { setLoading(true); load(true) }}
            disabled={loading}
            style={{
              padding: '7px 14px', borderRadius: 8, fontSize: 12, fontWeight: 600,
              border: '1px solid rgba(59,130,246,0.2)',
              background: 'rgba(59,130,246,0.06)', color: '#94a3b8',
              cursor: loading ? 'not-allowed' : 'pointer', fontFamily: 'inherit',
              transition: 'all 0.15s',
            }}
          >
            {loading ? 'Loading...' : 'Refresh'}
          </button>
        </div>
      </div>

      {error && (
        <div style={{ marginBottom: 20, padding: '12px 16px', borderRadius: 10, background: 'rgba(248,113,113,0.08)', border: '1px solid rgba(248,113,113,0.2)', color: '#f87171', fontSize: 13 }}>
          Could not load market data: {error}. Make sure the backend is running.
        </div>
      )}

      {/* FX Strip */}
      <div style={{ marginBottom: 28 }}>
        <SectionLabel>FX — Colombian Peso</SectionLabel>
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
          {loading
            ? Array.from({ length: 5 }).map((_, i) => (
                <div key={i} style={{ flex: '1 1 160px', background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(59,130,246,0.1)', borderRadius: 12, padding: '14px 18px', display: 'flex', flexDirection: 'column', gap: 8 }}>
                  <Skeleton height={12} width="50%" />
                  <Skeleton height={24} width="70%" />
                  <Skeleton height={12} width="40%" />
                </div>
              ))
            : (data?.fx ?? []).filter(m => m.ok).map(m => <FxCard key={m.key} item={m} />)
          }
        </div>
      </div>

      {/* Colombian Stocks */}
      <div style={{ marginBottom: 28 }}>
        <SectionLabel>Colombian Stocks — BVC</SectionLabel>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: 12 }}>
          {loading
            ? Array.from({ length: 12 }).map((_, i) => (
                <div key={i} style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(59,130,246,0.1)', borderRadius: 12, padding: '16px 18px', display: 'flex', flexDirection: 'column', gap: 10 }}>
                  <Skeleton height={13} width="60%" />
                  <Skeleton height={26} width="75%" />
                  <Skeleton height={28} />
                </div>
              ))
            : (data?.stocks ?? []).filter(m => m.ok).map(m => <StockCard key={m.key} item={m} />)
          }
        </div>
      </div>

      {/* Indices + Commodities */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 20 }}>
        <CompactCard title="Global Indices">
          {loading
            ? Array.from({ length: 6 }).map((_, i) => (
                <div key={i} style={{ padding: '12px 18px', borderBottom: '1px solid rgba(59,130,246,0.06)', display: 'flex', gap: 12, alignItems: 'center' }}>
                  <Skeleton width="50%" height={13} />
                  <Skeleton width="30%" height={13} />
                </div>
              ))
            : (data?.indices ?? []).filter(m => m.ok).map(m => (
                <CompactRow key={m.key} item={m} suffix={m.currency} />
              ))
          }
        </CompactCard>

        <CompactCard title="Commodities">
          {loading
            ? Array.from({ length: 6 }).map((_, i) => (
                <div key={i} style={{ padding: '12px 18px', borderBottom: '1px solid rgba(59,130,246,0.06)', display: 'flex', gap: 12, alignItems: 'center' }}>
                  <Skeleton width="50%" height={13} />
                  <Skeleton width="30%" height={13} />
                </div>
              ))
            : (data?.commodities ?? []).filter(m => m.ok).map(m => (
                <CompactRow key={m.key} item={m} suffix={m.unit} />
              ))
          }
        </CompactCard>
      </div>

      {/* Footer */}
      <p style={{ marginTop: 24, textAlign: 'center', fontSize: 11, color: '#334155' }}>
        Data from Yahoo Finance · refreshes every 5 minutes · Colombian stocks prices in COP
      </p>
    </div>
  )
}
