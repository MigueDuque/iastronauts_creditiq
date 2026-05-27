import json
import logging
import os

import boto3
from botocore.exceptions import ClientError

from shared.job_store import load as job_load, STATUS

logger = logging.getLogger()
logger.setLevel(logging.INFO)

def _sfn_client():
    kwargs = {}
    if url := os.environ.get("SFN_ENDPOINT_URL"):
        kwargs["endpoint_url"] = url
    return boto3.client("stepfunctions", **kwargs)

s3     = boto3.client("s3")
BUCKET = os.environ["MAIN_BUCKET"]

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


def _s3_status_fallback(analysis_id: str) -> dict:
    """Read status from S3 — written by the orchestrator background thread in local dev."""
    try:
        data = job_load(analysis_id, STATUS)
        return _response(200, {
            "analysis_id": analysis_id,
            "status": data.get("status", "pending"),
            "error": data.get("error"),
        })
    except ClientError:
        return _response(200, {"analysis_id": analysis_id, "status": "pending"})


def lambda_handler(event: dict, context) -> dict:
    """
    GET /analyses/{analysis_id}
    Header: x-tenant-id
    """
    try:
        analysis_id = event.get("pathParameters", {}).get("analysis_id")
        if not analysis_id:
            return _response(400, {"error": "analysis_id es requerido"})

        execution = _sfn_client().describe_execution(executionArn=_execution_arn(analysis_id))

        sfn_status = execution["status"]

        # When RUNNING, check S3 for a pause status written by PauseFunction.
        # This distinguishes "agent actively running" from "paused waiting for analyst".
        if sfn_status == "RUNNING":
            try:
                s3_data = job_load(analysis_id, STATUS)
                s3_status = s3_data.get("status", "processing")
                # Only trust pause statuses from S3 — not "processing" written by /continue
                status = s3_status if s3_status in ("extraction_complete", "analysis_complete") else "processing"
            except ClientError:
                status = "processing"
        else:
            status = _SFN_TO_STATUS.get(sfn_status, "unknown")

        payload = {
            "analysis_id": analysis_id,
            "status": status,
            "started_at": execution.get("startDate"),
            "stopped_at": execution.get("stopDate"),
        }

        if status == "failed":
            payload["error"] = "El pipeline falló. Revisa los logs de Step Functions para detalles."

        return _response(200, payload)

    except (ClientError, Exception) as e:
        code = e.response["Error"]["Code"] if isinstance(e, ClientError) else type(e).__name__
        logger.warning("analysis_status | SFN error (%s), falling back to S3", code)
        return _s3_status_fallback(analysis_id)


def _response(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body, ensure_ascii=False, default=str),
    }
