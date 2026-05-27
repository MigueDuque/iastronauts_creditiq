"""
pause/handler.py

Invoked by Step Functions with waitForTaskToken.
Saves the task token + pause status to S3, then returns immediately.
The execution stays paused until the /continue API calls SendTaskSuccess.
"""
import logging

from shared.job_store import save as job_save, STATUS

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def lambda_handler(event: dict, context) -> None:
    job_id = event["job_id"]
    task_token = event["task_token"]
    pause_status = event["pause_status"]   # "extraction_complete" | "analysis_complete"

    job_save(job_id, STATUS, {
        "status": pause_status,
        "task_token": task_token,
    })

    logger.info("paused | job=%s status=%s", job_id, pause_status)
    # Return without calling SendTaskSuccess — SFN waits for /continue
