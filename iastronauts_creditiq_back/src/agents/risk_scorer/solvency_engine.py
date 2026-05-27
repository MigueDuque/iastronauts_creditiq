"""
solvency_engine.py

Measures the entity's ability to meet long-term obligations (leverage risk).

Compares total liabilities against equity and total assets to determine
structural financial health and long-term debt-servicing capacity.

Scoring (0–100, higher = less risk):
  Component 1 — Global indebtedness ratio:  max 40 pts
  Component 2 — Debt/equity ratio:          max 35 pts
  Component 3 — Liabilities trend:          max 25 pts
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from shared.models.base import RiskLevel


@dataclass
class SolvencyRiskResult:
    level: RiskLevel
    score: int
    endeudamiento_global: float
    deuda_patrimonio: float
    equity_ratio: float
    liabilities_trend: str              # "increasing" | "stable" | "decreasing" | "no_data"
    liabilities_growth_pct: float | None
    key_findings: List[str] = field(default_factory=list)
    risk_drivers: List[str] = field(default_factory=list)


def _level_from_score(score: int) -> RiskLevel:
    if score >= 70:
        return RiskLevel.LOW
    elif score >= 40:
        return RiskLevel.MEDIUM
    return RiskLevel.HIGH


def score_solvency(
    financial_ratios: dict,
    analysis_results: list,
) -> SolvencyRiskResult:
    ratios = financial_ratios.get("ratios", {})
    totals = financial_ratios.get("totals", {})

    endeudamiento_global = ratios.get("endeudamiento_global", 0.0)
    deuda_patrimonio = ratios.get("deuda_patrimonio", 0.0)
    equity_ratio = ratios.get("equity_ratio", 0.0)
    total_assets = totals.get("total_assets", 0.0)

    if equity_ratio == 0.0 and total_assets > 0:
        total_equity = totals.get("total_equity", 0.0)
        equity_ratio = round(total_equity / total_assets, 4)

    # Use the "Total pasivos" balance-sheet account for trend to avoid double-counting
    # that occurs when financial_math sums both the aggregate row and its sub-items.
    total_pasivos_account = next(
        (a for a in analysis_results
         if a.get("account_name", "").lower().strip() in ("total pasivos", "pasivos totales")),
        None,
    )
    total_liabilities = (
        total_pasivos_account.get("current_value", 0.0)
        if total_pasivos_account
        else totals.get("total_liabilities", 0.0)
    )
    liabilities_prev = (
        (total_pasivos_account.get("previous_value") or 0.0)
        if total_pasivos_account
        else 0.0
    )

    liabilities_growth_pct: float | None = None
    liabilities_trend = "no_data"
    if liabilities_prev and liabilities_prev > 0:
        liabilities_growth_pct = round((total_liabilities - liabilities_prev) / liabilities_prev * 100, 2)
        if liabilities_growth_pct > 20.0:
            liabilities_trend = "increasing"
        elif liabilities_growth_pct < -10.0:
            liabilities_trend = "decreasing"
        else:
            liabilities_trend = "stable"

    # ─── Component 1: Global indebtedness ─────────────────────────────────────
    if endeudamiento_global < 0.10:
        pts_endeud = 40
    elif endeudamiento_global < 0.40:
        pts_endeud = 28
    elif endeudamiento_global < 0.60:
        pts_endeud = 15
    else:
        pts_endeud = 0

    # ─── Component 2: Debt/equity ─────────────────────────────────────────────
    if deuda_patrimonio < 0.15:
        pts_dp = 35
    elif deuda_patrimonio < 0.50:
        pts_dp = 25
    elif deuda_patrimonio < 1.0:
        pts_dp = 10
    else:
        pts_dp = 0

    # ─── Component 3: Liabilities trend ───────────────────────────────────────
    if liabilities_trend == "decreasing":
        pts_trend = 25
    elif liabilities_trend in ("stable", "no_data"):
        pts_trend = 20
    elif liabilities_trend == "increasing":
        pts_trend = 5
    else:
        pts_trend = 20

    score = pts_endeud + pts_dp + pts_trend

    # ─── Build narrative findings ──────────────────────────────────────────────
    findings: List[str] = []
    drivers: List[str] = []

    if endeudamiento_global < 0.10:
        findings.append(f"Endeudamiento global muy bajo ({endeudamiento_global:.4f}): estructura de capital prácticamente libre de deuda.")
    elif endeudamiento_global > 0.60:
        findings.append(f"Endeudamiento global crítico ({endeudamiento_global:.2f}): más del 60% de activos financiados con deuda.")
        drivers.append("Endeudamiento global superior al 60%")

    if deuda_patrimonio < 0.15:
        findings.append(f"Relación deuda/patrimonio excelente ({deuda_patrimonio:.4f}): capital propio domina la estructura financiera.")
    elif deuda_patrimonio > 1.0:
        findings.append(f"Apalancamiento excesivo: deuda supera el patrimonio ({deuda_patrimonio:.2f}x).")
        drivers.append("Deuda/patrimonio > 1.0")

    if equity_ratio >= 0.80:
        findings.append(f"Ratio de capital propio elevado ({equity_ratio:.2%}): solvencia estructural sólida.")

    if liabilities_trend == "increasing" and liabilities_growth_pct is not None:
        findings.append(f"Pasivos totales en crecimiento ({liabilities_growth_pct:+.1f}%) — evaluar sostenibilidad.")
        drivers.append(f"Pasivos crecieron {liabilities_growth_pct:.1f}% en el período")
    elif liabilities_trend == "decreasing" and liabilities_growth_pct is not None:
        findings.append(f"Pasivos totales en reducción ({liabilities_growth_pct:+.1f}%): mejora en la posición de solvencia.")

    return SolvencyRiskResult(
        level=_level_from_score(score),
        score=score,
        endeudamiento_global=endeudamiento_global,
        deuda_patrimonio=deuda_patrimonio,
        equity_ratio=equity_ratio,
        liabilities_trend=liabilities_trend,
        liabilities_growth_pct=liabilities_growth_pct,
        key_findings=findings,
        risk_drivers=drivers,
    )
