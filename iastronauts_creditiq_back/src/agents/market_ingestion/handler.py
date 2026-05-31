"""
Agent 1 — Market Ingestion.

Queries the three data sources, normalizes each response into a flat record, and
writes a single raw snapshot to market/raw_snapshot.json.

Design rules:
  * Every source is wrapped in its own try/except. One dead source (e.g. yfinance
    rate-limited) must never blank the whole snapshot — it just lands in errors[]
    and its previous value is carried forward by the interpreter.
  * No interpretation here. This agent only fetches + normalizes. Significance,
    anomalies and signals are Agent 2's job ("Math First, Synthesis Second").
  * Output is deterministic in shape: metrics[] + news[] + errors[] always exist.

Sources:
  * yfinance            → FX / index spot + sparkline history
  * tradingeconomics    → Colombian macro (bond yield, inflation, policy rate)
  * GNews               → real-time financial headlines (es / Colombia)

Env:
  GNEWS_API_KEY         required for live news (else news[] is empty + an error)
  TE_API_KEY            Trading Economics 'key:secret' (else falls back to guest)
  MARKET_NEWS_QUERY     override the GNews query string
"""

import logging
import os
from typing import Any, Optional

from shared import market_store

logger = logging.getLogger("agents.market_ingestion")
logger.setLevel(logging.INFO)

# ── Metric catalog ──────────────────────────────────────────────────────────────
# One row per Market Overview card. `provider` selects the fetcher; `ref` is the
# provider-specific lookup (yfinance ticker, or TE indicator name). Keeping this
# as data means fixing a wrong symbol is a one-line edit, not a logic change.
METRICS: list[dict[str, Any]] = [
    {"key": "USDCOP", "label": "USD / COP",       "provider": "yfinance", "ref": "COP=X",   "unit": "COP", "decimals": 2},
    {"key": "COLCAP", "label": "COLCAP",          "provider": "yfinance", "ref": "^COLCAP", "unit": "pts", "decimals": 2},
    {"key": "TES10Y", "label": "TES 10Y",         "provider": "te", "ref": "government bond 10y", "unit": "%", "decimals": 2},
    {"key": "INFL",   "label": "Inflación (COL)", "provider": "te", "ref": "inflation rate",      "unit": "%", "decimals": 2},
    {"key": "BANREP", "label": "Tasa BanRep",     "provider": "te", "ref": "interest rate",       "unit": "%", "decimals": 2},
]

_DEFAULT_NEWS_QUERY = (
    "BanRep OR inflación OR TES OR COLCAP OR tasa de interés OR mercados"
)


# ── yfinance ─────────────────────────────────────────────────────────────────────

def _fetch_yfinance(ref: str) -> dict[str, Any]:
    """Return {value, prev_close, history[]} for a yfinance ticker.

    history[] is ~10 recent closes for the dashboard sparkline. Raises on any
    problem (no data, network) so the caller records the source as failed.
    """
    import yfinance as yf

    hist = yf.Ticker(ref).history(period="1mo", interval="1d")
    closes = [float(c) for c in hist["Close"].dropna().tolist()]
    if not closes:
        raise ValueError(f"yfinance returned no closes for {ref}")
    value = closes[-1]
    prev = closes[-2] if len(closes) > 1 else value
    return {"value": value, "prev_close": prev, "history": closes[-10:]}


# ── tradingeconomics ─────────────────────────────────────────────────────────────

_te_logged_in = False


def _te_login() -> None:
    global _te_logged_in
    if _te_logged_in:
        return
    import tradingeconomics as te

    te.login(os.environ.get("TE_API_KEY", "guest:guest"))
    _te_logged_in = True


def _fetch_te(ref: str) -> dict[str, Any]:
    """Return {value, prev_close, history[]} for a Colombian TE indicator.

    Uses the latest indicator value as `value`; a short historical series (when
    available) provides both prev_close and the sparkline.
    """
    import tradingeconomics as te

    _te_login()

    series: list[float] = []
    try:
        hist = te.getHistoricalData(
            country="colombia", indicator=ref, output_type="dict"
        )
        if isinstance(hist, list):
            series = [
                float(r["Value"])
                for r in hist
                if isinstance(r, dict) and r.get("Value") is not None
            ]
    except Exception as exc:  # historical is best-effort; fall back to spot below
        logger.info("te history miss for %s: %s", ref, exc)

    if series:
        value = series[-1]
        prev = series[-2] if len(series) > 1 else value
        return {"value": value, "prev_close": prev, "history": series[-10:]}

    # No historical series — read the single latest indicator value.
    data = te.getIndicatorData(country="colombia", output_type="dict")
    match = _match_indicator(data, ref)
    if match is None:
        raise ValueError(f"TE indicator '{ref}' not found for Colombia")
    value = float(match["LatestValue"])
    prev = float(match.get("PreviousValue") or value)
    return {"value": value, "prev_close": prev, "history": [prev, value]}


def _match_indicator(data: Any, ref: str) -> Optional[dict]:
    """Find the indicator row whose Category loosely matches `ref`."""
    if not isinstance(data, list):
        return None
    needle = ref.lower().replace("rate", "").strip()
    for row in data:
        if not isinstance(row, dict):
            continue
        category = str(row.get("Category", "")).lower()
        if needle and needle in category and row.get("LatestValue") is not None:
            return row
    return None


# ── GNews ────────────────────────────────────────────────────────────────────────

def _fetch_news(max_items: int = 8) -> list[dict[str, Any]]:
    """Fetch + normalize Colombian financial headlines from GNews."""
    import requests

    api_key = os.environ.get("GNEWS_API_KEY", "")
    if not api_key:
        raise ValueError("GNEWS_API_KEY no está configurada")

    query = os.environ.get("MARKET_NEWS_QUERY", _DEFAULT_NEWS_QUERY)
    resp = requests.get(
        "https://gnews.io/api/v4/search",
        params={
            "q": query,
            "lang": "es",
            "country": "co",
            "max": max_items,
            "sortby": "publishedAt",
            "apikey": api_key,
        },
        timeout=10,
    )
    resp.raise_for_status()
    articles = resp.json().get("articles", [])
    return [
        {
            "title": a.get("title"),
            "description": a.get("description"),
            "url": a.get("url"),
            "image_url": a.get("image"),
            "published_at": a.get("publishedAt"),
            "source": (a.get("source") or {}).get("name"),
            "ok": True,
        }
        for a in articles
        if a.get("title")
    ]


# ── Orchestration ────────────────────────────────────────────────────────────────

def ingest() -> dict[str, Any]:
    """Run every source, build + persist the raw snapshot, return it."""
    metrics: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for spec in METRICS:
        try:
            fetch = _fetch_yfinance if spec["provider"] == "yfinance" else _fetch_te
            data = fetch(spec["ref"])
            change_pct = _pct_change(data["value"], data["prev_close"])
            metrics.append({
                "key": spec["key"],
                "label": spec["label"],
                "value": round(data["value"], spec["decimals"]),
                "prev_close": round(data["prev_close"], spec["decimals"]),
                "change_pct": change_pct,
                "unit": spec["unit"],
                "history": data["history"],
                "source": spec["provider"],
                "ok": True,
            })
        except Exception as exc:
            logger.warning("ingest metric %s failed: %s", spec["key"], exc)
            errors.append({"key": spec["key"], "reason": f"{type(exc).__name__}: {exc}"})

    news: list[dict[str, Any]] = []
    try:
        news = _fetch_news()
    except Exception as exc:
        logger.warning("ingest news failed: %s", exc)
        errors.append({"key": "news", "reason": f"{type(exc).__name__}: {exc}"})

    snapshot = {
        "as_of": market_store.now_iso(),
        "metrics": metrics,
        "news": news,
        "errors": errors,
    }
    market_store.write_raw_snapshot(snapshot)
    logger.info(
        "ingest complete | metrics=%d news=%d errors=%d",
        len(metrics), len(news), len(errors),
    )
    return snapshot


def _pct_change(value: float, prev: float) -> float:
    if not prev:
        return 0.0
    return round((value - prev) / abs(prev) * 100, 2)


def lambda_handler(event, context) -> dict[str, Any]:
    """EventBridge / manual entrypoint. event is ignored — this is a singleton job."""
    return ingest()
