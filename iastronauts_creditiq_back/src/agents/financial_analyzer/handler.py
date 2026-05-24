from shared.models import ExtractorOutput, AnalyzerOutput, FinancialHealth


def lambda_handler(event: dict, context) -> dict:
    """
    Input:  ExtractorOutput
    Output: AnalyzerOutput
    """
    payload = ExtractorOutput.model_validate(event)

    # TODO: calcular variaciones, detectar materialidad, aplicar RAG NIIF, generar narrativa con LLM
    result = AnalyzerOutput(
        job_id=payload.job_id,
        tenant_id=payload.tenant_id,
        business_context=payload.business_context,
        niif_standards=payload.niif_standards,
        report_language=payload.report_language,
        output_formats=payload.output_formats,
        company_name=payload.company_name,
        analysis_results=[],
        high_materiality_accounts=[],
        niif_notes_required=[],
        overall_financial_health=FinancialHealth.STABLE,
        executive_narrative="",
    )

    return result.model_dump(mode="json")