from pydantic import BaseModel, Field

from .base import OutputFormat
from .orchestrator import BusinessContext


class ExtractedAccount(BaseModel):
    account_id: str
    raw_account_name: str
    normalized_account_name: str
    category: str        # "assets" | "liabilities" | "equity" | "revenue" | "expense"
    subcategory: str
    current_value: float
    previous_value: float | None = None
    currency: str
    confidence_score: float = Field(ge=0.0, le=1.0)
    source_file: str


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
    statement_type: str  # "balance_sheet" | "income_statement" | "cash_flow"
    currency: str
    periods: list[str]
    accounts: list[ExtractedAccount]
    extraction_confidence: float = Field(ge=0.0, le=1.0)
    extraction_warnings: list[str] = Field(default_factory=list)