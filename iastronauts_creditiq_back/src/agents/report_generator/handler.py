import os
from datetime import datetime

from shared.models import ScorerOutput, FinalReportOutput
from shared.s3_report_store import fetch_historical_reports, save_report, slugify


def lambda_handler(event: dict, context) -> dict:
    """
    Input:  ScorerOutput
    Output: FinalReportOutput
    """
    payload = ScorerOutput.model_validate(event)
    bucket = os.environ["MAIN_BUCKET"]
    reference_date = datetime.utcnow()

    # Fetch same-trimester + December reports from last year for this company
    historical = fetch_historical_reports(
        tenant_id=payload.tenant_id,
        company_slug=slugify(payload.company_name),
        reference_date=reference_date,
        bucket=bucket,
    )

    # TODO: generar narrativa ejecutiva, notas NIIF y subir entregables a S3 con LLM
    # `historical` contiene los FinalReportOutput del mismo trimestre y diciembre del año anterior
    result = FinalReportOutput(
        job_id=payload.job_id,
        tenant_id=payload.tenant_id,
        company_name=payload.company_name,
        periods=payload.periods,
        generated_at=reference_date,
        validation_score=payload.validation_score,
        overall_risk_score=payload.overall_risk_score,
        overall_financial_health=payload.overall_financial_health,
        executive_summary=payload.executive_narrative,
        board_summary="",
        analysis_results=payload.analysis_results,
        niif_note_drafts=[],
        markdown_report_url="",
    )

    s3_key = save_report(result, bucket)
    result.markdown_report_url = f"s3://{bucket}/{s3_key}"

    return result.model_dump(mode="json")
