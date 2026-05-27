"""
job_store.py

Single source of truth for S3 job artifact paths and read/write helpers.

Key structure:
    jobs/{YYYY-MM-DD}/{job_id}/status.json
    jobs/{YYYY-MM-DD}/{job_id}/extractor_response.json
    jobs/{YYYY-MM-DD}/{job_id}/financial_analyzer_response.json
    jobs/{YYYY-MM-DD}/{job_id}/risk_scorer_response.json
    jobs/{YYYY-MM-DD}/{job_id}/report_generator_response.json
    jobs/{YYYY-MM-DD}/{job_id}/revisor_response.json

The date folder is derived from job_id when possible (job_id starts with YYYY-MM-DD),
otherwise falls back to today's UTC date so the key is always deterministic from job_id.
"""

import json
import logging
import os
import re
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger("shared.job_store")

BUCKET = os.environ.get("MAIN_BUCKET", "")

# Matches job_ids that start with an ISO date: 2026-05-26-<rest>
_DATE_PREFIX_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")

# Canonical artifact names — one per agent
EXTRACTOR = "extractor_response"
FINANCIAL_ANALYZER = "financial_analyzer_response"
RISK_SCORER = "risk_scorer_response"
REPORT_GENERATOR = "report_generator_response"
REVISOR = "revisor_response"
STATUS = "status"


def _date_for(job_id: str) -> str:
    """
    Extract or derive the date folder for a given job_id.
    If job_id starts with YYYY-MM-DD (e.g. '2026-05-26-uuid'), use that date.
    Otherwise use today's UTC date so the key is still deterministic per day.
    """
    m = _DATE_PREFIX_RE.match(job_id)
    if m:
        return m.group(1)
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def job_key(job_id: str, artifact: str) -> str:
    """Return the S3 key for a job artifact, e.g. jobs/2026-05-26/abc123/extractor_response.json"""
    date = _date_for(job_id)
    return f"jobs/{date}/{job_id}/{artifact}.json"


def save(job_id: str, artifact: str, payload: dict, s3_client=None) -> str:
    """
    Serialize payload as JSON and upload to S3.
    Returns the S3 key written.
    Raises on S3 errors — callers should catch and log if non-fatal.
    """
    client = s3_client or boto3.client("s3")
    key = job_key(job_id, artifact)
    client.put_object(
        Bucket=BUCKET,
        Key=key,
        Body=json.dumps(payload, ensure_ascii=False, default=str),
        ContentType="application/json",
    )
    logger.info("job_store.save | job=%s artifact=%s key=%s", job_id, artifact, key)
    return key


def load(job_id: str, artifact: str, s3_client=None) -> dict:
    """
    Download and parse a job artifact from S3.
    Raises ClientError if the key does not exist.
    """
    client = s3_client or boto3.client("s3")
    key = job_key(job_id, artifact)
    obj = client.get_object(Bucket=BUCKET, Key=key)
    return json.loads(obj["Body"].read())


def load_first(job_id: str, artifacts: list[str], s3_client=None) -> dict | None:
    """
    Try artifacts in order; return the first one found, or None.
    Used by report_url fallback to serve the most complete available output.
    """
    client = s3_client or boto3.client("s3")
    for artifact in artifacts:
        try:
            return load(job_id, artifact, s3_client=client)
        except ClientError:
            continue
    return None
