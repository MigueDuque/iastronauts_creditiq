"""
operational_engine.py

Evaluates whether profit margins are sufficient to cover operating costs
and whether the business model is structurally sustainable.

Metrics used:
  - Net margin (margen_neto_pct)
  - ROE and ROA (return on equity/assets)
  - Expense ratio (total expenses / total revenue)
  - Admin cost growth vs revenue growth
  - Profitability trend (current vs previous net income)

Scoring (0–100, higher = less risk):
  Component 1 — Net margin / ROE:       max 40 pts
  Component 2 — Expense ratio:          max 35 pts
  Component 3 — Profitability trend:    max 25 pts
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from shared.models.base import RiskLevel


@dataclass
class OperationalRiskResult:
    level: RiskLevel
    score: int
    margen_neto_pct: float
    roe_pct: float
    roa_pct: float
    expense_ratio_pct: float          # total expenses / total revenue
    admin_cost_growth_pct: float | None
    revenue_growth_pct: float | None
    profitability_trend: str          # "improving" | "stable" | "deteriorating" | "no_data"
    key_findings: List[str] = field(default_factory=list)
    risk_drivers: List[str] = field(default_factory=list)


def _level_from_score(score: int) -> RiskLevel:
    if score >= 70:
        return RiskLevel.LOW
    elif score >= 40:
        return RiskLevel.MEDIUM
    return RiskLevel.HIGH


def score_operational(
    financial_ratios: dict,
    analysis_results: list,
) -> OperationalRiskResult:
    ratios = financial_ratios.get("ratios", {})
    totals = financial_ratios.get("totals", {})

    margen_neto = ratios.get("margen_neto_pct", 0.0)
    roe = ratios.get("roe_pct", 0.0)
    roa = ratios.get("roa_pct", 0.0)
    total_revenue = totals.get("total_revenue", 0.0)
    total_expense_abs = abs(totals.get("total_expense", 0.0))
    expense_ratio = round(total_expense_abs / total_revenue * 100, 2) if total_revenue > 0 else 0.0

    # Admin cost trend
    admin_current = sum(
        abs(a.get("current_value", 0.0))
        for a in analysis_results
        if "administrat" in a.get("account_name", "").lower()
        and a.get("current_value", 0.0) < 0
    )
    admin_previous = sum(
        abs(a.get("previous_value") or 0.0)
        for a in analysis_results
        if "administrat" in a.get("account_name", "").lower()
        and (a.get("previous_value") or 0.0) < 0
    )

    admin_growth: float | None = None
    if admin_previous > 0:
        admin_growth = round((admin_current - admin_previous) / admin_previous * 100, 2)

    # Revenue trend
    revenue_previous = sum(
        (a.get("previous_value") or 0.0)
        for a in analysis_results
        if a.get("account_name", "").lower().strip() in (
            "ingresos operacionales", "ingresos netos", "total ingresos"
        )
    )
    revenue_growth: float | None = None
    if revenue_previous and revenue_previous > 0:
        revenue_growth = round((total_revenue - revenue_previous) / revenue_previous * 100, 2)

    # Profitability trend from net income
    net_income = totals.get("net_income", 0.0)
    net_income_prev = None
    for a in analysis_results:
        name = a.get("account_name", "").lower()
        if "utilidad neta" in name or "utilidad del período" in name:
            prev = a.get("previous_value")
            if prev is not None:
                net_income_prev = prev
                break

    profitability_trend = "no_data"
    if net_income_prev is not None and net_income_prev != 0:
        delta_pct = (net_income - net_income_prev) / abs(net_income_prev) * 100
        if delta_pct > 10.0:
            profitability_trend = "improving"
        elif delta_pct < -10.0:
            profitability_trend = "deteriorating"
        else:
            profitability_trend = "stable"

    # ─── Component 1: Net margin / ROE ────────────────────────────────────────
    # For investment funds, net margin > 50% is common; use ROE as primary
    if roe >= 15.0:
        pts_margin = 40
    elif roe >= 5.0:
        pts_margin = 28
    elif roe >= 0.0:
        pts_margin = 12
    else:
        pts_margin = 0

    # Boost if net margin is also strong
    if margen_neto > 50.0:
        pts_margin = min(pts_margin + 5, 40)
    elif margen_neto < 0.0:
        pts_margin = max(pts_margin - 10, 0)

    # ─── Component 2: Expense ratio ───────────────────────────────────────────
    if expense_ratio < 5.0:
        pts_expense = 35
    elif expense_ratio < 15.0:
        pts_expense = 25
    elif expense_ratio < 30.0:
        pts_expense = 12
    else:
        pts_expense = 0

    # ─── Component 3: Profitability trend ─────────────────────────────────────
    if profitability_trend == "improving":
        pts_trend = 25
    elif profitability_trend == "stable":
        pts_trend = 18
    elif profitability_trend == "no_data":
        pts_trend = 15
    else:  # deteriorating
        pts_trend = 0

    # Penalty: admin costs growing much faster than revenue
    if admin_growth is not None and revenue_growth is not None:
        if admin_growth > 100.0 and admin_growth > revenue_growth:
            pts_trend = max(pts_trend - 8, 0)

    score = pts_margin + pts_expense + pts_trend

    # ─── Build narrative findings ──────────────────────────────────────────────
    findings: List[str] = []
    drivers: List[str] = []

    if roe < 0.0:
        findings.append(f"ROE negativo ({roe:.1f}%): el capital propio no está generando retorno — pérdida estructural.")
        drivers.append("ROE negativo")
    elif roe < 5.0:
        findings.append(f"ROE bajo ({roe:.1f}%): rentabilidad sobre patrimonio insuficiente.")
        drivers.append(f"ROE menor al 5% ({roe:.1f}%)")
    elif roe >= 15.0:
        findings.append(f"ROE elevado ({roe:.1f}%): gestión patrimonial eficiente.")

    if expense_ratio > 30.0:
        findings.append(f"Ratio de gastos elevado ({expense_ratio:.1f}% de ingresos): estructura de costos que presiona márgenes.")
        drivers.append(f"Gastos operacionales representan {expense_ratio:.1f}% de ingresos")
    elif expense_ratio < 5.0:
        findings.append(f"Estructura de costos eficiente ({expense_ratio:.1f}% de ingresos): operación con bajos gastos relativos.")

    if admin_growth is not None and revenue_growth is not None:
        if admin_growth > 100.0 and admin_growth > revenue_growth:
            findings.append(f"Gastos administrativos crecen {admin_growth:.1f}% vs ingresos {revenue_growth:+.1f}%: deterioro en eficiencia operativa.")
            drivers.append(f"Gastos administrativos crecen {admin_growth:.0f}% vs ingresos {revenue_growth:.0f}%")
        elif admin_growth is not None:
            findings.append(f"Gastos administrativos: +{admin_growth:.1f}%; ingresos: {revenue_growth:+.1f}%.")

    if profitability_trend == "improving":
        findings.append("Tendencia de rentabilidad en mejora respecto al período anterior.")
    elif profitability_trend == "deteriorating":
        findings.append("Tendencia de rentabilidad deteriorando respecto al período anterior.")
        drivers.append("Utilidad neta en caída vs período anterior")

    return OperationalRiskResult(
        level=_level_from_score(score),
        score=score,
        margen_neto_pct=margen_neto,
        roe_pct=roe,
        roa_pct=roa,
        expense_ratio_pct=expense_ratio,
        admin_cost_growth_pct=admin_growth,
        revenue_growth_pct=revenue_growth,
        profitability_trend=profitability_trend,
        key_findings=findings,
        risk_drivers=drivers,
    )
