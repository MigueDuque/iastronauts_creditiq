import json
import logging
import os
import time

import boto3
from botocore.exceptions import ClientError

from shared.job_store import load as job_load, STATUS, TENANT
from shared.tenant_context import TenantBoundaryViolation
from shared.tenant_middleware import extract_tenant_context, validate_requested_tenant

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# A "processing" status older than this is treated as a dead worker. The longest a
# single agent can legitimately run is ~330s (the Lambda/SFN task timeouts); past this
# ceiling the worker has crashed hard (OOM / timeout) without writing a terminal
# status, so we surface "failed" instead of an eternal spinner. Applies only to the
# manual/reanalyze path and the S3 fallback — when SFN reports RUNNING, SFN supervises
# the timeout itself and is trusted.
STALE_PROCESSING_SECONDS = 420


def _is_stale_processing(s3_data: dict) -> bool:
    """True when status is 'processing' and its heartbeat is older than the ceiling."""
    if s3_data.get("status") != "processing":
        return False
    updated = s3_data.get("updated_at")
    if not isinstance(updated, (int, float)):
        return False  # legacy status with no heartbeat — cannot judge; leave as-is
    return (time.time() - updated) > STALE_PROCESSING_SECONDS

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
        if _is_stale_processing(data):
            logger.warning("analysis_status | job=%s stale processing (fallback) -> failed", analysis_id)
            return _response(200, {
                "analysis_id": analysis_id,
                "status": "failed",
                "error": "El proceso excedió el tiempo máximo sin completarse. Reintenta el análisis.",
            })
        payload: dict = {
            "analysis_id": analysis_id,
            "status": data.get("status", "pending"),
            "error": data.get("error"),
        }
        if "progress" in data:
            payload["progress"] = data["progress"]
        return _response(200, payload)
    except ClientError:
        return _response(200, {"analysis_id": analysis_id, "status": "pending"})


def lambda_handler(event: dict, context) -> dict:
    """
    GET /analyses/{analysis_id}
    Header: Authorization (JWT) or x-tenant-id (dev only)
    """
    try:
        try:
            tenant_ctx = extract_tenant_context(event)
        except TenantBoundaryViolation:
            return _response(401, {"error": "Unauthorized — se requiere identidad de tenant válida"})

        analysis_id = event.get("pathParameters", {}).get("analysis_id")
        if not analysis_id:
            return _response(400, {"error": "analysis_id es requerido"})

        # Verify this job belongs to the authenticated tenant before any S3/SFN reads.
        try:
            tenant_data = job_load(analysis_id, TENANT)
            validate_requested_tenant(tenant_ctx, tenant_data["tenant_id"])
        except TenantBoundaryViolation:
            return _response(403, {"error": "Forbidden — este análisis no pertenece a su tenant"})
        except ClientError:
            pass  # tenant.json absent for jobs created before this change — allow through

        # Manual re-run override: when a job is re-run via /reanalyze (outside Step
        # Functions), status.json carries source="reanalyze". The SFN execution is
        # already terminal and would otherwise report "completed", so trust S3 here.
        try:
            s3_data = job_load(analysis_id, STATUS)
            if s3_data.get("source") == "reanalyze":
                # A reanalyze worker that crashed hard never wrote a terminal status;
                # time it out so the frontend shows an error instead of hanging.
                if _is_stale_processing(s3_data):
                    logger.warning("analysis_status | job=%s stale processing -> failed", analysis_id)
                    return _response(200, {
                        "analysis_id": analysis_id,
                        "status": "failed",
                        "error": "El proceso excedió el tiempo máximo sin completarse. Reintenta el análisis.",
                    })
                payload = {
                    "analysis_id": analysis_id,
                    "status": s3_data.get("status", "processing"),
                }
                if s3_data.get("progress"):
                    payload["progress"] = s3_data["progress"]
                if s3_data.get("error"):
                    payload["error"] = s3_data["error"]
                return _response(200, payload)
        except ClientError:
            pass

        execution = _sfn_client().describe_execution(executionArn=_execution_arn(analysis_id))

        sfn_status = execution["status"]

        # When RUNNING, check S3 for a pause status written by PauseFunction.
        # This distinguishes "agent actively running" from "paused waiting for analyst".
        _PAUSE_STATUSES = frozenset({
            "extraction_complete", "analysis_complete", "scoring_complete", "report_complete"
        })
        s3_progress: dict | None = None
        if sfn_status == "RUNNING":
            try:
                s3_data = job_load(analysis_id, STATUS)
                s3_status = s3_data.get("status", "processing")
                # Only trust recognised pause statuses from S3 — ignore "processing"
                # written by /continue so we don't flash a stale pause state.
                status = s3_status if s3_status in _PAUSE_STATUSES else "processing"
                s3_progress = s3_data.get("progress")
            except ClientError:
                status = "processing"
        elif sfn_status == "FAILED" and execution.get("error") == "ReviewExpired":
            # The analyst review window expired — surface a distinct status so the
            # frontend can show a helpful message instead of a generic "failed".
            status = "review_expired"
            try:
                s3_progress = job_load(analysis_id, STATUS).get("progress")
            except ClientError:
                pass
        else:
            status = _SFN_TO_STATUS.get(sfn_status, "unknown")
            try:
                s3_data = job_load(analysis_id, STATUS)
                s3_progress = s3_data.get("progress")
            except ClientError:
                pass

        payload = {
            "analysis_id": analysis_id,
            "status": status,
            "started_at": execution.get("startDate"),
            "stopped_at": execution.get("stopDate"),
        }
        if s3_progress:
            payload["progress"] = s3_progress

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
