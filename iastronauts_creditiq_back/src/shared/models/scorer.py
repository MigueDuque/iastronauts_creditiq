from pydantic import BaseModel, Field

from .base import RiskLevel, FinancialHealth, OutputFormat
from .orchestrator import BusinessContext
from .analyzer import AccountAnalysis


class ScorerOutput(BaseModel):
    """
    Output del Agente 3 (Risk Scorer) → input del Report Generator.
    Propaga el contexto global y los resultados del analyzer.
    """
    # Contexto global propagado desde AnalyzerOutput
    job_id: str
    tenant_id: str
    business_context: BusinessContext
    niif_standards: list[str]
    report_language: str
    output_formats: list[OutputFormat]

    # Resultados del analyzer propagados para el Report Generator
    company_name: str
    currency: str
    periods: list[str]
    financial_ratios: dict
    analysis_results: list[AccountAnalysis]
    high_materiality_accounts: list[str]
    niif_notes_required: list[str]
    overall_financial_health: FinancialHealth
    executive_narrative: str
    niif18_compliance: dict = {}

    # NIIF structural compliance result from DocumentExtractor (passed through)
    niif_validation: dict = {}

    # Output del scorer
    validation_score: int = Field(ge=0, le=100)
    overall_risk_score: RiskLevel
    issues_found: list[str]
    compliance_flags: list[str]
    requires_human_review: bool
    analysis_confidence: float = Field(ge=0.0, le=1.0)
    anti_hallucination_passed: bool

    # 5 risk dimensions (liquidity, credit, solvency, market, operational)
    risk_dimensions: dict = {}
    # 3 report-facing risk categories (credito, mercado, financiero) built from the
    # 5 dimensions; each carries level/score/key_findings/risk_drivers/metrics.
    risk_categories: dict = {}
    # LLM-generated risk narrative (paragraphs + recommendations + headline +
    # per-category narratives under "category_narratives")
    risk_summary: dict = {}
    # True when scoring thresholds were adjusted for investment fund context
    fund_context_adjusted: bool = False