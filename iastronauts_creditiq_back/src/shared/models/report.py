from datetime import datetime
from pydantic import BaseModel, Field

from .base import RiskLevel, FinancialHealth
from .analyzer import AccountAnalysis


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
    analysis_role: str = "general"   # AI Analysis Perspectives (role_context.py)
    company_name: str
    periods: list[str]                           # e.g. ["2024-12", "2023-12"]
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    validation_score: int = Field(ge=0, le=100)
    overall_risk_score: RiskLevel
    overall_financial_health: FinancialHealth
    executive_summary: str = ""
    board_summary: str = ""
    analysis_results: list[AccountAnalysis]      # cuenta a cuenta — base para históricos
    niif_note_drafts: list[NiifNoteDraft] = []
    markdown_report_url: str = ""
    pdf_report_url: str | None = None
    docx_report_url: str | None = None
    report_sections: dict = {}
    financial_ratios: dict = {}
    fund_analysis: dict = {}
    executive_kpis: dict = {}
    sheet_concentration: dict = {}
    structured_analysis: dict = {}
    cross_statement_signals: list[dict] = []
    earnings_sustainability: str = ""
    historical_context: list[dict] = []

    # ── Risk section (propagated from ScorerOutput) ──────────────────────────
    # 3 report-facing categories: credito, mercado, financiero
    risk_categories: dict = {}
    # LLM risk narrative: paragraphs + category_narratives + recommendations + headline
    risk_summary: dict = {}

    # ── Comparative period basis (propagated from ExtractorOutput) ───────────
    comparative_basis: dict = {}

    # ── Fund Policy Assessment (Sprint 2 Item 1, propagated from Analyzer) ───
    # Per-dimension status vs. regulatory/internal limits. Empty for non-fund entities.
    fund_policy_assessment: dict = {}

    # ── Top Variations with period labels (Sprint 2 Item 5) ──────────────────
    # Top 15 non-cash-flow accounts by |absolute_variation|, with explicit period labels.
    top_variations: list[dict] = []
