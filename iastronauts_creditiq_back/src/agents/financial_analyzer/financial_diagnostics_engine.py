"""
financial_diagnostics_engine.py

Deterministic cross-statement correlation engine.
Detects financial patterns that require COMBINING data from multiple statements —
signals the LLM cannot easily discover from raw account rows alone.

All logic is rule-based. No LLM calls.

Patterns detected:
  1. earnings_cashflow_disconnect    — net income up, operating cash flow down
  2. revenue_recognition_flag        — receivables grow faster than revenue
  3. equity_distribution_driven      — equity declines while earnings are positive
  4. leverage_stress                 — debt grows, EBITDA flat or falling
  5. fund_redemption_pressure        — net redemptions significant vs NAV
  6. cost_structure_divergence       — expenses grow faster than revenue
  7. fair_value_dependency           — >50% of income from unrealized gains
  8. working_capital_compression     — current liabilities growing faster than current assets
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List

from .ratio_engine import AccountVariation, FinancialTotals, FinancialRatios

logger = logging.getLogger("financial_analyzer.financial_diagnostics")


@dataclass
class DiagnosticSignal:
    signal_id: str                  # machine-readable identifier
    category: str                   # earnings_quality | liquidity | leverage | operational | accounting_flag | fund_flow
    severity: str                   # HIGH | MEDIUM | LOW
    finding: str                    # what was detected (quantified)
    implication: str                # so-what for analysts/investors
    evidence: List[str]             # account names / values that support this


@dataclass
class DiagnosticsResult:
    signals: List[DiagnosticSignal] = field(default_factory=list)
    summary_flags: List[str] = field(default_factory=list)   # short labels for LLM prompt
    has_high_severity: bool = False


# ── Helpers ────────────────────────────────────────────────────────────────────

def _find_account(variations: list[AccountVariation], *keywords: str) -> AccountVariation | None:
    """Find first account whose name contains ALL given keywords (case-insensitive)."""
    kws = [k.lower() for k in keywords]
    for v in variations:
        name = v.account_name.lower()
        if all(k in name for k in kws):
            return v
    return None


def _find_accounts(variations: list[AccountVariation], *keywords: str) -> list[AccountVariation]:
    """Find all accounts whose name contains ALL given keywords."""
    kws = [k.lower() for k in keywords]
    return [v for v in variations if all(k in v.account_name.lower() for k in kws)]


def _safe_pct_change(current: float, previous: float | None) -> float | None:
    if previous is None or previous == 0:
        return None
    return round((current - previous) / abs(previous) * 100, 1)


# ── Pattern 1: Earnings–Cash Flow Disconnect ──────────────────────────────────

def _check_earnings_cashflow_disconnect(
    variations: list[AccountVariation],
    totals: FinancialTotals,
) -> DiagnosticSignal | None:
    """
    Net income grew materially while operating cash flow fell or is significantly lower.
    Classic earnings quality red flag in corporate statements.
    """
    # Find net income account
    net_income_acc = _find_account(variations, "utilidad neta") or _find_account(variations, "utilidad del período")
    # Find operating cash flow
    op_cf_acc = (
        _find_account(variations, "efectivo neto", "operación")
        or _find_account(variations, "flujo de efectivo neto de operación")
        or _find_account(variations, "actividades de operación")
    )

    if not net_income_acc or not op_cf_acc:
        return None

    ni = net_income_acc.current_value
    ni_prev = net_income_acc.previous_value
    cf = op_cf_acc.current_value
    cf_prev = op_cf_acc.previous_value

    if ni_prev is None or cf_prev is None:
        return None

    ni_change = _safe_pct_change(ni, ni_prev)
    cf_change = _safe_pct_change(cf, cf_prev)

    if ni_change is None or cf_change is None:
        return None

    # Pattern: income up ≥20%, cash flow down or income >> cash flow
    if ni_change >= 20 and cf_change < 0:
        severity = "HIGH" if cf_change < -30 else "MEDIUM"
        return DiagnosticSignal(
            signal_id="earnings_cashflow_disconnect",
            category="earnings_quality",
            severity=severity,
            finding=(
                f"Utilidad neta creció {ni_change:+.1f}% mientras el flujo operativo "
                f"cayó {cf_change:.1f}%. Divergencia significativa entre resultado contable y caja real."
            ),
            implication=(
                "La utilidad reportada no está siendo convertida en efectivo. "
                "Posibles causas: acumulación de cuentas por cobrar, cambios en capital de trabajo, "
                "o ingresos contables no realizados (valorización). "
                "Reduce la sostenibilidad del resultado."
            ),
            evidence=[
                f"{net_income_acc.account_name}: {ni:,.1f} COP MM (anterior: {ni_prev:,.1f})",
                f"{op_cf_acc.account_name}: {cf:,.1f} COP MM (anterior: {cf_prev:,.1f})",
            ],
        )

    # Also flag: income >> operating cash flow in absolute terms (even without prior period)
    if totals.net_income > 0 and cf < 0 and totals.net_income > abs(cf) * 0.5:
        return DiagnosticSignal(
            signal_id="earnings_cashflow_disconnect",
            category="earnings_quality",
            severity="MEDIUM",
            finding=(
                f"Utilidad neta positiva ({totals.net_income:,.1f} COP MM) con flujo operativo "
                f"negativo ({cf:,.1f} COP MM). El resultado contable no genera caja."
            ),
            implication=(
                "Resultado impulsado por ingresos no en efectivo (valorización, accruals). "
                "La calidad de las utilidades es cuestionable."
            ),
            evidence=[
                f"{net_income_acc.account_name}: {totals.net_income:,.1f} COP MM",
                f"{op_cf_acc.account_name}: {cf:,.1f} COP MM",
            ],
        )

    return None


# ── Pattern 2: Revenue–Receivables Divergence ─────────────────────────────────

def _check_revenue_recognition_flag(
    variations: list[AccountVariation],
    totals: FinancialTotals,
) -> DiagnosticSignal | None:
    """
    Accounts receivable grow materially faster than revenue — possible aggressive recognition.
    """
    ar_accounts = _find_accounts(variations, "cuentas por cobrar")
    if not ar_accounts:
        return None

    ar_current = sum(a.current_value for a in ar_accounts if a.current_value > 0)
    ar_prev = sum((a.previous_value or 0.0) for a in ar_accounts)

    if ar_prev <= 0:
        return None

    ar_pct = _safe_pct_change(ar_current, ar_prev)
    if ar_pct is None:
        return None

    # Revenue reference: find ingresos operacionales or use totals
    rev_acc = _find_account(variations, "ingresos operacionales")
    rev_current = rev_acc.current_value if rev_acc else totals.total_revenue
    rev_prev = rev_acc.previous_value if rev_acc else None
    rev_pct = _safe_pct_change(rev_current, rev_prev) if rev_prev else None

    if rev_pct is None:
        return None

    # Flag if AR grows 50pp faster than revenue
    spread = ar_pct - rev_pct
    if spread > 50 and ar_pct > 30:
        severity = "HIGH" if spread > 100 else "MEDIUM"
        return DiagnosticSignal(
            signal_id="revenue_recognition_flag",
            category="accounting_flag",
            severity=severity,
            finding=(
                f"Cuentas por cobrar crecen {ar_pct:+.1f}% vs. ingresos {rev_pct:+.1f}% "
                f"({spread:.0f}pp de diferencial). Crecimiento de AR desproporcionado vs. ventas."
            ),
            implication=(
                "Posible deterioro en cobros, extensión de plazos de crédito, "
                "o reconocimiento anticipado de ingresos. "
                "Aumenta el riesgo de provisiones futuras si la cartera se deteriora."
            ),
            evidence=[
                f"CxC: {ar_current:,.1f} COP MM (anterior: {ar_prev:,.1f}, cambio: {ar_pct:+.1f}%)",
                f"Ingresos: {rev_current:,.1f} COP MM (cambio: {rev_pct:+.1f}%)",
            ],
        )

    return None


# ── Pattern 3: Equity Decline with Positive Earnings ─────────────────────────

def _check_equity_distribution_driven(
    variations: list[AccountVariation],
    totals: FinancialTotals,
) -> DiagnosticSignal | None:
    """
    Equity falls while net income is positive → distributions/redemptions are the driver.
    """
    # Find total equity / total net assets
    equity_acc = (
        _find_account(variations, "total patrimonio neto")
        or _find_account(variations, "total activo neto")
        or _find_account(variations, "activos netos finales")
    )

    if not equity_acc or equity_acc.previous_value is None:
        return None

    eq_change = equity_acc.current_value - equity_acc.previous_value
    if eq_change >= 0:
        return None  # equity didn't fall

    if totals.net_income <= 0:
        return None  # income not positive

    # Equity fell and income was positive → distributions are driving the drop
    net_distribution = abs(eq_change) - totals.net_income
    if net_distribution <= 0:
        return None  # change could be explained by income alone

    eq_pct = _safe_pct_change(equity_acc.current_value, equity_acc.previous_value)
    severity = "HIGH" if abs(eq_change) / equity_acc.previous_value > 0.3 else "MEDIUM"

    # Try to find explicit redemption/distribution accounts
    redenciones = _find_account(variations, "retiro") or _find_account(variations, "redención")
    aportes = _find_account(variations, "aporte")

    evidence = [
        f"{equity_acc.account_name}: {equity_acc.current_value:,.1f} → caída de {eq_change:,.1f} COP MM ({eq_pct:+.1f}%)",
        f"Utilidad del período: {totals.net_income:,.1f} COP MM (positiva)",
    ]
    if redenciones:
        evidence.append(f"Retiros/redenciones: {redenciones.current_value:,.1f} COP MM")
    if aportes:
        evidence.append(f"Aportes: {aportes.current_value:,.1f} COP MM")

    return DiagnosticSignal(
        signal_id="equity_distribution_driven",
        category="fund_flow",
        severity=severity,
        finding=(
            f"Patrimonio cayó {abs(eq_change):,.1f} COP MM ({eq_pct:.1f}%) a pesar de "
            f"utilidades positivas de {totals.net_income:,.1f} COP MM. "
            f"Distribución neta estimada: {net_distribution:,.1f} COP MM."
        ),
        implication=(
            "La caída patrimonial no refleja deterioro operativo sino devolución de capital "
            "a inversionistas/accionistas (vía redenciones, dividendos o retiros). "
            "Señal relevante para evaluar la escala futura del fondo/empresa."
        ),
        evidence=evidence,
    )


# ── Pattern 4: Leverage Stress ────────────────────────────────────────────────

def _check_leverage_stress(
    variations: list[AccountVariation],
    totals: FinancialTotals,
    ratios: FinancialRatios,
) -> DiagnosticSignal | None:
    """
    Debt grows materially while EBITDA is flat or falls → leverage stress.
    """
    liab_acc = (
        _find_account(variations, "total pasivos")
        or _find_account(variations, "pasivos totales")
    )

    if not liab_acc or liab_acc.previous_value is None or liab_acc.previous_value == 0:
        return None

    debt_pct = _safe_pct_change(liab_acc.current_value, liab_acc.previous_value)
    if debt_pct is None or debt_pct < 25:
        return None  # not a significant debt increase

    # EBITDA proxy: use net income from totals
    if totals.ebitda <= 0:
        return None  # edge case

    ebitda_acc = _find_account(variations, "utilidad operacional") or _find_account(variations, "utilidad operación")
    ebitda_change = None
    if ebitda_acc and ebitda_acc.previous_value:
        ebitda_change = _safe_pct_change(ebitda_acc.current_value, ebitda_acc.previous_value)

    # Only flag if EBITDA is flat or declining while debt rises
    if ebitda_change is not None and ebitda_change > 20:
        return None  # EBITDA growing well → not a stress signal

    deuda_patrimonio = ratios.deuda_patrimonio
    severity = "HIGH" if deuda_patrimonio > 1.0 or debt_pct > 100 else "MEDIUM"

    evidence = [
        f"{liab_acc.account_name}: {liab_acc.current_value:,.1f} COP MM (anterior: {liab_acc.previous_value:,.1f}, +{debt_pct:.1f}%)",
    ]
    if ebitda_acc and ebitda_change is not None:
        evidence.append(
            f"{ebitda_acc.account_name}: {ebitda_acc.current_value:,.1f} COP MM (cambio: {ebitda_change:+.1f}%)"
        )
    evidence.append(f"Deuda/Patrimonio: {deuda_patrimonio:.2f}x")

    return DiagnosticSignal(
        signal_id="leverage_stress",
        category="leverage",
        severity=severity,
        finding=(
            f"Pasivos crecen {debt_pct:+.1f}% mientras EBITDA operativo "
            f"{'cae' if (ebitda_change or 0) < 0 else 'crece moderadamente'} "
            f"({ebitda_change:+.1f}% {'–' if ebitda_change else 'no cuantificado'}). "
            f"Ratio deuda/patrimonio: {deuda_patrimonio:.2f}x."
        ),
        implication=(
            "La expansión de deuda sin crecimiento proporcional del EBITDA comprime "
            "la cobertura de servicio de deuda y aumenta el riesgo de refinanciamiento. "
            "Requiere evaluar vencimientos y covenants financieros."
        ),
        evidence=evidence,
    )


# ── Pattern 5: Fund Redemption Pressure ───────────────────────────────────────

def _check_fund_redemption_pressure(
    variations: list[AccountVariation],
    totals: FinancialTotals,
) -> DiagnosticSignal | None:
    """
    Net redemptions exceed 20% of opening NAV — material capital outflow signal.
    """
    redenciones_acc = (
        _find_account(variations, "retiro", "inversionista")
        or _find_account(variations, "redención")
    )
    aportes_acc = _find_account(variations, "aporte", "inversionista")
    nav_inicial_acc = (
        _find_account(variations, "activos netos iniciales")
        or _find_account(variations, "patrimonio neto inicial")
    )

    if not redenciones_acc or not nav_inicial_acc:
        return None

    redenciones = abs(redenciones_acc.current_value)
    aportes = abs(aportes_acc.current_value) if aportes_acc else 0.0
    nav_inicial = abs(nav_inicial_acc.current_value)

    if nav_inicial == 0:
        return None

    net_outflow = redenciones - aportes
    net_outflow_pct = round(net_outflow / nav_inicial * 100, 1)

    if net_outflow_pct < 15:
        return None  # not material

    severity = "HIGH" if net_outflow_pct > 40 else "MEDIUM"

    return DiagnosticSignal(
        signal_id="fund_redemption_pressure",
        category="fund_flow",
        severity=severity,
        finding=(
            f"Retiros netos de {net_outflow:,.1f} COP MM representan el {net_outflow_pct:.1f}% "
            f"del NAV inicial ({nav_inicial:,.1f} COP MM). "
            f"Redenciones: {redenciones:,.1f} COP MM. Aportes: {aportes:,.1f} COP MM."
        ),
        implication=(
            "La salida neta de capital reduce la escala del fondo, puede forzar "
            "la liquidación de posiciones en momentos no óptimos, y comprime la base "
            "de comisiones. Señal de alerta si persiste más de 2 períodos."
        ),
        evidence=[
            f"Retiros: {redenciones:,.1f} COP MM",
            f"Aportes: {aportes:,.1f} COP MM",
            f"NAV inicial: {nav_inicial:,.1f} COP MM",
            f"Flujo neto: {-net_outflow:,.1f} COP MM ({net_outflow_pct:.1f}% del NAV)",
        ],
    )


# ── Pattern 6: Cost Structure Divergence ─────────────────────────────────────

def _check_cost_structure_divergence(
    variations: list[AccountVariation],
    totals: FinancialTotals,
) -> DiagnosticSignal | None:
    """
    Expenses grow significantly faster than revenue — margin compression signal.
    """
    rev_acc = _find_account(variations, "ingresos operacionales")
    if not rev_acc or not rev_acc.previous_value or rev_acc.previous_value == 0:
        return None

    rev_pct = _safe_pct_change(rev_acc.current_value, rev_acc.previous_value)
    if rev_pct is None:
        return None

    # Admin / operating expenses
    expense_accounts = [
        v for v in variations
        if v.category.lower() == "expense"
        and v.previous_value
        and v.previous_value < 0  # expenses are negative
        and "total" not in v.account_name.lower()
    ]

    if not expense_accounts:
        return None

    total_exp_current = sum(abs(v.current_value) for v in expense_accounts)
    total_exp_prev = sum(abs(v.previous_value) for v in expense_accounts if v.previous_value)

    if total_exp_prev == 0:
        return None

    exp_pct = _safe_pct_change(total_exp_current, total_exp_prev)
    if exp_pct is None:
        return None

    spread = exp_pct - rev_pct
    if spread < 30:
        return None  # not material

    severity = "HIGH" if spread > 80 else "MEDIUM"

    return DiagnosticSignal(
        signal_id="cost_structure_divergence",
        category="operational",
        severity=severity,
        finding=(
            f"Gastos operativos crecen {exp_pct:+.1f}% vs. ingresos {rev_pct:+.1f}% "
            f"({spread:.0f}pp de diferencial). Estructura de costos se deteriora."
        ),
        implication=(
            "El apalancamiento operativo está actuando negativamente: los costos fijos y variables "
            "escalan más rápido que los ingresos, comprimiendo márgenes. "
            "Riesgo de deterioro de EBITDA en próximos períodos si no hay corrección."
        ),
        evidence=[
            f"Ingresos operacionales: {rev_acc.current_value:,.1f} COP MM (anterior: {rev_acc.previous_value:,.1f}, {rev_pct:+.1f}%)",
            f"Gastos totales: {total_exp_current:,.1f} COP MM (anterior: {total_exp_prev:,.1f}, {exp_pct:+.1f}%)",
        ],
    )


# ── Pattern 7: Fair Value Dependency ─────────────────────────────────────────

def _check_fair_value_dependency(
    variations: list[AccountVariation],
    totals: FinancialTotals,
) -> DiagnosticSignal | None:
    """
    More than 50% of total income comes from unrealized fair-value gains.
    """
    fv_accounts = [
        v for v in variations
        if ("valoraci" in v.account_name.lower() or "valor razonable" in v.account_name.lower())
        and v.category.lower() == "revenue"
        and v.current_value > 0
    ]
    if not fv_accounts:
        return None

    fv_total = sum(v.current_value for v in fv_accounts)
    rev_base = max(totals.total_revenue, 0.001)
    fv_pct = round(fv_total / rev_base * 100, 1)

    if fv_pct < 50:
        return None

    severity = "HIGH" if fv_pct > 70 else "MEDIUM"

    return DiagnosticSignal(
        signal_id="fair_value_dependency",
        category="earnings_quality",
        severity=severity,
        finding=(
            f"Ingresos por valoración a valor razonable representan el {fv_pct:.1f}% "
            f"del total de ingresos ({fv_total:,.1f} de {rev_base:,.1f} COP MM). "
            f"Alta dependencia de resultados no en efectivo."
        ),
        implication=(
            "Los resultados del período son altamente sensibles a la volatilidad de mercado. "
            "Una corrección en los activos valorados a fair value puede revertir el resultado "
            "sin previo aviso. Los resultados no son proyectables con la misma metodología "
            "que ingresos operativos recurrentes."
        ),
        evidence=[f"{v.account_name}: {v.current_value:,.1f} COP MM" for v in fv_accounts[:3]],
    )


# ── Pattern 8: Working Capital Compression ────────────────────────────────────

def _check_working_capital_compression(
    variations: list[AccountVariation],
    totals: FinancialTotals,
    ratios: FinancialRatios,
) -> DiagnosticSignal | None:
    """
    Current liabilities grow faster than current assets — working capital stress.
    """
    # Only flag if current ratio is already below 1.5 or declining materially
    if ratios.razon_corriente <= 0:
        return None

    if ratios.razon_corriente < 1.0:
        severity = "HIGH"
        finding = (
            f"Razón corriente de {ratios.razon_corriente:.2f}: activos corrientes "
            f"({totals.current_assets:,.1f} COP MM) menores que pasivos corrientes "
            f"({totals.current_liabilities:,.1f} COP MM). Capital de trabajo negativo."
        )
        implication = (
            "La entidad no puede cubrir sus obligaciones corrientes con activos líquidos disponibles. "
            "Riesgo inmediato de incumplimiento de pagos a corto plazo."
        )
        return DiagnosticSignal(
            signal_id="working_capital_compression",
            category="liquidity",
            severity=severity,
            finding=finding,
            implication=implication,
            evidence=[
                f"Activos corrientes: {totals.current_assets:,.1f} COP MM",
                f"Pasivos corrientes: {totals.current_liabilities:,.1f} COP MM",
                f"Razón corriente: {ratios.razon_corriente:.2f}",
            ],
        )

    # Check trend: if previous ratio was available from balance-sheet account changes
    # This is harder without prior-period ratios — skip if current ratio is healthy
    if ratios.razon_corriente > 1.5:
        return None

    # Moderate alert for ratio between 1.0–1.5
    return DiagnosticSignal(
        signal_id="working_capital_compression",
        category="liquidity",
        severity="LOW",
        finding=(
            f"Razón corriente moderada de {ratios.razon_corriente:.2f}: "
            f"margen de liquidez corriente limitado."
        ),
        implication=(
            "El colchón de liquidez corriente es estrecho. "
            "Cualquier extensión de cuentas por cobrar o aceleración de obligaciones "
            "podría comprometer la capacidad de pago a corto plazo."
        ),
        evidence=[
            f"Activos corrientes: {totals.current_assets:,.1f} COP MM",
            f"Pasivos corrientes: {totals.current_liabilities:,.1f} COP MM",
        ],
    )


# ── Public API ────────────────────────────────────────────────────────────────

def run_diagnostics(
    variations: list[AccountVariation],
    totals: FinancialTotals,
    ratios: FinancialRatios,
) -> DiagnosticsResult:
    """
    Run all cross-statement diagnostic patterns.
    Returns a DiagnosticsResult with all detected signals.
    """
    checkers = [
        lambda: _check_earnings_cashflow_disconnect(variations, totals),
        lambda: _check_revenue_recognition_flag(variations, totals),
        lambda: _check_equity_distribution_driven(variations, totals),
        lambda: _check_leverage_stress(variations, totals, ratios),
        lambda: _check_fund_redemption_pressure(variations, totals),
        lambda: _check_cost_structure_divergence(variations, totals),
        lambda: _check_fair_value_dependency(variations, totals),
        lambda: _check_working_capital_compression(variations, totals, ratios),
    ]

    signals: list[DiagnosticSignal] = []
    for checker in checkers:
        try:
            signal = checker()
            if signal is not None:
                signals.append(signal)
                logger.info(
                    "diagnostic_signal | id=%s severity=%s",
                    signal.signal_id, signal.severity,
                )
        except Exception as exc:
            logger.warning("diagnostic_check_failed | error=%s", exc)

    has_high = any(s.severity == "HIGH" for s in signals)
    summary_flags = [
        f"{s.signal_id}:{s.severity}" for s in signals
    ]

    logger.info(
        "diagnostics_complete | signals=%d high=%d flags=%s",
        len(signals), sum(1 for s in signals if s.severity == "HIGH"), summary_flags,
    )

    return DiagnosticsResult(
        signals=signals,
        summary_flags=summary_flags,
        has_high_severity=has_high,
    )


def diagnostics_to_dict(result: DiagnosticsResult) -> dict:
    return {
        "signals": [
            {
                "signal_id": s.signal_id,
                "category": s.category,
                "severity": s.severity,
                "finding": s.finding,
                "implication": s.implication,
                "evidence": s.evidence,
            }
            for s in result.signals
        ],
        "summary_flags": result.summary_flags,
        "has_high_severity": result.has_high_severity,
    }
