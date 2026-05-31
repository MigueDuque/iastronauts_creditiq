import { useState, useEffect, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import EarthIntelligenceCard from '../components/EarthIntelligenceCard'

/* ─── tiny sparkline component ─────────────────────────────────────────────── */
function Sparkline({
  points,
  color,
  width = 80,
  height = 28,
}: {
  points: number[]
  color: string
  width?: number
  height?: number
}) {
  if (points.length < 2) return null
  const min = Math.min(...points)
  const max = Math.max(...points)
  const range = max - min || 1
  const step = width / (points.length - 1)
  const pts = points
    .map((v, i) => `${i * step},${height - ((v - min) / range) * height}`)
    .join(' ')
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} style={{ display: 'block' }}>
      <polyline points={pts} fill="none" stroke={color} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

/* ─── data ──────────────────────────────────────────────────────────────────── */
const MARKET_METRICS = [
  {
    label: 'USD / COP',
    value: '3,895.50',
    change: '+0.62%',
    sign: 'up' as const,
    color: '#4ade80',
    points: [10, 12, 9, 14, 11, 16, 14, 18, 15, 17],
  },
  {
    label: 'TES 10Y',
    value: '10.15%',
    change: '+0.18%',
    sign: 'up' as const,
    color: '#f87171',
    points: [14, 13, 15, 12, 16, 14, 17, 13, 15, 16],
  },
  {
    label: 'COLCAP',
    value: '1,377.42',
    change: '+0.71%',
    sign: 'up' as const,
    color: '#4ade80',
    points: [10, 11, 13, 12, 15, 14, 16, 15, 17, 18],
  },
  {
    label: 'Inflación (COL)',
    value: '5.16%',
    change: '-0.08%',
    sign: 'down' as const,
    color: '#f87171',
    points: [18, 17, 18, 16, 17, 15, 16, 14, 15, 13],
  },
  {
    label: 'Tasa BanRep ↑',
    value: '11.75%',
    change: '0.00%',
    sign: 'stable' as const,
    color: '#94a3b8',
    points: [12, 12, 12, 12, 12, 12, 12, 12, 12, 12],
  },
]

const AI_SIGNALS = [
  {
    icon: (
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
        <polyline points="22 7 13.5 15.5 8.5 10.5 2 17" />
        <polyline points="16 7 22 7 22 13" />
      </svg>
    ),
    bg: 'rgba(251, 146, 60, 0.15)',
    color: '#fb923c',
    title: 'Fixed Income Pressure',
    desc: 'Treasury yields continue increasing across LATAM markets, impacting duration sensitive portfolios.',
    cta: 'View insight →',
  },
  {
    icon: (
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
        <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
      </svg>
    ),
    bg: 'rgba(34, 197, 94, 0.15)',
    color: '#22c55e',
    title: 'Banking Sector Stability',
    desc: 'Colombian banks maintain strong liquidity positions and solid capitalization ratios.',
    cta: 'View insight →',
  },
  {
    icon: (
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
        <circle cx="12" cy="12" r="3" />
        <path d="M12 1v4M12 19v4M4.22 4.22l2.83 2.83M16.95 16.95l2.83 2.83M1 12h4M19 12h4M4.22 19.78l2.83-2.83M16.95 7.05l2.83-2.83" />
      </svg>
    ),
    bg: 'rgba(99, 102, 241, 0.18)',
    color: '#818cf8',
    title: 'AI Market Insight',
    desc: 'Investor sentiment improved after latest inflation data, but remains cautious on global outlook.',
    cta: 'View insight →',
  },
]

/* ─── AI signal icons by impact category ─────────────────────────────────────── */
// Backend returns a `category` (Monetary Policy | Fixed Income | Global Outlook);
// the JSX icon + accent colors live here on the client (they can't cross the wire).
const TREND_ICON = (
  <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <polyline points="22 7 13.5 15.5 8.5 10.5 2 17" />
    <polyline points="16 7 22 7 22 13" />
  </svg>
)
const SHIELD_ICON = (
  <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
  </svg>
)
const GLOBE_ICON = (
  <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="12" cy="12" r="3" />
    <path d="M12 1v4M12 19v4M4.22 4.22l2.83 2.83M16.95 16.95l2.83 2.83M1 12h4M19 12h4M4.22 19.78l2.83-2.83M16.95 7.05l2.83-2.83" />
  </svg>
)

const SIGNAL_STYLE_BY_CATEGORY: Record<string, { icon: ReactNode; bg: string; color: string }> = {
  'Fixed Income': { icon: TREND_ICON, bg: 'rgba(251, 146, 60, 0.15)', color: '#fb923c' },
  'Monetary Policy': { icon: SHIELD_ICON, bg: 'rgba(34, 197, 94, 0.15)', color: '#22c55e' },
  'Global Outlook': { icon: GLOBE_ICON, bg: 'rgba(99, 102, 241, 0.18)', color: '#818cf8' },
}

interface Signal {
  icon: ReactNode
  bg: string
  color: string
  title: string
  desc: string
  cta: string
}

/** Map a backend pulse signal onto a render-ready card (injects icon by category). */
function mapSignal(raw: unknown): Signal | null {
  if (!raw || typeof raw !== 'object') return null
  const o = raw as Record<string, unknown>
  const title = o.title as string | undefined
  if (!title) return null
  const category = (o.category as string) ?? 'Global Outlook'
  const style = SIGNAL_STYLE_BY_CATEGORY[category] ?? SIGNAL_STYLE_BY_CATEGORY['Global Outlook']
  return {
    ...style,
    title,
    desc: (o.desc as string) ?? '',
    cta: 'View insight →',
  }
}

interface MarketMetric {
  label: string
  value: string
  change: string
  sign: 'up' | 'down' | 'stable'
  color: string
  points: number[]
}

/** Map a backend overview card onto our MarketMetric shape (already near-identical). */
function mapMetric(raw: unknown): MarketMetric | null {
  if (!raw || typeof raw !== 'object') return null
  const o = raw as Record<string, unknown>
  if (!o.label) return null
  const sign = o.sign === 'up' || o.sign === 'down' ? o.sign : 'stable'
  return {
    label: String(o.label),
    value: String(o.value ?? ''),
    change: String(o.change ?? ''),
    sign,
    color: (o.color as string) ?? '#94a3b8',
    points: Array.isArray(o.points) ? (o.points as number[]) : [],
  }
}

interface NewsItem {
  thumb?: string
  title: string
  sub: string
  time: string
  url?: string
}

// Shown until live GNews data arrives (or if the request fails / returns empty).
const FALLBACK_NEWS: NewsItem[] = [
  {
    thumb: 'https://images.unsplash.com/photo-1486406146926-c627a92ad1ab?w=80&h=60&fit=crop',
    title: 'BanRep mantiene tasa de interés en 11.75%',
    sub: 'Impacto: Política monetaria',
    time: '2 min ago',
  },
  {
    thumb: 'https://images.unsplash.com/photo-1611974789855-9c2a0a7236a3?w=80&h=60&fit=crop',
    title: 'TES colombianos suben tras datos de inflación',
    sub: 'Impacto: Renta fija local',
    time: '28 min ago',
  },
  {
    thumb: 'https://images.unsplash.com/photo-1526304640581-d334cdbbf45e?w=80&h=60&fit=crop',
    title: 'Mercados emergentes muestran recuperación moderada',
    sub: 'Impacto: Flujos de capital',
    time: '1 h ago',
  },
]

const API = import.meta.env.VITE_API_URL || ''

/* ─── helpers ───────────────────────────────────────────────────────────────── */

/** Relative "x min ago" label from an ISO/parseable timestamp; '' if unusable. */
function relativeTime(iso?: string): string {
  if (!iso) return ''
  const t = new Date(iso).getTime()
  if (Number.isNaN(t)) return ''
  const diff = Math.max(0, Date.now() - t)
  const min = Math.round(diff / 60_000)
  if (min < 1) return 'just now'
  if (min < 60) return `${min} min ago`
  const hr = Math.round(min / 60)
  if (hr < 24) return `${hr} h ago`
  return `${Math.round(hr / 24)} d ago`
}

/** Map a loosely-shaped GNews/backend article onto our NewsItem. */
function normalizeNews(raw: unknown): NewsItem | null {
  if (!raw || typeof raw !== 'object') return null
  const o = raw as Record<string, unknown>
  const title = (o.title ?? o.headline) as string | undefined
  if (!title) return null
  const source = (o.source ?? o.publisher) as string | undefined
  const impact = (o.impact ?? o.summary ?? o.description ?? source) as string | undefined
  const published = (o.publishedAt ?? o.published_at ?? o.date) as string | undefined
  return {
    title,
    sub: impact ? String(impact).slice(0, 90) : '',
    time: relativeTime(published) || (typeof o.time === 'string' ? o.time : ''),
    thumb: (o.thumb ?? o.image ?? o.image_url ?? o.urlToImage) as string | undefined,
    url: (o.url ?? o.link) as string | undefined,
  }
}

function formatDate(d: Date) {
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
}
function formatTime(d: Date) {
  return d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: true })
}

/* ─── component ─────────────────────────────────────────────────────────────── */
export default function DashboardPage() {
  const [now, setNow] = useState(new Date())
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 60_000)
    return () => clearInterval(t)
  }, [])

  // Live Market Pulse: one /market/pulse call drives all three cards (overview,
  // AI signals, news). The hardcoded arrays remain the fallback when no endpoint
  // is configured, the request fails, or the backend has no cached pulse yet.
  const [overview, setOverview] = useState<MarketMetric[]>(MARKET_METRICS)
  const [signals, setSignals] = useState<Signal[]>(AI_SIGNALS)
  const [news, setNews] = useState<NewsItem[]>(FALLBACK_NEWS)
  useEffect(() => {
    if (!API) return
    let alive = true
    const load = async () => {
      try {
        const res = await fetch(`${API}/market/pulse`)
        // 503 = no cached pulse yet → keep showing fallback data.
        if (!res.ok || !alive) return
        const data = await res.json()

        const metrics = (Array.isArray(data?.overview) ? data.overview : [])
          .map(mapMetric).filter((m: MarketMetric | null): m is MarketMetric => m !== null)
        if (alive && metrics.length) setOverview(metrics)

        const sigs = (Array.isArray(data?.signals) ? data.signals : [])
          .map(mapSignal).filter((s: Signal | null): s is Signal => s !== null)
        if (alive && sigs.length) setSignals(sigs)

        const rawNews: unknown[] = Array.isArray(data?.news) ? data.news : []
        const items = rawNews.map(normalizeNews).filter((n): n is NewsItem => n !== null).slice(0, 3)
        if (alive && items.length) setNews(items)
      } catch (e) {
        console.error('[pulse]', e)
      }
    }
    load()
    const t = setInterval(load, 300_000) // refresh every 5 min
    return () => { alive = false; clearInterval(t) }
  }, [])

  return (
    <div
      style={{
        minHeight: '100vh',
        backgroundColor: '#06091a',
        color: '#e2e8f0',
        fontFamily: "'Inter', sans-serif",
        padding: '0 0 40px',
      }}
    >
      {/* ── TOP BAR ─────────────────────────────────────────────────────────── */}
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          padding: '20px 32px 0',
        }}
      >
        <div>
          <p style={{ fontSize: 18, fontWeight: 700, color: '#f1f5f9', margin: 0 }}>
            Good morning, IASTRONAUTS 👋
          </p>
          <p style={{ fontSize: 13, color: '#64748b', margin: '3px 0 0' }}>
            Here's your financial pulse for today.
          </p>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 20 }}>
          <span style={{ fontSize: 13, color: '#94a3b8' }}>
            {formatDate(now)}&nbsp;&nbsp;{formatTime(now)}
          </span>
          {/* Bell */}
          <div style={{ position: 'relative' }}>
            <button
              style={{
                background: 'transparent',
                border: 'none',
                cursor: 'pointer',
                padding: 6,
                color: '#94a3b8',
                display: 'flex',
              }}
            >
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" />
                <path d="M13.73 21a2 2 0 0 1-3.46 0" />
              </svg>
            </button>
            <span
              style={{
                position: 'absolute',
                top: 2,
                right: 2,
                width: 8,
                height: 8,
                borderRadius: '50%',
                backgroundColor: '#3b82f6',
                border: '2px solid #06091a',
              }}
            />
          </div>
        </div>
      </div>

      {/* ── HERO CARD (Earth-anchored financial command center) ─────────────── */}
      <div style={{ padding: '20px 32px 0' }}>
        <EarthIntelligenceCard
          eyebrow="AI MARKET PULSE"
          title="Global markets remain cautious as"
          highlight="interest rates stay elevated."
          subtitle="Colombian fixed-income funds may experience moderate pressure during the next quarter due to persistent inflation and treasury yield volatility."
          metrics={[
            { icon: '🏦', label: 'BanRep rate', sub: 'Unchanged at 11.75%', accent: 'rgba(251,146,60,0.28)' },
            { icon: '📈', label: 'COLCAP Index', sub: '+0.71% this week', accent: 'rgba(34,197,94,0.28)' },
            { icon: '⚡', label: 'USD/COP Volatility', sub: 'Increasing', accent: 'rgba(239,68,68,0.28)' },
          ]}
        />
      </div>

      {/* ── MARKET OVERVIEW STRIP ────────────────────────────────────────────── */}
      <div style={{ padding: '20px 32px 0' }}>
        <div
          style={{
            background: 'rgba(255,255,255,0.03)',
            border: '1px solid rgba(59,130,246,0.1)',
            borderRadius: 14,
            padding: '16px 0',
          }}
        >
          {/* Title row */}
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '0 28px 14px', borderBottom: '1px solid rgba(59,130,246,0.07)' }}>
            <span style={{ fontSize: 11, fontWeight: 600, letterSpacing: '0.1em', textTransform: 'uppercase', color: '#64748b' }}>
              MARKET OVERVIEW
            </span>
            <Link to="/analysis" style={{ fontSize: 12, color: '#38bdf8', textDecoration: 'none' }}>
              View all markets →
            </Link>
          </div>

          {/* Metric columns */}
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(5, 1fr)',
              gap: 0,
            }}
          >
            {overview.map((m, i) => (
              <div
                key={m.label}
                style={{
                  padding: '16px 28px',
                  borderRight: i < overview.length - 1 ? '1px solid rgba(59,130,246,0.07)' : 'none',
                }}
              >
                <p style={{ margin: '0 0 4px', fontSize: 11, color: '#64748b', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                  {m.label}
                </p>
                <p style={{ margin: '0 0 4px', fontSize: 22, fontWeight: 700, color: '#f1f5f9', letterSpacing: '-0.02em' }}>
                  {m.value}
                </p>
                <p
                  style={{
                    margin: '0 0 8px',
                    fontSize: 12,
                    fontWeight: 600,
                    color: m.sign === 'up' ? '#4ade80' : m.sign === 'down' ? '#f87171' : '#94a3b8',
                  }}
                >
                  {m.sign === 'up' ? '▲' : m.sign === 'down' ? '▼' : '—'} {m.change}
                </p>
                <Sparkline points={m.points} color={m.color} />
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* ── AI SIGNALS + NEWS ────────────────────────────────────────────────── */}
      <div style={{ padding: '20px 32px 0', display: 'grid', gridTemplateColumns: '1fr 340px', gap: 20, alignItems: 'stretch' }}>
        {/* AI SIGNALS */}
        <div style={{ display: 'flex', flexDirection: 'column' }}>
          {/* Header */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16 }}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#38bdf8" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="12" cy="12" r="3" />
              <path d="M12 1v4M12 19v4M4.22 4.22l2.83 2.83M16.95 16.95l2.83 2.83M1 12h4M19 12h4M4.22 19.78l2.83-2.83M16.95 7.05l2.83-2.83" />
            </svg>
            <span style={{ fontSize: 12, fontWeight: 700, letterSpacing: '0.1em', textTransform: 'uppercase', color: '#94a3b8' }}>
              AI SIGNALS
            </span>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 16, flex: 1 }}>
            {signals.map((s) => (
              <div
                key={s.title}
                style={{
                  background: 'rgba(255,255,255,0.03)',
                  border: '1px solid rgba(59,130,246,0.1)',
                  borderRadius: 14,
                  padding: '22px 20px',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 12,
                  transition: 'border-color 0.2s, background 0.2s',
                  cursor: 'default',
                }}
                onMouseEnter={(e) => {
                  (e.currentTarget as HTMLDivElement).style.borderColor = 'rgba(59,130,246,0.22)'
                    ; (e.currentTarget as HTMLDivElement).style.background = 'rgba(255,255,255,0.05)'
                }}
                onMouseLeave={(e) => {
                  (e.currentTarget as HTMLDivElement).style.borderColor = 'rgba(59,130,246,0.1)'
                    ; (e.currentTarget as HTMLDivElement).style.background = 'rgba(255,255,255,0.03)'
                }}
              >
                {/* icon */}
                <div
                  style={{
                    width: 44,
                    height: 44,
                    borderRadius: 12,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    background: s.bg,
                    color: s.color,
                  }}
                >
                  {s.icon}
                </div>
                <h3 style={{ margin: 0, fontSize: 14, fontWeight: 700, color: '#f1f5f9', lineHeight: 1.3 }}>
                  {s.title}
                </h3>
                <p style={{ margin: 0, fontSize: 12.5, color: '#94a3b8', lineHeight: 1.6, flex: 1 }}>
                  {s.desc}
                </p>
                <button
                  style={{
                    background: 'transparent',
                    border: 'none',
                    padding: 0,
                    cursor: 'pointer',
                    fontSize: 12,
                    fontWeight: 600,
                    color: '#fb923c',
                    textAlign: 'left',
                    fontFamily: 'inherit',
                  }}
                >
                  {s.cta}
                </button>
              </div>
            ))}
          </div>
        </div>

        {/* RELEVANT NEWS */}
        <div style={{ display: 'flex', flexDirection: 'column' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16 }}>
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#38bdf8" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <rect x="3" y="3" width="18" height="18" rx="2" ry="2" />
              <line x1="3" y1="9" x2="21" y2="9" />
              <line x1="9" y1="21" x2="9" y2="9" />
            </svg>
            <span style={{ fontSize: 12, fontWeight: 700, letterSpacing: '0.1em', textTransform: 'uppercase', color: '#94a3b8' }}>
              RELEVANT NEWS
            </span>
          </div>

          <div
            style={{
              background: 'rgba(255,255,255,0.03)',
              border: '1px solid rgba(59,130,246,0.1)',
              borderRadius: 14,
              overflow: 'hidden',
              flex: 1,
              display: 'flex',
              flexDirection: 'column',
            }}
          >
            {news.map((n, i) => (
              <div
                key={n.title}
                onClick={() => n.url && window.open(n.url, '_blank', 'noopener,noreferrer')}
                style={{
                  display: 'flex',
                  gap: 12,
                  padding: '16px 18px',
                  borderBottom: i < news.length - 1 ? '1px solid rgba(59,130,246,0.07)' : 'none',
                  cursor: 'pointer',
                  transition: 'background 0.2s',
                }}
                onMouseEnter={(e) => {
                  (e.currentTarget as HTMLDivElement).style.background = 'rgba(255,255,255,0.03)'
                }}
                onMouseLeave={(e) => {
                  (e.currentTarget as HTMLDivElement).style.background = 'transparent'
                }}
              >
                {/* thumbnail */}
                <img
                  src={n.thumb}
                  alt=""
                  style={{
                    width: 68,
                    height: 52,
                    borderRadius: 8,
                    objectFit: 'cover',
                    flexShrink: 0,
                    background: '#1e293b',
                  }}
                  onError={(e) => {
                    ; (e.target as HTMLImageElement).style.display = 'none'
                  }}
                />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <p
                    style={{
                      margin: '0 0 4px',
                      fontSize: 13,
                      fontWeight: 600,
                      color: '#e2e8f0',
                      lineHeight: 1.4,
                      display: '-webkit-box',
                      WebkitLineClamp: 2,
                      WebkitBoxOrient: 'vertical',
                      overflow: 'hidden',
                    }}
                  >
                    {n.title}
                  </p>
                  <p style={{ margin: '0 0 4px', fontSize: 11, color: '#64748b' }}>{n.sub}</p>
                  <p style={{ margin: 0, fontSize: 11, color: '#475569' }}>{n.time}</p>
                </div>
              </div>
            ))}

            {/* View all */}
            <div style={{ padding: '12px 18px', textAlign: 'right' }}>
              <Link to="/analysis" style={{ fontSize: 12, color: '#38bdf8', textDecoration: 'none', fontWeight: 600 }}>
                View all news →
              </Link>
            </div>
          </div>
        </div>
      </div>

      {/* ── FOOTER BAR ───────────────────────────────────────────────────────── */}
      <div
        style={{
          marginTop: 24,
          padding: '12px 32px 0',
          textAlign: 'center',
          fontSize: 11,
          color: '#334155',
        }}
      >
        All data is updated in real-time from trusted financial sources.
      </div>
    </div>
  )
}
