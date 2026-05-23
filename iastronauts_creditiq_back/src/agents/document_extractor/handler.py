from shared.models import OrchestratorOutput, ExtractorOutput


def lambda_handler(event: dict, context) -> dict:
    """
    Input:  OrchestratorOutput
    Output: ExtractorOutput
    """
    payload = OrchestratorOutput.model_validate(event)

    # TODO: implementar extracción real
    # - PDF: Amazon Textract
    # - Excel/CSV: pandas + openpyxl
    result = ExtractorOutput(
        job_id=payload.job_id,
        tenant_id=payload.tenant_id,
        business_context=payload.business_context,
        niif_standards=payload.niif_standards,
        report_language=payload.report_language,
        output_formats=payload.output_formats,
        company_name=payload.business_context.company_name or "",
        statement_type="balance_sheet",
        currency="COP",
        periods=[],
        accounts=[],
        extraction_confidence=0.0,
    )

    return result.model_dump(mode="json")
