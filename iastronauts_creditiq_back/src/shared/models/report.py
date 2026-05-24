from datetime import datetime
from pydantic import BaseModel, Field

from .base import RiskLevel


class NiifNoteDraft(BaseModel):
    note_id: str
    niif_reference: str
    title: str
    content: str
    affected_account_ids: list[str]
    requires_disclosure: bool

class FinalReportOutput(BaseModel):
    """
    Output del Agente 4 (Report Generator). Entregable final del pipeline.
    """
    job_id: str
    tenant_id: str
    company_name: str
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    validation_score: int
    overall_risk_score: RiskLevel
    executive_summary: str
    board_summary: str
    niif_note_drafts: list[NiifNoteDraft]
    markdown_report_url: str
    pdf_report_url: str | None = None
    ppt_report_url: str | None = None
