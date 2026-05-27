"""
liquidity_engine.py

Measures the entity's ability to meet short-term obligations.

For investment funds, the primary liquidity concerns are:
  - Redemption pressure (net investor outflows vs liquid assets)
  - Cash position relative to total AUM
  - Current ratio from the balance sheet

Scoring (0–100, higher = less risk):
  Component 1 — Current ratio (razon_corriente):   max 40 pts
  Component 2 — Cash % of total assets:            max 30 pts
  Component 3 — Redemption pressure (fund) or
                Working capital adequacy (corporate): max 30 pts
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from shared.models.base import RiskLevel


@dataclass
class LiquidityRiskResult:
    level: RiskLevel
    score: int                              # 0–100, higher = less risk
    razon_corriente: float
    prueba_acida: float
    capital_trabajo_cop_mm: float
    cash_pct_assets: float                  # Efectivo / Total activos
    net_redemption_pressure_pct: float | None  # net outflow / opening NAV (funds only)
    key_findings: List[str] = field(default_factory=list)
    risk_drivers: List[str] = field(default_factory=list)
    is_fund_adjusted: bool = False


def _level_from_score(score: int) -> RiskLevel:
    if score >= 70:
        return RiskLevel.LOW
    elif score >= 40:
        return RiskLevel.MEDIUM
    return RiskLevel.HIGH


def score_liquidity(
    financial_ratios: dict,
    executive_synthesis: dict,
    fund_analysis: dict,
    analysis_results: list,
    is_investment_fund: bool,
) -> LiquidityRiskResult:
    ratios = financial_ratios.get("ratios", {})
    totals = financial_ratios.get("totals", {})

    razon_corriente = ratios.get("razon_corriente", 0.0)
    prueba_acida = ratios.get("prueba_acida", 0.0)
    capital_trabajo = ratios.get("capital_trabajo_cop_mm", totals.get("current_assets", 0.0) - totals.get("current_liabilities", 0.0))
    total_assets = totals.get("total_assets", 0.0)

    # Cash from individual account analysis
    cash_value = sum(
        a.get("current_value", 0.0)
        for a in analysis_results
        if "efectivo" in a.get("account_name", "").lower()
        and "neto" not in a.get("account_name", "").lower()
        and "flujo" not in a.get("account_name", "").lower()
        and a.get("current_value", 0.0) > 0
        and a.get("current_value", 0.0) < total_assets * 0.5  # exclude aggregate totals
    )
    cash_pct = round(cash_value / total_assets * 100, 2) if total_assets > 0 else 0.0

    # Redemption pressure (investment funds)
    net_redemption_pct: float | None = None
    redemptions = 0.0
    contributions = 0.0

    if is_investment_fund:
        for a in analysis_results:
            name = a.get("account_name", "").lower()
            val = a.get("current_value", 0.0)
            if "retiro" in name and "inversionista" in name:
                redemptions += abs(val)
            elif "aporte" in name and "inversionista" in name:
                contributions += abs(val)

        opening_nav = next(
            (a.get("current_value", 0.0)
             for a in analysis_results
             if "patrimonio neto inicial" in a.get("account_name", "").lower()),
            0.0,
        )
        if opening_nav > 0:
            net_outflow = redemptions - contributions
            net_redemption_pct = round(net_outflow / opening_nav * 100, 2)

    # ─── Component 1: Current ratio ───────────────────────────────────────────
    if razon_corriente >= 2.0:
        pts_cr = 40
    elif razon_corriente >= 1.5:
        pts_cr = 30
    elif razon_corriente >= 1.0:
        pts_cr = 15
    else:
        pts_cr = 0

    # ─── Component 2: Cash % of assets ───────────────────────────────────────
    if cash_pct >= 10.0:
        pts_cash = 30
    elif cash_pct >= 5.0:
        pts_cash = 20
    elif cash_pct >= 2.0:
        pts_cash = 10
    else:
        pts_cash = 0

    # ─── Component 3: Redemption pressure / working capital ──────────────────
    if is_investment_fund and net_redemption_pct is not None:
        if net_redemption_pct < 10.0:
            pts_pressure = 30
        elif net_redemption_pct < 25.0:
            pts_pressure = 15
        elif net_redemption_pct < 50.0:
            pts_pressure = 5
        else:
            pts_pressure = 0
    else:
        # Corporate: working capital relative to total assets
        wc_pct = round(capital_trabajo / total_assets * 100, 2) if total_assets > 0 else 0.0
        if wc_pct >= 20.0:
            pts_pressure = 30
        elif wc_pct >= 10.0:
            pts_pressure = 20
        elif wc_pct >= 0.0:
            pts_pressure = 10
        else:
            pts_pressure = 0

    score = pts_cr + pts_cash + pts_pressure

    # ─── Build narrative findings ──────────────────────────────────────────────
    findings: List[str] = []
    drivers: List[str] = []

    if razon_corriente >= 2.0:
        findings.append(f"Razón corriente elevada ({razon_corriente:,.2f}): capacidad de pago a corto plazo sólida.")
    elif razon_corriente < 1.0:
        findings.append(f"Razón corriente crítica ({razon_corriente:,.2f}): pasivos corrientes superan activos corrientes.")
        drivers.append("Razón corriente inferior a 1.0")

    if cash_pct < 2.0:
        findings.append(f"Posición de efectivo baja ({cash_pct:.1f}% de activos totales).")
        drivers.append("Efectivo insuficiente como % de activos")
    elif cash_pct >= 10.0:
        findings.append(f"Posición de efectivo adecuada ({cash_pct:.1f}% de activos totales).")

    if net_redemption_pct is not None:
        if net_redemption_pct > 50.0:
            findings.append(f"Presión de redención crítica: retiros netos equivalen al {net_redemption_pct:.1f}% del patrimonio inicial.")
            drivers.append("Retiros netos superan el 50% del NAV inicial")
        elif net_redemption_pct > 25.0:
            findings.append(f"Presión de redención elevada ({net_redemption_pct:.1f}% del NAV inicial): seguimiento requerido.")
            drivers.append("Retiros netos entre 25–50% del NAV inicial")
        elif net_redemption_pct > 10.0:
            findings.append(f"Presión de redención moderada ({net_redemption_pct:.1f}% del NAV inicial).")
        else:
            findings.append(f"Presión de redención baja ({net_redemption_pct:.1f}% del NAV inicial).")

    return LiquidityRiskResult(
        level=_level_from_score(score),
        score=score,
        razon_corriente=razon_corriente,
        prueba_acida=prueba_acida,
        capital_trabajo_cop_mm=round(capital_trabajo, 3),
        cash_pct_assets=cash_pct,
        net_redemption_pressure_pct=net_redemption_pct,
        key_findings=findings,
        risk_drivers=drivers,
        is_fund_adjusted=is_investment_fund,
    )
