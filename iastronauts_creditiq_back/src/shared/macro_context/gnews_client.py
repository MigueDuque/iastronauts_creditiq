"""
GNews client — fetches economic and market news relevant to Colombia.
Uses the GNews REST API v4 via urllib (no extra dependency).

Rate-limit strategy:
  - Free plan: 100 req/day, ~1 req/s burst limit.
  - Total requests per call: 4 (2 top-headlines + 2 targeted searches).
  - In-memory cache (TTL=4h) so warm Lambda containers skip re-fetching.
  - 0.7s inter-request delay to stay within burst limit.
  - 429 responses are retried once after a 5s wait.
"""
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

_GNEWS_BASE = "https://gnews.io/api/v4"
_MAX_ARTICLES = 10
_INTER_REQUEST_DELAY = 0.7   # seconds between API calls
_RETRY_WAIT_429 = 5          # seconds to wait on a 429 before one retry

# Top-headlines: only Spanish/Colombia — covers local business and world news
_HEADLINE_CATEGORIES = ["business", "world"]

# Targeted searches — limited to 2 highest-signal queries to stay within free quota
_PRIORITY_QUERIES_ES = [
    "inflacion Colombia BanRep tasa interes",
    "COLCAP TES bonos Colombia mercado",
]

# ── In-memory cache ───────────────────────────────────────────────────────────
_CACHE_TTL_SECONDS = 4 * 3600   # 4 hours — macro news doesn't change per-analysis
_cache_data: dict[str, Any] | None = None
_cache_ts: float = 0.0


def _cache_valid() -> bool:
    return _cache_data is not None and (time.time() - _cache_ts) < _CACHE_TTL_SECONDS


def _fetch_url(url: str) -> list[dict]:
    """Single GET with one 429-retry. Returns articles list or []."""
    for attempt in range(2):
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("articles", [])
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt == 0:
                logger.warning("GNews 429 rate-limit — waiting %ds before retry", _RETRY_WAIT_429)
                time.sleep(_RETRY_WAIT_429)
                continue
            logger.warning("GNews HTTP error [%s]: %s", url.split("?")[0], exc)
            return []
        except Exception as exc:
            logger.warning("GNews request error: %s", exc)
            return []
    return []


def _fetch_top_headlines(category: str, api_key: str) -> list[dict]:
    params = urllib.parse.urlencode({
        "category": category,
        "lang": "es",
        "country": "co",
        "max": _MAX_ARTICLES,
        "apikey": api_key,
    })
    return _fetch_url(f"{_GNEWS_BASE}/top-headlines?{params}")


def _fetch_search(query: str, api_key: str) -> list[dict]:
    params = urllib.parse.urlencode({
        "q": query,
        "lang": "es",
        "max": 5,
        "apikey": api_key,
    })
    return _fetch_url(f"{_GNEWS_BASE}/search?{params}")


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
    Fetch Colombian economic and market news (4 API calls max, cached 4h).

    Returns:
      {"articles": [...], "available": bool, "source": "gnews"}
    """
    global _cache_data, _cache_ts

    if _cache_valid():
        logger.info("GNews cache hit — skipping API calls")
        return _cache_data  # type: ignore[return-value]

    api_key = os.getenv("GNEWS_API_KEY", "")
    if not api_key:
        logger.warning("GNEWS_API_KEY not set — GNews client disabled")
        return {"articles": [], "available": False, "source": "gnews"}

    all_articles: list[dict] = []
    requests_made = 0

    # 2 top-headlines calls (business + world, Spanish/Colombia)
    for cat in _HEADLINE_CATEGORIES:
        raw = _fetch_top_headlines(cat, api_key)
        all_articles.extend([_normalise_article(a) for a in raw])
        requests_made += 1
        if requests_made < len(_HEADLINE_CATEGORIES) + len(_PRIORITY_QUERIES_ES):
            time.sleep(_INTER_REQUEST_DELAY)

    # 2 targeted Colombian financial searches
    for query in _PRIORITY_QUERIES_ES:
        raw = _fetch_search(query, api_key)
        all_articles.extend([_normalise_article(a) for a in raw])
        requests_made += 1
        if requests_made < len(_HEADLINE_CATEGORIES) + len(_PRIORITY_QUERIES_ES):
            time.sleep(_INTER_REQUEST_DELAY)

    deduped = _deduplicate(all_articles)
    logger.info("GNews fetched %d unique articles (%d API calls)", len(deduped), requests_made)

    result: dict[str, Any] = {
        "articles": deduped[:30],
        "available": len(deduped) > 0,
        "source": "gnews",
    }

    _cache_data = result
    _cache_ts = time.time()
    return result
