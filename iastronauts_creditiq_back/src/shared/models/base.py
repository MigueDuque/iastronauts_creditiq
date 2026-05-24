from enum import Enum


class MaterialityLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class FinancialHealth(str, Enum):
    STABLE = "STABLE"
    DECLINING = "DECLINING"
    GROWING = "GROWING"
    CRITICAL = "CRITICAL"


class OutputFormat(str, Enum):
    MARKDOWN = "markdown"
    PDF = "pdf"
