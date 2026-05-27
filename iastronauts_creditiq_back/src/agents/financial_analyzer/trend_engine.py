"""
trend_engine.py  —  Improvement #6: Multi-Period Trend Engine

Detects directional trends and generates human-readable trend labels.
Supports QoQ/YoY framing when multi-period history is available.
Pure deterministic math — no LLM.
"""

import logging
from dataclasses import dataclass
from enum import Enum

from .ratio_engine import AccountVariation

logger = logging.getLogger("financial_analyzer.trend_engine")

# Percentage thresholds
_STABLE_UPPER: float = 5.0       # |pct| < 5 % → stable
_STRONG_UPPER: float = 30.0      # |pct| ≥ 30 % → strong trend
_MODERATE_UPPER: float = 15.0    # |pct| ≥ 15 % → moderate


class Trend(str, Enum):
    STRONG_INCREASE = "strong_increase"   # ≥ +30 %
    INCREASING = "increasing"             # +5 % to +30 %
    STABLE = "stable"                     # |pct| < 5 %
    DECREASING = "decreasing"             # -30 % to -5 %
    STRONG_DECREASE = "strong_decrease"   # ≤ -30 %
    NEW_ACCOUNT = "new_account"           # no prior period; appeared this period
    ELIMINATED = "eliminated"             # had prior value; now zero


@dataclass
class TrendAnalysis:
    """Extended trend result with human-readable label and persistence signals."""
    trend: Trend
    trend_label: str             # human-readable label for UI and LLM prompt
    is_persistent: bool          # True if magnitude qualifies as strong
    magnitude_class: str         # "STRONG" | "MODERATE" | "MILD" | "STABLE" | "N/A"
    period_coverage: str         # describes what period comparison was performed


def detect_trend(variation: AccountVariation) -> Trend:
    """
    Classify the directional trend of a single account variation.
    Kept for backwards compatibility with existing callers.
    """
    return detect_trend_full(variation).trend


def detect_trend_full(variation: AccountVariation) -> TrendAnalysis:
    """
    Full trend analysis with label and persistence data.
    Use this in the new pipeline; detect_trend() is a simple wrapper.
    """
    if not variation.has_previous_value:
        if variation.current_value != 0.0:
            return TrendAnalysis(
                trend=Trend.NEW_ACCOUNT,
                trend_label="Nueva cuenta — sin período anterior para comparar",
                is_persistent=False,
                magnitude_class="N/A",
                period_coverage="Período actual únicamente",
            )
        return TrendAnalysis(
            trend=Trend.STABLE,
            trend_label="Sin movimiento",
            is_persistent=False,
            magnitude_class="STABLE",
            period_coverage="Sin datos previos",
        )

    if variation.previous_value != 0.0 and variation.current_value == 0.0:
        return TrendAnalysis(
            trend=Trend.ELIMINATED,
            trend_label="Cuenta eliminada — balance cero en período actual",
            is_persistent=True,
            magnitude_class="STRONG",
            period_coverage="Comparativo dos períodos",
        )

    pct = variation.variation_pct
    abs_pct = abs(pct)

    if abs_pct < _STABLE_UPPER:
        return TrendAnalysis(
            trend=Trend.STABLE,
            trend_label=f"Estable ({pct:+.1f}%)",
            is_persistent=False,
            magnitude_class="STABLE",
            period_coverage="Comparativo dos períodos",
        )

    if pct >= _STRONG_UPPER:
        label = f"Crecimiento fuerte ({pct:+.1f}%, {variation.absolute_variation:+,.1f} COP MM)"
        magnitude = "STRONG"
    elif pct >= _MODERATE_UPPER:
        label = f"Crecimiento moderado ({pct:+.1f}%, {variation.absolute_variation:+,.1f} COP MM)"
        magnitude = "MODERATE"
    elif pct > 0:
        label = f"Crecimiento leve ({pct:+.1f}%, {variation.absolute_variation:+,.1f} COP MM)"
        magnitude = "MILD"
    elif pct <= -_STRONG_UPPER:
        label = f"Contracción fuerte ({pct:+.1f}%, {variation.absolute_variation:+,.1f} COP MM)"
        magnitude = "STRONG"
    elif pct <= -_MODERATE_UPPER:
        label = f"Contracción moderada ({pct:+.1f}%, {variation.absolute_variation:+,.1f} COP MM)"
        magnitude = "MODERATE"
    else:
        label = f"Contracción leve ({pct:+.1f}%, {variation.absolute_variation:+,.1f} COP MM)"
        magnitude = "MILD"

    if pct >= _STRONG_UPPER:
        trend = Trend.STRONG_INCREASE
    elif pct > _STABLE_UPPER:
        trend = Trend.INCREASING
    elif pct <= -_STRONG_UPPER:
        trend = Trend.STRONG_DECREASE
    else:
        trend = Trend.DECREASING

    return TrendAnalysis(
        trend=trend,
        trend_label=label,
        is_persistent=magnitude == "STRONG",
        magnitude_class=magnitude,
        period_coverage="Comparativo dos períodos",
    )


def generate_multi_period_label(
    current_variation: AccountVariation,
    historical_values: list[float],  # ordered oldest → most recent (excluding current period)
) -> str:
    """
    Generate a QoQ/YoY narrative label when multi-period history is available.
    historical_values: account current_value for each prior period (oldest first).
    Returns a human-readable trend persistence description.
    """
    if not historical_values or len(historical_values) < 2:
        return ""

    # Check if current direction is consistent with the last 2 historical periods
    direction_current = "up" if current_variation.absolute_variation > 0 else "down"

    # Compute direction for each adjacent historical pair
    directions: list[str] = []
    for i in range(1, len(historical_values)):
        diff = historical_values[i] - historical_values[i - 1]
        directions.append("up" if diff > 0 else "down")

    # Add current period direction
    directions.append(direction_current)

    consecutive = 1
    for d in reversed(directions[:-1]):
        if d == direction_current:
            consecutive += 1
        else:
            break

    if consecutive >= 3 and direction_current == "up":
        return f"Tercer período consecutivo de crecimiento."
    if consecutive >= 3 and direction_current == "down":
        return f"Tercer período consecutivo de contracción."
    if consecutive == 2 and direction_current == "up":
        return f"Segundo período consecutivo de crecimiento."
    if consecutive == 2 and direction_current == "down":
        return f"Segundo período consecutivo de contracción."

    return ""
