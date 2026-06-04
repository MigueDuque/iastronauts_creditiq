import json
import logging

from shared.tenant_context import TenantBoundaryViolation
from shared.tenant_middleware import extract_tenant_context, validate_requested_tenant
from shared.job_store import load as job_load, TENANT
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


def lambda_handler(event: dict, context) -> dict:
    """
    POST /analyses/{analysis_id}/chat
    Body: { "message": "...", "tenant_id": "..." }
    """
    try:
        try:
            tenant_ctx = extract_tenant_context(event)
        except TenantBoundaryViolation:
            return _r(401, {"error": "Unauthorized"})

        analysis_id = (event.get("pathParameters") or {}).get("analysis_id")
        if not analysis_id:
            return _r(400, {"error": "analysis_id requerido"})

        try:
            tenant_data = job_load(analysis_id, TENANT)
            validate_requested_tenant(tenant_ctx, tenant_data["tenant_id"])
        except TenantBoundaryViolation:
            return _r(403, {"error": "Forbidden — este análisis no pertenece a su tenant"})
        except ClientError:
            pass

        body: dict = {}
        if event.get("body"):
            try:
                body = json.loads(event["body"])
            except (json.JSONDecodeError, TypeError):
                return _r(400, {"error": "Body JSON inválido"})

        message = (body.get("message") or "").strip()
        if not message:
            return _r(400, {"error": "message es requerido"})

        tenant_id = tenant_ctx.tenant_id or body.get("tenant_id") or ""

        from agents.revisor_inteligente.handler import chat_handler
        result = chat_handler(
            {"job_id": analysis_id, "message": message, "tenant_id": tenant_id},
            context,
        )

        if "error" in result:
            return _r(result.get("statusCode", 500), {"error": result["error"]})

        return _r(200, result)

    except Exception as exc:
        logger.error("chat_handler_error | %s", exc)
        return _r(500, {"error": str(exc)})


def _r(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body, ensure_ascii=False, default=str),
    }
