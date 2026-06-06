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
from shared.models import AnalyzerOutput, ScorerOutput
from shared.job_store import save as job_save, RISK_SCORER

from .scoring import compute_risk
from .llm_reasoning import generate_risk_narrative

logger = logging.getLogger("risk_scorer")
logger.setLevel(logging.INFO)


def lambda_handler(event: dict, context) -> dict:
    """
    Input:  AnalyzerOutput
    Output: ScorerOutput
    """
    payload = AnalyzerOutput.model_validate(event)
    logger.info("RiskScorer: job_id=%s company=%s", payload.job_id, payload.company_name)

    # ─── 1+2. Deterministic engines + composite (pure core, shared with eval) ──
    comp = compute_risk(payload)
    is_fund = comp.is_fund
    liquidity, credit, solvency, market, operational = (
        comp.liquidity, comp.credit, comp.solvency, comp.market, comp.operational
    )
    composite = comp.composite
    risk_categories = comp.risk_categories

    logger.info(
        "Risk scores — liquidity:%d credit:%d solvency:%d market:%d operational:%d",
        liquidity.score, credit.score, solvency.score, market.score, operational.score,
    )

    # Mejora 6: propagate anti-hallucination failures onto the affected accounts
    # so the report/frontend can mark individual claims as unverified.
    _halluc_by_id = {
        f["account_id"]: f["detail"]
        for f in composite.anti_hallucination_result.get("failures", [])
        if f.get("account_id")
    }
    if _halluc_by_id:
        for acct in payload.analysis_results:
            if acct.account_id in _halluc_by_id:
                acct.hallucination_flag = True
                acct.hallucination_detail = _halluc_by_id[acct.account_id]

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
            risk_categories=risk_categories,
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
        # Mejora 1: full, auditable breakdown of the composite score.
        "composite_score_detail": composite.composite_score_detail,
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
        earnings_quality=payload.earnings_quality,
        portfolio_concentration=payload.portfolio_concentration,
        fund_analysis=payload.fund_analysis,
        macro_context=payload.macro_context,
        executive_kpis=payload.executive_kpis,
        portfolio_thesis=payload.portfolio_thesis,
        insight_tiers=payload.insight_tiers,
        narrative_layers=payload.narrative_layers,
        executive_synthesis=payload.executive_synthesis,
        structured_analysis=payload.structured_analysis,
        cross_statement_signals=payload.cross_statement_signals,
        earnings_sustainability=payload.earnings_sustainability,
        financial_diagnostics=payload.financial_diagnostics,
        sheet_concentration=payload.sheet_concentration,
        niif_validation=payload.niif_validation,
        validation_score=composite.validation_score,
        overall_risk_score=composite.overall_risk_score,
        issues_found=composite.issues_found,
        compliance_flags=composite.compliance_flags,
        requires_human_review=composite.requires_human_review,
        analysis_confidence=composite.analysis_confidence,
        anti_hallucination_passed=composite.anti_hallucination_passed,
        # ── Transparency detail objects (Mejoras 1, 2, 4, 6, 7, 9) ──────────────
        composite_score_detail=composite.composite_score_detail,
        validation_score_detail=composite.validation_score_detail,
        anti_hallucination_result=composite.anti_hallucination_result,
        analysis_confidence_detail=composite.analysis_confidence_detail,
        data_quality_warnings=composite.data_quality_warnings,
        anomaly_detection_summary=composite.anomaly_detection_summary,
        risk_dimensions=risk_dimensions,
        risk_categories=risk_categories,
        risk_summary=risk_summary,
        fund_context_adjusted=is_fund,
        computation_trace=composite.computation_trace,
        comparative_basis=payload.comparative_basis,
        fund_policy_assessment=payload.fund_policy_assessment,
        top_variations=payload.top_variations,
    )

    logger.info("RiskScorer complete: overall_risk=%s", result.overall_risk_score.value)
    output = result.model_dump(mode="json")
    try:
        job_save(result.job_id, RISK_SCORER, output)
    except Exception as exc:
        logger.error("Failed to persist scorer output to S3: %s", exc)
    return output
