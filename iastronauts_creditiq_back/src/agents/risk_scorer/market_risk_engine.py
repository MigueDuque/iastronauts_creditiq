"""
market_risk_engine.py

Measures vulnerability to external market fluctuations.

For investment funds (primary driver):
  - Fair value income dependency: % of P&L derived from unrealized gains
  - Equity market exposure: % of assets in mark-to-market instruments
  - Issuer concentration (HHI, effective positions)
  - Sector concentration

For operating companies:
  - Currency exposure (FX risk)
  - Interest rate risk (financial expenses trend)
  - Revenue concentration

Scoring (0–100, higher = less risk):
  Component 1 — Fair value income dependency: max 35 pts
  Component 2 — Asset concentration (HHI):   max 35 pts
  Component 3 — Sector / diversification:    max 30 pts
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Dict

from shared.models.base import RiskLevel


@dataclass
class MarketRiskResult:
    level: RiskLevel
    score: int
    fair_value_income_pct: float            # fair value gains / total income
    equity_exposure_pct: float              # equities % of investable assets (funds)
    top_issuer_pct: float | None            # top single issuer % (funds)
    top3_concentration_pct: float | None    # top 3 positions as % of portfolio
    hhi: float | None                       # Herfindahl-Hirschman Index (0–1)
    effective_positions: float | None       # 1 / HHI equivalent
    key_findings: List[str] = field(default_factory=list)
    risk_drivers: List[str] = field(default_factory=list)
    is_fund_adjusted: bool = False


def _level_from_score(score: int) -> RiskLevel:
    if score >= 70:
        return RiskLevel.LOW
    elif score >= 40:
        return RiskLevel.MEDIUM
    return RiskLevel.HIGH


def score_market_risk(
    financial_ratios: dict,
    earnings_quality: dict,
    portfolio_concentration: dict,
    fund_analysis: dict,
    analysis_results: list,
    is_investment_fund: bool,
) -> MarketRiskResult:
    totals = financial_ratios.get("totals", {})
    total_revenue = totals.get("total_revenue", 0.0)
    total_assets = totals.get("total_assets", 0.0)

    # Fair value income dependency
    fair_value_income_ratio = earnings_quality.get("fair_value_income_ratio", 0.0)
    fair_value_pct = round(fair_value_income_ratio * 100, 2)

    # If fair_value_income_ratio is 0 but we have the accounts, compute directly.
    # Match only income accounts (category=revenue) to avoid picking up asset accounts
    # like "Activos financieros a valor razonable".
    if fair_value_pct == 0.0 and total_revenue > 0:
        fv_income = sum(
            a.get("current_value", 0.0)
            for a in analysis_results
            if (
                "ganancia por valoraci" in a.get("account_name", "").lower()
                or (
                    "valoraci" in a.get("account_name", "").lower()
                    and a.get("category", "").lower() == "revenue"
                )
            )
            and a.get("current_value", 0.0) > 0
        )
        # Use ingresos operacionales as the income base if available (avoids double-counting totals)
        income_base = next(
            (
                a.get("current_value", 0.0)
                for a in analysis_results
                if a.get("account_name", "").lower().strip() == "ingresos operacionales"
                and a.get("current_value", 0.0) > 0
            ),
            total_revenue,
        )
        fair_value_pct = round(fv_income / income_base * 100, 2) if income_base > 0 else 0.0

    # Equity exposure (investment funds)
    equity_exposure_pct = 0.0
    if is_investment_fund:
        equity_assets = sum(
            a.get("current_value", 0.0)
            for a in analysis_results
            if "acciones" in a.get("account_name", "").lower()
            and "total" not in a.get("account_name", "").lower()
            and a.get("current_value", 0.0) > 0
        )
        investable = sum(
            a.get("current_value", 0.0)
            for a in analysis_results
            if (
                "acciones" in a.get("account_name", "").lower()
                or "fondo" in a.get("account_name", "").lower()
                or "efectivo" in a.get("account_name", "").lower()
            )
            and "total" not in a.get("account_name", "").lower()
            and "flujo" not in a.get("account_name", "").lower()
            and "neto" not in a.get("account_name", "").lower()
            and a.get("current_value", 0.0) > 0
        )
        if investable > 0:
            equity_exposure_pct = round(equity_assets / investable * 100, 2)

    # Concentration from portfolio_concentration engine output
    hhi = portfolio_concentration.get("hhi")
    effective_positions = portfolio_concentration.get("effective_positions")

    # Build top-issuer concentration from individual equity positions (exclude aggregates)
    top_issuer_pct: float | None = None
    top3_concentration_pct: float | None = None

    if is_investment_fund:
        equity_positions = sorted(
            [
                a.get("current_value", 0.0)
                for a in analysis_results
                if (
                    "acciones" in a.get("account_name", "").lower()
                    or "fondo de invers" in a.get("account_name", "").lower()
                )
                and "total" not in a.get("account_name", "").lower()
                and a.get("current_value", 0.0) > 0
            ],
            reverse=True,
        )
        portfolio_total = sum(equity_positions) if equity_positions else 0.0
        if portfolio_total > 0 and equity_positions:
            top_issuer_pct = round(equity_positions[0] / portfolio_total * 100, 2)
            top3_sum = sum(equity_positions[:3])
            top3_concentration_pct = round(top3_sum / portfolio_total * 100, 2)

    # ─── Component 1: Fair value dependency ───────────────────────────────────
    if fair_value_pct < 30.0:
        pts_fv = 35
    elif fair_value_pct < 50.0:
        pts_fv = 25
    elif fair_value_pct < 70.0:
        pts_fv = 12
    else:
        pts_fv = 0

    # ─── Component 2: Issuer / HHI concentration ──────────────────────────────
    if hhi is not None:
        if hhi < 0.15:
            pts_hhi = 35
        elif hhi < 0.25:
            pts_hhi = 20
        elif hhi < 0.40:
            pts_hhi = 8
        else:
            pts_hhi = 0
    elif top_issuer_pct is not None:
        if top_issuer_pct < 15.0:
            pts_hhi = 35
        elif top_issuer_pct < 25.0:
            pts_hhi = 20
        elif top_issuer_pct < 40.0:
            pts_hhi = 8
        else:
            pts_hhi = 0
    else:
        pts_hhi = 20  # neutral if no data

    # ─── Component 3: Equity exposure / diversification ───────────────────────
    if is_investment_fund:
        # Pure equity fund → inherent market risk; score on diversification breadth
        if top3_concentration_pct is not None:
            if top3_concentration_pct < 50.0:
                pts_div = 30
            elif top3_concentration_pct < 70.0:
                pts_div = 15
            else:
                pts_div = 5
        else:
            pts_div = 10
    else:
        # Corporate: lower equity exposure means less market risk
        if equity_exposure_pct < 20.0:
            pts_div = 30
        elif equity_exposure_pct < 50.0:
            pts_div = 20
        else:
            pts_div = 10

    score = pts_fv + pts_hhi + pts_div

    # ─── Build narrative findings ──────────────────────────────────────────────
    findings: List[str] = []
    drivers: List[str] = []

    if fair_value_pct >= 70.0:
        findings.append(f"Alta dependencia de valorización a valor razonable ({fair_value_pct:.1f}% del ingreso): resultados vulnerables a correcciones de mercado.")
        drivers.append(f"Ingresos por valoración representan el {fair_value_pct:.1f}% del P&G")
    elif fair_value_pct >= 50.0:
        findings.append(f"Exposición moderada a valorización ({fair_value_pct:.1f}% de ingresos): sensibilidad a volatilidad de mercado.")
    elif fair_value_pct > 0.0:
        findings.append(f"Dependencia baja de valorización ({fair_value_pct:.1f}%): base de ingresos más estable.")

    if hhi is not None and hhi >= 0.40:
        findings.append(f"Concentración de cartera crítica (HHI={hhi:.3f}; posiciones efectivas: {effective_positions:.1f}): diversificación insuficiente.")
        drivers.append(f"HHI crítico ({hhi:.3f}) — equivale a {effective_positions:.1f} posiciones efectivas")
    elif hhi is not None and hhi >= 0.25:
        findings.append(f"Concentración elevada (HHI={hhi:.3f}): portafolio con posiciones dominantes.")
        drivers.append(f"HHI elevado ({hhi:.3f})")

    if top_issuer_pct is not None and top_issuer_pct >= 30.0:
        findings.append(f"Mayor posición individual representa el {top_issuer_pct:.1f}% de la cartera: riesgo idiosincrático significativo.")
        drivers.append(f"Posición individual >30% de la cartera")

    if top3_concentration_pct is not None and top3_concentration_pct >= 70.0:
        findings.append(f"Top 3 posiciones concentran el {top3_concentration_pct:.1f}% del portafolio de inversiones.")
        drivers.append("Top 3 posiciones >70% del portafolio")

    if is_investment_fund and equity_exposure_pct >= 90.0:
        findings.append(f"Exposición de renta variable total (~{equity_exposure_pct:.0f}%): todos los resultados están sujetos a volatilidad de mercado accionario.")

    return MarketRiskResult(
        level=_level_from_score(score),
        score=score,
        fair_value_income_pct=fair_value_pct,
        equity_exposure_pct=equity_exposure_pct,
        top_issuer_pct=top_issuer_pct,
        top3_concentration_pct=top3_concentration_pct,
        hhi=hhi,
        effective_positions=effective_positions,
        key_findings=findings,
        risk_drivers=drivers,
        is_fund_adjusted=is_investment_fund,
    )
