"""
market_risk_engine.py

Measures vulnerability to external market fluctuations.

For investment funds (primary driver):
  - Fair value income dependency: % of P&L derived from unrealized gains
  - Equity market exposure: % of assets in mark-to-market instruments
  - Issuer concentration (HHI, effective positions)
  - Sector concentration
  - Interest-rate exposure: % of portfolio in fixed-income (Instrumentos de Deuda)
  - Currency (FX) exposure: assets denominated in foreign currency

For operating companies:
  - Currency exposure (FX risk)
  - Interest rate risk (fixed-income holdings / financial expenses)
  - Revenue concentration

Scoring (0–100, higher = less risk):
  Component 1 — Fair value income dependency: max 35 pts
  Component 2 — Asset concentration (HHI):   max 35 pts
  Component 3 — Sector / diversification:    max 30 pts
  Interest-rate + FX exposure:               up to −12 pts deduction
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
    fixed_income_pct: float | None = None   # % of portfolio in Instrumentos de Deuda (rate-sensitive)
    interest_rate_sensitivity: str = "no_data"  # low | moderate | high | no_data
    fx_exposure_pct: float | None = None    # % of assets in foreign-currency accounts
    fx_exposure_detected: bool = False
    reporting_currency: str = "COP"
    # Mejora 3: self-describing concentration block (HHI definition, effective
    # positions, interpretation, regulatory threshold and alert level).
    concentration_metrics: Dict = field(default_factory=dict)
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
    sheet_concentration: dict | None = None,
    currency: str = "COP",
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

    # ─── Interest-rate exposure (fixed income from sheet_concentration) ───────
    fixed_income_pct: float | None = None
    interest_rate_sensitivity = "no_data"
    instrument_breakdown = (sheet_concentration or {}).get("instrument_breakdown") or []
    if (sheet_concentration or {}).get("instrument_available") and instrument_breakdown:
        debt_row = next(
            (ib for ib in instrument_breakdown if ib.get("instrument_type") == "Instrumentos de Deuda"),
            None,
        )
        fixed_income_pct = debt_row.get("pct", 0.0) if debt_row else 0.0
        if fixed_income_pct >= 50.0:
            interest_rate_sensitivity = "high"
        elif fixed_income_pct >= 20.0:
            interest_rate_sensitivity = "moderate"
        else:
            interest_rate_sensitivity = "low"

    # ─── FX exposure (foreign-currency denominated accounts) ──────────────────
    reporting_currency = (currency or "COP").upper()
    _FX_KEYWORDS = ("dolar", "dólar", "usd", "euro", " eur", "divisa",
                    "moneda extranjera", "exterior", "yen", "libra", "gbp")
    fx_value = sum(
        a.get("current_value", 0.0)
        for a in analysis_results
        if any(kw in a.get("account_name", "").lower() for kw in _FX_KEYWORDS)
        and a.get("current_value", 0.0) > 0
        and a.get("current_value", 0.0) < total_assets * 0.9  # exclude aggregate totals
    )
    fx_exposure_pct = round(fx_value / total_assets * 100, 2) if total_assets > 0 else 0.0
    fx_exposure_detected = fx_exposure_pct > 1.0 or reporting_currency != "COP"

    # ─── Rate + FX deduction (caps the market score for hidden exposures) ─────
    rate_fx_deduction = 0
    if interest_rate_sensitivity == "high":
        rate_fx_deduction += 6
    elif interest_rate_sensitivity == "moderate":
        rate_fx_deduction += 3
    if fx_exposure_pct >= 30.0:
        rate_fx_deduction += 6
    elif fx_exposure_pct >= 10.0:
        rate_fx_deduction += 3

    score = max(0, pts_fv + pts_hhi + pts_div - rate_fx_deduction)

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

    # Interest-rate findings
    if interest_rate_sensitivity == "high":
        findings.append(
            f"Exposición elevada a tasa de interés: {fixed_income_pct:.1f}% del portafolio en instrumentos de deuda — sensibilidad a movimientos de tasas."
        )
        drivers.append(f"Renta fija representa {fixed_income_pct:.1f}% del portafolio (riesgo de tasa)")
    elif interest_rate_sensitivity == "moderate":
        findings.append(
            f"Exposición moderada a tasa de interés ({fixed_income_pct:.1f}% en instrumentos de deuda)."
        )

    # FX findings
    if fx_exposure_pct >= 30.0:
        findings.append(
            f"Riesgo cambiario significativo: {fx_exposure_pct:.1f}% de activos en moneda extranjera (reporte en {reporting_currency})."
        )
        drivers.append(f"Exposición cambiaria >30% de activos")
    elif fx_exposure_pct >= 10.0:
        findings.append(
            f"Exposición cambiaria moderada ({fx_exposure_pct:.1f}% de activos en moneda extranjera)."
        )
    elif reporting_currency != "COP":
        findings.append(
            f"Entidad reporta en {reporting_currency}: resultados sujetos a conversión y riesgo cambiario estructural."
        )

    # ─── Mejora 3: self-describing concentration metrics ──────────────────────
    # HHI is opaque to non-portfolio-theory readers; ship its definition,
    # formula, regulatory threshold and a plain-language interpretation inline.
    concentration_metrics: Dict = {}
    if hhi is not None or top_issuer_pct is not None:
        if hhi is not None and hhi >= 0.40:
            alert_level = "CRITICO"
        elif hhi is not None and hhi >= 0.25:
            alert_level = "ALTO"
        elif top_issuer_pct is not None and top_issuer_pct >= 30.0:
            alert_level = "ALTO"
        elif hhi is not None and hhi >= 0.15:
            alert_level = "MODERADO"
        else:
            alert_level = "BAJO"

        if hhi is not None:
            eff_pos = effective_positions if effective_positions is not None else (round(1 / hhi, 2) if hhi > 0 else None)
            if alert_level in ("CRITICO", "ALTO"):
                interpretation = (
                    f"HHI={hhi:.3f} equivale a una cartera concentrada en "
                    f"{eff_pos:.1f} posiciones efectivas. Diversificación insuficiente."
                    if eff_pos is not None else
                    f"HHI={hhi:.3f}: diversificación insuficiente."
                )
            elif alert_level == "MODERADO":
                interpretation = (
                    f"HHI={hhi:.3f} ({eff_pos:.1f} posiciones efectivas): concentración moderada, monitorear límites."
                    if eff_pos is not None else f"HHI={hhi:.3f}: concentración moderada."
                )
            else:
                interpretation = (
                    f"HHI={hhi:.3f} ({eff_pos:.1f} posiciones efectivas): cartera diversificada."
                    if eff_pos is not None else f"HHI={hhi:.3f}: cartera diversificada."
                )
            concentration_metrics["hhi"] = {
                "value": round(hhi, 4),
                "definition": (
                    "Índice de Herfindahl-Hirschman: mide concentración de cartera. "
                    "Rango: 0 (máxima diversificación) a 1 (concentración total en un activo). "
                    "Calculado como suma de cuadrados de las participaciones individuales."
                ),
                "effective_positions": eff_pos,
                "effective_positions_definition": (
                    "Posiciones efectivas = 1/HHI. Indica el número equivalente de posiciones "
                    "igualmente ponderadas que replicarían la concentración observada."
                ),
                "interpretation": interpretation,
                "threshold_used": "HHI > 0.25 = concentración alta (referencia regulatoria SFC Colombia)",
                "alert_level": alert_level,
            }
        if top_issuer_pct is not None:
            concentration_metrics["top_issuer_pct"] = top_issuer_pct
        if top3_concentration_pct is not None:
            concentration_metrics["top3_concentration_pct"] = top3_concentration_pct

    return MarketRiskResult(
        level=_level_from_score(score),
        score=score,
        fair_value_income_pct=fair_value_pct,
        equity_exposure_pct=equity_exposure_pct,
        top_issuer_pct=top_issuer_pct,
        top3_concentration_pct=top3_concentration_pct,
        hhi=hhi,
        effective_positions=effective_positions,
        fixed_income_pct=fixed_income_pct,
        interest_rate_sensitivity=interest_rate_sensitivity,
        fx_exposure_pct=fx_exposure_pct,
        fx_exposure_detected=fx_exposure_detected,
        reporting_currency=reporting_currency,
        concentration_metrics=concentration_metrics,
        key_findings=findings,
        risk_drivers=drivers,
        is_fund_adjusted=is_investment_fund,
    )
