"""
variation_reliability.py  —  Improvement #1: Variation Reliability Engine

Determines whether a percentage variation is statistically meaningful for financial
interpretation. Unreliable variations (near-zero baseline, new accounts, extreme
reclassifications) must NOT be surfaced as real anomalies or causes.
"""

from dataclasses import dataclass
from enum import Enum

from .ratio_engine import AccountVariation


class VariationReliability(str, Enum):
    RELIABLE = "RELIABLE"
    NEW_ACCOUNT = "NEW_ACCOUNT"                    # no prior period — no baseline exists
    INSUFFICIENT_BASELINE = "INSUFFICIENT_BASELINE"  # prior ≈ 0 → % is meaningless
    EXTREME_VARIATION = "EXTREME_VARIATION"          # >500% likely a restatement/reclassification
    PERIOD_LENGTH_MISMATCH = "PERIOD_LENGTH_MISMATCH"  # comparing different durations (e.g. Q1 vs full-year)


# Previous must be ≥ 5% of current to produce a meaningful percentage
_BASELINE_RATIO_FLOOR: float = 0.05
# Absolute floor in COP MM — below this the previous value is near-zero
_ABSOLUTE_FLOOR_COP_MM: float = 0.5
# Percentage above which we flag a probable reclassification
_EXTREME_VARIATION_PCT: float = 500.0

# Statement types whose values are cumulative flows — period length matters for comparison
_FLOW_STATEMENT_TYPES: frozenset[str] = frozenset({"income_statement", "cash_flow"})
_FLOW_CATEGORIES: frozenset[str] = frozenset({"revenue", "expense"})


@dataclass
class ReliabilityResult:
    reliability: VariationReliability
    display_label: str    # shown in UI and LLM prompt instead of raw %
    suppress_pct: bool    # True → do NOT show variation_pct as a real signal
    explanation: str      # audit-trail text


def assess_reliability(
    variation: AccountVariation,
    periods_homogeneous: bool = True,
    current_period_months: int = 12,
    comparative_period_months: int = 12,
    annualization_factor: float | None = None,
) -> ReliabilityResult:
    """
    Assess whether the percentage variation of an account is statistically reliable.
    Call this before anomaly detection and before building the LLM prompt.

    periods_homogeneous: False when the current and comparative columns cover different
        durations (e.g. Q1 2025 = 3 months vs FY 2024 = 12 months). Only meaningful
        for flow accounts (income statement, cash flow); balance-sheet items are always
        point-in-time snapshots, so callers should pass periods_homogeneous=True for them.
    """
    # Period-length mismatch: the raw % variation is structurally misleading for flow accounts.
    # Check this FIRST — it overrides other reliability signals for these accounts.
    is_flow = (
        getattr(variation, "statement_type", None) in _FLOW_STATEMENT_TYPES
        or variation.category in _FLOW_CATEGORIES
    )
    if is_flow and not periods_homogeneous and variation.has_previous_value:
        annualized_note = ""
        if annualization_factor and annualization_factor > 1:
            annualized_approx = variation.current_value * annualization_factor
            annualized_note = (
                f" Equivalente anualizado estimado: {annualized_approx:,.1f} COP MM "
                f"(×{annualization_factor:.2f})."
            )
        return ReliabilityResult(
            reliability=VariationReliability.PERIOD_LENGTH_MISMATCH,
            display_label=(
                f"Períodos no homogéneos ({current_period_months}m vs {comparative_period_months}m) "
                f"— variación % no interpretable directamente"
            ),
            suppress_pct=True,
            explanation=(
                f"'{variation.account_name}' es una cuenta de flujo comparada entre períodos de "
                f"distinta duración: {current_period_months} meses actuales vs "
                f"{comparative_period_months} meses comparativos. "
                f"Una variación de {variation.variation_pct:+.0f}% refleja la diferencia de "
                f"duración, no necesariamente un cambio real de actividad.{annualized_note}"
            ),
        )

    if not variation.has_previous_value:
        return ReliabilityResult(
            reliability=VariationReliability.NEW_ACCOUNT,
            display_label="Nueva cuenta / sin período anterior",
            suppress_pct=True,
            explanation=(
                f"No existe valor del período anterior para '{variation.account_name}'. "
                f"Se reporta como cuenta nueva con valor actual de "
                f"{variation.current_value:,.1f} COP MM."
            ),
        )

    prev = abs(variation.previous_value)
    curr = abs(variation.current_value)
    pct = abs(variation.variation_pct)

    # Near-zero baseline renders the percentage meaningless
    insufficient = (prev < _ABSOLUTE_FLOOR_COP_MM) or (
        curr > 0 and prev / curr < _BASELINE_RATIO_FLOOR
    )
    if insufficient:
        return ReliabilityResult(
            reliability=VariationReliability.INSUFFICIENT_BASELINE,
            display_label="Base insuficiente — % no representativo",
            suppress_pct=True,
            explanation=(
                f"Valor anterior ({variation.previous_value:.2f} COP MM) es demasiado pequeño "
                f"respecto al actual ({variation.current_value:.2f} COP MM). "
                f"La variación de {variation.variation_pct:+.0f}% no es económicamente representativa."
            ),
        )

    if pct >= _EXTREME_VARIATION_PCT:
        return ReliabilityResult(
            reliability=VariationReliability.EXTREME_VARIATION,
            display_label="Variación extrema — posible reclasificación contable",
            suppress_pct=True,
            explanation=(
                f"Variación de {variation.variation_pct:+.0f}% supera el umbral de "
                f"{_EXTREME_VARIATION_PCT:.0f}%. Probable reclasificación contable, "
                f"reexpresión o cambio de taxonomía — no un cambio económico real."
            ),
        )

    return ReliabilityResult(
        reliability=VariationReliability.RELIABLE,
        display_label="Confiable",
        suppress_pct=False,
        explanation="",
    )
