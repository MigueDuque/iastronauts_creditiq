"""
TradingEconomics client — fetches Colombian macroeconomic indicators.
Returns structured dicts; never raises on partial failures.
"""
import os
import logging
from typing import Any

logger = logging.getLogger(__name__)

_SAFE_DEFAULTS: dict[str, Any] = {
    "interest_rate": None,
    "inflation_rate": None,
    "gdp_growth_rate": None,
    "unemployment_rate": None,
    "current_account_gdp": None,
    "government_debt_gdp": None,
    "exchange_rate_usdcop": None,
    "source": "tradingeconomics",
    "available": False,
}


def _init_te() -> bool:
    """Initialise the TE SDK with the API key. Returns True if ready."""
    api_key = os.getenv("TRADINGECONOMICS_API_KEY", "")
    if not api_key:
        logger.warning("TRADINGECONOMICS_API_KEY not set — TE client disabled")
        return False
    try:
        import tradingeconomics as te  # type: ignore
        te.login(api_key)
        return True
    except Exception as exc:
        logger.warning("TradingEconomics init failed: %s", exc)
        return False


def fetch_colombia_indicators() -> dict[str, Any]:
    """
    Fetch key macroeconomic indicators for Colombia.
    Returns a flat dict with the latest available values.
    Falls back to _SAFE_DEFAULTS on any error.
    """
    if not _init_te():
        return _SAFE_DEFAULTS.copy()

    try:
        import tradingeconomics as te  # type: ignore

        result: dict[str, Any] = {
            "source": "tradingeconomics",
            "available": True,
        }

        raw = te.getIndicatorData(country="Colombia", output_type="df")
        if raw is None or (hasattr(raw, "empty") and raw.empty):
            logger.warning("TradingEconomics returned empty data for Colombia")
            return {**_SAFE_DEFAULTS, "available": True}

        # Normalise to a lowercase-name keyed dict
        # "Category" = indicator name; "LatestValue" = numeric value in TE SDK v4.x
        rows: dict[str, Any] = {}
        for _, row in raw.iterrows():
            indicator = str(row.get("Category", "")).lower().strip()
            value = row.get("LatestValue")  # numeric; "LastUpdate" is a date string — not a fallback
            rows[indicator] = value

        result["interest_rate"] = _safe_float(rows.get("interest rate"))
        result["inflation_rate"] = _safe_float(rows.get("inflation rate"))
        result["gdp_growth_rate"] = _safe_float(rows.get("gdp growth rate"))
        result["unemployment_rate"] = _safe_float(rows.get("unemployment rate"))
        result["current_account_gdp"] = _safe_float(rows.get("current account to gdp"))
        result["government_debt_gdp"] = _safe_float(rows.get("government debt to gdp"))

        # Exchange rate USDCOP via TE markets if available
        try:
            fx = te.getMarketsData(marketsField="currency", output_type="df")
            if fx is not None and not fx.empty:
                cop_row = fx[fx["Symbol"].str.contains("COP", na=False)]
                if not cop_row.empty:
                    result["exchange_rate_usdcop"] = _safe_float(
                        cop_row.iloc[0].get("Last")
                    )
        except Exception:
            pass

        logger.info("TradingEconomics fetch OK: %s", list(result.keys()))
        return result

    except Exception as exc:
        logger.error("TradingEconomics fetch error: %s", exc, exc_info=True)
        return _SAFE_DEFAULTS.copy()


def _safe_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
