"""
handler.py — AWS Lambda entry point for the FinancialAnalyzer agent.

Input:  ExtractorOutput  (passed by Step Functions from DocumentExtractor)
Output: AnalyzerOutput   (consumed by RiskScorer)
"""

import logging

from shared.models import ExtractorOutput

from .service import FinancialAnalyzerService

logger = logging.getLogger("financial_analyzer.handler")
logger.setLevel(logging.INFO)


def lambda_handler(event: dict, context) -> dict:
    """
    Validates the incoming ExtractorOutput, runs the full analysis pipeline,
    and returns the serialized AnalyzerOutput.
    """
    payload = ExtractorOutput.model_validate(event)

    logger.info(
        "handler_start | job=%s tenant=%s accounts=%d periods=%s",
        payload.job_id, payload.tenant_id, len(payload.accounts), payload.periods,
    )

    result = FinancialAnalyzerService().analyze(payload, lambda_context=context)

    logger.info(
        "handler_done | job=%s health=%s high_mat=%d niif_notes=%d",
        result.job_id, result.overall_financial_health.value,
        len(result.high_materiality_accounts), len(result.niif_notes_required),
    )

    return result.model_dump(mode="json")
