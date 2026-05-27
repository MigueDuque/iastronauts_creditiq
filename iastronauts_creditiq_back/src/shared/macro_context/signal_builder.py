"""
signal_builder.py — builds executive-level qualitative macro signals.
Signals are directional, non-deterministic, and suitable for investment committee memos.
"""
from typing import Any


def build_macro_signals(
    macro_context: dict[str, str],
    market_context: dict[str, str],
    sector_context: list[dict[str, str]],
    market_assets: list[dict[str, Any]],
    te_data: dict[str, Any],
) -> list[str]:
    """
    Produces 3–6 executive macro signals from the classified context.
    Conservative language: uses 'may', 'appears', 'suggests', 'could'.
    """
    signals: list[str] = []

    ir_env = macro_context.get("interest_rate_environment", "stable")
    inflation = macro_context.get("inflation_trend", "stable")
    currency = macro_context.get("currency_environment", "stable")
    cycle = macro_context.get("economic_cycle", "unknown")
    liquidity = macro_context.get("market_liquidity", "stable")

    equity_sent = market_context.get("equity_market_sentiment", "neutral")
    fi_env = market_context.get("fixed_income_environment", "neutral")
    volatility = market_context.get("market_volatility", "medium")
    risk_appetite = market_context.get("investor_risk_appetite", "moderate")

    # Interest rate signal
    if ir_env == "declining":
        signals.append(
            "A declining interest-rate environment in Colombia may support fixed-income "
            "portfolio appreciation and reduce financing costs for leveraged issuers."
        )
    elif ir_env == "increasing":
        signals.append(
            "Rising interest rates in Colombia could pressure fixed-income valuations "
            "and increase refinancing risk for highly leveraged portfolios."
        )
    else:
        signals.append(
            "A stable interest-rate environment suggests limited near-term repricing "
            "pressure on fixed-income holdings."
        )

    # Inflation signal
    if inflation == "moderating":
        signals.append(
            "Moderating inflation trends may provide BanRep room to ease monetary policy, "
            "which could be favorable for both equity valuations and bond prices."
        )
    elif inflation == "accelerating":
        signals.append(
            "Accelerating inflation may constrain BanRep's ability to cut rates, "
            "sustaining pressure on real returns across asset classes."
        )

    # Currency signal
    if currency == "volatile":
        signals.append(
            "Elevated COP/USD volatility may introduce translation risk for issuers "
            "with USD-denominated obligations or foreign revenue exposure."
        )
    elif currency == "depreciating":
        signals.append(
            "A depreciating COP environment may benefit commodity exporters such as "
            "Ecopetrol while pressuring importers and foreign-debt servicing costs."
        )

    # Equity / market sentiment
    if equity_sent == "positive":
        signals.append(
            "Positive equity market sentiment in Colombian and regional markets appears "
            "to support unrealized valuation gains in equity positions."
        )
    elif equity_sent == "negative":
        signals.append(
            "Negative equity market sentiment suggests caution on mark-to-market "
            "valuations, particularly for illiquid or small-cap positions."
        )

    # Fixed income environment
    if fi_env == "favorable":
        signals.append(
            "The fixed-income environment appears favorable, suggesting that "
            "TES and corporate bond holdings may benefit from price appreciation."
        )
    elif fi_env == "unfavorable":
        signals.append(
            "An unfavorable fixed-income environment may weigh on bond portfolio "
            "valuations and require reassessment of duration exposure."
        )

    # Sector-specific signals
    sector_map = {s["sector"]: s["trend"] for s in sector_context}
    energy_trend = sector_map.get("energy", "neutral")
    fin_trend = sector_map.get("financials", "neutral")
    infra_trend = sector_map.get("infrastructure", "neutral")

    if energy_trend == "positive":
        signals.append(
            "Positive momentum in the energy sector suggests Colombian energy issuers "
            "may be experiencing favorable pricing and operational conditions."
        )
    if fin_trend == "positive":
        signals.append(
            "Positive sentiment among Colombian financial issuers may reflect "
            "improving credit conditions and net interest margin resilience."
        )
    if infra_trend == "positive":
        signals.append(
            "Infrastructure issuers such as ISA and Grupo Argos appear to be "
            "benefiting from constructive market conditions in the current period."
        )

    # Volatility signal
    if volatility == "high":
        signals.append(
            "Elevated market volatility suggests heightened uncertainty in asset pricing; "
            "conservative valuation assumptions appear warranted."
        )
    elif volatility == "low":
        signals.append(
            "Low market volatility suggests a relatively stable pricing environment "
            "that may support accurate mark-to-market valuations."
        )

    # Economic cycle signal
    if cycle == "recovery":
        signals.append(
            "Colombia's economic cycle appears to be in a recovery phase, which may "
            "support gradual improvement in corporate earnings and credit quality."
        )
    elif cycle == "contraction":
        signals.append(
            "Signs of economic contraction may translate into pressure on corporate "
            "revenues and credit spreads across domestic issuers."
        )

    # Cap to 6 most relevant signals
    return signals[:6]


def build_asset_market_context(assets: list[dict[str, Any]]) -> list[dict[str, str]]:
    """
    Converts raw yfinance asset data into executive market_assets_context entries.
    Only includes non-proxy, non-currency tickers.
    """
    skip_sectors = {"em_proxy", "colombia_etf", "currency", "global_equity"}
    result: list[dict[str, str]] = []

    for asset in assets:
        if asset.get("sector") in skip_sectors:
            continue

        ticker = asset.get("ticker", "")
        company = asset.get("company", "")
        trend = asset.get("trend", "neutral")
        pct = asset.get("pct_change_90d")
        vol_level = asset.get("volatility_level", "medium")

        pct_str = f"{pct:+.1f}%" if pct is not None else "N/A"
        signal = (
            f"{company} showed {trend} market performance over the trailing 90 days "
            f"({pct_str}), with {vol_level} volatility, suggesting "
            + (
                "favorable sector conditions."
                if trend == "positive"
                else "cautious market sentiment."
                if trend == "negative"
                else "broadly stable conditions."
            )
        )
        result.append({
            "ticker": ticker,
            "company": company,
            "trend": trend,
            "market_signal": signal,
        })

    return result
