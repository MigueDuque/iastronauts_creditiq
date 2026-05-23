import uuid
from datetime import datetime
from pydantic import BaseModel, Field

from .base import OutputFormat


class BusinessContext(BaseModel):
    company_name: str | None = None
    industry: str | None = None
    fiscal_year: str | None = None
    reporting_period: str | None = None
    key_events: list[str] = Field(default_factory=list)
    strategic_context: str | None = None
    regulatory_context: str | None = None
    analyst_instructions: list[str] = Field(default_factory=list)
    raw_context: str


class FileToProcess(BaseModel):
    file_name: str
    s3_location: str
    file_type: str  # "pdf" | "excel" | "csv"


class OrchestratorOutput(BaseModel):
    """
    Output del Orchestrator Agent → input del Document Extractor.
    Punto de entrada único del pipeline.
    """
    job_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    tenant_id: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    business_context: BusinessContext
    files_to_process: list[FileToProcess]
    historical_eeff_json: dict = Field(default_factory=dict)
    niif_standards: list[str] = Field(default_factory=list)
    report_language: str = "es"
    output_formats: list[OutputFormat] = Field(default_factory=list)
