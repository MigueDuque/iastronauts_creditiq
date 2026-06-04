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
# Written once at job creation; never overwritten by agents or status updates.
TENANT = "tenant"
CHAT_LOG = "chat_log"
ANALYSIS_SUMMARY = "analysis_summary"

# job_id → date folder, resolved once and reused. job_ids are bare UUIDs with no
# embedded date, so the only way to find an existing job's folder is to look it up
# in S3 — we cache the answer to keep the hot path listing-free.
_job_date_cache: dict[str, str] = {}


def _date_for(job_id: str) -> str:
    """
    Deterministic date folder for a job_id, used when *writing* a brand-new job.
    If job_id starts with YYYY-MM-DD (e.g. '2026-05-26-uuid'), use that date.
    Otherwise use today's UTC date so the key is deterministic on the creation day.

    Reads must use _resolve_date() instead: a job created yesterday lives under
    yesterday's folder, but today's date would point at a non-existent key.
    """
    m = _DATE_PREFIX_RE.match(job_id)
    if m:
        return m.group(1)
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def job_key(job_id: str, artifact: str, date: str | None = None) -> str:
    """Return the S3 key for a job artifact, e.g. jobs/2026-05-26/abc123/extractor_response.json"""
    date = date or _date_for(job_id)
    return f"jobs/{date}/{job_id}/{artifact}.json"


def _find_existing_folder(job_id: str, client) -> str | None:
    """
    Scan jobs/ for the date folder that actually contains this job_id and cache it.
    Only called on a cache/fast-path miss (i.e. loading a job from a prior day).
    """
    needle = f"/{job_id}/"
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=BUCKET, Prefix="jobs/"):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if needle in key:
                folder = key.split("/")[1]
                _job_date_cache[job_id] = folder
                return folder
    return None


def save(job_id: str, artifact: str, payload: dict, s3_client=None) -> str:
    """
    Serialize payload as JSON and upload to S3.
    Returns the S3 key written.
    Raises on S3 errors — callers should catch and log if non-fatal.

    Writes go to the job's already-resolved folder when known (so a re-run on a
    later day keeps all artifacts together), otherwise the deterministic
    creation-day folder.
    """
    client = s3_client or boto3.client("s3")
    date = _job_date_cache.get(job_id) or _date_for(job_id)
    key = job_key(job_id, artifact, date)
    client.put_object(
        Bucket=BUCKET,
        Key=key,
        Body=json.dumps(payload, ensure_ascii=False, default=str),
        ContentType="application/json",
    )
    _job_date_cache.setdefault(job_id, date)
    logger.info("job_store.save | job=%s artifact=%s key=%s", job_id, artifact, key)
    return key


def load(job_id: str, artifact: str, s3_client=None) -> dict:
    """
    Download and parse a job artifact from S3.
    Raises ClientError if the key does not exist in any date folder.

    Fast path uses the cached / deterministic folder (covers same-day jobs with no
    extra S3 calls). On a miss, locates the real folder for jobs created on a
    different day — this is what makes "load a previous job" work in the GUI.
    """
    client = s3_client or boto3.client("s3")
    date = _job_date_cache.get(job_id) or _date_for(job_id)
    try:
        return _get(client, job_key(job_id, artifact, date))
    except ClientError:
        folder = _find_existing_folder(job_id, client)
        if not folder or folder == date:
            raise
        return _get(client, job_key(job_id, artifact, folder))


def exists(job_id: str, artifact: str, s3_client=None) -> bool:
    """True if the artifact exists in the job's resolved date folder."""
    client = s3_client or boto3.client("s3")
    date = _job_date_cache.get(job_id) or _date_for(job_id)
    try:
        client.head_object(Bucket=BUCKET, Key=job_key(job_id, artifact, date))
        return True
    except ClientError:
        folder = _find_existing_folder(job_id, client)
        if not folder or folder == date:
            return False
        try:
            client.head_object(Bucket=BUCKET, Key=job_key(job_id, artifact, folder))
            return True
        except ClientError:
            return False


def save_bytes(
    job_id: str,
    artifact: str,
    data: bytes,
    content_type: str,
    extension: str = "bin",
    s3_client=None,
) -> str:
    """Upload raw bytes to the job's S3 folder (same date-based path as JSON artifacts).

    Returns the S3 key written.  Use this for non-JSON deliverables (.docx, .pdf, etc.)
    that must live alongside the agent JSON outputs.
    """
    client = s3_client or boto3.client("s3")
    date = _job_date_cache.get(job_id) or _date_for(job_id)
    key = f"jobs/{date}/{job_id}/{artifact}.{extension}"
    client.put_object(
        Bucket=BUCKET,
        Key=key,
        Body=data,
        ContentType=content_type,
    )
    _job_date_cache.setdefault(job_id, date)
    logger.info("job_store.save_bytes | job=%s artifact=%s key=%s", job_id, artifact, key)
    return key


def load_text(job_id: str, artifact: str, extension: str = "md", s3_client=None) -> str:
    """Download a raw-text artifact (e.g. analysis_summary.md) from the job folder.

    Returns the decoded string, or raises ClientError when the key does not exist.
    Uses the same date-folder resolution logic as load().
    """
    client = s3_client or boto3.client("s3")
    date = _job_date_cache.get(job_id) or _date_for(job_id)
    key = f"jobs/{date}/{job_id}/{artifact}.{extension}"
    try:
        obj = client.get_object(Bucket=BUCKET, Key=key)
        return obj["Body"].read().decode("utf-8")
    except ClientError:
        folder = _find_existing_folder(job_id, client)
        if not folder or folder == date:
            raise
        key2 = f"jobs/{folder}/{job_id}/{artifact}.{extension}"
        obj = client.get_object(Bucket=BUCKET, Key=key2)
        return obj["Body"].read().decode("utf-8")


def _get(client, key: str) -> dict:
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
