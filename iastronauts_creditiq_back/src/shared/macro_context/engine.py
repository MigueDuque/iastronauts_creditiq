"""
MacroContextEngine — orchestrates all data sources into structured macro context.

Responsibilities:
  1. Fetch raw data from TradingEconomics, GNews, yfinance
  2. Classify qualitative states via macro_classifier
  3. Build executive signals via signal_builder
  4. Optionally enrich via LLM (news scoring + narrative polish)
  5. Return strict MacroContextOutput dict

NOT responsible for: financial analysis, risk scoring, hallucination validation.
"""
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from .tradingeconomics_client import fetch_colombia_indicators
from .gnews_client import fetch_colombia_news
from .yfinance_client import fetch_market_data
from .macro_classifier import (
    classify_interest_rate_environment,
    classify_inflation_trend,
    classify_currency_environment,
    classify_economic_cycle,
    classify_market_liquidity,
    classify_equity_sentiment,
    classify_fixed_income_environment,
    classify_market_volatility,
    classify_risk_appetite,
    classify_sector_trends,
)
from .signal_builder import build_macro_signals, build_asset_market_context

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).parent / "prompts" / "macro_context_prompt.txt"


def _load_prompt() -> str:
    try:
        return _PROMPT_PATH.read_text(encoding="utf-8")
    except Exception:
        return "You are a macro context analyst. Return ONLY valid JSON."


def _score_news_with_llm(
    articles: list[dict[str, Any]],
    llm_provider: Any,
    analysis_period: str,
) -> list[dict[str, Any]]:
    """
    Use LLM to score article relevance and extract top news_context entries.
    Falls back to heuristic scoring if LLM unavailable.
    """
    if not articles:
        return []

    if llm_provider is None:
        return _heuristic_news_score(articles)

    try:
        prompt = _load_prompt()
        articles_text = json.dumps(
            [{"headline": a["headline"], "summary": a["summary"]} for a in articles[:20]],
            ensure_ascii=False,
            indent=2,
        )
        user_content = (
            f"Analysis period: {analysis_period}\n\n"
            f"News articles to evaluate:\n{articles_text}\n\n"
            "Select the 5 most relevant articles for a Colombian investment portfolio context. "
            "For each, assign a relevance score 0.0–1.0 (higher = more relevant to macro/market). "
            'Return JSON array: [{"headline": "...", "summary": "...", "relevance": 0.0}]'
        )
        result = llm_provider.generate_json(
            system_prompt=(
                "You are a macro analyst selecting the most relevant economic and financial "
                "news for a Colombian portfolio investment committee. "
                "Return ONLY a JSON array, no markdown."
            ),
            user_prompt=user_content,
            temperature=0.1,
        )
        if isinstance(result, list):
            return result[:5]
        if isinstance(result, dict) and "articles" in result:
            return result["articles"][:5]
        return _heuristic_news_score(articles)
    except Exception as exc:
        logger.warning("LLM news scoring failed: %s — using heuristic fallback", exc)
        return _heuristic_news_score(articles)


_HIGH_RELEVANCE_KEYWORDS = [
    "banrep", "banco de la república", "tasa de interés", "inflación",
    "colcap", "tes", "ecopetrol", "bancolombia", "isa ", "grupo sura",
    "grupo argos", "renta fija", "bolsa", "colombia economía", "peso colombiano",
    "ipc", "pib", "desempleo",
]


def _heuristic_news_score(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keyword-based relevance scoring fallback."""
    scored: list[dict[str, Any]] = []
    for art in articles:
        text = (art.get("headline", "") + " " + art.get("summary", "")).lower()
        hits = sum(1 for kw in _HIGH_RELEVANCE_KEYWORDS if kw in text)
        relevance = min(0.5 + hits * 0.1, 1.0)
        scored.append({
            "headline": art.get("headline", ""),
            "summary": art.get("summary", ""),
            "relevance": round(relevance, 2),
        })
    scored.sort(key=lambda x: x["relevance"], reverse=True)
    return scored[:5]


def generate_macro_context(
    analysis_period: str | None = None,
    country: str = "Colombia",
    llm_provider: Any = None,
) -> dict[str, Any]:
    """
    Main entry point. Returns a MacroContextOutput dict.

    Args:
        analysis_period: e.g. "2025-H1" or "2024-Q4". Auto-derived if None.
        country: target country (currently only Colombia is supported).
        llm_provider: optional LLMProvider instance for news scoring.
                      Pass None to run in fully deterministic mode.

    Returns:
        Strict MacroContextOutput dict suitable for JSON serialisation.
    """
    if analysis_period is None:
        now = datetime.now()
        half = "H1" if now.month <= 6 else "H2"
        analysis_period = f"{now.year}-{half}"

    logger.info("MacroContextEngine start | period=%s country=%s", analysis_period, country)

    # ── 1. Fetch raw data ────────────────────────────────────────────────────
    te_data = fetch_colombia_indicators()
    news_data = fetch_colombia_news()
    market_data = fetch_market_data()

    # ── 2. Classify qualitative states ──────────────────────────────────────
    ir_env = classify_interest_rate_environment(te_data.get("interest_rate"))
    inflation_trend = classify_inflation_trend(te_data.get("inflation_rate"))
    currency_env = classify_currency_environment(market_data.get("usdcop_trend", "stable"))
    economic_cycle = classify_economic_cycle(te_data.get("gdp_growth_rate"))
    market_liquidity = classify_market_liquidity(
        te_data.get("interest_rate"), te_data.get("gdp_growth_rate")
    )

    assets = market_data.get("assets", [])
    equity_sentiment = classify_equity_sentiment(
        market_data.get("colombia_etf_trend", "neutral"),
        market_data.get("global_equity_trend", "neutral"),
    )
    fi_env = classify_fixed_income_environment(ir_env, inflation_trend)
    volatility = classify_market_volatility(assets)
    risk_appetite = classify_risk_appetite(equity_sentiment, volatility, ir_env)
    sector_context = classify_sector_trends(assets)

    macro_context: dict[str, str] = {
        "interest_rate_environment": ir_env,
        "inflation_trend": inflation_trend,
        "market_liquidity": market_liquidity,
        "currency_environment": currency_env,
        "economic_cycle": economic_cycle,
    }
    market_ctx: dict[str, str] = {
        "equity_market_sentiment": equity_sentiment,
        "fixed_income_environment": fi_env,
        "market_volatility": volatility,
        "investor_risk_appetite": risk_appetite,
    }

    # ── 3. Build signals ─────────────────────────────────────────────────────
    macro_signals = build_macro_signals(
        macro_context, market_ctx, sector_context, assets, te_data
    )
    market_assets_context = build_asset_market_context(assets)

    # ── 4. Score and select news ─────────────────────────────────────────────
    news_context = _score_news_with_llm(
        news_data.get("articles", []), llm_provider, analysis_period
    )

    # ── 5. Assemble output ───────────────────────────────────────────────────
    output: dict[str, Any] = {
        "country": country,
        "analysis_period": analysis_period,
        "macro_context": macro_context,
        "market_context": market_ctx,
        "sector_context": sector_context,
        "news_context": news_context,
        "market_assets_context": market_assets_context,
        "macro_signals": macro_signals,
        "_data_availability": {
            "tradingeconomics": te_data.get("available", False),
            "gnews": news_data.get("available", False),
            "yfinance": market_data.get("available", False),
        },
    }

    logger.info(
        "MacroContextEngine complete | signals=%d assets=%d news=%d",
        len(macro_signals),
        len(market_assets_context),
        len(news_context),
    )
    return output
