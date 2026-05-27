import json
import logging
import os

import boto3
from botocore.exceptions import ClientError

from shared.job_store import load_first as job_load_first, load as job_load, FINANCIAL_ANALYZER, EXTRACTOR

logger = logging.getLogger()
logger.setLevel(logging.INFO)

def _sfn_client():
    kwargs = {}
    if url := os.environ.get("SFN_ENDPOINT_URL"):
        kwargs["endpoint_url"] = url
    return boto3.client("stepfunctions", **kwargs)

s3 = boto3.client("s3")
BUCKET = os.environ["MAIN_BUCKET"]
EXPIRATION = 3600  # 1 hora


def _execution_arn(analysis_id: str) -> str:
    region = os.environ["AWS_REGION"]
    account_id = os.environ["AWS_ACCOUNT_ID"]
    stage = os.environ["STAGE"]
    return f"arn:aws:states:{region}:{account_id}:execution:creditiq-analysis-workflow-{stage}:{analysis_id}"


def _s3_report_fallback(analysis_id: str) -> dict:
    """
    Return the most recent available agent output from S3.
    Tries in precedence order: financial_analyzer → extractor.
    Used in local dev when SFN execution doesn't exist.
    """
    data = job_load_first(analysis_id, [FINANCIAL_ANALYZER, EXTRACTOR])
    if data:
        return _response(200, data)
    return _response(409, {"error": "El análisis aún no está completo", "status": "processing"})


def lambda_handler(event: dict, context) -> dict:
    """
    GET /analyses/{analysis_id}/report
    Returns a presigned URL to download the generated markdown report.
    The S3 key is extracted from the Step Functions execution output produced by ReportGenerator.
    """
    try:
        analysis_id = event.get("pathParameters", {}).get("analysis_id")
        if not analysis_id:
            return _response(400, {"error": "analysis_id es requerido"})

        execution = _sfn_client().describe_execution(executionArn=_execution_arn(analysis_id))

        if execution["status"] == "RUNNING":
            # Pipeline may be paused at a review gate — serve best available agent output from S3
            data = job_load_first(analysis_id, [FINANCIAL_ANALYZER, EXTRACTOR])
            if data:
                return _response(200, data)
            return _response(409, {"error": "El análisis aún no está completo", "status": "processing"})

        if execution["status"] != "SUCCEEDED":
            return _response(409, {"error": "El análisis falló o fue cancelado", "status": "failed"})

        # ReportGenerator output contains markdown_report_url: "s3://{bucket}/{key}"
        output = json.loads(execution.get("output", "{}"))
        markdown_url = output.get("markdown_report_url", "")

        if not markdown_url.startswith("s3://"):
            # Agents are stubs — serve best available output so the accounts table renders
            data = job_load_first(analysis_id, [FINANCIAL_ANALYZER, EXTRACTOR])
            if data:
                return _response(200, data)
            return _response(404, {"error": "No se encontró el reporte generado"})

        # Strip "s3://{bucket}/" prefix to get the S3 key
        s3_key = markdown_url.removeprefix(f"s3://{BUCKET}/")

        presigned_url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": BUCKET, "Key": s3_key},
            ExpiresIn=EXPIRATION,
        )

        return _response(200, {
            "analysis_id": analysis_id,
            "markdown_url": presigned_url,
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
