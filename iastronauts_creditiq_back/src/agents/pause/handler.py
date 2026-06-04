"""
pause/handler.py

Invoked by Step Functions with waitForTaskToken.
Saves the task token + pause status to S3, then returns immediately.
The execution stays paused until the /continue API calls SendTaskSuccess.

The task token is written to a separate `pause_token` artifact so that
concurrent status polls reading `status.json` never race with the token
write, and the continue handler can clear the token independently.
"""
import logging

from shared.job_store import save as job_save, STATUS

logger = logging.getLogger()
logger.setLevel(logging.INFO)

PAUSE_TOKEN = "pause_token"


def lambda_handler(event: dict, context) -> None:
    job_id = event["job_id"]
    task_token = event["task_token"]
    pause_status = event["pause_status"]   # "extraction_complete" | "analysis_complete" | "scoring_complete"

    # Write status and token as separate artifacts so readers of status.json
    # never see a partially-written document that mixes status with the token.
    #
    # The token artifact records the pause_status it belongs to. /continue uses
    # this — NOT status.json — to choose which upstream artifact to resume with.
    # status.json can drift ahead of the live SFN pause point (e.g. the manual
    # agent_runner path advances it to "analysis_complete" while the token is
    # still parked at "extraction_complete"); trusting it would feed the wrong
    # agent output into the next state and crash it with a ValidationError.
    job_save(job_id, PAUSE_TOKEN, {"task_token": task_token, "pause_status": pause_status})
    job_save(job_id, STATUS, {"status": pause_status})

    logger.info("paused | job=%s status=%s", job_id, pause_status)
    # Return without calling SendTaskSuccess — SFN waits for /continue
