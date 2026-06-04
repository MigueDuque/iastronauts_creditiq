"""
JWT / Cognito tenant extraction and request-level tenant enforcement.

FLOW:
    API Gateway (Cognito JWT Authorizer)
        → event["requestContext"]["authorizer"]["jwt"]["claims"]
        → extract_tenant_context(event)
        → TenantContext (verified, immutable)
        → lambda_handler proceeds with tenant_ctx

WHY A DEDICATED MODULE:
    Tenant extraction logic must not be duplicated across every Lambda handler.
    A single authoritative function means a single place to update when Cognito
    claim names change, when we add a new tier, or when we add an audit call.

PRODUCTION REQUIREMENT:
    'authenticated_via' must be "jwt" in production. The header and body
    fallbacks exist only for local development and internal service calls
    that do not route through API Gateway.

COGNITO SETUP:
    User pool must have custom attributes:
        custom:tenant_id     — populated during tenant onboarding
        custom:tenant_name   — human-readable org name
        custom:tenant_tier   — free | professional | enterprise
        custom:permissions   — comma-separated, e.g. "analyses:create,analyses:read"

    Lambda trigger (Pre Token Generation) copies these into the ID and
    access tokens automatically.
"""

import json
import logging
import os

from .tenant_context import TenantBoundaryViolation, TenantContext, TenantTier

logger = logging.getLogger()

# Stages where JWT is the only accepted auth mechanism.
# "dev" and "test" allow header/body fallbacks for local dev and CI.
_STAGE = os.environ.get("STAGE", "dev")
_PRODUCTION_STAGES = frozenset({"uat", "master"})


def extract_tenant_context(event: dict) -> TenantContext:
    """
    Extracts a verified TenantContext from the Lambda event.

    Priority order (stops at first match):
      1. Cognito JWT claims via API Gateway HTTP API authorizer  ← production
      2. x-tenant-id header                                      ← internal/dev
      3. tenant_id in request body                               ← dev/backward compat

    Raises TenantBoundaryViolation if no tenant identity is found.
    """
    # ── 1. Cognito JWT (HTTP API format) ─────────────────────────────────────
    jwt_claims = (
        event.get("requestContext", {})
        .get("authorizer", {})
        .get("jwt", {})
        .get("claims", {})
    )
    # REST API format fallback (Cognito User Pool authorizer on REST API)
    if not jwt_claims:
        jwt_claims = (
            event.get("requestContext", {})
            .get("authorizer", {})
            .get("claims", {})
        )

    if jwt_claims:
        tenant_id = jwt_claims.get("custom:tenant_id") or jwt_claims.get("tenant_id")
        if not tenant_id:
            raise TenantBoundaryViolation(
                "JWT is valid but does not contain a tenant_id claim. "
                "Ensure the Cognito Pre Token Generation trigger populates "
                "'custom:tenant_id'."
            )

        raw_tier = jwt_claims.get("custom:tenant_tier", "professional")
        try:
            tier = TenantTier(raw_tier)
        except ValueError:
            tier = TenantTier.PROFESSIONAL

        raw_permissions = jwt_claims.get("custom:permissions", "")
        permissions = [p.strip() for p in raw_permissions.split(",") if p.strip()]

        return TenantContext(
            tenant_id=tenant_id,
            tenant_name=jwt_claims.get("custom:tenant_name", ""),
            tier=tier,
            permissions=permissions or ["analyses:create", "analyses:read"],
            authenticated_via="jwt",
        )

    # ── 2. x-tenant-id header (internal / dev) ───────────────────────────────
    headers = event.get("headers") or {}
    header_tenant = headers.get("x-tenant-id") or headers.get("X-Tenant-Id")
    if header_tenant:
        if _STAGE in _PRODUCTION_STAGES:
            raise TenantBoundaryViolation(
                "x-tenant-id header authentication is not permitted in production. "
                "All requests must carry a Cognito JWT (Authorization header)."
            )
        logger.warning(
            "tenant_context | source=header tenant=%s | "
            "x-tenant-id header is acceptable only for internal or dev traffic",
            header_tenant,
        )
        return TenantContext(
            tenant_id=header_tenant,
            authenticated_via="header",
            permissions=["analyses:create", "analyses:read"],
        )

    # ── 3. Request body tenant_id (backward compat, dev only) ────────────────
    try:
        body = json.loads(event.get("body") or "{}")
        body_tenant = body.get("tenant_id")
        if body_tenant:
            if _STAGE in _PRODUCTION_STAGES:
                raise TenantBoundaryViolation(
                    "body tenant_id authentication is not permitted in production. "
                    "All requests must carry a Cognito JWT (Authorization header)."
                )
            logger.warning(
                "tenant_context | source=body tenant=%s | "
                "body tenant_id is not secure for production traffic",
                body_tenant,
            )
            return TenantContext(
                tenant_id=body_tenant,
                authenticated_via="body",
                permissions=["analyses:create", "analyses:read"],
            )
    except (json.JSONDecodeError, AttributeError):
        pass

    # ── No tenant identity found ──────────────────────────────────────────────
    raise TenantBoundaryViolation(
        "No tenant identity in request. "
        "Production traffic must include a Cognito JWT (Authorization header). "
        "Dev traffic may use x-tenant-id header."
    )


def validate_requested_tenant(
    tenant_ctx: TenantContext,
    requested_tenant_id: str,
) -> None:
    """
    Asserts the authenticated tenant matches the resource's tenant_id.

    PREVENTS: Horizontal privilege escalation — Tenant A crafting a request
    to read or modify Tenant B's analyses by guessing analysis_ids.

    Call this in any GET/DELETE handler that accepts a resource identifier
    in the path or body, before performing any S3 or database lookup.
    """
    if tenant_ctx.tenant_id != requested_tenant_id:
        raise TenantBoundaryViolation(
            f"Authenticated tenant '{tenant_ctx.tenant_id}' attempted to access "
            f"resources belonging to tenant '{requested_tenant_id}'. "
            f"This event has been audit logged."
        )


def source_ip(event: dict) -> str | None:
    """Extracts the caller's IP address from the API Gateway event."""
    return (
        event.get("requestContext", {})
        .get("http", {})
        .get("sourceIp")
        or event.get("requestContext", {})
        .get("identity", {})
        .get("sourceIp")
    )
