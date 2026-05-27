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


# Previous must be ≥ 5% of current to produce a meaningful percentage
_BASELINE_RATIO_FLOOR: float = 0.05
# Absolute floor in COP MM — below this the previous value is near-zero
_ABSOLUTE_FLOOR_COP_MM: float = 0.5
# Percentage above which we flag a probable reclassification
_EXTREME_VARIATION_PCT: float = 500.0


@dataclass
class ReliabilityResult:
    reliability: VariationReliability
    display_label: str    # shown in UI and LLM prompt instead of raw %
    suppress_pct: bool    # True → do NOT show variation_pct as a real signal
    explanation: str      # audit-trail text


def assess_reliability(variation: AccountVariation) -> ReliabilityResult:
    """
    Assess whether the percentage variation of an account is statistically reliable.
    Call this before anomaly detection and before building the LLM prompt.
    """
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
