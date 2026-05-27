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
    # Legacy states (kept for backward compatibility with RevisorInteligente checks)
    STABLE = "STABLE"
    DECLINING = "DECLINING"
    GROWING = "GROWING"
    CRITICAL = "CRITICAL"
    # Improvement #9: Finance-oriented taxonomy
    LIQUID = "LIQUID"               # Strong cash position, low debt
    LEVERAGED = "LEVERAGED"         # High debt relative to equity / assets
    SPECULATIVE = "SPECULATIVE"     # High risk, volatile results, low coverage
    CASH_STRESSED = "CASH_STRESSED" # Negative or deteriorating operating cash flow
    VALUATION_DRIVEN = "VALUATION_DRIVEN"  # Results depend on unrealized fair-value gains
    CONCENTRATED = "CONCENTRATED"   # Heavy concentration in few accounts / issuers


class OutputFormat(str, Enum):
    MARKDOWN = "markdown"
    PDF = "pdf"
