from pydantic import BaseModel, Field

from .base import OutputFormat
from .orchestrator import BusinessContext


class ExtractedAccount(BaseModel):
    account_id: str
    raw_account_name: str
    normalized_account_name: str
    category: str        # "assets" | "liabilities" | "equity" | "revenue" | "expense"
    current_value: float
    previous_value: float | None = None
    currency: str
    confidence_score: float = Field(ge=0.0, le=1.0)
    source_file: str


class NiifValidationFlag(BaseModel):
    """A single NIIF structural compliance finding from the DocumentExtractor validator."""
    rule_id: str                    # e.g. "NIC1-005"
    standard: str                   # e.g. "NIC 1"
    severity: str                   # "ERROR" | "WARNING" | "INFO"
    message: str                    # human-readable explanation in Spanish
    affected_accounts: list[str]    # account_ids involved, empty when rule is global


class NiifValidationResult(BaseModel):
    """
    NIIF structural compliance result produced by DocumentExtractor.
    Consumed by FinancialAnalyzer to contextualize anomalies and by
    ReportGenerator to include in disclosure notes.
    """
    is_niif_compliant: bool          # False if any ERROR flag exists or score < 60
    compliance_score: int            # 0–100; starts at 100, deducted per flag severity
    flags: list[NiifValidationFlag]
    missing_categories: list[str]    # categories with zero accounts
    accounting_equation_balanced: bool
    equation_gap_pct: float          # |assets − (liabilities + equity)| / assets


class ExtractorOutput(BaseModel):
    """
    Output del Agente 1 (Document Extractor) → input del Financial Analyzer.
    Propaga el contexto global del orquestador.
    """
    # Contexto global propagado desde OrchestratorOutput
    job_id: str
    tenant_id: str
    business_context: BusinessContext
    niif_standards: list[str]
    report_language: str
    output_formats: list[OutputFormat]

    # Output del extractor
    company_name: str
    statement_type: str = "balance_sheet"  # "balance_sheet" | "income_statement" | "cash_flow"
    currency: str
    periods: list[str]
    accounts: list[ExtractedAccount]
    extraction_confidence: float = Field(ge=0.0, le=1.0)
    extraction_warnings: list[str] = Field(default_factory=list)
    rendicion_text_s3_key: str | None = None

    # NIIF structural compliance — populated after extraction, before Agent 2
    niif_validation: NiifValidationResult | None = None

    # Fund metadata — populated when the EEFF is a Fondo de Inversión Colectivo (FIC/CIV)
    fund_metadata: dict | None = None   # keys: fund_type, creation_date, administrator,
                                        # custodian, risk_profile, benchmark,
                                        # investment_policy_summary