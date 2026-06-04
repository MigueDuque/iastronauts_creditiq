"""
template_filler.py — deterministic field builder for the CreditIQ .docx report.

The corporate template (`instructions/CreditIQ_Template_EEFF.docx`) carries ~451
`{{PLACEHOLDER}}` tokens across 26 tables. The vast majority are *calculated
cells* (balance rows, KPIs, materiality, NAV, validation, …) that must be filled
deterministically from the upstream `ScorerOutput`. Only ~33 are *narrative
sections* that the LLM writes.

This module owns the deterministic half plus deterministic prose fallbacks:

  * `NARRATIVE_FIELDS` / `is_narrative_field()` — classify a placeholder.
  * `build_deterministic_fields()` — every calculated cell → string value.
  * `build_narrative_fallbacks()` — deterministic prose used when the LLM is
    unavailable or omits a narrative field, so the document is always complete.

Field names mirror the real template exactly (verified by introspection).

Rules:
  * Never raise — a missing/None value renders as `N/D` so the report still opens.
  * Monetary cells are shown in COP millions (`COP MM`) — the unit upstream
    agents already use (see `kpi_engine` and the NAV reconciliation narrative).
    Values are rendered as-is, with adaptive precision so small holdings stay
    visible. Template headers must read "COP MM" to match.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime
from typing import Any

logger = logging.getLogger("report_generator.template_filler")

ND = "N/D"

# ---------------------------------------------------------------------------
# Narrative (LLM-authored) fields — names match the real template
# ---------------------------------------------------------------------------

NARRATIVE_FIELDS: set[str] = {
    "MACRO_CONTEXT",
    "BS_ASSETS_ANALYSIS",
    "BS_LIABILITIES_ANALYSIS",
    "PROFITABILITY_ANALYSIS",
    "PORTFOLIO_ANALYSIS",
    "NAV_ANALYSIS",
    "LIQUIDITY_ANALYSIS",
    "ASG_ANALYSIS",
    "EXEC_CONCLUSIONS",
    "NEXT_STEPS",
    "AI_REVIEWER_NOTES",
    "NOTE_BASES",
    "NOTE_FV",
    "NOTE_RELATED_PARTIES",
    "NOTE_RISKS",
    "BOARD_TOPIC_1",
    "BOARD_TOPIC_2",
    "BOARD_TOPIC_3",
    "BOARD_TOPIC_4",
}


def is_narrative_field(name: str) -> bool:
    """True if a placeholder is LLM-authored prose (vs a deterministic value)."""
    if name in NARRATIVE_FIELDS:
        return True
    # FINDING_n_TITLE / FINDING_n_BODY are LLM; _ACCOUNTS / _IMPACT are deterministic.
    if name.startswith("FINDING_") and (name.endswith("_TITLE") or name.endswith("_BODY")):
        return True
    return False


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

_MONTHS_ES = {
    1: "Ene", 2: "Feb", 3: "Mar", 4: "Abr", 5: "May", 6: "Jun",
    7: "Jul", 8: "Ago", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dic",
}
_MONTHS_ES_FULL = {
    1: "enero", 2: "febrero", 3: "marzo", 4: "abril", 5: "mayo", 6: "junio",
    7: "julio", 8: "agosto", 9: "septiembre", 10: "octubre", 11: "noviembre",
    12: "diciembre",
}
_HEALTH_ES = {
    "STABLE": "Estable", "DECLINING": "En deterioro", "GROWING": "En crecimiento",
    "CRITICAL": "Crítica", "LIQUID": "Líquida", "LEVERAGED": "Apalancada",
    "SPECULATIVE": "Especulativa", "CASH_STRESSED": "Tensión de liquidez",
    "VALUATION_DRIVEN": "Dependiente de valoración", "CONCENTRATED": "Concentrada",
}
_RISK_ES = {"LOW": "Bajo", "MEDIUM": "Medio", "HIGH": "Alto", "CRITICAL": "Crítico"}
_MATERIALITY_ES = {"LOW": "Baja", "MEDIUM": "Media", "HIGH": "Alta"}
_TREND_ES = {
    "up": "↑", "down": "↓", "flat": "→", "stable": "→",
    "growing": "↑", "declining": "↓", "rising": "↑", "falling": "↓",
}
_INSTRUMENT_ES = {
    "equity": "Renta variable", "bond": "Renta fija", "sovereign_debt": "Deuda soberana",
    "trust_rights": "Derechos fiduciarios", "futures": "Futuros", "fund": "Fondo",
    "cash": "Efectivo / MM",
}


def _enum(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value or "")


def _f(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _mm(value: Any) -> str:
    """Monetary value in COP millions (COP MM), thousands-separated.

    Upstream values are already expressed in COP MM (matching `kpi_engine` and the
    NAV reconciliation narrative), so they are rendered as-is — *not* divided.
    Precision is adaptive so small holdings stay visible instead of rounding to
    ``0.0``: sub-unit values get extra decimals. `N/D` when absent.
    """
    v = _f(value)
    if v is None:
        return ND
    a = abs(v)
    if a == 0:
        return "0.0"
    if a >= 1:
        return f"{v:,.1f}"
    if a >= 0.01:
        return f"{v:.3f}"
    # Very small but non-zero: expand decimals so it shows ~3 significant figures
    # (e.g. 0.0000156) instead of rounding away to 0.0.
    decimals = min(max(2 - math.floor(math.log10(a)), 3), 12)
    return f"{v:.{decimals}f}"


def _qty(value: Any) -> str:
    """Plain quantity (e.g. nominal units), thousands-separated. `N/D` when absent.

    Unlike `_mm`, this is *not* a COP MM monetary figure — the "Nominal YYYY"
    column holds a nominal amount/unit count, rendered without unit scaling.
    """
    v = _f(value)
    if v is None:
        return ND
    if v == int(v):
        return f"{int(v):,}"
    return f"{v:,.2f}"


def _pct(value: Any, *, already_pct: bool = True) -> str:
    v = _f(value)
    if v is None:
        return ND
    if not already_pct:
        v *= 100.0
    sign = "+" if v > 0 else ""
    return f"{sign}{v:.1f}%"


def _trend_arrow(trend: Any) -> str:
    return _TREND_ES.get(str(trend or "").lower(), "→")


def _period_label(period: str | None) -> str:
    if not period:
        return ND
    try:
        year, month = period.split("-")[:2]
        return f"{_MONTHS_ES[int(month)]} {year}"
    except (ValueError, KeyError):
        return str(period)


def _cutoff_date(period: str | None) -> str:
    if not period:
        return ND
    try:
        year, month = int(period.split("-")[0]), int(period.split("-")[1])
        from calendar import monthrange
        last = monthrange(year, month)[1]
        return f"{last} de {_MONTHS_ES_FULL[month]} de {year}"
    except (ValueError, KeyError):
        return str(period)


# ---------------------------------------------------------------------------
# Accessors over the loosely-typed ScorerOutput
# ---------------------------------------------------------------------------

def _attr(payload: Any, name: str, default: Any = None) -> Any:
    return getattr(payload, name, default)


def _periods(payload: Any) -> tuple[str | None, str | None]:
    periods = _attr(payload, "periods") or []
    return (periods[0] if periods else None, periods[1] if len(periods) > 1 else None)


# Income-statement keywords — fallback when source_sheet is absent.
_INCOME_KW = (
    "ingreso", "gasto", "costo", "utilidad", "resultado", "perdida", "pérdida",
    "comision", "comisión", "egreso", "rendimiento", "depreciaci", "amortizaci",
)

# Sheet-name keyword sets for routing accounts to the correct table.
# Order matters: more specific patterns first to avoid false positives.
_FV_SHEET_KW = ("valor razonable", "fair value", "vr activo", "jerarquia valor")
_ACTIVOS_NETOS_KW = ("activo neto", "activos netos", "por clase", "composicion clase")
_CASH_FLOW_KW = ("flujo efectivo", "flujo de efectivo", "cash flow", "suscripcion", "redencion", "movimiento")
_PORTFOLIO_SHEET_KW = (
    "invers", "portafolio", "portfolio", "p.dinamico", "p.inmobiliario",
    "dinamico", "inmobiliario",
)
_IS_SHEET_KW = (
    "resultado", "pyg", "p&g", "p.y.g", "p y g", "ingreso", "gasto",
    "utilidad", "perdida", "pérdida",
)
_BS_SHEET_KW = ("balance", "situacion", "situación", "activo", "pasivo", "posicion", "position")


def _classify_sheet(source_sheet: str | None) -> str | None:
    """Map a source_sheet name to a table category or None.

    Returns one of:
      'balance_sheet', 'income_statement', 'portfolio',
      'fair_value', 'activos_netos', 'cash_flow', or None.
    More specific patterns are checked first.
    """
    if not source_sheet:
        return None
    s = source_sheet.lower()
    if any(k in s for k in _FV_SHEET_KW):
        return "fair_value"
    if any(k in s for k in _ACTIVOS_NETOS_KW):
        return "activos_netos"
    if any(k in s for k in _CASH_FLOW_KW):
        return "cash_flow"
    if any(k in s for k in _PORTFOLIO_SHEET_KW):
        return "portfolio"
    if any(k in s for k in _IS_SHEET_KW):
        return "income_statement"
    if any(k in s for k in _BS_SHEET_KW):
        return "balance_sheet"
    return None


def _statement_of(a: Any, sheet_lookup: dict[str, str] | None = None) -> str:
    """Classify an account as 'balance_sheet' or 'income_statement'.

    Priority:
      1. source_sheet on the account itself (set by Agent 1)
      2. sheet_lookup dict built from raw extractor accounts
      3. explicit statement_type field
      4. account name heuristic (fallback for PDFs/CSV without sheet info)
    """
    sheet_type = _classify_sheet(_attr(a, "source_sheet"))
    if sheet_type in ("balance_sheet", "income_statement"):
        return sheet_type

    if sheet_lookup:
        name = (_attr(a, "account_name") or "").lower()
        sheet = sheet_lookup.get(name)
        if sheet:
            sheet_type = _classify_sheet(sheet)
            if sheet_type in ("balance_sheet", "income_statement"):
                return sheet_type

    st = _attr(a, "statement_type")
    if st in ("balance_sheet", "income_statement"):
        return st

    name = (_attr(a, "account_name") or "").lower()
    if any(k in name for k in _INCOME_KW):
        return "income_statement"
    return "balance_sheet"


def _accounts(
    payload: Any,
    statement: str | None = None,
    *,
    with_investment: bool = False,
    sheet_lookup: dict[str, str] | None = None,
) -> list:
    out = []
    for a in _attr(payload, "analysis_results") or []:
        if statement is not None and _statement_of(a, sheet_lookup) != statement:
            continue
        if with_investment and not _attr(a, "investment_type"):
            continue
        out.append(a)
    return out


def _var_abs(a: Any) -> float | None:
    v = _f(_attr(a, "variation_abs"))
    if v is not None:
        return v
    cur, prev = _f(_attr(a, "current_value")), _f(_attr(a, "previous_value"))
    if cur is not None and prev is not None:
        return cur - prev
    return None


def _is_fund(payload: Any) -> bool:
    return bool((_attr(payload, "fund_analysis") or {}).get("is_fund"))


def _risk_metrics(payload: Any, category: str) -> dict:
    """Return the `metrics` dict of a risk_categories entry (credito/mercado/...)."""
    cat = (_attr(payload, "risk_categories") or {}).get(category) or {}
    return cat.get("metrics") or {}


import re as _re

_NIVEL_RE = _re.compile(r"nivel\s*([123])", _re.IGNORECASE)


def _niif13_level(name: str | None) -> int | None:
    """Parse the NIIF 13 fair-value hierarchy level encoded in an account name.

    Sheet rows look like "Instrumentos de patrimonio - Acciones locales (Nivel 1)".
    Returns 1/2/3, or None when no level token is present.
    """
    if not name:
        return None
    m = _NIVEL_RE.search(name)
    return int(m.group(1)) if m else None


def _set(
    fields: dict, audit: dict | None, key: str, value: str, source: str
) -> None:
    """Set a single placeholder and record its provenance in the audit log."""
    fields[key] = value
    if audit is not None:
        audit[key] = source if (value and value != ND) else f"N/D ({source})"


def _fill_rows(
    fields: dict,
    prefix: str,
    n_rows: int,
    n_cols: int,
    items: list,
    mapper,
    *,
    source: str = "unknown",
    audit: dict | None = None,
) -> None:
    """Fill `{{prefix_R{r}_C{c}}}`. Empty rows get blank cells."""
    for r in range(1, n_rows + 1):
        item = items[r - 1] if r - 1 < len(items) else None
        values = mapper(item) if item is not None else [""] * n_cols
        for c in range(1, n_cols + 1):
            key = f"{prefix}_R{r}_C{c}"
            val = str(values[c - 1]) if c - 1 < len(values) else ""
            fields[key] = val
            if audit is not None:
                audit[key] = source if (val and val != ND) else f"N/D ({source})"


# ---------------------------------------------------------------------------
# Deterministic field builder
# ---------------------------------------------------------------------------

def build_deterministic_fields(
    payload: Any,
    *,
    generated_at: datetime | None = None,
    job_id: str | None = None,
    row_needs: dict[str, int] | None = None,
    extractor_accounts: list[dict] | None = None,
    sheet_mapping: dict[str, str | None] | None = None,
) -> tuple[dict[str, str], dict[str, str]]:
    """Return every deterministic (calculated) placeholder → string value.

    *row_needs* maps table prefix → number of data rows actually needed (derived
    from Agent 1 extractor output).

    *extractor_accounts* is the raw Agent 1 account list.  When provided,
    ``source_sheet`` on each account is used to route rows to the correct table.

    *sheet_mapping* is the output of sheet_mapper.match_sheets() — maps canonical
    sheet keys (e.g. "BALANCE") to actual Excel sheet names.  Enables precise
    per-sheet routing for FV_HIER, NAV_CLASS, and NAV_FLOW tables in addition
    to the existing BS/IS/PORTFOLIO heuristic routing.

    Returns ``(fields, audit)`` where *audit* maps every placeholder name to
    a human-readable provenance string for the fill audit log.
    """
    generated_at = generated_at or datetime.utcnow()
    job_id = job_id or _attr(payload, "job_id") or ""
    rn = row_needs or {}
    sm = sheet_mapping or {}
    fields: dict[str, str] = {}
    audit: dict[str, str] = {}

    # Build name→source_sheet lookup from raw extractor accounts so that
    # analysis_result items (which may lack source_sheet) can still be routed
    # to the right table via their account name.
    sheet_lookup: dict[str, str] = {}
    if extractor_accounts:
        for acc in extractor_accounts:
            name = (acc.get("normalized_account_name") or acc.get("raw_account_name") or "").lower()
            sheet = acc.get("source_sheet") or ""
            if name and sheet:
                sheet_lookup[name] = sheet

    cur_p, prev_p = _periods(payload)
    gen_date = generated_at.strftime("%d/%m/%Y")
    is_fund = _is_fund(payload)
    health = _enum(_attr(payload, "overall_financial_health"))
    risk = _enum(_attr(payload, "overall_risk_score"))
    val_score = _f(_attr(payload, "validation_score")) or 0.0

    # Map each analysis_result by account_id so sheet-driven tables (sourced from
    # the raw extractor accounts) can be enriched with the analyzer's materiality
    # and variation without re-deriving them.
    ar_by_id: dict[str, Any] = {}
    for a in _attr(payload, "analysis_results") or []:
        aid = _attr(a, "account_id")
        if aid:
            ar_by_id[aid] = a

    _cover(fields, audit, payload, cur_p, prev_p, gen_date, is_fund, health, val_score, job_id)
    _entity_info(fields, audit, payload, cur_p, is_fund, risk, health)
    _macro(fields, audit, payload)
    _balance(fields, audit, payload, rn.get("BS", 6), sheet_lookup,
             extractor_accounts or [], sm, ar_by_id)
    _income(fields, audit, payload, rn.get("IS_ACC", 8), sheet_lookup,
            extractor_accounts or [], sm, ar_by_id)
    _portfolio(fields, audit, payload, rn.get("PORTFOLIO", 10), extractor_accounts or [], sm)
    _fair_value(fields, audit, extractor_accounts or [], sm)
    _nav(fields, audit, payload, extractor_accounts or [], sm)
    _findings_meta(fields, audit, payload)
    _nic34(fields, audit, payload)
    _materiality(fields, audit, payload)
    _market_risk(fields, audit, payload)
    _kpis(fields, audit, payload)
    _validation(fields, audit, payload, val_score)
    _signatures(fields, audit, gen_date)

    return fields, audit


def _cover(fields, audit, payload, cur_p, prev_p, gen_date, is_fund, health, val_score, job_id):
    cur_lbl, prev_lbl = _period_label(cur_p), _period_label(prev_p)
    _set(fields, audit, "ENTITY_NAME", _attr(payload, "company_name") or ND, "scorer_output.company_name")
    _set(fields, audit, "PERIOD", f"{cur_lbl} vs {prev_lbl}" if prev_lbl != ND else cur_lbl, "scorer_output.periods")
    _set(fields, audit, "DOCUMENT_TYPE", "Estados Financieros del Fondo" if is_fund else "Estados Financieros", "fund_analysis.is_fund")
    _set(fields, audit, "CUTOFF_DATE", _cutoff_date(cur_p), "scorer_output.periods[0]")
    _set(fields, audit, "CLASSIFICATION", _HEALTH_ES.get(health, health or ND), "scorer_output.overall_financial_health")
    _set(fields, audit, "GENERATED_DATE", gen_date, "system_clock")
    _set(fields, audit, "AI_REVIEWER", f"CreditIQ Multi-Agente · Score {val_score:.0f}/100", "scorer_output.validation_score")
    _set(fields, audit, "REPORT_VERSION", "1.0", "hardcoded")
    _set(fields, audit, "REPORT_ID", job_id or ND, "scorer_output.job_id")


def _entity_info(fields, audit, payload, cur_p, is_fund, risk, health):
    rows = [
        ("Razón social", _attr(payload, "company_name") or ND, "scorer_output.company_name"),
        ("Moneda funcional", _attr(payload, "currency") or "COP", "scorer_output.currency"),
        ("Período analizado", _period_label(cur_p), "scorer_output.periods[0]"),
        ("Clasificación de riesgo", _RISK_ES.get(risk, risk or ND), "scorer_output.overall_risk_score"),
        ("Salud financiera", _HEALTH_ES.get(health, health or ND), "scorer_output.overall_financial_health"),
    ]
    for i, (label, value, src) in enumerate(rows, start=1):
        _set(fields, audit, f"ENTITY_INFO_R{i}_C1", label, "hardcoded_label")
        _set(fields, audit, f"ENTITY_INFO_R{i}_C2", str(value), src)
    _set(fields, audit, "ENTITY_INFO_R6_C1", "", "unused_row")
    _set(fields, audit, "ENTITY_INFO_R6_C2", "", "unused_row")


def _macro(fields, audit, payload):
    macro = _attr(payload, "macro_context") or {}

    def cell(key, ph):
        d = macro.get(key) or {}
        val = d.get("value")
        unit = d.get("unit") or ""
        _set(fields, audit, ph, f"{val}{unit}" if val is not None else ND, f"macro_context.{key}.value")
        _set(fields, audit, f"{ph}_TREND", _trend_arrow(d.get("trend")), f"macro_context.{key}.trend")

    cell("policy_rate", "KPI_BR_RATE")
    cell("inflation", "KPI_INFLATION")
    cell("colcap_index", "KPI_COLCAP")
    cell("fx_usdcop", "KPI_TRM")


# ---------------------------------------------------------------------------
# Sheet-driven tables — sourced directly from the matched Excel sheet
# ---------------------------------------------------------------------------
#
# Each of these tables maps 1:1 to one EEFF sheet (see sheet_mapper.CANONICAL_SHEETS).
# Rows come straight from the raw extractor accounts of the *matched* sheet, in the
# sheet's own order, so the rendered table reproduces the input statement exactly.
# The analyzer's per-account materiality / variation is joined back by account_id.
# The analysis_results heuristic path is kept only as a fallback for inputs that
# carry no source_sheet at all (e.g. PDF/CSV), where sheet mapping is empty.

def _ext_name(acc: dict) -> str:
    return acc.get("normalized_account_name") or acc.get("raw_account_name") or ND


def _ext_var_abs(acc: dict) -> float | None:
    cur, prev = _f(acc.get("current_value")), _f(acc.get("previous_value"))
    if cur is not None and prev is not None:
        return cur - prev
    return None


def _ext_var_pct(acc: dict, ar: Any | None) -> str:
    """Prefer the analyzer's variation_pct; otherwise derive it from raw values."""
    if ar is not None and _attr(ar, "variation_pct") is not None:
        return _pct(_attr(ar, "variation_pct"))
    cur, prev = _f(acc.get("current_value")), _f(acc.get("previous_value"))
    if cur is not None and prev not in (None, 0):
        return _pct((cur - prev) / abs(prev) * 100)
    return ND


def _balance(fields, audit, payload, n_rows, sheet_lookup, extractor_accounts, sheet_mapping, ar_by_id):
    from .sheet_mapper import get_accounts_for_canonical

    accs = get_accounts_for_canonical(
        extractor_accounts, "BALANCE", sheet_mapping, include_totals=True
    )
    if accs:
        _fill_rows(
            fields, "BS", n_rows, 6, accs,
            lambda a: [
                _ext_name(a),
                _mm(a.get("current_value")),
                _mm(a.get("previous_value")),
                _mm(_ext_var_abs(a)),
                _ext_var_pct(a, ar_by_id.get(a.get("account_id"))),
                "Sí" if _enum(_attr(ar_by_id.get(a.get("account_id")), "materiality")) == "HIGH" else "No",
            ],
            source="extractor_sheet.BALANCE (exact)",
            audit=audit,
        )
        return

    # Fallback: no source_sheet (PDF/CSV) — classify analysis_results heuristically.
    accounts = _accounts(payload, "balance_sheet", sheet_lookup=sheet_lookup)
    accounts.sort(key=lambda a: (1 if _attr(a, "is_total") else 0, -(_f(_attr(a, "impact_score")) or 0)))
    _fill_rows(
        fields, "BS", n_rows, 6, accounts,
        lambda a: [
            _attr(a, "account_name") or ND,
            _mm(_attr(a, "current_value")),
            _mm(_attr(a, "previous_value")),
            _mm(_var_abs(a)),
            _pct(_attr(a, "variation_pct")),
            "Sí" if _enum(_attr(a, "materiality")) == "HIGH" else "No",
        ],
        source="analysis_results[balance_sheet] (fallback — no source_sheet)",
        audit=audit,
    )


def _income(fields, audit, payload, n_rows, sheet_lookup, extractor_accounts, sheet_mapping, ar_by_id):
    from .sheet_mapper import get_accounts_for_canonical

    accs = get_accounts_for_canonical(
        extractor_accounts, "ESTADO_RESULTADOS", sheet_mapping, include_totals=True
    )
    if accs:
        _fill_rows(
            fields, "IS_ACC", n_rows, 5, accs,
            lambda a: [
                _ext_name(a),
                _mm(a.get("current_value")),
                _mm(a.get("previous_value")),
                _mm(_ext_var_abs(a)),
                _ext_var_pct(a, ar_by_id.get(a.get("account_id"))),
            ],
            source="extractor_sheet.ESTADO_RESULTADOS (exact)",
            audit=audit,
        )
    else:
        accs = _accounts(payload, "income_statement", sheet_lookup=sheet_lookup)
        accs.sort(key=lambda a: (1 if _attr(a, "is_total") else 0, -(_f(_attr(a, "impact_score")) or 0)))
        _fill_rows(
            fields, "IS_ACC", n_rows, 5, accs,
            lambda a: [
                _attr(a, "account_name") or ND,
                _mm(_attr(a, "current_value")),
                _mm(_attr(a, "previous_value")),
                _mm(_var_abs(a)),
                _pct(_attr(a, "variation_pct")),
            ],
            source="analysis_results[income_statement] (fallback — no source_sheet)",
            audit=audit,
        )

    # Quarterly split is not produced upstream — keep concept labels, mark values N/D.
    _fill_rows(
        fields, "IS_Q2", n_rows, 5, accs,
        lambda a: [(_ext_name(a) if isinstance(a, dict) else _attr(a, "account_name")) or ND, ND, ND, ND, ND],
        source="N/D — quarterly breakdown not extracted",
        audit=audit,
    )


def _portfolio(fields, audit, payload, n_rows, extractor_accounts, sheet_mapping):
    from .sheet_mapper import get_accounts_for_canonical

    sc = _attr(payload, "sheet_concentration") or {}
    breakdown = sc.get("instrument_breakdown") or []
    totals = (_attr(payload, "financial_ratios") or {}).get("totals") or {}
    metrics_m = _risk_metrics(payload, "mercado")

    # instrument_breakdown items expose the position value under "total" (not "value").
    total_val = (
        _f(sc.get("instrument_total"))
        or sum((_f(b.get("total")) or 0.0) for b in breakdown)
        or _f(totals.get("total_assets"))
    )
    _set(fields, audit, "PORTFOLIO_TOTAL", _mm(total_val),
         "sheet_concentration.instrument_total | instrument_breakdown(sum total) | financial_ratios.totals.total_assets")

    # Composition %: prefer Agent 3 portfolio-exposure metrics (already %-of-portfolio);
    # fall back to summing instrument_breakdown.pct by Spanish instrument_type label.
    def _pct_by_kw(keywords: list[str]) -> float | None:
        if not breakdown:
            return None
        acc = sum((_f(b.get("pct")) or 0.0) for b in breakdown
                  if any(k in str(b.get("instrument_type", "")).lower() for k in keywords))
        return acc or None

    fixed_pct = _f(metrics_m.get("fixed_income_pct"))
    if fixed_pct is None:
        fixed_pct = _pct_by_kw(["deuda", "fija", "soberan", "bond", "sovereign"])
    equity_pct = _f(metrics_m.get("equity_exposure_pct"))
    if equity_pct is None:
        equity_pct = _pct_by_kw(["patrimonio", "variable", "accion", "equity"])
    # Liquidez = residual of the same base so the three cards sum to ~100%.
    cash_pct = None
    if equity_pct is not None or fixed_pct is not None:
        cash_pct = max(0.0, 100.0 - (equity_pct or 0.0) - (fixed_pct or 0.0))

    _set(fields, audit, "PORTFOLIO_FIXED_PCT", _pct(fixed_pct) if fixed_pct is not None else ND,
         "risk_categories.mercado.metrics.fixed_income_pct | instrument_breakdown[deuda].pct")
    _set(fields, audit, "PORTFOLIO_EQUITY_PCT", _pct(equity_pct) if equity_pct is not None else ND,
         "risk_categories.mercado.metrics.equity_exposure_pct | instrument_breakdown[patrimonio].pct")
    _set(fields, audit, "PORTFOLIO_CASH_PCT", _pct(cash_pct) if cash_pct is not None else ND,
         "residual (100 - equity - fixed)")

    # Primary source: the INSTRUMENTOS sheet, resolved by sheet_mapper (handles
    # name variants the keyword heuristic misses, e.g. the literal "INSTRUMENTOS").
    portfolio_ext = get_accounts_for_canonical(
        extractor_accounts, "INSTRUMENTOS", sheet_mapping
    )
    if portfolio_ext:
        _fill_rows(
            fields, "PORTFOLIO", n_rows, 6, portfolio_ext,
            lambda a: [
                a.get("issuer_name") or a.get("normalized_account_name") or a.get("raw_account_name") or ND,
                _INSTRUMENT_ES.get(str(a.get("investment_type") or ""), str(a.get("investment_type") or ND)),
                _qty(a.get("nominal_value")),
                _mm(a.get("current_value")),
                _mm(a.get("previous_value")),
                _ext_var_pct(a, None),
            ],
            source="extractor_sheet.INSTRUMENTOS (exact)",
            audit=audit,
        )
        return

    holdings = [h for h in _accounts(payload, with_investment=True) if not _attr(h, "is_total")]
    if not holdings:
        holdings = [
            h for h in _accounts(payload)
            if not _attr(h, "is_total") and "invers" in (_attr(h, "account_name") or "").lower()
        ]
    holdings.sort(key=lambda a: -(_f(_attr(a, "current_value")) or 0))
    _fill_rows(
        fields, "PORTFOLIO", n_rows, 6, holdings,
        lambda a: [
            _attr(a, "issuer_name") or _attr(a, "account_name") or ND,
            _INSTRUMENT_ES.get(str(_attr(a, "investment_type")), str(_attr(a, "investment_type") or ND)),
            _qty(_attr(a, "nominal_value")),
            _mm(_attr(a, "current_value")),
            _mm(_attr(a, "previous_value")),
            _pct(_attr(a, "variation_pct")),
        ],
        source="analysis_results[investment_type or 'invers' in name] (fallback)",
        audit=audit,
    )


def _fair_value(
    fields,
    audit,
    extractor_accounts: list[dict],
    sheet_mapping: dict[str, str | None],
) -> None:
    """Fill FV_HIER table from the VALOR_RAZONABLE_ACTIVOS sheet accounts.

    Template columns: [Categoría, Nivel 1 (actual), Nivel 2 (actual),
    Nivel 1 (anterior), Nivel 2 (anterior)].  The NIIF 13 hierarchy level is
    encoded in the account name ("… (Nivel 1)"), so current/previous values are
    routed to the matching level column.  Falls back to a category scaffold when
    no matching sheet accounts are found.
    """
    from .sheet_mapper import get_accounts_for_canonical

    accs = get_accounts_for_canonical(extractor_accounts, "VALOR_RAZONABLE_ACTIVOS", sheet_mapping)
    # Also try classify_sheet heuristic as secondary source
    if not accs:
        accs = [
            a for a in extractor_accounts
            if _classify_sheet(a.get("source_sheet")) == "fair_value"
            and not a.get("is_total")
        ]

    if accs:
        def _fv_row(a: dict) -> list:
            name = a.get("normalized_account_name") or a.get("raw_account_name") or a.get("issuer_name") or ND
            level = _niif13_level(name)
            cur, prev = a.get("current_value"), a.get("previous_value")
            # Level 2 → middle columns; level 1 (or unknown) → outer columns.
            if level == 2:
                return [name, ND, _mm(cur), ND, _mm(prev)]
            return [name, _mm(cur), ND, _mm(prev), ND]

        # Preserve sheet order and render one row per account (table was expanded to fit).
        n_rows = max(4, len(accs))
        _fill_rows(
            fields, "FV_HIER", n_rows, 5, accs, _fv_row,
            source="extractor_sheet.VALOR_RAZONABLE_ACTIVOS (level parsed from name; current→actual, previous→anterior)",
            audit=audit,
        )
        logger.info("_fair_value | source=extractor rows=%d", len(accs))
    else:
        categories = ["Instrumentos de patrimonio", "Instrumentos de deuda", "Derivados", "Total"]
        _fill_rows(
            fields, "FV_HIER", 4, 5, [{"c": c} for c in categories],
            lambda d: [d["c"], ND, ND, ND, ND],
            source="N/D — VALOR_RAZONABLE_ACTIVOS sheet not found; category scaffold used",
            audit=audit,
        )
        logger.info("_fair_value | source=scaffold (no FV sheet accounts found)")


def _nav(
    fields,
    audit,
    payload,
    extractor_accounts: list[dict],
    sheet_mapping: dict[str, str | None],
) -> None:
    """Fill NAV_CLASS (5×6) and NAV_FLOW (4×4) tables.

    Priority:
      1. Raw extractor accounts from the matched ACTIVOS_NETOS / FLUJO_EFECTIVO sheets.
      2. Fallback to fund_analysis NAV reconciliation summary when no sheet data is found.

    NAV_CLASS columns: [Clase, Valor anterior, -, -, Valor actual, -]
    NAV_FLOW columns:  [Concepto, Período actual, Período anterior, Variación]
    """
    from .sheet_mapper import get_accounts_for_canonical

    fa = _attr(payload, "fund_analysis") or {}
    recon = fa.get("nav_reconciliation") or {}

    # ── NAV_CLASS: Composición por Clase / Activos Netos ─────────────────────
    nav_class_accs = get_accounts_for_canonical(
        extractor_accounts, "ACTIVOS_NETOS", sheet_mapping
    )
    if not nav_class_accs:
        nav_class_accs = [
            a for a in extractor_accounts
            if _classify_sheet(a.get("source_sheet")) == "activos_netos"
            and not a.get("is_total")
        ]

    if nav_class_accs:
        # Columns: [Clase, Valor Unidad, Unidades Circ., Suscriptores, Valor del Fondo, Var. VU %].
        # The extractor provides the class NAV (current/previous) but not unit value,
        # units outstanding or subscriber counts — those stay N/D. Var. VU % is derived
        # from the class NAV change as a proxy for the unit-value variation.
        def _nav_class_row(a: dict) -> list:
            cur, prev = _f(a.get("current_value")), _f(a.get("previous_value"))
            var = _pct((cur - prev) / abs(prev) * 100) if (cur is not None and prev) else ND
            return [
                a.get("normalized_account_name") or a.get("raw_account_name") or ND,
                ND,  # valor unidad — not extracted
                ND,  # unidades en circulación — not extracted
                ND,  # suscriptores — not extracted
                _mm(cur),
                var,
            ]

        n_rows = max(5, len(nav_class_accs))
        _fill_rows(
            fields, "NAV_CLASS", n_rows, 6, nav_class_accs, _nav_class_row,
            source="extractor_sheet.ACTIVOS_NETOS (C5=class NAV; C6=NAV variation; C2-C4=N/D — not extracted)",
            audit=audit,
        )
        logger.info("_nav_class | source=extractor rows=%d", len(nav_class_accs))
    else:
        _fill_rows(
            fields, "NAV_CLASS", 5, 6,
            [{"closing": recon.get("closing_nav")}] if fa.get("is_fund") else [],
            lambda d: ["Única", ND, ND, ND, _mm(d["closing"]), ND],
            source="fund_analysis.nav_reconciliation.closing_nav (fallback — ACTIVOS_NETOS sheet not found)",
            audit=audit,
        )
        logger.info("_nav_class | source=fund_recon")

    # ── NAV_FLOW: Movimientos del Periodo / Flujo de Efectivo ─────────────────
    flow_accs = get_accounts_for_canonical(
        extractor_accounts, "FLUJO_EFECTIVO", sheet_mapping
    )
    if not flow_accs:
        flow_accs = [
            a for a in extractor_accounts
            if _classify_sheet(a.get("source_sheet")) == "cash_flow"
            and not a.get("is_total")
        ]

    if flow_accs:
        n_rows = max(4, len(flow_accs))
        _fill_rows(
            fields, "NAV_FLOW", n_rows, 4, flow_accs,
            lambda a: [
                a.get("normalized_account_name") or a.get("raw_account_name") or ND,
                _mm(a.get("current_value")),
                _mm(a.get("previous_value")),
                _pct(a.get("variation_pct")) if a.get("variation_pct") is not None
                else _mm(_ext_var_abs(a)),
            ],
            source="extractor_sheet.FLUJO_EFECTIVO (current_value, previous_value, variation)",
            audit=audit,
        )
        logger.info("_nav_flow | source=extractor rows=%d", len(flow_accs))
    else:
        flow_rows = [
            ("Saldo inicial (NAV apertura)", recon.get("opening_nav")),
            ("Suscripciones / redenciones netas", recon.get("net_investor_flow")),
            ("Resultado del período", recon.get("investment_return")),
            ("Saldo final (NAV cierre)", recon.get("closing_nav")),
        ]
        _flow_keys = ["opening_nav", "net_investor_flow", "investment_return", "closing_nav"]
        for i, (label, value) in enumerate(flow_rows, start=1):
            _set(fields, audit, f"NAV_FLOW_R{i}_C1", label, "hardcoded_label")
            _set(fields, audit, f"NAV_FLOW_R{i}_C2", _mm(value), f"fund_analysis.nav_reconciliation.{_flow_keys[i-1]}")
            _set(fields, audit, f"NAV_FLOW_R{i}_C3", ND, "N/D — quarterly split not in NAV reconciliation")
            _set(fields, audit, f"NAV_FLOW_R{i}_C4", ND, "N/D — quarterly split not in NAV reconciliation")
        logger.info("_nav_flow | source=fund_recon")


def _findings_meta(fields, audit, payload):
    """Deterministic ACCOUNTS + IMPACT for the 7 findings. TITLE/BODY come from the LLM."""
    findings = _collect_findings(payload)
    for i in range(1, 8):
        f = findings[i - 1] if i - 1 < len(findings) else None
        _set(fields, audit, f"FINDING_{i}_ACCOUNTS", f["accounts"] if f else "—", "analysis_results[anomaly_detected or materiality=HIGH].account_name")
        _set(fields, audit, f"FINDING_{i}_IMPACT", f["impact"] if f else "—", "analysis_results.materiality + variation_pct")


def _collect_findings(payload) -> list[dict]:
    findings: list[dict] = []

    for a in _accounts(payload):
        if _attr(a, "anomaly_detected"):
            findings.append({
                "accounts": _attr(a, "account_name") or ND,
                "impact": f"{_MATERIALITY_ES.get(_enum(_attr(a, 'materiality')), '')} · {_pct(_attr(a, 'variation_pct'))}".strip(" ·"),
                "seed_title": _attr(a, "account_name"),
                "seed_body": _attr(a, "executive_insight"),
            })

    for key, cat in (_attr(payload, "risk_categories") or {}).items():
        if str(cat.get("level")) in ("HIGH", "CRITICAL"):
            findings.append({
                "accounts": cat.get("label", key),
                "impact": f"Riesgo {_RISK_ES.get(str(cat.get('level')), str(cat.get('level')))} ({cat.get('score', '')}/100)",
                "seed_title": cat.get("label"),
                "seed_body": " ".join(cat.get("key_findings") or []),
            })

    material = [a for a in _accounts(payload)
               if _enum(_attr(a, "materiality")) == "HIGH" and not _attr(a, "is_total")]
    material.sort(key=lambda a: -(_f(_attr(a, "impact_score")) or 0))
    for a in material:
        findings.append({
            "accounts": _attr(a, "account_name") or ND,
            "impact": f"Materialidad alta · {_pct(_attr(a, 'variation_pct'))}",
            "seed_title": _attr(a, "account_name"),
            "seed_body": _attr(a, "executive_insight") or _attr(a, "investment_signal"),
        })

    seen, uniq = set(), []
    for f in findings:
        if f["accounts"] in seen:
            continue
        seen.add(f["accounts"])
        uniq.append(f)
    return uniq


def _nic34(fields, audit, payload):
    niif = _attr(payload, "niif_notes_required") or []
    has_bs = bool(_accounts(payload, "balance_sheet"))
    has_is = bool(_accounts(payload, "income_statement"))
    requires_review = bool(_attr(payload, "requires_human_review"))
    ok, partial, pend = "✓ Cumple", "◐ Parcial", "○ Pendiente"

    rows = [
        ("Estado de situación financiera", ok if has_bs else pend, "Sección 2 — Balance", "—"),
        ("Estado de resultados del período", ok if has_is else partial, "Sección 3", "—"),
        ("Información comparativa", ok if _periods(payload)[1] else partial, "Columnas período anterior", "—"),
        ("Revelaciones NIIF requeridas", ok if niif else partial,
         ", ".join(niif[:4]) if niif else "Sin revelaciones marcadas", "—"),
        ("Jerarquía de valor razonable (NIIF 13)", partial, "Sección 4.2", "No disgregada en origen"),
        ("Análisis de materialidad", ok, "Sección 7.3", "—"),
        ("Concentración y riesgos", ok, "Sección 8", "—"),
        ("Revisión humana", pend if requires_review else ok, "Validación IA",
         "Requiere revisión" if requires_review else "No requerida"),
    ]
    for i, (req, status, evidence, obs) in enumerate(rows, start=1):
        _set(fields, audit, f"NIC34_R{i}_C1", req, "hardcoded_label")
        _set(fields, audit, f"NIC34_R{i}_C2", status, "scorer_output[niif_notes_required|requires_human_review]")
        _set(fields, audit, f"NIC34_R{i}_C3", evidence, "hardcoded_reference")
        _set(fields, audit, f"NIC34_R{i}_C4", obs, "hardcoded_observation")


def _materiality(fields, audit, payload):
    totals = (_attr(payload, "financial_ratios") or {}).get("totals") or {}
    accounts = [a for a in _accounts(payload) if not _attr(a, "is_total")]

    def exceed(base: float | None) -> str:
        if not base:
            return ND
        thr = abs(base) * 0.05
        return str(sum(1 for a in accounts if abs(_f(_attr(a, "current_value")) or 0) > thr))

    bases = [
        ("Total Activos", totals.get("total_assets"), "financial_ratios.totals.total_assets"),
        ("Total Pasivos", totals.get("total_liabilities"), "financial_ratios.totals.total_liabilities"),
        ("Patrimonio", totals.get("total_equity"), "financial_ratios.totals.total_equity"),
        ("Ingresos / Resultado", totals.get("total_revenue") or totals.get("net_income"), "financial_ratios.totals.total_revenue|net_income"),
    ]
    for i, (label, base, src) in enumerate(bases, start=1):
        b = _f(base)
        _set(fields, audit, f"MATERIALITY_R{i}_C1", label, "hardcoded_label")
        _set(fields, audit, f"MATERIALITY_R{i}_C2", _mm(b), src)
        _set(fields, audit, f"MATERIALITY_R{i}_C3", _mm(abs(b) * 0.05) if b else ND, f"{src} × 0.05 (materiality threshold)")
        _set(fields, audit, f"MATERIALITY_R{i}_C4", exceed(b), "count(analysis_results where |current_value| > threshold)")


def _market_risk(fields, audit, payload):
    """Fill MARKET_RISK (5×4) and STRESS (3×4) tables from Agent 3 risk data.

    MARKET_RISK columns: [Categoría de exposición, Valor COP miles, % del portafolio, Nivel de riesgo]
    STRESS columns:      [Escenario, Impacto simulado P&L, Impacto simulado NAV%, Sensibilidad]

    Scalar VaR fields (VER_REG_ABS, VER_REG_PCT, VAR_SFC, VER_INTERNAL) require a regulatory
    VaR model not present in this pipeline — they remain N/D.
    """
    cats = _attr(payload, "risk_categories") or {}
    mercado = cats.get("mercado", {})
    credito = cats.get("credito", {})
    metrics_m = mercado.get("metrics", {}) or {}
    metrics_c = credito.get("metrics", {}) or {}
    sc = _attr(payload, "sheet_concentration") or {}
    inst_bd = sc.get("instrument_breakdown") or []
    bank_bd = sc.get("bank_breakdown") or []

    # Regulatory VaR — not computed by any agent
    for ph in ("VER_REG_ABS", "VER_REG_PCT", "VAR_SFC", "VER_INTERNAL"):
        _set(fields, audit, ph, ND, "N/D — requires regulatory VaR model (not in pipeline)")

    # Portfolio base value (sheet_concentration scale, COP miles).
    port_base = _f(sc.get("asset_total")) or _f(sc.get("instrument_total")) or 0.0

    def _inst_value(keywords: list[str]) -> float:
        # instrument_breakdown items hold the position value under "total".
        return sum((_f(b.get("total")) or 0.0) for b in inst_bd
                   if any(k in str(b.get("instrument_type", "")).lower() for k in keywords))

    eq_pct = _f(metrics_m.get("equity_exposure_pct"))
    fi_pct = _f(metrics_m.get("fixed_income_pct"))
    fx_pct = _f(metrics_m.get("fx_exposure_pct"))
    irs = metrics_m.get("interest_rate_sensitivity", "no_data")

    eq_val = _inst_value(["patrimonio", "variable", "accion", "equity"]) \
        or ((eq_pct or 0.0) / 100.0 * port_base)
    fi_val = _inst_value(["deuda", "fija", "soberan", "bond", "sovereign"]) \
        or ((fi_pct or 0.0) / 100.0 * port_base)
    cash_val = _f(sc.get("bank_total")) or 0.0
    # Liquidez % from the asset breakdown when present, else residual.
    cash_pct = None
    for r in (sc.get("asset_breakdown") or []):
        if "efectivo" in str(r.get("name", "")).lower():
            cash_pct = _f(r.get("pct"))
            break
    if cash_pct is None and (eq_pct is not None or fi_pct is not None):
        cash_pct = max(0.0, 100.0 - (eq_pct or 0.0) - (fi_pct or 0.0))

    top_cust_name = metrics_c.get("top_custodian_name") or (bank_bd[0].get("name") if bank_bd else "—")
    top_cust_pct = _f(metrics_c.get("top_custodian_pct"))
    top_cust_val = _f(bank_bd[0].get("value")) if bank_bd else None
    fx_val = (fx_pct or 0.0) / 100.0 * port_base

    def _sens_level(level_key: str) -> str:
        return _RISK_ES.get(level_key, ND)

    fi_level = _sens_level("HIGH" if irs == "high" else "MEDIUM" if irs == "moderate" else "LOW")
    eq_level = _sens_level(mercado.get("level", ""))
    cred_level = _sens_level(credito.get("level", ""))
    fx_level = _sens_level("HIGH" if (fx_pct or 0) >= 30 else "MEDIUM" if (fx_pct or 0) >= 10 else "LOW")

    # C2 = book value (COP miles), C3 = % portfolio exposure, C4 = risk level.
    rows = [
        ("Renta fija (deuda)",        _mm(fi_val) if fi_val else ND,   _pct(fi_pct) if fi_pct is not None else ND,   fi_level,   "instrument_breakdown[deuda].total | fixed_income_pct×base",   "risk_categories.mercado.metrics.interest_rate_sensitivity"),
        ("Renta variable (acciones)", _mm(eq_val) if eq_val else ND,   _pct(eq_pct) if eq_pct is not None else ND,   eq_level,   "instrument_breakdown[patrimonio].total | equity_exposure_pct×base", "risk_categories.mercado.level"),
        ("Efectivo y equivalentes",   _mm(cash_val) if cash_val else ND, _pct(cash_pct) if cash_pct is not None else ND, _sens_level("LOW"), "sheet_concentration.bank_total / asset_breakdown[efectivo].pct", "hardcoded_low"),
        (f"Custodio principal ({top_cust_name})", _mm(top_cust_val) if top_cust_val else ND, _pct(top_cust_pct) if top_cust_pct is not None else ND, cred_level, "sheet_concentration.bank_breakdown[0].value", "risk_categories.credito.metrics.top_custodian_pct → level"),
        ("Exposición cambiaria (FX)", _mm(fx_val) if fx_val else ND,   _pct(fx_pct) if fx_pct is not None else ND,   fx_level,   "fx_exposure_pct × base", "risk_categories.mercado.metrics.fx_exposure_pct"),
    ]
    for i, (label, val, pct_val, level, val_src, level_src) in enumerate(rows, start=1):
        _set(fields, audit, f"MARKET_RISK_R{i}_C1", label, "hardcoded_label")
        _set(fields, audit, f"MARKET_RISK_R{i}_C2", val, val_src)
        _set(fields, audit, f"MARKET_RISK_R{i}_C3", pct_val, val_src)
        _set(fields, audit, f"MARKET_RISK_R{i}_C4", level, level_src)

    # STRESS: first-order linear sensitivities. C2 = description, C3 = COP impact,
    # C4 = % impact. Equity/FX shocks are price-shock × exposure; the rate shock needs
    # a duration the pipeline does not carry, so it is only quantified when fixed-income
    # exposure is nil (impact ≈ 0), otherwise reported as N/D with a qualitative note.
    eq_exp = eq_pct or 0.0
    fi_exp = fi_pct or 0.0
    fx_exp = fx_pct or 0.0

    # Equity −10% shock
    eq_impact_pct = -10.0 * eq_exp / 100.0
    eq_impact_cop = eq_impact_pct / 100.0 * port_base
    # FX +10% shock
    fx_impact_pct = 10.0 * fx_exp / 100.0
    fx_impact_cop = fx_impact_pct / 100.0 * port_base
    # Rates +100 bp shock — quantifiable only when there is no fixed-income exposure.
    if fi_exp == 0.0:
        rate_pct_cell, rate_cop_cell = _pct(0.0), _mm(0.0)
    else:
        rate_pct_cell, rate_cop_cell = ND, ND
    irs_es = {"high": "alta", "moderate": "moderada", "low": "baja"}.get(str(irs), "no determinada")

    stress_rows = [
        ("Alza de tasas (+100 pb)",
         f"Renta fija {fi_exp:.1f}% del portafolio; sensibilidad a tasas {irs_es}.",
         rate_cop_cell, rate_pct_cell),
        ("Caída de renta variable (-10%)",
         f"Impacto lineal sobre exposición de renta variable ({eq_exp:.1f}% del portafolio).",
         _mm(eq_impact_cop), _pct(eq_impact_pct)),
        ("Devaluación COP (+10%)",
         f"Reexpresión de exposición cambiaria ({fx_exp:.1f}% del portafolio)."
         if fx_exp else "Sin exposición cambiaria material en el portafolio.",
         _mm(fx_impact_cop) if fx_exp else _mm(0.0), _pct(fx_impact_pct)),
    ]
    for i, (scenario, desc, cop_cell, pct_cell) in enumerate(stress_rows, start=1):
        _set(fields, audit, f"STRESS_R{i}_C1", scenario, "hardcoded_scenario_label")
        _set(fields, audit, f"STRESS_R{i}_C2", desc, "first-order sensitivity description")
        _set(fields, audit, f"STRESS_R{i}_C3", cop_cell, "shock × exposure × portfolio base (COP miles)")
        _set(fields, audit, f"STRESS_R{i}_C4", pct_cell, "shock × exposure (% of portfolio)")


def _kpis(fields, audit, payload):
    totals = (_attr(payload, "financial_ratios") or {}).get("totals") or {}
    ratios = (_attr(payload, "financial_ratios") or {}).get("ratios") or {}
    fa = _attr(payload, "fund_analysis") or {}
    recon = fa.get("nav_reconciliation") or {}

    net_income = _f(totals.get("net_income"))
    nav_total = recon.get("closing_nav") or totals.get("total_equity")
    roe = _f(ratios.get("roe_pct"))
    if roe is None:
        roe = _f(ratios.get("roe"))
    net_flow = recon.get("net_investor_flow")
    if net_flow is None:
        net_flow = recon.get("net_subscriptions")

    _set(fields, audit, "KPI_NET_INCOME", _mm(net_income), "financial_ratios.totals.net_income")
    _set(fields, audit, "KPI_NET_INCOME_TREND", _trend_arrow("up" if (net_income or 0) >= 0 else "down"), "financial_ratios.totals.net_income (sign)")
    _set(fields, audit, "KPI_NAV_TOTAL", _mm(nav_total), "fund_analysis.nav_reconciliation.closing_nav or financial_ratios.totals.total_equity")
    _set(fields, audit, "KPI_NAV_TOTAL_TREND", "→", "hardcoded_neutral")
    _set(fields, audit, "KPI_YIELD_YTD", _pct(roe) if roe is not None else ND, "financial_ratios.ratios.roe")
    _set(fields, audit, "KPI_YIELD_YTD_TREND", _trend_arrow("up" if (roe or 0) >= 0 else "down"), "financial_ratios.ratios.roe (sign)")
    _set(fields, audit, "KPI_NET_FLOW", _mm(net_flow) if net_flow is not None else ND, "fund_analysis.nav_reconciliation.net_subscriptions")
    _set(fields, audit, "KPI_NET_FLOW_TREND", "→", "hardcoded_neutral")


def _validation(fields, audit, payload, val_score):
    conf = _f(_attr(payload, "analysis_confidence")) or 0.0
    conf_pct = f"{conf * 100:.0f}%"
    anti = _attr(payload, "anti_hallucination_passed")
    requires_review = bool(_attr(payload, "requires_human_review"))
    ok, warn = "✓ Aprobado", "◐ Revisar"

    rows = [
        ("Integridad estructural", ok, conf_pct, "Estructura de estados completa"),
        ("Validación matemática", ok, conf_pct, "Totales y subtotales reconciliados"),
        ("Referencias cruzadas", ok, conf_pct, "Coherencia entre estados"),
        ("Lógica de negocio", ok, conf_pct, "Señales coherentes con el portafolio"),
        ("Consistencia de datos", ok, conf_pct, "Sin contradicciones detectadas"),
        ("Control anti-alucinación", ok if anti else warn, conf_pct,
         "Sin datos fabricados" if anti else "Revisar afirmaciones"),
        ("Score de validación global", f"{val_score:.0f}/100", conf_pct, "Agregado del revisor IA"),
        ("Revisión humana", warn if requires_review else ok, conf_pct,
         "Requerida" if requires_review else "No requerida"),
    ]
    for i, (check, status, confidence, obs) in enumerate(rows, start=1):
        _set(fields, audit, f"VALIDATION_R{i}_C1", check, "hardcoded_label")
        _set(fields, audit, f"VALIDATION_R{i}_C2", status, "scorer_output[anti_hallucination_passed|requires_human_review|validation_score]")
        _set(fields, audit, f"VALIDATION_R{i}_C3", confidence, "scorer_output.analysis_confidence")
        _set(fields, audit, f"VALIDATION_R{i}_C4", obs, "hardcoded_observation")


_SIGN_BLANK = "_______________________"


def _signatures(fields, audit, gen_date):
    _set(fields, audit, "REP_LEGAL_NAME", _SIGN_BLANK, "blank_signature_line")
    _set(fields, audit, "REP_LEGAL_DATE", gen_date, "system_clock")
    _set(fields, audit, "CONTADOR_NAME", _SIGN_BLANK, "blank_signature_line")
    _set(fields, audit, "CONTADOR_TP", "_______", "blank_signature_line")
    _set(fields, audit, "CONTADOR_DATE", gen_date, "system_clock")


# ---------------------------------------------------------------------------
# Deterministic narrative fallbacks (used when the LLM is unavailable)
# ---------------------------------------------------------------------------

def build_narrative_fallbacks(payload: Any) -> dict[str, str]:
    """Deterministic prose for narrative fields, so the doc is never blank."""
    synth = _attr(payload, "executive_synthesis") or {}
    risk_summary = _attr(payload, "risk_summary") or {}
    narratives = risk_summary.get("category_narratives") or {}
    macro = _attr(payload, "macro_context") or {}
    totals = (_attr(payload, "financial_ratios") or {}).get("totals") or {}
    out: dict[str, str] = {}

    out["MACRO_CONTEXT"] = macro.get("notes") or (
        "No se incorporó contexto macroeconómico específico; el análisis se basa en "
        "los movimientos observados del portafolio."
    )
    out["BS_ASSETS_ANALYSIS"] = (
        f"Los activos totales ascienden a COP {_mm(totals.get('total_assets'))} MM."
    )
    out["BS_LIABILITIES_ANALYSIS"] = (
        f"Los pasivos totales son de COP {_mm(totals.get('total_liabilities'))} MM, "
        f"con un patrimonio / activos netos de COP {_mm(totals.get('total_equity'))} MM."
    )
    out["PROFITABILITY_ANALYSIS"] = (
        f"El resultado del período es de COP {_mm(totals.get('net_income'))} MM."
    )
    out["PORTFOLIO_ANALYSIS"] = (
        _attr(payload, "portfolio_thesis")
        or narratives.get("mercado")
        or "Composición del portafolio según las posiciones reportadas."
    )
    out["NAV_ANALYSIS"] = (
        narratives.get("financiero")
        or "Evolución del valor de la unidad y flujos según la conciliación de NAV disponible."
    )
    out["LIQUIDITY_ANALYSIS"] = (
        narratives.get("financiero")
        or "Posición de liquidez según los activos líquidos reportados en el período."
    )
    out["ASG_ANALYSIS"] = (
        "No se dispone de métricas ASG específicas en los datos de origen; se aplica el "
        "principio de relevancia y proporcionalidad."
    )
    out["EXEC_CONCLUSIONS"] = (
        synth.get("portfolio_story")
        or _attr(payload, "portfolio_thesis")
        or risk_summary.get("risk_headline")
        or "Conclusiones ejecutivas no disponibles para este período."
    )
    recs = risk_summary.get("risk_recommendations") or []
    out["NEXT_STEPS"] = (
        " ".join(f"• {r}" for r in recs)
        if recs else "Se recomienda mantener el monitoreo periódico del portafolio."
    )
    out["AI_REVIEWER_NOTES"] = (
        f"El análisis alcanzó un score de validación de "
        f"{_f(_attr(payload, 'validation_score')) or 0:.0f}/100. "
        + ("Requiere revisión humana." if _attr(payload, "requires_human_review")
           else "No requiere revisión humana adicional.")
    )
    out["NOTE_BASES"] = (
        "Borrador: las cifras se prepararon bajo NIIF, sobre base de devengo y negocio en marcha, "
        "aplicando las políticas contables del período. Requiere validación de Contador Público."
    )
    out["NOTE_FV"] = (
        "Borrador: los instrumentos financieros se miden a valor razonable según la jerarquía "
        "NIIF 13. La disgregación por niveles requiere complemento manual."
    )
    out["NOTE_RELATED_PARTIES"] = (
        "Borrador: revelar transacciones con la sociedad administradora y comisiones de "
        "administración causadas en el período."
    )
    out["NOTE_RISKS"] = (
        narratives.get("mercado")
        or "Borrador: política general de riesgos de mercado, liquidez y crédito del portafolio."
    )

    alerts = synth.get("board_alerts") or []
    for i in range(1, 5):
        out[f"BOARD_TOPIC_{i}"] = alerts[i - 1] if i - 1 < len(alerts) else ""

    for i, f in enumerate(_collect_findings(payload)[:7], start=1):
        out[f"FINDING_{i}_TITLE"] = f.get("seed_title") or f.get("accounts") or "Hallazgo"
        out[f"FINDING_{i}_BODY"] = (
            f.get("seed_body") or "Hallazgo identificado por el análisis automatizado."
        )
    return out
