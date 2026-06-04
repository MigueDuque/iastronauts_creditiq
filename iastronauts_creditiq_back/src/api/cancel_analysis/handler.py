import json
import os

import boto3
from botocore.exceptions import ClientError


def _sfn_client():
    kwargs = {}
    if url := os.environ.get("SFN_ENDPOINT_URL"):
        kwargs["endpoint_url"] = url
    return boto3.client("stepfunctions", **kwargs)

from shared.job_store import save as job_save, load as job_load, STATUS, TENANT
from shared.tenant_context import TenantBoundaryViolation
from shared.tenant_middleware import extract_tenant_context, validate_requested_tenant


def lambda_handler(event: dict, context) -> dict:
    try:
        tenant_ctx = extract_tenant_context(event)
    except TenantBoundaryViolation:
        return {"statusCode": 401, "body": json.dumps({"error": "Unauthorized"})}

    analysis_id = (event.get("pathParameters") or {}).get("analysis_id", "")
    if not analysis_id:
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "analysis_id required"}),
        }

    try:
        tenant_data = job_load(analysis_id, TENANT)
        validate_requested_tenant(tenant_ctx, tenant_data["tenant_id"])
    except TenantBoundaryViolation:
        return {"statusCode": 403, "body": json.dumps({"error": "Forbidden"})}
    except ClientError:
        pass  # tenant.json absent for legacy jobs — allow through

    region = os.environ.get("AWS_REGION", "us-east-1")
    account_id = os.environ.get("AWS_ACCOUNT_ID", "")
    stage = os.environ.get("STAGE", "dev")

    if account_id:
        execution_arn = (
            f"arn:aws:states:{region}:{account_id}:"
            f"execution:creditiq-analysis-workflow-{stage}:{analysis_id}"
        )
        try:
            _sfn_client().stop_execution(executionArn=execution_arn, cause="User cancelled via API")
        except Exception:
            pass  # best-effort: execution may already be in a terminal state

    try:
        job_save(analysis_id, STATUS, {"status": "cancelled"})
    except Exception:
        pass

    return {
        "statusCode": 200,
        "body": json.dumps({"analysis_id": analysis_id, "status": "cancelled"}),
    }
