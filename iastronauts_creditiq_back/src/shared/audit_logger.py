"""
Structured audit trail for financial compliance and security monitoring.

Every security-relevant action emits a JSON audit event to CloudWatch.
CloudWatch Logs Insights can query these directly:

    fields @timestamp, tenant_id, action, resource, status
    | filter action like "security."
    | sort @timestamp desc
    | limit 100

WHY AUDIT LOGGING:
    Financial platforms must demonstrate who accessed what, when, and from
    where — both for internal governance and regulatory compliance (SOC 2,
    ISO 27001, local financial supervisory requirements).

    Tenant boundary violations are security events, not just errors. They
    must be permanently recorded, not just raised as exceptions. A future
    SIEM integration can subscribe to the CloudWatch log group via Kinesis
    or EventBridge.

STORAGE:
    Audit events land in the same Lambda log group as application logs.
    The "audit": true field lets you isolate them with a Logs Insights filter.

    For production you may want a dedicated log group with longer retention
    (1 year minimum for financial systems) and a separate export to S3.
"""

import json
import logging
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

logger = logging.getLogger()


class AuditAction(str, Enum):
    # Analysis lifecycle
    ANALYSIS_STARTED = "analysis.started"
    ANALYSIS_COMPLETED = "analysis.completed"
    ANALYSIS_FAILED = "analysis.failed"

    # Data access
    REPORT_ACCESSED = "report.accessed"
    FILE_UPLOADED = "file.uploaded"
    PRESIGNED_URL_ISSUED = "file.presigned_url_issued"

    # AI operations
    LLM_CALL = "ai.llm_call"
    LLM_CALL_FAILED = "ai.llm_call_failed"

    # Human review
    HUMAN_REVIEW_REQUESTED = "review.human_review_requested"
    HUMAN_REVIEW_COMPLETED = "review.human_review_completed"

    # Security
    TENANT_BOUNDARY_VIOLATION = "security.tenant_boundary_violation"
    UNAUTHORIZED_ACCESS = "security.unauthorized_access"
    INVALID_JWT = "security.invalid_jwt"


class AuditEvent(BaseModel):
    """
    Canonical audit event. All fields map 1:1 to CloudWatch Logs Insights
    query fields so analysts can build dashboards without parsing nested JSON.
    """

    timestamp: str = Field(
        default_factory=lambda: datetime.utcnow().isoformat() + "Z"
    )
    tenant_id: str
    job_id: Optional[str] = None
    action: AuditAction
    actor: str = "system"           # Cognito sub/user_id or "system" for pipeline
    resource: Optional[str] = None  # S3 key, analysis_id, Lambda function name
    status: str = "success"         # "success" | "failure"
    ip_address: Optional[str] = None
    metadata: dict = Field(default_factory=dict)


def log_audit_event(
    tenant_id: str,
    action: AuditAction,
    *,
    job_id: Optional[str] = None,
    actor: str = "system",
    resource: Optional[str] = None,
    status: str = "success",
    ip_address: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> None:
    """
    Emits a structured audit event to CloudWatch as a JSON log line.

    This function never raises — audit logging must never block the
    happy path. Errors are swallowed and logged as warnings so that
    a CloudWatch outage does not take down the analysis pipeline.
    """
    try:
        event = AuditEvent(
            tenant_id=tenant_id,
            job_id=job_id,
            action=action,
            actor=actor,
            resource=resource,
            status=status,
            ip_address=ip_address,
            metadata=metadata or {},
        )
        # The "audit": true top-level field is the index key for Logs Insights
        logger.info(
            json.dumps({"audit": True, **event.model_dump()}, ensure_ascii=False)
        )
    except Exception as exc:
        logger.warning("audit_logger | failed to emit audit event: %s", exc)
