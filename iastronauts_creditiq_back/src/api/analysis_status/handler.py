import json
import logging
import os

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

sfn = boto3.client("stepfunctions")

_SFN_TO_STATUS = {
    "RUNNING": "processing",
    "SUCCEEDED": "completed",
    "FAILED": "failed",
    "TIMED_OUT": "failed",
    "ABORTED": "failed",
}


def _execution_arn(analysis_id: str) -> str:
    region = os.environ["AWS_REGION"]
    account_id = os.environ["AWS_ACCOUNT_ID"]
    stage = os.environ["STAGE"]
    return f"arn:aws:states:{region}:{account_id}:execution:creditiq-analysis-workflow-{stage}:{analysis_id}"


def lambda_handler(event: dict, context) -> dict:
    """
    GET /analyses/{analysis_id}
    Header: x-tenant-id
    """
    try:
        analysis_id = event.get("pathParameters", {}).get("analysis_id")
        if not analysis_id:
            return _response(400, {"error": "analysis_id es requerido"})

        execution = sfn.describe_execution(executionArn=_execution_arn(analysis_id))

        status = _SFN_TO_STATUS.get(execution["status"], "unknown")

        payload = {
            "analysis_id": analysis_id,
            "status": status,
            "started_at": execution.get("startDate"),
            "stopped_at": execution.get("stopDate"),
        }

        if status == "failed":
            payload["error"] = "El pipeline falló. Revisa los logs de Step Functions para detalles."

        return _response(200, payload)

    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code == "ExecutionDoesNotExist":
            return _response(404, {"error": "Análisis no encontrado"})
        logger.error(f"Error AWS: {e}")
        return _response(500, {"error": "Error consultando el análisis"})
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
