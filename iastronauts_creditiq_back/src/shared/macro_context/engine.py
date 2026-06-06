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


# ── Sub-score weights (sum to 1.0) ──────────────────────────────────────────
_SCORE_WEIGHTS = {
    "financial_relevance": 0.20,
    "temporal_proximity":  0.15,
    "macro_impact":        0.25,
    "sector_relation":     0.20,
    "asset_relation":      0.20,
}
# Composite ≥ this threshold to be included; hard cap at 5 items.
# 0.25 is calibrated so relevant macro news (banrep, inflation) passes even when
# portfolio context is absent (sector/asset weights score 0 → max achievable ≈ 0.60).
_NEWS_THRESHOLD: float = 0.25
_NEWS_CAP: int = 5

# Heuristic keyword sets per sub-score dimension
_FINANCIAL_KEYWORDS = [
    "banco", "tasa", "interés", "bolsa", "mercado", "acción", "acciones",
    "inversión", "rendimiento", "portafolio", "crédito", "deuda", "deuda pública",
    "emisión", "bono", "renta fija", "dividendo", "utilidad", "cotización",
    "financiero", "financiera", "capital", "fondo", "liquidez",
    # macro-financial terms treated as financial relevance signals
    "inflación", "ipc", "pib", "desempleo", "precio", "tasas",
]
_MACRO_KEYWORDS = [
    "banrep", "banco de la república", "tasa de interés", "tasa de política",
    "inflación", "ipc", "pib", "desempleo", "balanza comercial",
    "colcap", "dólar", "usd", "cop", "peso colombiano", "tes",
    "coltes", "déficit fiscal", "superfinanciera", "ocde",
]
_HIGH_RELEVANCE_KEYWORDS = _MACRO_KEYWORDS + [
    "ecopetrol", "bancolombia", "isa ", "grupo sura", "grupo argos",
    "renta fija", "colombia economía",
]


def _temporal_score(article: dict[str, Any], period_from: str | None, period_to: str | None) -> float:
    """Score 1.0 when article date falls inside the analysis window, 0.5 when unknown."""
    pub = article.get("published_at") or ""
    if not pub or not period_from or not period_to:
        return 0.5
    try:
        pub_dt = datetime.fromisoformat(pub.replace("Z", "+00:00"))
        from_dt = datetime.fromisoformat(period_from.replace("Z", "+00:00"))
        to_dt = datetime.fromisoformat(period_to.replace("Z", "+00:00"))
        if from_dt <= pub_dt <= to_dt:
            return 1.0
        # Partial credit for articles within 90 days of the window
        delta = min(abs((pub_dt - from_dt).days), abs((pub_dt - to_dt).days))
        return max(0.0, 1.0 - delta / 90.0)
    except Exception:
        return 0.5


def _heuristic_sub_scores(
    article: dict[str, Any],
    portfolio_context: dict[str, Any] | None,
    period_from: str | None,
    period_to: str | None,
) -> dict[str, float]:
    """Compute the five sub-scores using keyword heuristics and portfolio context."""
    text = (article.get("headline", "") + " " + article.get("summary", "")).lower()

    # 1. financial_relevance — presence of financial/economic vocabulary
    fin_hits = sum(1 for kw in _FINANCIAL_KEYWORDS if kw in text)
    financial_relevance = min(0.3 + fin_hits * 0.07, 1.0)

    # 2. temporal_proximity — date within analysis window
    temporal_proximity = _temporal_score(article, period_from, period_to)

    # 3. macro_impact — macro/central-bank/market-wide keywords
    mac_hits = sum(1 for kw in _MACRO_KEYWORDS if kw in text)
    macro_impact = min(0.2 + mac_hits * 0.12, 1.0)

    # 4. sector_relation — mentions a sector the portfolio holds
    sector_relation = 0.0
    if portfolio_context:
        sectors = list((portfolio_context.get("category_concentration") or {}).keys())
        sector_kw = [s.lower().replace("_", " ") for s in sectors]
        if any(kw in text for kw in sector_kw):
            sector_relation = 0.8
        elif any(kw in text for kw in ["sector", "industria", "renta"]):
            sector_relation = 0.3

    # 5. asset_relation — mentions a specific issuer/instrument the portfolio holds
    asset_relation = 0.0
    if portfolio_context:
        issuers = [
            (a.get("name") or "").lower()
            for a in (portfolio_context.get("top_accounts") or [])
            if a.get("name")
        ]
        instruments = [
            (i.get("instrument_type") or "").lower()
            for i in (portfolio_context.get("instrument_breakdown") or [])
            if i.get("instrument_type")
        ]
        if any(iss and iss in text for iss in issuers[:10]):
            asset_relation = 1.0
        elif any(inst and inst in text for inst in instruments[:6]):
            asset_relation = 0.6

    return {
        "financial_relevance": round(financial_relevance, 2),
        "temporal_proximity":  round(temporal_proximity, 2),
        "macro_impact":        round(macro_impact, 2),
        "sector_relation":     round(sector_relation, 2),
        "asset_relation":      round(asset_relation, 2),
    }


def _composite(sub_scores: dict[str, float]) -> float:
    return round(
        sum(_SCORE_WEIGHTS[dim] * sub_scores.get(dim, 0.0) for dim in _SCORE_WEIGHTS),
        3,
    )


def _score_news_with_llm(
    articles: list[dict[str, Any]],
    llm_provider: Any,
    analysis_period: str,
    portfolio_context: dict[str, Any] | None = None,
    period_from: str | None = None,
    period_to: str | None = None,
) -> list[dict[str, Any]]:
    """
    Score article relevance (5 sub-scores + composite) and select via threshold gate.
    Falls back to heuristic scoring when LLM is unavailable.
    Includes articles with composite ≥ _NEWS_THRESHOLD, capped at _NEWS_CAP.
    """
    if not articles:
        return []

    if llm_provider is None:
        return _heuristic_news_score(articles, portfolio_context, period_from, period_to)

    try:
        articles_text = json.dumps(
            [{"headline": a["headline"], "summary": a["summary"]} for a in articles[:20]],
            ensure_ascii=False,
            indent=2,
        )
        portfolio_hint = ""
        if portfolio_context:
            sectors = list((portfolio_context.get("category_concentration") or {}).keys())[:5]
            issuers = [
                a.get("name", "") for a in (portfolio_context.get("top_accounts") or [])[:8]
            ]
            instruments = [
                i.get("instrument_type", "")
                for i in (portfolio_context.get("instrument_breakdown") or [])[:5]
            ]
            portfolio_hint = (
                f"\nPortfolio context — sectors: {sectors}; "
                f"top issuers: {issuers}; instruments: {instruments}"
            )
        user_content = (
            f"Analysis period: {analysis_period}{portfolio_hint}\n\n"
            f"News articles to evaluate:\n{articles_text}\n\n"
            "For each article score these five dimensions (0.0–1.0):\n"
            "  financial_relevance: is this financial/economic news?\n"
            "  temporal_proximity: does it match the analysis period?\n"
            "  macro_impact: does it affect macro factors (rates, FX, inflation)?\n"
            "  sector_relation: does it relate to the portfolio sectors?\n"
            "  asset_relation: does it mention specific portfolio issuers/instruments?\n"
            "Also compute composite = weighted average "
            "(weights: fin=0.20, temp=0.15, macro=0.25, sector=0.20, asset=0.20).\n"
            f"Include only articles with composite ≥ {_NEWS_THRESHOLD}. Return at most {_NEWS_CAP}.\n"
            'Return JSON array: [{"headline":"...","summary":"...","financial_relevance":0.0,'
            '"temporal_proximity":0.0,"macro_impact":0.0,"sector_relation":0.0,'
            '"asset_relation":0.0,"composite":0.0}]'
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
        items: list[dict] = []
        if isinstance(result, list):
            items = result
        elif isinstance(result, dict) and "articles" in result:
            items = result["articles"]

        # Normalize: ensure composite field exists; re-compute if missing
        out = []
        for item in items:
            if "composite" not in item:
                sub = {k: float(item.get(k, 0.0)) for k in _SCORE_WEIGHTS}
                item["composite"] = _composite(sub)
            if float(item.get("composite", 0.0)) >= _NEWS_THRESHOLD:
                out.append(item)
        out.sort(key=lambda x: x.get("composite", 0.0), reverse=True)
        return out[:_NEWS_CAP] if out else _heuristic_news_score(
            articles, portfolio_context, period_from, period_to
        )
    except Exception as exc:
        logger.warning("LLM news scoring failed: %s — using heuristic fallback", exc)
        return _heuristic_news_score(articles, portfolio_context, period_from, period_to)


def _heuristic_news_score(
    articles: list[dict[str, Any]],
    portfolio_context: dict[str, Any] | None = None,
    period_from: str | None = None,
    period_to: str | None = None,
) -> list[dict[str, Any]]:
    """Portfolio-aware heuristic scoring with 5 sub-scores and threshold gate."""
    scored: list[dict[str, Any]] = []
    for art in articles:
        sub = _heuristic_sub_scores(art, portfolio_context, period_from, period_to)
        comp = _composite(sub)
        if comp >= _NEWS_THRESHOLD:
            scored.append({
                "headline": art.get("headline", ""),
                "summary": art.get("summary", ""),
                **sub,
                "composite": comp,
            })
    scored.sort(key=lambda x: x.get("composite", 0.0), reverse=True)
    return scored[:_NEWS_CAP]


def _news_window_for_period(analysis_period: str | None) -> tuple[str | None, str | None]:
    """
    Translate the reporting period into an ISO-8601 (from, to) window so the news we
    fetch matches the era of the financial statements being analysed — not 'today'.

    Supports 'YYYY-Qn', 'YYYY-Hn', 'YYYY-MM' and 'YYYY'. A 'YYYY-MM' period is treated
    as the half-year it falls in (these EEFF compare against the prior December, i.e.
    semester/annual cadence). Returns (None, None) when the period can't be parsed or
    lies in the future, so the caller falls back to latest news.
    """
    if not analysis_period:
        return None, None
    p = analysis_period.strip().upper().replace("/", "-")
    try:
        year = int(p[:4])
    except (ValueError, IndexError):
        return None, None

    bounds = {
        "Q1": ((1, 1), (3, 31)), "Q2": ((4, 1), (6, 30)),
        "Q3": ((7, 1), (9, 30)), "Q4": ((10, 1), (12, 31)),
        "H1": ((1, 1), (6, 30)), "H2": ((7, 1), (12, 31)),
    }
    start_md = end_md = None
    for tag, (smd, emd) in bounds.items():
        if tag in p:
            start_md, end_md = smd, emd
            break
    if start_md is None:
        parts = p.split("-")
        if len(parts) >= 2 and parts[1].isdigit():          # YYYY-MM → enclosing half
            month = max(1, min(12, int(parts[1])))
            start_md, end_md = ((1, 1), (6, 30)) if month <= 6 else ((7, 1), (12, 31))
        else:                                                # YYYY → full year
            start_md, end_md = (1, 1), (12, 31)

    start = datetime(year, *start_md)
    end = datetime(year, *end_md)
    now = datetime.now()
    if start > now:
        return None, None
    if end > now:
        end = now
    return (start.strftime("%Y-%m-%dT00:00:00Z"), end.strftime("%Y-%m-%dT23:59:59Z"))


def generate_macro_context(
    analysis_period: str | None = None,
    country: str = "Colombia",
    llm_provider: Any = None,
    portfolio_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Main entry point. Returns a MacroContextOutput dict.

    Args:
        analysis_period: e.g. "2025-H1" or "2024-Q4". Auto-derived if None.
        country: target country (currently only Colombia is supported).
        llm_provider: optional LLMProvider instance for news scoring.
                      Pass None to run in fully deterministic mode.
        portfolio_context: optional dict with keys category_concentration,
                           top_accounts, instrument_breakdown — used to score
                           sector_relation and asset_relation for each news item.

    Returns:
        Strict MacroContextOutput dict suitable for JSON serialisation.
    """
    if analysis_period is None:
        now = datetime.now()
        half = "H1" if now.month <= 6 else "H2"
        analysis_period = f"{now.year}-{half}"

    logger.info("MacroContextEngine start | period=%s country=%s", analysis_period, country)

    # ── 1. Fetch raw data ────────────────────────────────────────────────────
    # News is scoped to the reporting period (real context of the analysis), not today.
    news_from, news_to = _news_window_for_period(analysis_period)
    te_data = fetch_colombia_indicators()
    news_data = fetch_colombia_news(date_from=news_from, date_to=news_to)
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
        news_data.get("articles", []),
        llm_provider,
        analysis_period,
        portfolio_context=portfolio_context,
        period_from=news_from,
        period_to=news_to,
    )

    # ── 5. Assemble output ───────────────────────────────────────────────────
    output: dict[str, Any] = {
        "country": country,
        "analysis_period": analysis_period,
        "macro_context": macro_context,
        "market_context": market_ctx,
        "sector_context": sector_context,
        "news_context": news_context,
        "news_window": {"from": news_from, "to": news_to},
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
