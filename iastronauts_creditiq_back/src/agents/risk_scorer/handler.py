from shared.models import AnalyzerOutput, ScorerOutput, RiskLevel


def lambda_handler(event: dict, context) -> dict:
    """
    Input:  AnalyzerOutput
    Output: ScorerOutput
    """
    payload = AnalyzerOutput.model_validate(event)

    # TODO: validación matemática, detección de alucinaciones, compliance regulatorio
    result = ScorerOutput(
        job_id=payload.job_id,
        business_context=payload.business_context,
        niif_standards=payload.niif_standards,
        report_language=payload.report_language,
        output_formats=payload.output_formats,
        company_name=payload.company_name,
        analysis_results=payload.analysis_results,
        high_materiality_accounts=payload.high_materiality_accounts,
        niif_notes_required=payload.niif_notes_required,
        overall_financial_health=payload.overall_financial_health,
        executive_narrative=payload.executive_narrative,
        validation_score=0,
        overall_risk_score=RiskLevel.LOW,
        issues_found=[],
        compliance_flags=[],
        requires_human_review=False,
        analysis_confidence=0.0,
        anti_hallucination_passed=False,
    )

    return result.model_dump(mode="json")
