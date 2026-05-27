"""
POST /analyses/{analysis_id}/continue

Reads the task token saved by PauseFunction and calls SendTaskSuccess,
resuming the Step Functions execution from where it was paused.
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


def _sfn_client():
    kwargs = {}
    if url := os.environ.get("SFN_ENDPOINT_URL"):
        kwargs["endpoint_url"] = url
    return boto3.client("stepfunctions", **kwargs)


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

        if not task_token:
            return _response(409, {
                "error": "El pipeline no está pausado o ya fue reanudado",
                "status": current_status,
            })

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
