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

import boto3

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

# ── In-memory cache (keyed by date window) ────────────────────────────────────
_CACHE_TTL_SECONDS = 4 * 3600   # 4 hours — macro news doesn't change per-analysis
_cache: dict[str, tuple[dict[str, Any], float]] = {}


def _cache_get(key: str) -> dict[str, Any] | None:
    entry = _cache.get(key)
    if entry and (time.time() - entry[1]) < _CACHE_TTL_SECONDS:
        return entry[0]
    return None


def _cache_put(key: str, value: dict[str, Any]) -> None:
    _cache[key] = (value, time.time())


_gnews_key_cache: str | None = None

def _resolve_gnews_key() -> str:
    global _gnews_key_cache
    if _gnews_key_cache is not None:
        return _gnews_key_cache
    secret_arn = os.getenv("GNEWS_API_KEY_SECRET_ARN")
    if secret_arn:
        try:
            sm = boto3.client("secretsmanager")
            _gnews_key_cache = sm.get_secret_value(SecretId=secret_arn).get("SecretString", "")
            return _gnews_key_cache
        except Exception as exc:
            logger.warning("Could not read GNews secret from Secrets Manager: %s", exc)
    _gnews_key_cache = os.getenv("GNEWS_API_KEY", "")
    return _gnews_key_cache


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


def _fetch_search(
    query: str,
    api_key: str,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict]:
    params: dict[str, Any] = {
        "q": query,
        "lang": "es",
        "country": "co",
        "max": 5,
        "apikey": api_key,
    }
    # Scope the search to the reporting-period window when given, so the news reflects
    # the era of the financial statements rather than today's headlines.
    if date_from:
        params["from"] = date_from
    if date_to:
        params["to"] = date_to
        params["sortby"] = "relevance"
    return _fetch_url(f"{_GNEWS_BASE}/search?{urllib.parse.urlencode(params)}")


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


def fetch_colombia_news(
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[str, Any]:
    """
    Fetch Colombian economic and market news, cached 4h per date window.

    When (date_from, date_to) are given (ISO-8601), only the date-filterable *search*
    endpoint is used, scoped to that window — the period-accurate context for an
    analysis of historical statements. With no window, current top-headlines are also
    included (real-time mode, e.g. the dashboard — top-headlines cannot be date-filtered).

    Returns:
      {"articles": [...], "available": bool, "source": "gnews",
       "window": {"from": date_from, "to": date_to}}
    """
    cache_key = f"{date_from or 'latest'}|{date_to or 'latest'}"
    cached = _cache_get(cache_key)
    if cached is not None:
        logger.info("GNews cache hit — window=%s", cache_key)
        return cached

    window = {"from": date_from, "to": date_to}
    api_key = _resolve_gnews_key()
    if not api_key:
        logger.warning("GNEWS_API_KEY not set — GNews client disabled")
        return {"articles": [], "available": False, "source": "gnews", "window": window}

    period_scoped = bool(date_from or date_to)
    all_articles: list[dict] = []

    # Real-time top-headlines only when NOT scoped to a historical period.
    calls: list[tuple[str, str]] = []
    if not period_scoped:
        calls.extend(("headline", cat) for cat in _HEADLINE_CATEGORIES)
    calls.extend(("search", q) for q in _PRIORITY_QUERIES_ES)

    for i, (kind, arg) in enumerate(calls):
        if kind == "headline":
            raw = _fetch_top_headlines(arg, api_key)
        else:
            raw = _fetch_search(arg, api_key, date_from, date_to)
        all_articles.extend(_normalise_article(a) for a in raw)
        if i < len(calls) - 1:
            time.sleep(_INTER_REQUEST_DELAY)

    deduped = _deduplicate(all_articles)
    logger.info(
        "GNews fetched %d unique articles (%d calls, window=%s)",
        len(deduped), len(calls), cache_key,
    )

    result: dict[str, Any] = {
        "articles": deduped[:30],
        "available": len(deduped) > 0,
        "source": "gnews",
        "window": window,
    }
    _cache_put(cache_key, result)
    return result
