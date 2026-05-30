/**
 * CreditIQ — Brand Color Palette
 * ────────────────────────────────────────────────────────────────────────────
 * Single source of truth for the CreditIQ "financial command center" theme.
 *
 * The same values are mirrored as CSS custom properties in `src/index.css`
 * (the `@theme` block). Import THIS file from `.ts`/`.tsx` modules that need a
 * raw hex string (inline styles, SVG strokes, canvas, chart libraries); use the
 * CSS tokens (`text-primary`, `bg-surface`, `var(--color-…)`) inside JSX class
 * names and stylesheets.
 *
 * Keep the two in sync — if you change a brand color, change it in both places.
 */

// ── Core brand palette ──────────────────────────────────────────────────────
// The nine signature colors of the CreditIQ identity.

export const PALETTE = {
  /** Deepest backdrop — the void behind everything. */
  deepSpaceNavy: '#050816',
  /** Primary surface / panel base. */
  midnightBlue: '#0B1023',
  /** Primary action + interactive accent. */
  electricBlue: '#2F80FF',
  /** Secondary accent — data viz, links, "live AI" glow. */
  cyanGlow: '#56CCF2',
  /** Positive / success / healthy. */
  mintAiGreen: '#56F2C1',
  /** Primary readable text. */
  softWhite: '#F5F7FA',
  /** Muted / secondary text + hairlines. */
  mutedGray: '#94A3B8',
  /** Caution / medium risk. */
  warningOrange: '#FFB020',
  /** Critical / high risk / error. */
  criticalRed: '#FF4D6D',
} as const

// ── Semantic aliases ────────────────────────────────────────────────────────
// Use these names in app code so intent reads clearly and a future re-theme
// only touches the mapping below.

export const COLORS = {
  background: PALETTE.deepSpaceNavy,
  surface: PALETTE.midnightBlue,
  surfaceDeep: '#03040e',
  surfaceMuted: '#18203f',

  primary: PALETTE.electricBlue,
  secondary: PALETTE.cyanGlow,
  brandAccent: PALETTE.cyanGlow,

  textPrimary: PALETTE.softWhite,
  textMuted: PALETTE.mutedGray,

  success: PALETTE.mintAiGreen,
  warning: PALETTE.warningOrange,
  danger: PALETTE.criticalRed,
} as const

// ── Risk levels (RiskLevel enum: LOW | MEDIUM | HIGH) ────────────────────────

export const RISK_COLOR: Record<string, string> = {
  LOW: PALETTE.mintAiGreen,
  MEDIUM: PALETTE.warningOrange,
  HIGH: PALETTE.criticalRed,
}

// ── Financial health (FinancialHealth enum) ─────────────────────────────────

export const HEALTH_COLOR: Record<string, string> = {
  STABLE: PALETTE.cyanGlow,
  GROWING: PALETTE.electricBlue,
  LIQUID: PALETTE.mintAiGreen,
  DECLINING: PALETTE.warningOrange,
  LEVERAGED: PALETTE.warningOrange,
  SPECULATIVE: PALETTE.warningOrange,
  CONCENTRATED: PALETTE.warningOrange,
  VALUATION_DRIVEN: PALETTE.warningOrange,
  CASH_STRESSED: PALETTE.criticalRed,
  CRITICAL: PALETTE.criticalRed,
}

// ── Account categories ──────────────────────────────────────────────────────

export const CATEGORY_COLOR: Record<string, string> = {
  assets: PALETTE.electricBlue,
  liabilities: PALETTE.criticalRed,
  equity: PALETTE.cyanGlow,
  revenue: PALETTE.warningOrange,
  expense: PALETTE.cyanGlow,
  other: PALETTE.mutedGray,
}

// ── Per-agent accent (Analysis pipeline) ────────────────────────────────────

export const AGENT_COLOR: Record<string, string> = {
  agent1: PALETTE.warningOrange,
  agent2: PALETTE.cyanGlow,
  agent3: '#A78BFA',
  agent4: PALETTE.mintAiGreen,
}

/** Convert a hex color + 0–1 alpha to an 8-digit `#RRGGBBAA` string. */
export function withAlpha(hex: string, alpha: number): string {
  const a = Math.round(Math.min(Math.max(alpha, 0), 1) * 255)
    .toString(16)
    .padStart(2, '0')
  return `${hex}${a}`
}

export default PALETTE
