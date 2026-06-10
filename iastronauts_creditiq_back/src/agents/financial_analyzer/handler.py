"""
handler.py — AWS Lambda entry point for the FinancialAnalyzer agent.

Input:  ExtractorOutput  (passed by Step Functions from DocumentExtractor)
Output: AnalyzerOutput   (consumed by RiskScorer)
"""

import logging

from shared.models import ExtractorOutput
from shared.agent_handoff import hydrate_input, slim_envelope
from shared.job_store import EXTRACTOR

from .service import FinancialAnalyzerService

logger = logging.getLogger("financial_analyzer.handler")
logger.setLevel(logging.INFO)


def lambda_handler(event: dict, context) -> dict:
    """
    Validates the incoming ExtractorOutput, runs the full analysis pipeline,
    and returns the serialized AnalyzerOutput.
    """
    # Step Functions hands us a slim claim-check pointer; rehydrate the full
    # ExtractorOutput from S3 (falls back to the event for direct/local calls that
    # still pass the full payload). See shared/agent_handoff.py.
    payload = hydrate_input(event, EXTRACTOR, ExtractorOutput, discriminator="accounts")

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

    # The service self-persists the full AnalyzerOutput to S3; return only a pointer.
    return slim_envelope(result, status="analysis_complete")
