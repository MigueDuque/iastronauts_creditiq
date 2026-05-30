"""
credit_engine.py

Measures credit and default risk.

For investment funds:
  - Issuer concentration: exposure to any single issuer defaulting
  - Counterparty (custodian bank) risk — derived from sheet_concentration.bank_breakdown
  - Accounts receivable aging

For operating companies:
  - Accounts receivable aging (dias_cartera)
  - Receivables as % of total assets
  - Receivables trend (growing AR without matching revenue growth)

Scoring (0–100, higher = less risk):
  Component 1 — Collection days / issuer concentration:  max 40 pts
  Component 2 — Receivables % of assets:                 max 30 pts
  Component 3 — Receivables trend / related-party risk:  max 30 pts
  Counterparty/custodian concentration:                  up to −15 pts deduction
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from shared.models.base import RiskLevel


@dataclass
class CreditRiskResult:
    level: RiskLevel
    score: int
    dias_cartera: float
    cuentas_cobrar_pct_activos: float
    top_issuer_name: str | None             # fund-specific
    top_issuer_pct_portfolio: float | None  # fund-specific; pct of investment portfolio
    related_party_exposure: bool
    top_custodian_name: str | None = None       # counterparty: bank holding most of the cash
    top_custodian_pct: float | None = None      # % of total cash at the top custodian bank
    num_custodians: int | None = None           # number of distinct custodian banks
    key_findings: List[str] = field(default_factory=list)
    risk_drivers: List[str] = field(default_factory=list)
    is_fund_adjusted: bool = False


def _level_from_score(score: int) -> RiskLevel:
    if score >= 70:
        return RiskLevel.LOW
    elif score >= 40:
        return RiskLevel.MEDIUM
    return RiskLevel.HIGH


def score_credit(
    financial_ratios: dict,
    analysis_results: list,
    fund_analysis: dict,
    is_investment_fund: bool,
    sheet_concentration: dict | None = None,
) -> CreditRiskResult:
    ratios = financial_ratios.get("ratios", {})
    totals = financial_ratios.get("totals", {})

    dias_cartera = ratios.get("dias_cartera", 0.0)
    total_assets = totals.get("total_assets", 0.0)
    total_revenue = totals.get("total_revenue", 0.0)

    # Receivables from balance sheet
    cuentas_cobrar_value = sum(
        a.get("current_value", 0.0)
        for a in analysis_results
        if "cuentas por cobrar" in a.get("account_name", "").lower()
        and a.get("current_value", 0.0) > 0
    )
    cuentas_cobrar_pct = round(cuentas_cobrar_value / total_assets * 100, 2) if total_assets > 0 else 0.0

    # Receivables previous period for trend
    cuentas_cobrar_prev = sum(
        a.get("previous_value", 0.0) or 0.0
        for a in analysis_results
        if "cuentas por cobrar" in a.get("account_name", "").lower()
    )

    # Related-party exposure detection
    related_party = any(
        a.get("is_related_party", False) for a in analysis_results
    )

    # Fund-specific: top individual issuer concentration (excluding aggregate accounts)
    top_issuer_name: str | None = None
    top_issuer_pct: float | None = None

    if is_investment_fund:
        # investable_base = actual equity positions + fund positions (exclude investor share classes)
        investment_total = sum(
            a.get("current_value", 0.0)
            for a in analysis_results
            if a.get("account_name", "").lower().startswith("acciones")
            and "total" not in a.get("account_name", "").lower()
            and a.get("current_value", 0.0) > 0
        )
        fondo_total = sum(
            a.get("current_value", 0.0)
            for a in analysis_results
            if a.get("account_name", "").lower().startswith("fondo de invers")
            and "total" not in a.get("account_name", "").lower()
            and a.get("current_value", 0.0) > 0
        )
        investable_base = investment_total + fondo_total

        if investable_base > 0:
            # "Participaciones - Clase X" are investor share classes, not individual issuers.
            # Only use "Acciones - " and "Fondo de inversión - " accounts as issuers.
            candidates = [
                (a.get("account_name", ""), a.get("current_value", 0.0))
                for a in analysis_results
                if (
                    a.get("account_name", "").lower().startswith("acciones")
                    or a.get("account_name", "").lower().startswith("fondo de invers")
                )
                and "total" not in a.get("account_name", "").lower()
                and a.get("current_value", 0.0) > 0
            ]
            if candidates:
                top_name, top_val = max(candidates, key=lambda x: x[1])
                top_issuer_name = top_name
                top_issuer_pct = round(top_val / investable_base * 100, 2)

    # ─── Component 1: Dias cartera / issuer concentration ─────────────────────
    if is_investment_fund and top_issuer_pct is not None:
        if top_issuer_pct < 15.0:
            pts_dias = 40
        elif top_issuer_pct < 20.0:
            pts_dias = 25
        elif top_issuer_pct < 30.0:
            pts_dias = 10
        else:
            pts_dias = 0
    else:
        if dias_cartera < 30.0:
            pts_dias = 40
        elif dias_cartera < 60.0:
            pts_dias = 25
        elif dias_cartera < 90.0:
            pts_dias = 10
        else:
            pts_dias = 0

    # ─── Component 2: Receivables % of assets ─────────────────────────────────
    if cuentas_cobrar_pct < 5.0:
        pts_ar = 30
    elif cuentas_cobrar_pct < 15.0:
        pts_ar = 20
    elif cuentas_cobrar_pct < 25.0:
        pts_ar = 10
    else:
        pts_ar = 0

    # ─── Component 3: Receivables trend & related-party risk ──────────────────
    pts_trend = 20  # start with base
    if related_party:
        pts_trend -= 10

    # AR growth outpacing revenue growth is a warning sign
    if cuentas_cobrar_prev > 0 and total_revenue > 0:
        ar_growth = (cuentas_cobrar_value - cuentas_cobrar_prev) / cuentas_cobrar_prev
        if ar_growth > 1.0:  # AR more than doubled
            pts_trend -= 10

    pts_trend = max(0, pts_trend)

    # Related-party risk: if none, award the remaining 10 pts
    if not related_party:
        pts_trend += 10

    # ─── Counterparty / custodian concentration (from sheet_concentration) ─────
    # Cash held at a single custodian bank is a counterparty-default exposure.
    top_custodian_name: str | None = None
    top_custodian_pct: float | None = None
    num_custodians: int | None = None
    custodian_deduction = 0

    bank_breakdown = (sheet_concentration or {}).get("bank_breakdown") or []
    if (sheet_concentration or {}).get("bank_available") and bank_breakdown:
        num_custodians = len(bank_breakdown)
        top_bank = bank_breakdown[0]
        top_custodian_name = top_bank.get("name")
        top_custodian_pct = top_bank.get("pct")
        if top_custodian_pct is not None:
            if top_custodian_pct >= 90.0:
                custodian_deduction = 15
            elif top_custodian_pct >= 70.0:
                custodian_deduction = 10
            elif top_custodian_pct >= 50.0:
                custodian_deduction = 5

    score = max(0, pts_dias + pts_ar + pts_trend - custodian_deduction)

    # ─── Build narrative findings ──────────────────────────────────────────────
    findings: List[str] = []
    drivers: List[str] = []

    if is_investment_fund and top_issuer_pct is not None:
        if top_issuer_pct >= 30.0:
            findings.append(f"Concentración crítica en emisor individual: {top_issuer_name} representa el {top_issuer_pct:.1f}% de la cartera de inversiones.")
            drivers.append(f"Concentración en emisor único >30% ({top_issuer_name})")
        elif top_issuer_pct >= 20.0:
            findings.append(f"Concentración elevada en un emisor: {top_issuer_name} ({top_issuer_pct:.1f}% de la cartera).")
            drivers.append(f"Concentración en emisor único >20% ({top_issuer_name})")
        elif top_issuer_pct >= 15.0:
            findings.append(f"Concentración moderada en {top_issuer_name} ({top_issuer_pct:.1f}% de la cartera).")
        else:
            findings.append(f"Diversificación adecuada por emisor; mayor posición: {top_issuer_name} ({top_issuer_pct:.1f}%).")
    else:
        if dias_cartera < 30.0:
            findings.append(f"Rotación de cartera de clientes eficiente ({dias_cartera:.1f} días).")
        elif dias_cartera >= 90.0:
            findings.append(f"Cartera de clientes con mora extendida ({dias_cartera:.1f} días de cobro promedio).")
            drivers.append("Días de cobro superiores a 90 días")

    if cuentas_cobrar_pct > 15.0:
        findings.append(f"Cuentas por cobrar representan el {cuentas_cobrar_pct:.1f}% de activos totales — exposición crediticia significativa.")
        drivers.append("Alto peso de cuentas por cobrar en activos")

    if related_party:
        findings.append("Partes relacionadas detectadas en el análisis de cuentas — riesgo de operaciones vinculadas (NIC 24).")
        drivers.append("Operaciones con partes relacionadas identificadas")

    if top_custodian_pct is not None:
        if top_custodian_pct >= 90.0:
            findings.append(
                f"Riesgo de contraparte crítico: {top_custodian_pct:.1f}% del efectivo está depositado en un único custodio ({top_custodian_name})."
            )
            drivers.append(f"Efectivo concentrado >90% en un solo custodio ({top_custodian_name})")
        elif top_custodian_pct >= 70.0:
            findings.append(
                f"Concentración elevada de contraparte: {top_custodian_pct:.1f}% del efectivo en {top_custodian_name}."
            )
            drivers.append(f"Efectivo concentrado >70% en un solo custodio ({top_custodian_name})")
        elif num_custodians and num_custodians >= 3 and top_custodian_pct < 50.0:
            findings.append(
                f"Riesgo de contraparte diversificado: efectivo distribuido en {num_custodians} custodios (mayor: {top_custodian_pct:.1f}%)."
            )

    return CreditRiskResult(
        level=_level_from_score(score),
        score=score,
        dias_cartera=dias_cartera,
        cuentas_cobrar_pct_activos=cuentas_cobrar_pct,
        top_issuer_name=top_issuer_name,
        top_issuer_pct_portfolio=top_issuer_pct,
        related_party_exposure=related_party,
        top_custodian_name=top_custodian_name,
        top_custodian_pct=top_custodian_pct,
        num_custodians=num_custodians,
        key_findings=findings,
        risk_drivers=drivers,
        is_fund_adjusted=is_investment_fund,
    )
