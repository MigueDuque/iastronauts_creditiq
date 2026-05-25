from enum import Enum

from pydantic import BaseModel, Field

from .report import FinalReportOutput


class ValidationSeverity(str, Enum):
    ERROR = "ERROR"       # Pipeline fails — report structurally invalid
    WARNING = "WARNING"   # validation_score degraded — needs attention
    INFO = "INFO"         # Informational — no score impact


class ValidationCategory(str, Enum):
    STRUCTURAL = "STRUCTURAL"               # Cat 1: JSON & field presence
    MATHEMATICAL = "MATHEMATICAL"           # Cat 2: Arithmetic correctness
    CROSS_REFERENCE = "CROSS_REFERENCE"     # Cat 3: ID relationships
    BUSINESS_LOGIC = "BUSINESS_LOGIC"       # Cat 4: Financial coherence
    CONSISTENCY = "CONSISTENCY"             # Cat 5: URL patterns & coverage
    NARRATIVE = "NARRATIVE"                 # Cat 6: LLM-assessed quality


class ValidationFlag(BaseModel):
    check_id: str                       # e.g. "2.1", "3.3"
    category: ValidationCategory
    severity: ValidationSeverity
    message: str
    affected_field: str | None = None   # e.g. "analysis_results[1].variation_pct"
    expected_value: str | None = None
    actual_value: str | None = None


class RevisorOutput(FinalReportOutput):
    """
    Output del Agente 6 (Revisor Inteligente).
    FinalReportOutput enriquecido con resultados de validación post-generación.
    validation_score puede ser menor al original si se encontraron flags.
    """
    validation_flags: list[ValidationFlag] = Field(default_factory=list)
    errors_count: int = 0
    warnings_count: int = 0
    validation_passed: bool = True
