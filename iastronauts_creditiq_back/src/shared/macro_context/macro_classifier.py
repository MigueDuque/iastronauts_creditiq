"""
macro_classifier.py — converts raw numeric indicators into qualitative macro states.
Pure functions; no I/O.
"""
from typing import Any


def classify_interest_rate_environment(
    current_rate: float | None,
    prev_rate: float | None = None,
) -> str:
    """
    Returns: 'increasing' | 'declining' | 'stable' | 'unknown'
    Uses directional change if both values available; otherwise uses absolute level.
    Colombia neutral rate circa 5–8%.
    """
    if current_rate is None:
        return "unknown"
    if prev_rate is not None:
        delta = current_rate - prev_rate
        if delta > 0.25:
            return "increasing"
        if delta < -0.25:
            return "declining"
        return "stable"
    # No historical baseline — use absolute level heuristic for Colombia
    if current_rate > 8.0:
        return "increasing"
    if current_rate < 5.0:
        return "declining"
    return "stable"


def classify_inflation_trend(inflation_rate: float | None) -> str:
    """
    Returns: 'accelerating' | 'moderating' | 'stable' | 'deflating' | 'unknown'
    BanRep target: 3%. Above 6% is elevated for Colombia.
    """
    if inflation_rate is None:
        return "unknown"
    if inflation_rate > 8.0:
        return "accelerating"
    if inflation_rate > 4.0:
        return "moderating"
    if inflation_rate >= 1.0:
        return "stable"
    if inflation_rate < 0:
        return "deflating"
    return "stable"


def classify_currency_environment(usdcop_trend: str) -> str:
    """
    Maps yfinance USD/COP trend to a macro currency environment label.
    """
    mapping = {
        "depreciating": "volatile",
        "appreciating": "appreciating",
        "stable": "stable",
    }
    return mapping.get(usdcop_trend, "volatile")


def classify_economic_cycle(gdp_growth: float | None) -> str:
    """
    Returns: 'expansion' | 'peak' | 'contraction' | 'recovery' | 'unknown'
    """
    if gdp_growth is None:
        return "unknown"
    if gdp_growth >= 4.0:
        return "expansion"
    if gdp_growth >= 2.0:
        return "recovery"
    if gdp_growth >= 0.0:
        return "peak"
    return "contraction"


def classify_market_liquidity(
    interest_rate: float | None,
    gdp_growth: float | None,
) -> str:
    """
    Heuristic: high rates + low growth → tight; low rates + growth → ample.
    """
    if interest_rate is None:
        return "stable"
    if interest_rate > 9.0:
        return "tight"
    if interest_rate < 5.0:
        return "ample"
    return "stable"


def classify_equity_sentiment(
    colombia_etf_trend: str,
    global_equity_trend: str,
) -> str:
    """Combines local ETF and global equity signals."""
    scores = {"positive": 1, "neutral": 0, "negative": -1}
    total = scores.get(colombia_etf_trend, 0) + scores.get(global_equity_trend, 0)
    if total >= 1:
        return "positive"
    if total <= -1:
        return "negative"
    return "neutral"


def classify_fixed_income_environment(
    interest_rate_env: str,
    inflation_trend: str,
) -> str:
    """
    Declining rates + moderating inflation → favorable for fixed income.
    Increasing rates + accelerating inflation → unfavorable.
    """
    if interest_rate_env == "declining" and inflation_trend in ("moderating", "stable"):
        return "favorable"
    if interest_rate_env == "increasing" or inflation_trend == "accelerating":
        return "unfavorable"
    return "neutral"


def classify_market_volatility(assets: list[dict[str, Any]]) -> str:
    """Aggregate volatility across fetched assets."""
    levels = [a.get("volatility_level") for a in assets]
    high_count = levels.count("high")
    low_count = levels.count("low")
    total = len([l for l in levels if l not in (None, "unknown")])
    if total == 0:
        return "medium"
    if high_count / max(total, 1) > 0.4:
        return "high"
    if low_count / max(total, 1) > 0.5:
        return "low"
    return "medium"


def classify_risk_appetite(
    equity_sentiment: str,
    volatility: str,
    interest_rate_env: str,
) -> str:
    """Risk appetite: high when positive sentiment + low vol + declining rates."""
    score = 0
    score += {"positive": 1, "neutral": 0, "negative": -1}.get(equity_sentiment, 0)
    score += {"low": 1, "medium": 0, "high": -1}.get(volatility, 0)
    score += {"declining": 1, "stable": 0, "increasing": -1}.get(interest_rate_env, 0)
    if score >= 2:
        return "high"
    if score <= -2:
        return "low"
    return "moderate"


def classify_sector_trends(assets: list[dict[str, Any]]) -> list[dict[str, str]]:
    """
    Aggregate asset trends by sector into a sector_context list.
    Always includes financials, energy, infrastructure, consumer (defaults neutral).
    """
    sector_scores: dict[str, list[int]] = {}
    score_map = {"positive": 1, "neutral": 0, "negative": -1}

    for asset in assets:
        sector = asset.get("sector", "")
        trend = asset.get("trend", "neutral")
        if sector in ("em_proxy", "colombia_etf", "currency", "global_equity"):
            continue
        sector_scores.setdefault(sector, []).append(score_map.get(trend, 0))

    mandatory = ["financials", "energy", "infrastructure", "consumer"]
    for s in mandatory:
        sector_scores.setdefault(s, [])

    result: list[dict[str, str]] = []
    trend_label = {1: "positive", 0: "neutral", -1: "negative"}
    for sector, scores in sorted(sector_scores.items()):
        if scores:
            avg = sum(scores) / len(scores)
            label = "positive" if avg > 0.2 else "negative" if avg < -0.2 else "neutral"
        else:
            label = "neutral"
        result.append({"sector": sector, "trend": label})

    return result
