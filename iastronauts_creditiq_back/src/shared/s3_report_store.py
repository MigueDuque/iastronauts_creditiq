import json
import re
from datetime import datetime

import boto3

from .models.report import FinalReportOutput

_METADATA_RE = re.compile(r"<!--\s*CREDITIQ_REPORT\s*([\s\S]*?)-->", re.DOTALL)

_DOCX_TEMPLATE_KEY = "instructions/CreditIQ_Template_EEFF.docx"


def slugify(text: str) -> str:
    """Converts a company name to a filesystem-safe slug."""
    slug = text.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    return slug.strip("-") or "unknown"


def _trimester_months(month: int) -> list[int]:
    q = (month - 1) // 3
    return [q * 3 + 1, q * 3 + 2, q * 3 + 3]


def load_docx_template(bucket: str, *, s3_client=None, template_key: str = _DOCX_TEMPLATE_KEY) -> bytes | None:
    """Download the .docx report template from S3. Returns None if not found."""
    client = s3_client or boto3.client("s3")
    try:
        obj = client.get_object(Bucket=bucket, Key=template_key)
        return obj["Body"].read()
    except Exception:
        return None


def deserialize_from_markdown(content: str) -> FinalReportOutput:
    """Reconstructs a FinalReportOutput from a legacy .md file."""
    match = _METADATA_RE.search(content)
    if not match:
        raise ValueError("El archivo no contiene metadatos CREDITIQ_REPORT válidos.")
    data = json.loads(match.group(1).strip())
    return FinalReportOutput.model_validate(data)


def fetch_historical_reports(
    tenant_id: str,
    company_slug: str,
    reference_date: datetime,
    bucket: str,
    *,
    s3_client=None,
) -> list[FinalReportOutput]:
    """Returns historical .md reports from the same quarter one year ago + December prior year.

    New reports are stored as .docx in the job store and won't appear here — this
    function exists for backward-compatible context from legacy runs.
    """
    client = s3_client or boto3.client("s3")
    prev_year = reference_date.year - 1
    trimester = _trimester_months(reference_date.month)

    prefixes: set[str] = set()
    for month in trimester:
        prefixes.add(f"reports/{tenant_id}/{company_slug}/{prev_year}/{month:02d}/")
    if 12 not in trimester:
        prefixes.add(f"reports/{tenant_id}/{company_slug}/{prev_year}/12/")

    reports: list[FinalReportOutput] = []
    paginator = client.get_paginator("list_objects_v2")

    for prefix in sorted(prefixes):
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if not key.endswith(".md"):
                    continue
                body = client.get_object(Bucket=bucket, Key=key)["Body"].read()
                try:
                    reports.append(deserialize_from_markdown(body.decode("utf-8")))
                except (ValueError, KeyError, json.JSONDecodeError):
                    pass

    return reports
