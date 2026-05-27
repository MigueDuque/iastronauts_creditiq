import json
import os

import boto3

from shared.job_store import save as job_save, STATUS


def lambda_handler(event: dict, context) -> dict:
    analysis_id = (event.get("pathParameters") or {}).get("analysis_id", "")
    if not analysis_id:
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "analysis_id required"}),
        }

    region = os.environ.get("AWS_REGION", "us-east-1")
    account_id = os.environ.get("AWS_ACCOUNT_ID", "")
    stage = os.environ.get("STAGE", "dev")

    if account_id:
        execution_arn = (
            f"arn:aws:states:{region}:{account_id}:"
            f"execution:creditiq-analysis-workflow-{stage}:{analysis_id}"
        )
        try:
            sfn = boto3.client("states", region_name=region)
            sfn.stop_execution(executionArn=execution_arn, cause="User cancelled via API")
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
