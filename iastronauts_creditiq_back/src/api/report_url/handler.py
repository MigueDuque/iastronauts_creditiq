import json
import logging
import os

import boto3
from botocore.exceptions import ClientError

from shared.job_store import load_first as job_load_first, FINANCIAL_ANALYZER, EXTRACTOR, REPORT_GENERATOR

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def _sfn_client():
    kwargs = {}
    if url := os.environ.get("SFN_ENDPOINT_URL"):
        kwargs["endpoint_url"] = url
    return boto3.client("stepfunctions", **kwargs)


s3 = boto3.client("s3")
BUCKET = os.environ["MAIN_BUCKET"]
EXPIRATION = 3600  # 1 hour


def _execution_arn(analysis_id: str) -> str:
    region = os.environ["AWS_REGION"]
    account_id = os.environ["AWS_ACCOUNT_ID"]
    stage = os.environ["STAGE"]
    return f"arn:aws:states:{region}:{account_id}:execution:creditiq-analysis-workflow-{stage}:{analysis_id}"


def _presign(s3_url: str | None) -> str | None:
    """Convert an s3://bucket/key URL to a presigned GET URL. Returns None on failure."""
    if not s3_url or not s3_url.startswith("s3://"):
        return None
    key = s3_url.removeprefix(f"s3://{BUCKET}/")
    try:
        return s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": BUCKET, "Key": key},
            ExpiresIn=EXPIRATION,
        )
    except Exception:
        return None


def _build_response_body(analysis_id: str, output: dict) -> dict:
    """Build the API response body from a ReportGenerator output dict."""
    return {
        "analysis_id": analysis_id,
        "docx_url": _presign(output.get("docx_report_url")),
        "expires_in": EXPIRATION,
    }


def _s3_report_fallback(analysis_id: str) -> dict:
    """Load from S3 job store when SFN execution is unavailable (local dev)."""
    data = job_load_first(analysis_id, [REPORT_GENERATOR, FINANCIAL_ANALYZER, EXTRACTOR])
    if data:
        docx_url = _presign(data.get("docx_report_url"))
        if docx_url:
            return _response(200, {
                "analysis_id": analysis_id,
                "docx_url": docx_url,
                "expires_in": EXPIRATION,
            })
        return _response(404, {
            "error": "El reporte .docx aún no ha sido generado para este análisis."
        })
    return _response(409, {"error": "El análisis aún no está completo", "status": "processing"})


def lambda_handler(event: dict, context) -> dict:
    """
    GET /analyses/{analysis_id}/report
    Returns a presigned URL to download the generated .docx report.
    """
    try:
        analysis_id = event.get("pathParameters", {}).get("analysis_id")
        if not analysis_id:
            return _response(400, {"error": "analysis_id es requerido"})

        execution = _sfn_client().describe_execution(executionArn=_execution_arn(analysis_id))

        if execution["status"] == "RUNNING":
            data = job_load_first(analysis_id, [REPORT_GENERATOR, FINANCIAL_ANALYZER, EXTRACTOR])
            if data:
                return _response(200, _build_response_body(analysis_id, data))
            return _response(409, {"error": "El análisis aún no está completo", "status": "processing"})

        if execution["status"] != "SUCCEEDED":
            return _response(409, {"error": "El análisis falló o fue cancelado", "status": "failed"})

        output = json.loads(execution.get("output", "{}"))
        docx_url = _presign(output.get("docx_report_url"))

        if not docx_url:
            data = job_load_first(analysis_id, [REPORT_GENERATOR, FINANCIAL_ANALYZER, EXTRACTOR])
            if data:
                fallback_url = _presign(data.get("docx_report_url"))
                if fallback_url:
                    return _response(200, {"analysis_id": analysis_id, "docx_url": fallback_url, "expires_in": EXPIRATION})
            return _response(404, {
                "error": "El reporte .docx aún no ha sido generado para este análisis."
            })

        return _response(200, {
            "analysis_id": analysis_id,
            "docx_url": docx_url,
            "expires_in": EXPIRATION,
        })

    except (ClientError, Exception) as e:
        code = e.response["Error"]["Code"] if isinstance(e, ClientError) else type(e).__name__
        logger.warning("report_url | SFN error (%s), falling back to S3", code)
        return _s3_report_fallback(analysis_id)


def _response(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body, ensure_ascii=False, default=str),
    }
