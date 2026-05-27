"""
anomaly_detector.py

Rule-based detection of financial anomalies, red flags, and structural risks.
All rules are deterministic — no LLM involvement.
Operates at two levels: per-account and balance-sheet structural.
"""

import logging
from dataclasses import dataclass, field

from .ratio_engine import AccountVariation, FinancialRatios, FinancialTotals
from .trend_engine import Trend

logger = logging.getLogger("financial_analyzer.anomaly_detector")

# ── Opening/closing balance exclusion ────────────────────────────────────────
# These accounts carry the prior-period closing balance as their "previous" value,
# so large swings (e.g., +381%) are structurally normal — not anomalies.
_OPENING_CLOSING_KEYWORDS: tuple[str, ...] = (
    "patrimonio neto inicial",
    "patrimonio neto final",
    "saldo inicial",
    "saldo apertura",
    "balance inicial",
    "opening balance",
    "closing balance",
    "efectivo inicial",
    "efectivo final",
    "activos netos iniciales",
)


def _is_opening_closing_balance(account_name: str) -> bool:
    name = account_name.lower()
    return any(kw in name for kw in _OPENING_CLOSING_KEYWORDS)


# ── Thresholds ────────────────────────────────────────────────────────────────

# Per-account — percentage-based
_SUSPICIOUS_GROWTH_PCT: float = 200.0      # +200 % or more is suspicious
_EXTREME_INCREASE_PCT: float = 100.0       # +100 % triggers extreme increase flag
_STRONG_DETERIORATION_PCT: float = -50.0   # -50 % or worse triggers deterioration flag
_SEVERE_DETERIORATION_PCT: float = -75.0   # -75 % → HIGH severity

# Per-account — absolute magnitude vs materiality threshold
_LARGE_NEW_ACCOUNT_MULTIPLIER: int = 5     # current_value > 5× threshold → anomalous new account
_EXTREME_MAGNITUDE_MULTIPLIER: int = 5     # |abs_var| > 5× threshold → extreme magnitude

# Structural
_MIN_CURRENT_RATIO: float = 1.0            # razon_corriente < 1.0 → liquidity risk
_MIN_QUICK_RATIO: float = 0.5             # prueba_acida < 0.5 → severe liquidity risk
_MAX_DEBT_EQUITY: float = 3.0             # deuda_patrimonio > 3.0 → over-leveraged
_MAX_DEBT_RATIO: float = 0.9              # endeudamiento_global > 90 % → extreme leverage
_SEVERE_LOSS_MARGIN_PCT: float = -10.0    # net margin < -10 % → severe losses


@dataclass
class AnomalyResult:
    """Outcome of anomaly detection for a single account."""
    anomaly_detected: bool
    anomaly_types: list[str] = field(default_factory=list)
    severity: str = "NONE"     # "NONE" | "LOW" | "MEDIUM" | "HIGH"
    description: str = ""


# Severity ranking for comparison
_SEVERITY_RANK: dict[str, int] = {"HIGH": 3, "MEDIUM": 2, "LOW": 1, "NONE": 0}


def _top_severity(levels: list[str]) -> str:
    return max(levels, key=lambda s: _SEVERITY_RANK.get(s, 0), default="NONE")


# ── Per-account anomaly detection ─────────────────────────────────────────────

def detect_account_anomaly(
    variation: AccountVariation,
    threshold: float,
    trend: Trend,
) -> AnomalyResult:
    """
    Apply rule-based anomaly detection to a single account variation.

    Rules checked (in order of severity):
    1. ELIMINATED: account had value, now zero.
    2. SUSPICIOUS_GROWTH: variation ≥ +200 %.
    3. EXTREME_INCREASE: variation ≥ +100 %.
    4. STRONG_DETERIORATION: variation ≤ -50 % (HIGH if ≤ -75 %).
    5. EXTREME_MAGNITUDE: |abs_var| ≥ 5× materiality threshold.
    6. LARGE_NEW_ACCOUNT: no prior period but current value ≥ 5× threshold.

    Opening/closing balance accounts are excluded entirely — their swings reflect
    accounting structure (prior-period carry-forward), not real anomalies.
    """
    if _is_opening_closing_balance(variation.account_name):
        return AnomalyResult(anomaly_detected=False)

    if not variation.has_previous_value:
        if abs(variation.current_value) >= threshold * _LARGE_NEW_ACCOUNT_MULTIPLIER:
            return AnomalyResult(
                anomaly_detected=True,
                anomaly_types=["large_new_account"],
                severity="MEDIUM",
                description=(
                    f"Nueva cuenta detectada con valor {variation.current_value:,.1f} COP MM "
                    f"(supera {_LARGE_NEW_ACCOUNT_MULTIPLIER}× el umbral de materialidad "
                    f"de {threshold:.1f} COP MM)."
                ),
            )
        return AnomalyResult(anomaly_detected=False)

    pct = variation.variation_pct
    anomaly_types: list[str] = []
    severities: list[str] = []

    if trend == Trend.ELIMINATED:
        anomaly_types.append("account_eliminated")
        severities.append("HIGH")

    if pct >= _SUSPICIOUS_GROWTH_PCT:
        anomaly_types.append("suspicious_growth")
        severities.append("HIGH")
    elif pct >= _EXTREME_INCREASE_PCT:
        anomaly_types.append("extreme_increase")
        severities.append("MEDIUM")

    if pct <= _SEVERE_DETERIORATION_PCT:
        anomaly_types.append("severe_deterioration")
        severities.append("HIGH")
    elif pct <= _STRONG_DETERIORATION_PCT:
        anomaly_types.append("strong_deterioration")
        severities.append("MEDIUM")

    if abs(variation.absolute_variation) >= threshold * _EXTREME_MAGNITUDE_MULTIPLIER:
        anomaly_types.append("extreme_absolute_magnitude")
        severities.append("HIGH")

    if not anomaly_types:
        return AnomalyResult(anomaly_detected=False)

    top = _top_severity(severities)
    description = (
        f"Variación de {pct:+.1f}% ({variation.absolute_variation:+,.1f} COP MM). "
        f"Anomalías: {', '.join(anomaly_types)}."
    )

    return AnomalyResult(
        anomaly_detected=True,
        anomaly_types=anomaly_types,
        severity=top,
        description=description,
    )


# ── Balance-sheet structural anomaly detection ────────────────────────────────

def detect_structural_anomalies(
    totals: FinancialTotals,
    ratios: FinancialRatios,
) -> list[str]:
    """
    Detect balance-sheet level anomalies: liquidity crises, over-leverage,
    negative equity, and severe losses.
    Returns a list of plain-text issue descriptions; empty when none found.
    """
    issues: list[str] = []

    if ratios.razon_corriente > 0 and ratios.razon_corriente < _MIN_CURRENT_RATIO:
        issues.append(
            f"LIQUIDEZ CRÍTICA — razón corriente {ratios.razon_corriente:.2f}: "
            f"los pasivos corrientes superan los activos corrientes."
        )

    if ratios.prueba_acida > 0 and ratios.prueba_acida < _MIN_QUICK_RATIO:
        issues.append(
            f"LIQUIDEZ INMEDIATA MUY BAJA — prueba ácida {ratios.prueba_acida:.2f}: "
            f"liquidez insuficiente sin depender de inventarios."
        )

    if ratios.deuda_patrimonio > _MAX_DEBT_EQUITY:
        issues.append(
            f"APALANCAMIENTO EXTREMO — deuda/patrimonio {ratios.deuda_patrimonio:.2f}: "
            f"endeudamiento supera {_MAX_DEBT_EQUITY}× el patrimonio."
        )

    if ratios.endeudamiento_global > _MAX_DEBT_RATIO:
        issues.append(
            f"ENDEUDAMIENTO GLOBAL CRÍTICO — {ratios.endeudamiento_global * 100:.1f}% "
            f"de los activos financiados con deuda."
        )

    if totals.total_equity < 0:
        issues.append(
            f"INSOLVENCIA PATRIMONIAL — patrimonio neto negativo "
            f"({totals.total_equity:,.1f} COP MM)."
        )

    if totals.total_revenue > 0:
        margin_pct = totals.net_income / totals.total_revenue * 100
        if margin_pct < _SEVERE_LOSS_MARGIN_PCT:
            issues.append(
                f"PÉRDIDAS OPERATIVAS SEVERAS — margen neto {margin_pct:.1f}%."
            )

    return issues
