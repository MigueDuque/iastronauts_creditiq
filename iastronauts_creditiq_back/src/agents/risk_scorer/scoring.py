"""
Deterministic risk scoring — pure core of Agent 3.

This module contains the *side-effect-free* portion of the RiskScorer pipeline:
the 5 deterministic engines + composite score + report-facing risk categories.
It performs no LLM calls, no S3 access, and no logging side effects.

Both the Lambda handler (handler.py) and the eval harness (tests/eval/) call
`compute_risk()` so the regression suite exercises the exact production code
path rather than a copy that can silently drift.
"""

from dataclasses import dataclass
from typing import Any

from shared.models import AnalyzerOutput

from .liquidity_engine import score_liquidity
from .credit_engine import score_credit
from .solvency_engine import score_solvency
from .market_risk_engine import score_market_risk
from .operational_engine import score_operational
from .composite_scorer import compute_composite, build_risk_categories


def is_investment_fund(payload: AnalyzerOutput) -> bool:
    fund = payload.fund_analysis or {}
    if fund.get("is_investment_fund"):
        return True
    company = (payload.company_name or "").lower()
    return any(kw in company for kw in ("fondo", "fund", "p.acciones", "acciones", "renta", "liquidez"))


def enrich_business_context(payload: AnalyzerOutput, is_fund: bool):
    """Fill business_context fields the pipeline can derive itself.

    `analysis_confidence` weights `input_quality` (completeness of business_context) at
    25%. `fiscal_year` and `industry` were arriving null on every run — even though both
    are derivable from data already in hand — which forced input_quality to 0.0 and dragged
    the documented confidence down to ~59% ("Confianza Baja") regardless of the actual
    analysis quality. Derive them here so the score reflects what is genuinely known;
    `strategic_context` is left untouched because it truly requires a human analyst.
    """
    bc = payload.business_context
    if bc is None:
        return bc

    # fiscal_year ← reporting_period or the most recent period (both are "YYYY-MM").
    if not getattr(bc, "fiscal_year", None):
        ref = getattr(bc, "reporting_period", None) or (payload.periods[0] if payload.periods else None)
        if ref and len(ref) >= 4 and ref[:4].isdigit():
            bc.fiscal_year = ref[:4]

    # industry ← deterministic label when the entity is an investment fund.
    if not getattr(bc, "industry", None) and is_fund:
        bc.industry = "Fondo de inversión / gestión de activos"

    return bc


@dataclass
class RiskComputation:
    """All deterministic outputs of Agent 3, before the LLM narrative."""
    is_fund: bool
    liquidity: Any
    credit: Any
    solvency: Any
    market: Any
    operational: Any
    composite: Any
    risk_categories: dict


def compute_risk(payload: AnalyzerOutput) -> RiskComputation:
    """Run the deterministic risk pipeline.

    NOTE: mutates `payload.business_context` (derives fiscal_year/industry) exactly as
    the production handler does, so confidence scoring is identical in eval and prod.
    """
    analysis_list = [a.model_dump() for a in payload.analysis_results]
    is_fund = is_investment_fund(payload)
    enrich_business_context(payload, is_fund)
    sheet_concentration = payload.sheet_concentration or {}

    # ── 5 deterministic risk engines ───────────────────────────────────────────
    liquidity = score_liquidity(
        financial_ratios=payload.financial_ratios,
        executive_synthesis=payload.executive_synthesis,
        fund_analysis=payload.fund_analysis,
        analysis_results=analysis_list,
        is_investment_fund=is_fund,
    )
    solvency = score_solvency(
        financial_ratios=payload.financial_ratios,
        analysis_results=analysis_list,
    )
    fund_policy_assessment = payload.fund_policy_assessment or {}
    market = score_market_risk(
        financial_ratios=payload.financial_ratios,
        earnings_quality=payload.earnings_quality,
        portfolio_concentration=payload.portfolio_concentration,
        fund_analysis=payload.fund_analysis,
        analysis_results=analysis_list,
        is_investment_fund=is_fund,
        sheet_concentration=sheet_concentration,
        currency=payload.currency,
        fund_policy_assessment=fund_policy_assessment,
    )
    credit = score_credit(
        financial_ratios=payload.financial_ratios,
        analysis_results=analysis_list,
        fund_analysis=payload.fund_analysis,
        is_investment_fund=is_fund,
        sheet_concentration=sheet_concentration,
        fund_policy_assessment=fund_policy_assessment,
    )
    operational = score_operational(
        financial_ratios=payload.financial_ratios,
        analysis_results=analysis_list,
    )

    # ── Composite score + anti-hallucination ───────────────────────────────────
    composite = compute_composite(
        liquidity=liquidity,
        credit=credit,
        solvency=solvency,
        market=market,
        operational=operational,
        analysis_results=analysis_list,
        financial_ratios=payload.financial_ratios,
        is_investment_fund=is_fund,
        business_context=payload.business_context,
    )

    # ── Report-facing risk categories (Crédito / Mercado / Financiero) ──────────
    risk_categories = build_risk_categories(
        liquidity=liquidity,
        credit=credit,
        solvency=solvency,
        market=market,
        operational=operational,
    )

    return RiskComputation(
        is_fund=is_fund,
        liquidity=liquidity,
        credit=credit,
        solvency=solvency,
        market=market,
        operational=operational,
        composite=composite,
        risk_categories=risk_categories,
    )
