"""
Tenant context model and S3 boundary enforcement.

TenantContext is the single source of truth for who is making a request.
It is built once at the API boundary (from a verified JWT or internal header),
then propagated immutably through the entire agent pipeline.

WHY: A plain string tenant_id is unverifiable mid-pipeline. TenantContext
carries authentication metadata (how it was verified, tier, permissions) so
downstream agents can audit and enforce boundaries without re-parsing JWTs.
"""

from enum import Enum
from pydantic import BaseModel, Field


class TenantTier(str, Enum):
    FREE = "free"
    PROFESSIONAL = "professional"
    ENTERPRISE = "enterprise"


class TenantBoundaryViolation(Exception):
    """
    Raised when a tenant attempts to access another tenant's resources,
    or when tenant_id changes across agent pipeline steps.

    This is a security event — always audit log before raising.
    """


class TenantContext(BaseModel):
    """
    Immutable tenant security context. Built once at the API boundary,
    passed unchanged through every Step Functions state.

    Fields:
        tenant_id:          Globally unique tenant identifier (UUID recommended).
        tenant_name:        Human-readable name for logs and reports.
        tier:               Determines rate limits, feature access, model choice.
        permissions:        Comma-separated capability list from JWT claims.
        authenticated_via:  How this context was obtained. "jwt" is the only
                            production-safe value; "header"/"body" are dev-only.
    """

    tenant_id: str
    tenant_name: str = ""
    tier: TenantTier = TenantTier.PROFESSIONAL
    permissions: list[str] = Field(default_factory=list)
    authenticated_via: str = "header"  # jwt | header | body

    def can(self, permission: str) -> bool:
        """Returns True if tenant holds the given permission or wildcard '*'."""
        return "*" in self.permissions or permission in self.permissions

    def assert_s3_key(self, key: str) -> None:
        """
        Raises TenantBoundaryViolation if an S3 key falls outside the
        tenant's allowed prefixes.

        WHY: Without this check, a bug or injection could make one tenant
        read or overwrite another tenant's files by crafting a key like
        'reports/other-tenant/...'. This is the last line of defense before
        any S3 operation.
        """
        allowed_prefixes = (
            f"uploads/{self.tenant_id}/",
            f"reports/{self.tenant_id}/",
            f"rag/{self.tenant_id}/",
        )
        if not any(key.startswith(p) for p in allowed_prefixes):
            raise TenantBoundaryViolation(
                f"S3 key '{key}' is outside tenant '{self.tenant_id}' boundary. "
                f"Allowed prefixes: {allowed_prefixes}"
            )

    def assert_analysis_ownership(self, analysis_tenant_id: str) -> None:
        """
        Raises TenantBoundaryViolation if the analysis belongs to a different tenant.

        WHY: Prevents horizontal privilege escalation — Tenant A polling
        /analyses/{id} for an analysis_id that belongs to Tenant B.
        """
        if self.tenant_id != analysis_tenant_id:
            raise TenantBoundaryViolation(
                f"Tenant '{self.tenant_id}' cannot access resources owned by "
                f"tenant '{analysis_tenant_id}'"
            )
