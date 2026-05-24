import json
import logging
import os

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

sfn = boto3.client("stepfunctions")
s3 = boto3.client("s3")
BUCKET = os.environ["MAIN_BUCKET"]
EXPIRATION = 3600  # 1 hora


def _execution_arn(analysis_id: str) -> str:
    region = os.environ["AWS_REGION"]
    account_id = os.environ["AWS_ACCOUNT_ID"]
    stage = os.environ["STAGE"]
    return f"arn:aws:states:{region}:{account_id}:execution:creditiq-analysis-workflow-{stage}:{analysis_id}"


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

        execution = sfn.describe_execution(executionArn=_execution_arn(analysis_id))

        if execution["status"] == "RUNNING":
            return _response(409, {"error": "El análisis aún no está completo", "status": "processing"})

        if execution["status"] != "SUCCEEDED":
            return _response(409, {"error": "El análisis falló o fue cancelado", "status": "failed"})

        # ReportGenerator output contains markdown_report_url: "s3://{bucket}/{key}"
        output = json.loads(execution.get("output", "{}"))
        markdown_url = output.get("markdown_report_url", "")

        if not markdown_url.startswith("s3://"):
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

    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code == "ExecutionDoesNotExist":
            return _response(404, {"error": "Análisis no encontrado"})
        logger.error(f"Error AWS: {e}")
        return _response(500, {"error": "Error generando URL del reporte"})
    except Exception as e:
        logger.error(f"Error inesperado: {e}")
        return _response(500, {"error": str(e)})


def _response(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body, ensure_ascii=False, default=str),
    }
