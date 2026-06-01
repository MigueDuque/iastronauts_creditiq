import json
import logging
import os
import threading

import boto3
from botocore.exceptions import ClientError

from shared.audit_logger import AuditAction, log_audit_event
from shared.models import BusinessContext, FileToProcess, OrchestratorOutput, OutputFormat
from shared.s3_report_store import slugify
from shared.tenant_context import TenantBoundaryViolation
from shared.tenant_middleware import extract_tenant_context, source_ip, validate_requested_tenant
from shared.job_store import save as job_save, load as job_load, STATUS, EXTRACTOR

logger = logging.getLogger()
logger.setLevel(logging.INFO)

def _sfn_client():
    kwargs = {}
    if url := os.environ.get("SFN_ENDPOINT_URL"):
        kwargs["endpoint_url"] = url
    return boto3.client("stepfunctions", **kwargs)

s3  = boto3.client("s3")
WORKFLOW_ARN     = os.environ["WORKFLOW_ARN"]
BUCKET           = os.environ["MAIN_BUCKET"]
STAGE            = os.environ.get("STAGE", "dev")
LOCAL_DEV_BYPASS = os.environ.get("LOCAL_DEV_BYPASS_SFN", "").lower() == "true"


def _report_exists(tenant_id: str, company_name: str, reporting_period: str) -> bool:
    """Return True if at least one report .md already exists for this period."""
    try:
        parts = reporting_period.split("-")
        if len(parts) < 2:
            return False
        year, month = parts[0], parts[1].zfill(2)
        company_slug = slugify(company_name) if company_name else "unknown"
        prefix = f"reports/{tenant_id}/{company_slug}/{year}/{month}/"
        resp = s3.list_objects_v2(Bucket=BUCKET, Prefix=prefix, MaxKeys=1)
        return bool(resp.get("Contents"))
    except ClientError:
        return False


def _save_job_status(job_id: str, status: str, error: str | None = None, progress: dict | None = None) -> None:
    body: dict = {"status": status}
    if error:
        body["error"] = error
    if progress is not None:
        body["progress"] = progress
    job_save(job_id, STATUS, body)


def _run_extractor_bg(event: dict) -> None:
    """Local-dev only: runs the ExtractorFunction in-process and saves output to S3."""
    from shared.progress_store import build_progress
    job_id = event.get("job_id", "unknown")
    try:
        from agents.document_extractor.handler import lambda_handler as extractor_handler  # noqa: PLC0415
        result = extractor_handler(event, None)
        # extractor handler already calls job_save internally; build completion summary
        n_accounts = len((result or {}).get("accounts", []))
        confidence = (result or {}).get("extraction_confidence", 0)
        agent1_step = (
            f"{n_accounts} accounts extracted · {confidence * 100:.1f}% confidence"
            if n_accounts else "Accounts extracted"
        )
        _save_job_status(job_id, "extraction_complete", progress=build_progress(
            running_agent=None, current_step=None,
            done_agents=[1], done_steps={1: agent1_step},
        ))
        logger.info("local_dev_extractor | done job_id=%s", job_id)
    except Exception as exc:
        logger.error("local_dev_extractor | failed job_id=%s error=%s", job_id, exc, exc_info=True)
        _save_job_status(job_id, "failed", error=str(exc), progress=build_progress(
            running_agent=None, current_step=None,
            done_agents=[], failed_agent=1,
        ))


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

        # Accept both "files_to_process" (frontend) and "files" (legacy)
        files_raw = body.get("files_to_process") or body.get("files", [])
        if not files_raw:
            return _response(400, {"error": "Se requiere al menos un archivo en 'files_to_process'"})

        for f in files_raw:
            # Accept both "s3_location" (frontend) and "s3_key" (legacy)
            s3_key = f.get("s3_location") or f.get("s3_key", "")
            try:
                tenant_ctx.assert_s3_key(s3_key)
            except TenantBoundaryViolation:
                log_audit_event(
                    tenant_id=tenant_ctx.tenant_id,
                    action=AuditAction.TENANT_BOUNDARY_VIOLATION,
                    status="failure",
                    resource=s3_key,
                    ip_address=source_ip(event),
                    metadata={"reason": "s3_location belongs to a different tenant"},
                )
                return _response(403, {
                    "error": f"Forbidden — s3_location '{s3_key}' does not belong to your tenant"
                })

        # ── Build pipeline input ──────────────────────────────────────────────
        # Accept both flat fields and nested business_context object
        biz = body.get("business_context") or {}
        business_context = BusinessContext(
            company_name=body.get("company_name") or biz.get("company_name"),
            industry=body.get("industry") or biz.get("industry"),
            fiscal_year=body.get("fiscal_year") or biz.get("fiscal_year"),
            reporting_period=body.get("reporting_period") or biz.get("reporting_period"),
            key_events=body.get("key_events") or biz.get("key_events", []),
            strategic_context=body.get("strategic_context") or biz.get("strategic_context"),
            regulatory_context=body.get("regulatory_context") or biz.get("regulatory_context"),
            analyst_instructions=body.get("analyst_instructions") or biz.get("analyst_instructions", []),
            raw_context=body.get("raw_context") or biz.get("raw_context", ""),
        )

        files_to_process = [
            FileToProcess(
                file_name=f["file_name"],
                s3_location=f.get("s3_location") or f.get("s3_key", ""),
                file_type=f.get("file_type", "pdf"),
            )
            for f in files_raw
        ]

        output_formats = [
            OutputFormat(fmt) for fmt in body.get("output_formats", ["markdown"])
            if fmt in OutputFormat._value2member_map_
        ] or [OutputFormat.MARKDOWN]

        # ── Duplicate analysis guard ──────────────────────────────────────────
        force_rerun = body.get("force_rerun", False)
        company_name_raw = (
            body.get("company_name")
            or body.get("business_context", {}).get("company_name", "")
        )
        reporting_period_raw = (
            body.get("reporting_period")
            or body.get("business_context", {}).get("reporting_period", "")
        )

        if not force_rerun and company_name_raw and reporting_period_raw:
            if _report_exists(tenant_ctx.tenant_id, company_name_raw, reporting_period_raw):
                logger.info(
                    "orchestrator | duplicate detected tenant=%s company=%s period=%s",
                    tenant_ctx.tenant_id, company_name_raw, reporting_period_raw,
                )
                return _response(409, {
                    "error": "duplicate_analysis",
                    "message": (
                        f"Ya existe un análisis completado para '{company_name_raw}' "
                        f"en el período {reporting_period_raw}. "
                        "Confirme si desea ejecutarlo nuevamente."
                    ),
                    "company_name": company_name_raw,
                    "reporting_period": reporting_period_raw,
                })

        # tenant_id comes exclusively from the verified tenant context
        orchestrator_output = OrchestratorOutput(
            tenant_id=tenant_ctx.tenant_id,
            business_context=business_context,
            files_to_process=files_to_process,
            niif_standards=body.get("niif_standards", []),
            report_language=body.get("report_language", "es"),
            output_formats=output_formats,
        )

        if LOCAL_DEV_BYPASS:
            # LOCAL_DEV_BYPASS_SFN=true — skip Step Functions and run the extractor
            # directly in a background thread. The status/report handlers fall back to
            # S3 (jobs/{id}/status.json and extractor_output.json) when the execution
            # doesn't exist in SFN, so no Terminal 3 (sam local start-lambda) is needed.
            from shared.progress_store import build_progress
            logger.warning(
                "orchestrator | LOCAL_DEV_BYPASS_SFN active, skipping SFN. job_id=%s",
                orchestrator_output.job_id,
            )
            _save_job_status(orchestrator_output.job_id, "processing", progress=build_progress(
                running_agent=1, current_step="Processing documents",
                done_agents=[],
            ))
            thread = threading.Thread(
                target=_run_extractor_bg,
                args=(orchestrator_output.model_dump(mode="json"),),
                daemon=True,
            )
            thread.start()
        else:
            try:
                _sfn_client().start_execution(
                    stateMachineArn=WORKFLOW_ARN,
                    name=orchestrator_output.job_id,
                    input=json.dumps(orchestrator_output.model_dump(mode="json")),
                )
            except ClientError as e:
                code = e.response["Error"]["Code"]
                if code in ("StateMachineDoesNotExist", "AccessDeniedException") and STAGE == "dev":
                    from shared.progress_store import build_progress
                    logger.warning(
                        "orchestrator | SFN not available (%s), running extractor locally (dev only). job_id=%s",
                        code, orchestrator_output.job_id,
                    )
                    _save_job_status(orchestrator_output.job_id, "processing", progress=build_progress(
                        running_agent=1, current_step="Processing documents", done_agents=[],
                    ))
                    thread = threading.Thread(
                        target=_run_extractor_bg,
                        args=(orchestrator_output.model_dump(mode="json"),),
                        daemon=True,
                    )
                    thread.start()
                else:
                    raise

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
