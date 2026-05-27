"""
Risk Scorer — Agent 3

Input:  AnalyzerOutput
Output: ScorerOutput

Pipeline:
  1. Detect entity type (investment fund vs corporate)
  2. Run 5 deterministic risk engines in parallel
  3. Compute composite score + anti-hallucination checks
  4. LLM generates qualitative risk narrative
  5. Persist ScorerOutput to S3 jobs store
"""

import logging
from dataclasses import asdict

from shared.llm_provider import LLMProvider
from shared.models import AnalyzerOutput, ScorerOutput, RiskLevel

from .liquidity_engine import score_liquidity
from .credit_engine import score_credit
from .solvency_engine import score_solvency
from .market_risk_engine import score_market_risk
from .operational_engine import score_operational
from .composite_scorer import compute_composite
from .llm_reasoning import generate_risk_narrative

logger = logging.getLogger("risk_scorer")
logger.setLevel(logging.INFO)


def _is_investment_fund(payload: AnalyzerOutput) -> bool:
    fund = payload.fund_analysis or {}
    if fund.get("is_investment_fund"):
        return True
    company = (payload.company_name or "").lower()
    return any(kw in company for kw in ("fondo", "fund", "p.acciones", "acciones", "renta", "liquidez"))


def lambda_handler(event: dict, context) -> dict:
    """
    Input:  AnalyzerOutput
    Output: ScorerOutput
    """
    payload = AnalyzerOutput.model_validate(event)
    logger.info("RiskScorer: job_id=%s company=%s", payload.job_id, payload.company_name)

    analysis_list = [a.model_dump() for a in payload.analysis_results]
    is_fund = _is_investment_fund(payload)

    # ─── 1. Run 5 deterministic risk engines ──────────────────────────────────
    liquidity = score_liquidity(
        financial_ratios=payload.financial_ratios,
        executive_synthesis=payload.executive_synthesis,
        fund_analysis=payload.fund_analysis,
        analysis_results=analysis_list,
        is_investment_fund=is_fund,
    )

    credit = score_credit(
        financial_ratios=payload.financial_ratios,
        analysis_results=analysis_list,
        fund_analysis=payload.fund_analysis,
        is_investment_fund=is_fund,
    )

    solvency = score_solvency(
        financial_ratios=payload.financial_ratios,
        analysis_results=analysis_list,
    )

    market = score_market_risk(
        financial_ratios=payload.financial_ratios,
        earnings_quality=payload.earnings_quality,
        portfolio_concentration=payload.portfolio_concentration,
        fund_analysis=payload.fund_analysis,
        analysis_results=analysis_list,
        is_investment_fund=is_fund,
    )

    operational = score_operational(
        financial_ratios=payload.financial_ratios,
        analysis_results=analysis_list,
    )

    logger.info(
        "Risk scores — liquidity:%d credit:%d solvency:%d market:%d operational:%d",
        liquidity.score, credit.score, solvency.score, market.score, operational.score,
    )

    # ─── 2. Composite score + anti-hallucination ───────────────────────────────
    composite = compute_composite(
        liquidity=liquidity,
        credit=credit,
        solvency=solvency,
        market=market,
        operational=operational,
        analysis_results=analysis_list,
        financial_ratios=payload.financial_ratios,
        is_investment_fund=is_fund,
    )

    logger.info(
        "Composite: score=%d level=%s validation=%d human_review=%s",
        composite.composite_score,
        composite.overall_risk_score.value,
        composite.validation_score,
        composite.requires_human_review,
    )

    # ─── 3. LLM risk narrative ─────────────────────────────────────────────────
    all_drivers = (
        liquidity.risk_drivers
        + credit.risk_drivers
        + solvency.risk_drivers
        + market.risk_drivers
        + operational.risk_drivers
    )

    dimension_findings = {
        "liquidity": liquidity.key_findings,
        "credit": credit.key_findings,
        "solvency": solvency.key_findings,
        "market": market.key_findings,
        "operational": operational.key_findings,
    }

    try:
        llm = LLMProvider()
        period = payload.periods[0] if payload.periods else "N/A"
        risk_summary = generate_risk_narrative(
            company_name=payload.company_name,
            period=period,
            is_investment_fund=is_fund,
            composite_score=composite.composite_score,
            overall_risk=composite.overall_risk_score.value,
            dimension_scores=composite.dimension_scores,
            dimension_levels=composite.dimension_levels,
            dimension_findings=dimension_findings,
            all_risk_drivers=all_drivers,
            tenant_id=payload.tenant_id,
            job_id=payload.job_id,
            llm=llm,
        )
    except Exception as exc:
        logger.error("LLM narrative failed, using empty fallback: %s", exc)
        risk_summary = {}

    # ─── 4. Build ScorerOutput ─────────────────────────────────────────────────
    risk_dimensions = {
        "liquidity": asdict(liquidity),
        "credit": asdict(credit),
        "solvency": asdict(solvency),
        "market": asdict(market),
        "operational": asdict(operational),
        "composite_score": composite.composite_score,
        "weights_used": "fund" if is_fund else "corporate",
    }

    # Serialize RiskLevel enums inside risk_dimensions
    for dim_key in ("liquidity", "credit", "solvency", "market", "operational"):
        dim = risk_dimensions[dim_key]
        if "level" in dim and hasattr(dim["level"], "value"):
            dim["level"] = dim["level"].value

    result = ScorerOutput(
        job_id=payload.job_id,
        tenant_id=payload.tenant_id,
        business_context=payload.business_context,
        niif_standards=payload.niif_standards,
        report_language=payload.report_language,
        output_formats=payload.output_formats,
        company_name=payload.company_name,
        currency=payload.currency,
        periods=payload.periods,
        financial_ratios=payload.financial_ratios,
        analysis_results=payload.analysis_results,
        high_materiality_accounts=payload.high_materiality_accounts,
        niif_notes_required=payload.niif_notes_required,
        overall_financial_health=payload.overall_financial_health,
        executive_narrative=payload.executive_narrative,
        niif18_compliance=payload.niif18_compliance,
        niif_validation=payload.niif_validation,
        validation_score=composite.validation_score,
        overall_risk_score=composite.overall_risk_score,
        issues_found=composite.issues_found,
        compliance_flags=composite.compliance_flags,
        requires_human_review=composite.requires_human_review,
        analysis_confidence=composite.analysis_confidence,
        anti_hallucination_passed=composite.anti_hallucination_passed,
        risk_dimensions=risk_dimensions,
        risk_summary=risk_summary,
        fund_context_adjusted=is_fund,
    )

    logger.info("RiskScorer complete: overall_risk=%s", result.overall_risk_score.value)
    return result.model_dump(mode="json")
