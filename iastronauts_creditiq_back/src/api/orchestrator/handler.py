import json
import logging
import os

import boto3
from botocore.exceptions import ClientError

from shared.audit_logger import AuditAction, log_audit_event
from shared.models import BusinessContext, FileToProcess, OrchestratorOutput, OutputFormat
from shared.tenant_context import TenantBoundaryViolation
from shared.tenant_middleware import extract_tenant_context, source_ip, validate_requested_tenant

logger = logging.getLogger()
logger.setLevel(logging.INFO)

sfn = boto3.client("stepfunctions")
WORKFLOW_ARN = os.environ["WORKFLOW_ARN"]


def lambda_handler(event: dict, context) -> dict:
    """
    POST /analyses

    Body: {
        "files": [{ "file_name": "...", "s3_key": "...", "file_type": "pdf" }],
        "company_name": "...",
        "raw_context": "...",
        "niif_standards": ["NIIF 7"],
        "report_language": "es",
        "output_formats": ["markdown"]
    }

    tenant_id is always taken from the verified JWT — never from the request body.
    If a caller supplies tenant_id in the body and it does not match the JWT,
    the request is rejected and a security audit event is emitted.
    """
    # ── Tenant authentication ─────────────────────────────────────────────────
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

    # ── Parse body ────────────────────────────────────────────────────────────
    try:
        body = json.loads(event.get("body") or "{}")

        # Reject if caller tries to impersonate another tenant via body field
        body_tenant_id = body.get("tenant_id")
        if body_tenant_id and body_tenant_id != tenant_ctx.tenant_id:
            log_audit_event(
                tenant_id=tenant_ctx.tenant_id,
                action=AuditAction.TENANT_BOUNDARY_VIOLATION,
                status="failure",
                ip_address=source_ip(event),
                metadata={
                    "jwt_tenant": tenant_ctx.tenant_id,
                    "body_tenant": body_tenant_id,
                    "reason": "caller attempted to submit analysis under a different tenant_id",
                },
            )
            return _response(403, {
                "error": "Forbidden — tenant_id in body does not match authenticated tenant"
            })

        # Validate uploaded S3 keys belong to this tenant
        files_raw = body.get("files", [])
        if not files_raw:
            return _response(400, {"error": "Se requiere al menos un archivo en 'files'"})

        for f in files_raw:
            s3_key = f.get("s3_key", "")
            try:
                tenant_ctx.assert_s3_key(s3_key)
            except TenantBoundaryViolation:
                log_audit_event(
                    tenant_id=tenant_ctx.tenant_id,
                    action=AuditAction.TENANT_BOUNDARY_VIOLATION,
                    status="failure",
                    resource=s3_key,
                    ip_address=source_ip(event),
                    metadata={"reason": "s3_key belongs to a different tenant"},
                )
                return _response(403, {
                    "error": f"Forbidden — s3_key '{s3_key}' does not belong to your tenant"
                })

        # ── Build pipeline input ──────────────────────────────────────────────
        business_context = BusinessContext(
            company_name=body.get("company_name"),
            industry=body.get("industry"),
            fiscal_year=body.get("fiscal_year"),
            reporting_period=body.get("reporting_period"),
            key_events=body.get("key_events", []),
            strategic_context=body.get("strategic_context"),
            regulatory_context=body.get("regulatory_context"),
            analyst_instructions=body.get("analyst_instructions", []),
            raw_context=body.get("raw_context", ""),
        )

        files_to_process = [
            FileToProcess(
                file_name=f["file_name"],
                s3_location=f["s3_key"],
                file_type=f.get("file_type", "pdf"),
            )
            for f in files_raw
        ]

        output_formats = [
            OutputFormat(fmt) for fmt in body.get("output_formats", ["markdown"])
            if fmt in OutputFormat._value2member_map_
        ] or [OutputFormat.MARKDOWN]

        # tenant_id comes exclusively from the verified tenant context
        orchestrator_output = OrchestratorOutput(
            tenant_id=tenant_ctx.tenant_id,
            business_context=business_context,
            files_to_process=files_to_process,
            niif_standards=body.get("niif_standards", []),
            report_language=body.get("report_language", "es"),
            output_formats=output_formats,
        )

        sfn.start_execution(
            stateMachineArn=WORKFLOW_ARN,
            name=orchestrator_output.job_id,
            input=json.dumps(orchestrator_output.model_dump(mode="json")),
        )

        log_audit_event(
            tenant_id=tenant_ctx.tenant_id,
            action=AuditAction.ANALYSIS_STARTED,
            job_id=orchestrator_output.job_id,
            ip_address=source_ip(event),
            metadata={
                "company_name": body.get("company_name"),
                "files": [f.get("file_name") for f in files_raw],
                "authenticated_via": tenant_ctx.authenticated_via,
            },
        )
        logger.info(
            "orchestrator | started job_id=%s tenant=%s",
            orchestrator_output.job_id,
            tenant_ctx.tenant_id,
        )

        return _response(202, {
            "analysis_id": orchestrator_output.job_id,
            "tenant_id": tenant_ctx.tenant_id,
            "status": "pending",
            "message": "Análisis iniciado correctamente",
        })

    except (KeyError, ValueError) as e:
        logger.warning("orchestrator | invalid input: %s", e)
        return _response(400, {"error": str(e)})
    except ClientError as e:
        logger.error("orchestrator | AWS error: %s", e)
        return _response(500, {"error": "Error iniciando el análisis"})
    except Exception as e:
        logger.error("orchestrator | unexpected error: %s", e)
        return _response(500, {"error": str(e)})


def _response(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body, ensure_ascii=False, default=str),
    }
