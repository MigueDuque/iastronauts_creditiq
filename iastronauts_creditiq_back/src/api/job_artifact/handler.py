"""
Per-agent artifact endpoints for the frontend's "Load previous job" views.

  GET /analyses/{analysis_id}/extractor  → raw DocumentExtractor (Agent 1) output
  GET /analyses/{analysis_id}/scorer     → raw RiskScorer (Agent 3) output

Serves the raw agent artifact JSON straight from the S3 jobs store. These routes
previously existed only in local_server.py, so against the deployed API the
Agent 1 / Agent 3 result views rendered "hasn't run yet" even when the data was
in the bucket. One Lambda backs both routes, dispatching on the path suffix.
"""
import json
import logging

from botocore.exceptions import ClientError

from shared.job_store import load as job_load, EXTRACTOR, FINANCIAL_ANALYZER, RISK_SCORER, TENANT
from shared.tenant_context import TenantBoundaryViolation
from shared.tenant_middleware import extract_tenant_context, validate_requested_tenant

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def _artifact_for_path(path: str) -> str | None:
    """Map the request path suffix to a job_store artifact name."""
    p = (path or "").rstrip("/")
    if p.endswith("/extractor"):
        return EXTRACTOR
    if p.endswith("/scorer"):
        return RISK_SCORER
    if p.endswith("/analyzer"):
        return FINANCIAL_ANALYZER
    return None


def lambda_handler(event: dict, context) -> dict:
    try:
        tenant_ctx = extract_tenant_context(event)
    except TenantBoundaryViolation:
        return _response(401, {"error": "Unauthorized — se requiere identidad de tenant válida"})

    analysis_id = (event.get("pathParameters") or {}).get("analysis_id")
    path = (
        event.get("rawPath")
        or event.get("requestContext", {}).get("http", {}).get("path", "")
    )
    artifact = _artifact_for_path(path)

    if not analysis_id or not artifact:
        return _response(400, {"error": "Ruta inválida o analysis_id faltante"})

    try:
        tenant_data = job_load(analysis_id, TENANT)
        validate_requested_tenant(tenant_ctx, tenant_data["tenant_id"])
    except TenantBoundaryViolation:
        return _response(403, {"error": "Forbidden — este análisis no pertenece a su tenant"})
    except ClientError:
        pass  # tenant.json absent for legacy jobs — allow through

    try:
        data = job_load(analysis_id, artifact)
    except ClientError:
        # Artifact not yet produced for this job — let the frontend treat it as "not run".
        return _response(404, {})
    except Exception as exc:
        logger.error("job_artifact | job=%s artifact=%s error=%s", analysis_id, artifact, exc)
        return _response(500, {"error": "Error cargando el artefacto del job"})

    logger.info("job_artifact | job=%s artifact=%s ok", analysis_id, artifact)
    return _response(200, data)


def _response(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body, ensure_ascii=False, default=str),
    }
