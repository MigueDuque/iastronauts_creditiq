"""
trend_engine.py

Detects directional trends on financial account variations.
Pure deterministic math — no LLM.
"""

import logging
from enum import Enum

from .ratio_engine import AccountVariation

logger = logging.getLogger("financial_analyzer.trend_engine")

# Percentage thresholds for trend classification
_STABLE_UPPER: float = 5.0       # |pct| < 5 % → stable
_STRONG_UPPER: float = 30.0      # |pct| ≥ 30 % → strong trend


class Trend(str, Enum):
    STRONG_INCREASE = "strong_increase"   # variation_pct ≥ +30 %
    INCREASING = "increasing"             # +5 % ≤ variation_pct < +30 %
    STABLE = "stable"                     # |variation_pct| < 5 %
    DECREASING = "decreasing"             # -30 % < variation_pct ≤ -5 %
    STRONG_DECREASE = "strong_decrease"   # variation_pct ≤ -30 %
    NEW_ACCOUNT = "new_account"           # no prior period; account appeared this period
    ELIMINATED = "eliminated"             # prior value existed; current value is zero


def detect_trend(variation: AccountVariation) -> Trend:
    """
    Classify the directional trend of a single account variation.

    Accounts without a prior period (has_previous_value=False) are treated as
    NEW_ACCOUNT when they carry a non-zero current value.
    Accounts with a prior value that drop to zero are ELIMINATED.
    """
    if not variation.has_previous_value:
        return Trend.NEW_ACCOUNT if variation.current_value != 0.0 else Trend.STABLE

    if variation.previous_value != 0.0 and variation.current_value == 0.0:
        return Trend.ELIMINATED

    pct = variation.variation_pct

    if abs(pct) < _STABLE_UPPER:
        return Trend.STABLE
    if pct >= _STRONG_UPPER:
        return Trend.STRONG_INCREASE
    if pct <= -_STRONG_UPPER:
        return Trend.STRONG_DECREASE
    return Trend.INCREASING if pct > 0 else Trend.DECREASING
