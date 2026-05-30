"""
POST /analyses/{analysis_id}/continue

Resumes the pipeline from its pause point. Two paths:
  • SFN path — a task token saved by PauseFunction is present: call SendTaskSuccess
    to resume the Step Functions execution where it paused.
  • Manual path — no task token (the job was re-run via /reanalyze outside Step
    Functions, so the SFN execution already finished): fall back to invoking the
    agent_runner Lambda for the next agent, mirroring local_server's smart routing.
"""
import json
import logging
import os

import boto3
from botocore.exceptions import ClientError

from shared.job_store import load as job_load, load_first as job_load_first, save as job_save, STATUS, EXTRACTOR, FINANCIAL_ANALYZER

logger = logging.getLogger()
logger.setLevel(logging.INFO)

BUCKET = os.environ["MAIN_BUCKET"]

# current pipeline status → which agent to run next when driving the job manually.
_NEXT_AGENT_FOR_STATUS = {
    "extraction_complete": 2,
    "analysis_complete":   3,
    "scoring_complete":    4,
}


def _sfn_client():
    kwargs = {}
    if url := os.environ.get("SFN_ENDPOINT_URL"):
        kwargs["endpoint_url"] = url
    return boto3.client("stepfunctions", **kwargs)


def _continue_via_runner(analysis_id: str, current_status: str) -> dict:
    """Manual fallback: async-invoke agent_runner for the next agent."""
    agent = _NEXT_AGENT_FOR_STATUS.get(current_status)
    if not agent:
        return _response(409, {
            "error": f"Estado '{current_status}' no es un punto de continuación válido",
            "status": current_status,
        })
    # Mark processing up front so the first status poll doesn't see the stale state.
    job_save(analysis_id, STATUS, {"status": "processing", "source": "reanalyze"})
    boto3.client("lambda").invoke(
        FunctionName=os.environ["RUNNER_FUNCTION_NAME"],
        InvocationType="Event",
        Payload=json.dumps({"analysis_id": analysis_id, "agent": agent}).encode("utf-8"),
    )
    logger.info("continued (manual) | job=%s from=%s agent=%d", analysis_id, current_status, agent)
    return _response(202, {"analysis_id": analysis_id, "status": "processing"})


def lambda_handler(event: dict, context) -> dict:
    try:
        analysis_id = event.get("pathParameters", {}).get("analysis_id")
        if not analysis_id:
            return _response(400, {"error": "analysis_id es requerido"})

        # Load current status + task token saved by PauseFunction
        try:
            status_data = job_load(analysis_id, STATUS)
        except ClientError:
            return _response(404, {"error": "Job no encontrado"})

        task_token = status_data.get("task_token")
        current_status = status_data.get("status", "")

        # No SFN token → the job is being driven manually (post-reanalyze).
        # Continue by directly invoking the next agent via agent_runner.
        if not task_token:
            return _continue_via_runner(analysis_id, current_status)

        # Load the output of the last completed agent to pass as SFN state input
        if current_status == "extraction_complete":
            resume_payload = job_load(analysis_id, EXTRACTOR)
        elif current_status == "analysis_complete":
            resume_payload = job_load_first(analysis_id, [FINANCIAL_ANALYZER, EXTRACTOR]) or {}
        else:
            return _response(409, {
                "error": f"Estado '{current_status}' no es un punto de pausa válido",
            })

        # Resume the Step Functions execution
        _sfn_client().send_task_success(
            taskToken=task_token,
            output=json.dumps(resume_payload, ensure_ascii=False, default=str),
        )

        # Clear the task token so double-clicks are harmless
        job_save(analysis_id, STATUS, {"status": "processing"})

        logger.info("continued | job=%s from=%s", analysis_id, current_status)
        return _response(202, {"analysis_id": analysis_id, "status": "processing"})

    except ClientError as e:
        code = e.response["Error"]["Code"]
        logger.error("continue_failed | job=%s error=%s", analysis_id, e)
        if code == "TaskTimedOut":
            return _response(410, {"error": "El token de tarea expiró. Reinicia el análisis."})
        if code == "InvalidToken":
            return _response(409, {"error": "Token inválido — el análisis ya fue reanudado."})
        return _response(500, {"error": str(e)})
    except Exception as e:
        logger.error("continue_error | job=%s error=%s", analysis_id, e)
        return _response(500, {"error": str(e)})


def _response(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body, ensure_ascii=False),
    }
