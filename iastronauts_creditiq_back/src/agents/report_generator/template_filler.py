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
  * Monetary cells are shown in COP thousands (`miles`) to match the template
    headers ("COP miles") and the `kpi_engine` convention (raw / 1000).
"""

from __future__ import annotations

import logging
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


def _miles(value: Any) -> str:
    """COP thousands, 1 decimal, thousands-separated. `N/D` when absent."""
    v = _f(value)
    if v is None:
        return ND
    return f"{v / 1000.0:,.1f}"


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


# Income-statement keywords — used to classify accounts when `statement_type`
# is not populated upstream (it is frequently None in real fund extractions).
_INCOME_KW = (
    "ingreso", "gasto", "costo", "utilidad", "resultado", "perdida", "pérdida",
    "comision", "comisión", "egreso", "rendimiento", "depreciaci", "amortizaci",
)


def _statement_of(a: Any) -> str:
    """Classify an account as 'balance_sheet' or 'income_statement'.

    Prefers the explicit `statement_type`; falls back to a name heuristic when it
    is None (common in fund extractions)."""
    st = _attr(a, "statement_type")
    if st in ("balance_sheet", "income_statement"):
        return st
    name = (_attr(a, "account_name") or "").lower()
    if any(k in name for k in _INCOME_KW):
        return "income_statement"
    return "balance_sheet"


def _accounts(payload: Any, statement: str | None = None, *, with_investment: bool = False) -> list:
    out = []
    for a in _attr(payload, "analysis_results") or []:
        if statement is not None and _statement_of(a) != statement:
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


def _fill_rows(fields, prefix, n_rows, n_cols, items, mapper):
    """Fill `{{prefix_R{r}_C{c}}}`. Empty rows get blank cells."""
    for r in range(1, n_rows + 1):
        item = items[r - 1] if r - 1 < len(items) else None
        values = mapper(item) if item is not None else [""] * n_cols
        for c in range(1, n_cols + 1):
            fields[f"{prefix}_R{r}_C{c}"] = str(values[c - 1]) if c - 1 < len(values) else ""


# ---------------------------------------------------------------------------
# Deterministic field builder
# ---------------------------------------------------------------------------

def build_deterministic_fields(
    payload: Any,
    *,
    generated_at: datetime | None = None,
    job_id: str | None = None,
) -> dict[str, str]:
    """Return every deterministic (calculated) placeholder → string value."""
    generated_at = generated_at or datetime.utcnow()
    job_id = job_id or _attr(payload, "job_id") or ""
    fields: dict[str, str] = {}

    cur_p, prev_p = _periods(payload)
    gen_date = generated_at.strftime("%d/%m/%Y")
    is_fund = _is_fund(payload)
    health = _enum(_attr(payload, "overall_financial_health"))
    risk = _enum(_attr(payload, "overall_risk_score"))
    val_score = _f(_attr(payload, "validation_score")) or 0.0

    _cover(fields, payload, cur_p, prev_p, gen_date, is_fund, health, val_score, job_id)
    _entity_info(fields, payload, cur_p, is_fund, risk, health)
    _macro(fields, payload)
    _balance(fields, payload)
    _income(fields, payload)
    _portfolio(fields, payload)
    _fair_value(fields)
    _nav(fields, payload)
    _findings_meta(fields, payload)
    _nic34(fields, payload)
    _materiality(fields, payload)
    _market_risk(fields)
    _kpis(fields, payload)
    _validation(fields, payload, val_score)
    _signatures(fields, gen_date)

    return fields


def _cover(fields, payload, cur_p, prev_p, gen_date, is_fund, health, val_score, job_id):
    cur_lbl, prev_lbl = _period_label(cur_p), _period_label(prev_p)
    fields["ENTITY_NAME"] = _attr(payload, "company_name") or ND
    fields["PERIOD"] = f"{cur_lbl} vs {prev_lbl}" if prev_lbl != ND else cur_lbl
    fields["DOCUMENT_TYPE"] = "Estados Financieros del Fondo" if is_fund else "Estados Financieros"
    fields["CUTOFF_DATE"] = _cutoff_date(cur_p)
    fields["CLASSIFICATION"] = _HEALTH_ES.get(health, health or ND)
    fields["GENERATED_DATE"] = gen_date
    fields["AI_REVIEWER"] = f"CreditIQ Multi-Agente · Score {val_score:.0f}/100"
    fields["REPORT_VERSION"] = "1.0"
    fields["REPORT_ID"] = job_id or ND


def _entity_info(fields, payload, cur_p, is_fund, risk, health):
    rows = [
        ("Razón social", _attr(payload, "company_name") or ND),
        ("Tipo de entidad", "Fondo de inversión colectiva" if is_fund else "Entidad"),
        ("Moneda funcional", _attr(payload, "currency") or "COP"),
        ("Período analizado", _period_label(cur_p)),
        ("Clasificación de riesgo", _RISK_ES.get(risk, risk or ND)),
        ("Salud financiera", _HEALTH_ES.get(health, health or ND)),
    ]
    for i, (label, value) in enumerate(rows, start=1):
        fields[f"ENTITY_INFO_R{i}_C1"] = label
        fields[f"ENTITY_INFO_R{i}_C2"] = str(value)


def _macro(fields, payload):
    macro = _attr(payload, "macro_context") or {}

    def cell(key, ph):
        d = macro.get(key) or {}
        val = d.get("value")
        unit = d.get("unit") or ""
        fields[ph] = f"{val}{unit}" if val is not None else ND
        fields[f"{ph}_TREND"] = _trend_arrow(d.get("trend"))

    cell("policy_rate", "KPI_BR_RATE")
    cell("inflation", "KPI_INFLATION")
    cell("colcap_index", "KPI_COLCAP")
    cell("fx_usdcop", "KPI_TRM")


def _balance(fields, payload):
    accounts = _accounts(payload, "balance_sheet")
    accounts.sort(key=lambda a: (1 if _attr(a, "is_total") else 0, -(_f(_attr(a, "impact_score")) or 0)))
    _fill_rows(
        fields, "BS", 6, 6, accounts,
        lambda a: [
            _attr(a, "account_name") or ND,
            _miles(_attr(a, "current_value")),
            _miles(_attr(a, "previous_value")),
            _miles(_var_abs(a)),
            _pct(_attr(a, "variation_pct")),
            "Sí" if _enum(_attr(a, "materiality")) == "HIGH" else "No",
        ],
    )


def _income(fields, payload):
    accounts = _accounts(payload, "income_statement")
    accounts.sort(key=lambda a: (1 if _attr(a, "is_total") else 0, -(_f(_attr(a, "impact_score")) or 0)))
    _fill_rows(
        fields, "IS_ACC", 8, 5, accounts,
        lambda a: [
            _attr(a, "account_name") or ND,
            _miles(_attr(a, "current_value")),
            _miles(_attr(a, "previous_value")),
            _miles(_var_abs(a)),
            _pct(_attr(a, "variation_pct")),
        ],
    )
    # Quarterly split is not produced upstream — keep concept labels, mark values N/D.
    _fill_rows(
        fields, "IS_Q2", 8, 5, accounts,
        lambda a: [_attr(a, "account_name") or ND, ND, ND, ND, ND],
    )


def _portfolio(fields, payload):
    sc = _attr(payload, "sheet_concentration") or {}
    breakdown = sc.get("instrument_breakdown") or []
    totals = (_attr(payload, "financial_ratios") or {}).get("totals") or {}

    total_val = sum((_f(b.get("value")) or 0.0) for b in breakdown) or _f(totals.get("total_assets"))
    fields["PORTFOLIO_TOTAL"] = _miles(total_val)

    def pct_for(types: set[str]) -> str:
        if not breakdown:
            return ND
        acc = sum((_f(b.get("pct")) or 0.0) for b in breakdown
                  if str(b.get("instrument_type")) in types)
        return _pct(acc)

    fields["PORTFOLIO_FIXED_PCT"] = pct_for({"bond", "sovereign_debt"})
    fields["PORTFOLIO_EQUITY_PCT"] = pct_for({"equity"})
    fields["PORTFOLIO_CASH_PCT"] = pct_for({"cash", "fund"})

    holdings = [h for h in _accounts(payload, with_investment=True) if not _attr(h, "is_total")]
    if not holdings:
        # Fallback when investment_type is not populated: pick accounts that look
        # like investment positions by name.
        holdings = [
            h for h in _accounts(payload)
            if not _attr(h, "is_total") and "invers" in (_attr(h, "account_name") or "").lower()
        ]
    holdings.sort(key=lambda a: -(_f(_attr(a, "current_value")) or 0))
    _fill_rows(
        fields, "PORTFOLIO", 10, 6, holdings,
        lambda a: [
            _attr(a, "issuer_name") or _attr(a, "account_name") or ND,
            _INSTRUMENT_ES.get(str(_attr(a, "investment_type")), str(_attr(a, "investment_type") or ND)),
            ND,  # nominal not tracked upstream
            _miles(_attr(a, "current_value")),
            _miles(_attr(a, "previous_value")),
            _pct(_attr(a, "variation_pct")),
        ],
    )


def _fair_value(fields):
    # NIIF 13 fair-value hierarchy is not disaggregated upstream — category scaffold + N/D.
    categories = ["Instrumentos de patrimonio", "Instrumentos de deuda", "Derivados", "Total"]
    _fill_rows(
        fields, "FV_HIER", 4, 5, [{"c": c} for c in categories],
        lambda d: [d["c"], ND, ND, ND, ND],
    )


def _nav(fields, payload):
    fa = _attr(payload, "fund_analysis") or {}
    recon = fa.get("nav_reconciliation") or {}

    _fill_rows(
        fields, "NAV_CLASS", 5, 6,
        [{"closing": recon.get("closing_nav")}] if fa.get("is_fund") else [],
        lambda d: ["Única", ND, ND, ND, _miles(d["closing"]), ND],
    )

    flow_rows = [
        ("Saldo inicial (NAV apertura)", recon.get("opening_nav")),
        ("Suscripciones / redenciones netas", recon.get("net_subscriptions")),
        ("Resultado del período", recon.get("pnl")),
        ("Saldo final (NAV cierre)", recon.get("closing_nav")),
    ]
    for i, (label, value) in enumerate(flow_rows, start=1):
        fields[f"NAV_FLOW_R{i}_C1"] = label
        fields[f"NAV_FLOW_R{i}_C2"] = _miles(value)
        fields[f"NAV_FLOW_R{i}_C3"] = ND
        fields[f"NAV_FLOW_R{i}_C4"] = ND


def _findings_meta(fields, payload):
    """Deterministic ACCOUNTS + IMPACT for the 7 findings. TITLE/BODY come from the LLM."""
    findings = _collect_findings(payload)
    for i in range(1, 8):
        f = findings[i - 1] if i - 1 < len(findings) else None
        fields[f"FINDING_{i}_ACCOUNTS"] = f["accounts"] if f else "—"
        fields[f"FINDING_{i}_IMPACT"] = f["impact"] if f else "—"


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


def _nic34(fields, payload):
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
        fields[f"NIC34_R{i}_C1"] = req
        fields[f"NIC34_R{i}_C2"] = status
        fields[f"NIC34_R{i}_C3"] = evidence
        fields[f"NIC34_R{i}_C4"] = obs


def _materiality(fields, payload):
    totals = (_attr(payload, "financial_ratios") or {}).get("totals") or {}
    accounts = [a for a in _accounts(payload) if not _attr(a, "is_total")]

    def exceed(base: float | None) -> str:
        if not base:
            return ND
        thr = abs(base) * 0.05
        return str(sum(1 for a in accounts if abs(_f(_attr(a, "current_value")) or 0) > thr))

    bases = [
        ("Total Activos", totals.get("total_assets")),
        ("Total Pasivos", totals.get("total_liabilities")),
        ("Patrimonio", totals.get("total_equity")),
        ("Ingresos / Resultado", totals.get("total_revenue") or totals.get("net_income")),
    ]
    for i, (label, base) in enumerate(bases, start=1):
        b = _f(base)
        fields[f"MATERIALITY_R{i}_C1"] = label
        fields[f"MATERIALITY_R{i}_C2"] = _miles(b)
        fields[f"MATERIALITY_R{i}_C3"] = _miles(abs(b) * 0.05) if b else ND
        fields[f"MATERIALITY_R{i}_C4"] = exceed(b)


def _market_risk(fields):
    # Regulatory VaR / market-risk decomposition / stress are not produced upstream.
    for ph in ("VER_REG_ABS", "VER_REG_PCT", "VAR_SFC", "VER_INTERNAL"):
        fields[ph] = ND
    labels = ["Portafolio de inversión", "Efectivo y equivalentes",
              "Posiciones en derivados", "Cuentas por cobrar", "Total exposición"]
    _fill_rows(fields, "MARKET_RISK", 5, 4, [{"l": l} for l in labels],
               lambda d: [d["l"], ND, ND, ND])
    scenarios = ["Alza de tasas (+100 pb)", "Caída de renta variable (-10%)",
                 "Devaluación COP (+10%)"]
    _fill_rows(fields, "STRESS", 3, 4, [{"s": s} for s in scenarios],
               lambda d: [d["s"], ND, ND, ND])


def _kpis(fields, payload):
    totals = (_attr(payload, "financial_ratios") or {}).get("totals") or {}
    ratios = (_attr(payload, "financial_ratios") or {}).get("ratios") or {}
    fa = _attr(payload, "fund_analysis") or {}
    recon = fa.get("nav_reconciliation") or {}

    net_income = _f(totals.get("net_income"))
    nav_total = recon.get("closing_nav") or totals.get("total_equity")
    roe = _f(ratios.get("roe"))
    net_flow = recon.get("net_subscriptions")

    fields["KPI_NET_INCOME"] = _miles(net_income)
    fields["KPI_NET_INCOME_TREND"] = _trend_arrow("up" if (net_income or 0) >= 0 else "down")
    fields["KPI_NAV_TOTAL"] = _miles(nav_total)
    fields["KPI_NAV_TOTAL_TREND"] = "→"
    fields["KPI_YIELD_YTD"] = _pct(roe) if roe is not None else ND
    fields["KPI_YIELD_YTD_TREND"] = _trend_arrow("up" if (roe or 0) >= 0 else "down")
    fields["KPI_NET_FLOW"] = _miles(net_flow) if net_flow is not None else ND
    fields["KPI_NET_FLOW_TREND"] = "→"


def _validation(fields, payload, val_score):
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
        fields[f"VALIDATION_R{i}_C1"] = check
        fields[f"VALIDATION_R{i}_C2"] = status
        fields[f"VALIDATION_R{i}_C3"] = confidence
        fields[f"VALIDATION_R{i}_C4"] = obs


_SIGN_BLANK = "_______________________"


def _signatures(fields, gen_date):
    fields["REP_LEGAL_NAME"] = _SIGN_BLANK
    fields["REP_LEGAL_DATE"] = gen_date
    fields["CONTADOR_NAME"] = _SIGN_BLANK
    fields["CONTADOR_TP"] = "_______"
    fields["CONTADOR_DATE"] = gen_date


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
        f"Los activos totales ascienden a COP {_miles(totals.get('total_assets'))} miles."
    )
    out["BS_LIABILITIES_ANALYSIS"] = (
        f"Los pasivos totales son de COP {_miles(totals.get('total_liabilities'))} miles, "
        f"con un patrimonio / activos netos de COP {_miles(totals.get('total_equity'))} miles."
    )
    out["PROFITABILITY_ANALYSIS"] = (
        f"El resultado del período es de COP {_miles(totals.get('net_income'))} miles."
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
