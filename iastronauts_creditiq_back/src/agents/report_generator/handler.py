from shared.models import ScorerOutput, FinalReportOutput


def lambda_handler(event: dict, context) -> dict:
    """
    Input:  ScorerOutput
    Output: FinalReportOutput
    """
    payload = ScorerOutput.model_validate(event)

    # TODO: generar narrativa ejecutiva, notas NIIF y subir entregables a S3 con LLM
    result = FinalReportOutput(
        job_id=payload.job_id,
        company_name="",
        validation_score=payload.validation_score,
        overall_risk_score=payload.overall_risk_score,
        executive_summary="",
        board_summary="",
        niif_note_drafts=[],
        markdown_report_url=f"s3://creditiq/reports/{payload.job_id}/report.md",
    )

    return result.model_dump(mode="json")
