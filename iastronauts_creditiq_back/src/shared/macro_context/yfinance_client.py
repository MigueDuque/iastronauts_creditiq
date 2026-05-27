"""
yfinance client — fetches market data for Colombian equities and proxies.
Produces directional, high-level signals. NOT a trading analytics engine.
"""
import logging
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

# Colombian issuers and relevant market proxies
_COLOMBIA_TICKERS: list[dict[str, str]] = [
    {"ticker": "EC",    "company": "Ecopetrol",         "sector": "energy"},
    {"ticker": "CIB",   "company": "Bancolombia",        "sector": "financials"},
    # GRUPOSURA.CL and GRUPOARGOS.CL are Santiago DRVs — yfinance coverage is sparse;
    # included but may return empty (handled gracefully).
    {"ticker": "GRUPOSURA.CL", "company": "Grupo Sura", "sector": "financials"},
    {"ticker": "GRUPOARGOS.CL","company": "Grupo Argos", "sector": "infrastructure"},
    # ISA was acquired by Ecopetrol (2022) and delisted — removed.
    # EEB (Empresa de Energía de Bogotá) is a live BVC infrastructure proxy.
    {"ticker": "EEB.CL","company": "Empresa de Energía de Bogotá", "sector": "infrastructure"},
    {"ticker": "EWW",   "company": "iShares MSCI Mexico ETF (EM proxy)", "sector": "em_proxy"},
    {"ticker": "GXG",   "company": "Global X MSCI Colombia ETF", "sector": "colombia_etf"},
    {"ticker": "COP=X", "company": "USD/COP FX rate",   "sector": "currency"},
    {"ticker": "^GSPC", "company": "S&P 500 (global risk proxy)", "sector": "global_equity"},
]

_LOOKBACK_DAYS = 90


def _safe_pct_change(history: Any) -> float | None:
    """Return percentage change over the period, or None."""
    try:
        closes = history["Close"].dropna()
        if len(closes) < 2:
            return None
        start = float(closes.iloc[0])
        end = float(closes.iloc[-1])
        if start == 0:
            return None
        return round((end - start) / start * 100, 2)
    except Exception:
        return None


def _safe_volatility(history: Any) -> float | None:
    """Annualised daily return std dev as a simple volatility proxy."""
    try:
        closes = history["Close"].dropna()
        if len(closes) < 5:
            return None
        returns = closes.pct_change().dropna()
        return round(float(returns.std()) * (252 ** 0.5) * 100, 2)
    except Exception:
        return None


def _trend_from_pct(pct: float | None) -> str:
    if pct is None:
        return "neutral"
    if pct > 3:
        return "positive"
    if pct < -3:
        return "negative"
    return "neutral"


def _volatility_level(vol: float | None) -> str:
    if vol is None:
        return "unknown"
    if vol < 15:
        return "low"
    if vol < 30:
        return "medium"
    return "high"


def fetch_market_data() -> dict[str, Any]:
    """
    Fetch market data for Colombian equities and proxies.
    Returns:
      {
        "assets": [{ ticker, company, sector, pct_change_90d, volatility_annualised,
                     trend, volatility_level, last_price }],
        "colombia_etf_trend": str,
        "usdcop_trend": str,
        "global_equity_trend": str,
        "available": bool,
        "source": "yfinance"
      }
    """
    try:
        import yfinance as yf  # type: ignore
    except ImportError:
        logger.warning("yfinance not installed — market data client disabled")
        return {"assets": [], "available": False, "source": "yfinance"}

    end_date = datetime.now()
    start_date = end_date - timedelta(days=_LOOKBACK_DAYS)

    assets: list[dict[str, Any]] = []
    colombia_etf_trend = "neutral"
    usdcop_trend = "stable"
    global_equity_trend = "neutral"

    for meta in _COLOMBIA_TICKERS:
        try:
            ticker_obj = yf.Ticker(meta["ticker"])
            hist = ticker_obj.history(
                start=start_date.strftime("%Y-%m-%d"),
                end=end_date.strftime("%Y-%m-%d"),
            )
            if hist.empty:
                continue

            pct = _safe_pct_change(hist)
            vol = _safe_volatility(hist)
            trend = _trend_from_pct(pct)
            vol_level = _volatility_level(vol)

            last_price: float | None = None
            try:
                last_price = round(float(hist["Close"].iloc[-1]), 4)
            except Exception:
                pass

            asset: dict[str, Any] = {
                "ticker": meta["ticker"],
                "company": meta["company"],
                "sector": meta["sector"],
                "pct_change_90d": pct,
                "volatility_annualised": vol,
                "trend": trend,
                "volatility_level": vol_level,
                "last_price": last_price,
            }
            assets.append(asset)

            # Extract key signals for summary fields
            if meta["sector"] == "colombia_etf":
                colombia_etf_trend = trend
            elif meta["sector"] == "currency":
                # COP/USD: positive pct = USD strengthening = COP depreciating
                if pct is not None:
                    usdcop_trend = "depreciating" if pct > 2 else "appreciating" if pct < -2 else "stable"
            elif meta["ticker"] == "^GSPC":
                global_equity_trend = trend

        except Exception as exc:
            logger.warning("yfinance error for %s: %s", meta["ticker"], exc)

    logger.info("yfinance fetched %d assets", len(assets))
    return {
        "assets": assets,
        "colombia_etf_trend": colombia_etf_trend,
        "usdcop_trend": usdcop_trend,
        "global_equity_trend": global_equity_trend,
        "available": len(assets) > 0,
        "source": "yfinance",
    }
