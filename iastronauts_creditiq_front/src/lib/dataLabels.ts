// Spanish display labels for machine codes coming from the backend.
// The backend keeps English enum codes (category, risk levels, health states…)
// for logic and snapshots; everything user-facing must render in Spanish.

const CATEGORY_ES: Record<string, string> = {
  assets: 'Activos',
  liabilities: 'Pasivos',
  equity: 'Patrimonio',
  revenue: 'Ingresos',
  income: 'Ingresos',
  expense: 'Gastos',
  expenses: 'Gastos',
  other: 'Otros',
}

const LEVEL_ES: Record<string, string> = {
  LOW: 'BAJO',
  MEDIUM: 'MEDIO',
  HIGH: 'ALTO',
  CRITICAL: 'CRÍTICO',
}

const HEALTH_ES: Record<string, string> = {
  STABLE: 'ESTABLE',
  GROWING: 'EN CRECIMIENTO',
  DECLINING: 'EN DETERIORO',
  CRITICAL: 'CRÍTICO',
  LIQUID: 'LÍQUIDO',
  LEVERAGED: 'APALANCADO',
  SPECULATIVE: 'ESPECULATIVO',
  CASH_STRESSED: 'ESTRÉS DE CAJA',
  VALUATION_DRIVEN: 'IMPULSADO POR VALORIZACIÓN',
  CONCENTRATED: 'CONCENTRADO',
}

const POSITION_STATUS_ES: Record<string, string> = {
  new: 'Nueva',
  closed: 'Cerrada',
  increased: 'Aumentó',
  decreased: 'Disminuyó',
  stable: 'Estable',
}

export function categoryEs(code: string): string {
  return CATEGORY_ES[code?.toLowerCase()] ?? code
}

/** Risk / materiality levels: LOW | MEDIUM | HIGH | CRITICAL → Spanish. */
export function levelEs(code: string): string {
  return LEVEL_ES[code?.toUpperCase()] ?? code
}

/** FinancialHealth enum → Spanish (underscores handled). */
export function healthEs(code: string): string {
  return HEALTH_ES[code?.toUpperCase()] ?? code?.replace(/_/g, ' ')
}

export function positionStatusEs(code: string): string {
  return POSITION_STATUS_ES[code?.toLowerCase()] ?? code
}
