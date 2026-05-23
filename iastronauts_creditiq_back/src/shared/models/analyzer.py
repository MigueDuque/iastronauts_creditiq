from pydantic import BaseModel

from .base import MaterialityLevel, RiskLevel, FinancialHealth, OutputFormat
from .orchestrator import BusinessContext


class AccountAnalysis(BaseModel):
    account_id: str
    account_name: str
    current_value: float
    previous_value: float
    absolute_variation: float
    variation_pct: float
    materiality: MaterialityLevel
    requires_niif_note: bool
    niif_note_references: list[str]
    risk_level: RiskLevel
    possible_causes: list[str]
    executive_insight: str
    anomaly_detected: bool


class AnalyzerOutput(BaseModel):
    """
    Output del Agente 2 (Financial Analyzer) → input del Risk Scorer.
    Propaga el contexto global recibido de ExtractorOutput.
    """
    # Contexto global propagado desde ExtractorOutput
    job_id: str
    business_context: BusinessContext
    niif_standards: list[str]
    report_language: str
    output_formats: list[OutputFormat]

    # Output del analyzer
    company_name: str
    analysis_results: list[AccountAnalysis]
    high_materiality_accounts: list[str]
    niif_notes_required: list[str]
    overall_financial_health: FinancialHealth
    executive_narrative: str