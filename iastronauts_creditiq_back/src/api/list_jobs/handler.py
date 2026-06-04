"""
GET /jobs — list jobs found in S3 under jobs/, newest first.

Mirrors the local_server.py /jobs endpoint so the frontend "Load previous job"
picker works against the deployed API too.

Discovery note: jobs are found via ANY *.json artifact under jobs/{date}/{job_id}/,
not only status.json. The Step Functions path writes the agents' *_response.json
but not status.json, so a status.json-only scan would miss every SFN-run job.
"""
import json
import logging
import os

import boto3
from botocore.exceptions import ClientError

from shared.tenant_context import TenantBoundaryViolation
from shared.tenant_middleware import extract_tenant_context

logger = logging.getLogger()
logger.setLevel(logging.INFO)

BUCKET = os.environ["MAIN_BUCKET"]


def lambda_handler(event: dict, context) -> dict:
    try:
        tenant_ctx = extract_tenant_context(event)
    except TenantBoundaryViolation:
        return _response(401, {"error": "Unauthorized — se requiere identidad de tenant válida"})

    s3 = boto3.client("s3")

    # ── Single paginated listing of everything under jobs/ ──────────────────────
    all_keys: set[str] = set()
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=BUCKET, Prefix="jobs/"):
        for obj in page.get("Contents", []):
            all_keys.add(obj["Key"])

    # ── Discover jobs from ANY artifact: jobs/{date}/{job_id}/{artifact}.json ────
    job_dates: dict[str, str] = {}
    for key in all_keys:
        parts = key.split("/")
        if len(parts) == 4 and parts[3].endswith(".json"):
            job_dates[parts[2]] = parts[1]  # job_id → date_folder

    jobs: list[dict] = []
    for job_id, date_folder in job_dates.items():
        # Tenant isolation: skip jobs that belong to a different tenant.
        tenant_key = f"jobs/{date_folder}/{job_id}/tenant.json"
        if tenant_key in all_keys:
            try:
                obj = s3.get_object(Bucket=BUCKET, Key=tenant_key)
                stored_tenant = json.loads(obj["Body"].read()).get("tenant_id")
                if stored_tenant and stored_tenant != tenant_ctx.tenant_id:
                    continue
            except Exception:
                pass  # if we can't read ownership, include for backward compat
        base = f"jobs/{date_folder}/{job_id}"
        has_extractor = f"{base}/extractor_response.json"          in all_keys
        has_analyzer  = f"{base}/financial_analyzer_response.json" in all_keys
        has_scorer    = f"{base}/risk_scorer_response.json"        in all_keys
        has_report    = f"{base}/report_generator_response.json"   in all_keys

        # Prefer the explicit status.json when present (local-dev path)…
        status = "unknown"
        try:
            obj = s3.get_object(Bucket=BUCKET, Key=f"{base}/status.json")
            status = json.loads(obj["Body"].read()).get("status", "unknown")
        except Exception:
            pass

        # …otherwise derive disposition from which artifacts exist (SFN path).
        if status in ("unknown", "processing", "pending"):
            if has_report:
                status = "completed"
            elif has_scorer:
                status = "scoring_complete"
            elif has_analyzer:
                status = "analysis_complete"
            elif has_extractor:
                status = "extraction_complete"

        company_name: str | None = None
        periods: list[str] = []
        if has_extractor:
            try:
                ext_obj = s3.get_object(Bucket=BUCKET, Key=f"{base}/extractor_response.json")
                ext = json.loads(ext_obj["Body"].read())
                company_name = ext.get("company_name")
                periods = ext.get("periods", [])
            except Exception:
                pass

        jobs.append({
            "job_id": job_id,
            "date": date_folder,
            "status": status,
            "company_name": company_name,
            "periods": periods,
        })

    jobs.sort(key=lambda j: j["date"], reverse=True)
    logger.info("list_jobs | found=%d", len(jobs))
    return _response(200, {"jobs": jobs})


def _response(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body, ensure_ascii=False, default=str),
    }
