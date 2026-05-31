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
    earnings_quality: dict = {}
    portfolio_concentration: dict = {}
    fund_analysis: dict = {}
    macro_context: dict = {}
    executive_kpis: dict = {}
    portfolio_thesis: str = ""
    insight_tiers: dict = {}
    narrative_layers: dict = {}
    executive_synthesis: dict = {}
    structured_analysis: dict = {}
    cross_statement_signals: list[dict] = []
    earnings_sustainability: str = ""
    financial_diagnostics: dict = {}
    sheet_concentration: dict = {}

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

    # ── Transparency upgrades (additive; the scalars above stay canonical so the
    #    Revisor, Report Generator and frontend keep working). Each detail object
    #    explains how its scalar was computed so the number is auditable. ────────
    # Mejora 1: composite_score breakdown (formula, weights, weighted_components,
    #           weight_profile + rationale).
    composite_score_detail: dict = {}
    # Mejora 2: validation_score breakdown (5 weighted components + issues_penalized).
    validation_score_detail: dict = {}
    # Mejora 6: anti-hallucination per-account check results + impact on output.
    anti_hallucination_result: dict = {}
    # Mejora 7: how analysis_confidence was derived (independent of composite_score).
    analysis_confidence_detail: dict = {}
    # Mejora 9: explicit warnings for null business_context fields and their impact.
    data_quality_warnings: list = []
    # Mejora 4: anomaly detection decision (detected / included / filtered + criteria).
    anomaly_detection_summary: dict = {}

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
