import json
import logging
import os

import boto3
from botocore.exceptions import ClientError

from shared.audit_logger import AuditAction, log_audit_event
from shared.tenant_context import TenantBoundaryViolation
from shared.tenant_middleware import extract_tenant_context, source_ip

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")
BUCKET = os.environ["MAIN_BUCKET"]
EXPIRATION = 300  # seconds


def lambda_handler(event: dict, context) -> dict:
    """
    POST /upload-url
    Body: { "file_name": "balance.pdf", "file_type": "pdf" }

    tenant_id is extracted from the verified JWT (or x-tenant-id header in dev).
    The S3 key is scoped to uploads/{tenant_id}/ so tenants cannot write to each
    other's prefixes via a crafted presigned URL.
    """
    try:
        tenant_ctx = extract_tenant_context(event)
    except TenantBoundaryViolation as e:
        log_audit_event(
            tenant_id="unknown",
            action=AuditAction.INVALID_JWT,
            status="failure",
            ip_address=source_ip(event),
            metadata={"reason": str(e)},
        )
        return _response(401, {"error": "Unauthorized — no valid tenant identity"})

    try:
        body = json.loads(event.get("body") or "{}")
        file_name = body.get("file_name")
        file_type = body.get("file_type", "pdf")
        folder    = body.get("folder", "").strip("/")

        if not file_name:
            return _response(400, {"error": "file_name es requerido"})

        # tenant_id is always the first segment so assert_s3_key passes.
        # folder (org/fund/date) is appended when provided.
        folder_path = f"{folder}/" if folder else ""
        object_key  = f"uploads/{tenant_ctx.tenant_id}/{folder_path}{file_name}"

        presigned_url = s3.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": BUCKET,
                "Key": object_key,
                "ContentType": _content_type(file_type),
            },
            ExpiresIn=EXPIRATION,
        )

        log_audit_event(
            tenant_id=tenant_ctx.tenant_id,
            action=AuditAction.PRESIGNED_URL_ISSUED,
            resource=object_key,
            ip_address=source_ip(event),
            metadata={
                "file_name": file_name,
                "file_type": file_type,
                "authenticated_via": tenant_ctx.authenticated_via,
            },
        )
        logger.info(
            "presigned_url | tenant=%s key=%s", tenant_ctx.tenant_id, object_key
        )

        return _response(200, {
            "upload_url": presigned_url,
            "s3_key": object_key,
            "expires_in": EXPIRATION,
        })

    except ClientError as e:
        logger.error("presigned_url | S3 error: %s", e)
        return _response(500, {"error": "Error generando URL de carga"})
    except Exception as e:
        logger.error("presigned_url | unexpected error: %s", e)
        return _response(500, {"error": str(e)})


def _content_type(file_type: str) -> str:
    return {
        "pdf": "application/pdf",
        "excel": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "csv": "text/csv",
    }.get(file_type.lower(), "application/octet-stream")


def _response(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body, ensure_ascii=False),
    }
