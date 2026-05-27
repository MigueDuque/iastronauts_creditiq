"""
GNews client — fetches economic and market news relevant to Colombia.
Uses the GNews REST API v4 via urllib (no extra dependency).
"""
import json
import logging
import os
import urllib.parse
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

_GNEWS_BASE = "https://gnews.io/api/v4"
_MAX_ARTICLES = 10
_CATEGORIES = ["business", "world"]

_COLOMBIA_QUERIES_ES = [
    "Colombia economia BanRep",
    "inflacion Colombia tasa interes",
    "COLCAP bolsa Colombia",
    "TES bonos Colombia deuda",
    "Ecopetrol Colombia mercado",
    "renta fija Colombia volatilidad",
]

_COLOMBIA_QUERIES_EN = [
    "Colombia economy interest rate",
    "Colombia inflation Banco de la Republica",
    "Ecopetrol stock market",
    "Colombia sovereign bonds TES",
]


def _fetch_top_headlines(category: str, api_key: str, lang: str = "es") -> list[dict]:
    """Fetch top headlines for a given category."""
    params = urllib.parse.urlencode({
        "category": category,
        "lang": lang,
        "country": "co",
        "max": _MAX_ARTICLES,
        "apikey": api_key,
    })
    url = f"{_GNEWS_BASE}/top-headlines?{params}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("articles", [])
    except Exception as exc:
        logger.warning("GNews top-headlines [%s] error: %s", category, exc)
        return []


def _fetch_search(query: str, api_key: str, lang: str = "es") -> list[dict]:
    """Search GNews for a specific query.
    Note: no 'country' filter — targeted searches rely on query terms for scoping.
    Adding country=co would exclude Reuters, FT, Bloomberg coverage of Colombia.
    """
    params = urllib.parse.urlencode({
        "q": query,
        "lang": lang,
        "max": 5,
        "apikey": api_key,
    })
    url = f"{_GNEWS_BASE}/search?{params}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("articles", [])
    except Exception as exc:
        logger.warning("GNews search [%s] error: %s", query, exc)
        return []


def _normalise_article(article: dict) -> dict[str, Any]:
    return {
        "headline": article.get("title", ""),
        "summary": article.get("description", "") or article.get("content", ""),
        "source": article.get("source", {}).get("name", ""),
        "published_at": article.get("publishedAt", ""),
        "url": article.get("url", ""),
    }


def _deduplicate(articles: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for art in articles:
        key = art.get("headline", "")[:80]
        if key and key not in seen:
            seen.add(key)
            out.append(art)
    return out


def fetch_colombia_news() -> dict[str, Any]:
    """
    Fetch Colombian economic and market news.
    Returns:
      {
        "articles": [{"headline", "summary", "source", "published_at", "url"}, ...],
        "available": bool,
        "source": "gnews"
      }
    Falls back to empty list on any failure.
    """
    api_key = os.getenv("GNEWS_API_KEY", "")
    if not api_key:
        logger.warning("GNEWS_API_KEY not set — GNews client disabled")
        return {"articles": [], "available": False, "source": "gnews"}

    all_articles: list[dict] = []

    # Top headlines per category (business + world in Spanish/Colombia)
    for cat in _CATEGORIES:
        raw = _fetch_top_headlines(cat, api_key, lang="es")
        all_articles.extend([_normalise_article(a) for a in raw])

    # Also try English-language category headlines for broader coverage
    for cat in _CATEGORIES:
        raw = _fetch_top_headlines(cat, api_key, lang="en")
        all_articles.extend([_normalise_article(a) for a in raw])

    # Targeted searches — Spanish-language Colombian financial topics
    for query in _COLOMBIA_QUERIES_ES:
        raw = _fetch_search(query, api_key, lang="es")
        all_articles.extend([_normalise_article(a) for a in raw])

    # Targeted searches — English-language international coverage of Colombia
    for query in _COLOMBIA_QUERIES_EN:
        raw = _fetch_search(query, api_key, lang="en")
        all_articles.extend([_normalise_article(a) for a in raw])

    deduped = _deduplicate(all_articles)
    logger.info("GNews fetched %d unique articles", len(deduped))

    return {
        "articles": deduped[:30],  # cap for LLM context
        "available": len(deduped) > 0,
        "source": "gnews",
    }
