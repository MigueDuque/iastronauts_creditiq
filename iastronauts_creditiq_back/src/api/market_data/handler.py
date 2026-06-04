"""
GET /market/data — stocks, FX, indices, commodities via yfinance.

Results are cached in memory for 5 minutes so yfinance isn't hammered on every
page navigation. Each ticker fetch runs in a thread pool for ~3s total instead
of ~15s sequential.
"""

import json
import logging
import time
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("api.market_data")
logger.setLevel(logging.INFO)

_CACHE: Optional[dict] = None
_CACHE_AT: float = 0.0
_CACHE_TTL = 300  # 5 minutes

# S3 key for the shared, cross-instance market snapshot. The scheduled
# (EventBridge) invocation is the only writer; the API request path is a
# pure reader. This keeps reads under API Gateway's hard 30s integration
# timeout — a live yfinance fetch takes 35–120s and would otherwise 503.
DATA_SNAPSHOT_KEY = "market/data_snapshot.json"

# ── Ticker catalogs ───────────────────────────────────────────────────────────────
STOCKS = [
    {"key": "NUTRESA",   "ticker": "NUTRESA.CL",   "label": "Nutresa"},
    {"key": "GRUPOSURA", "ticker": "GRUPOSURA.CL",  "label": "Grupo Sura"},
    {"key": "ISA",       "ticker": "ISA.CL",        "label": "ISA"},
    {"key": "CELSIA",    "ticker": "CELSIA.CL",     "label": "Celsia"},
    {"key": "CEMARGOS",  "ticker": "CEMARGOS.CL",   "label": "Cementos Argos"},
    {"key": "DAVVNDA",   "ticker": "PFDAVVNDA.CL",  "label": "Davivienda Pref"},
    {"key": "BOGOTA",    "ticker": "BOGOTA.CL",     "label": "Banco de Bogota"},
    {"key": "EXITO",     "ticker": "EXITO.CL",      "label": "Grupo Exito"},
    {"key": "MINEROS",   "ticker": "MINEROS.CL",    "label": "Mineros"},
    {"key": "ETB",       "ticker": "ETB.CL",        "label": "ETB"},
    {"key": "PROMIGAS",  "ticker": "PROMIGAS.CL",   "label": "Promigas"},
    {"key": "TERPEL",    "ticker": "TERPEL.CL",     "label": "Terpel"},
]

FX = [
    {"key": "USDCOP", "ticker": "COP=X",     "label": "USD / COP", "base": "USD"},
    {"key": "EURCOP", "ticker": "EURCOP=X",  "label": "EUR / COP", "base": "EUR"},
    {"key": "GBPCOP", "ticker": "GBPCOP=X",  "label": "GBP / COP", "base": "GBP"},
    {"key": "BRLCOP", "ticker": "BRLCOP=X",  "label": "BRL / COP", "base": "BRL"},
    {"key": "JPYCOP", "ticker": "JPYCOP=X",  "label": "JPY / COP", "base": "JPY"},
]

INDICES = [
    {"key": "SP500",   "ticker": "^GSPC", "label": "S&P 500",    "currency": "USD"},
    {"key": "NASDAQ",  "ticker": "^IXIC", "label": "NASDAQ",      "currency": "USD"},
    {"key": "DJI",     "ticker": "^DJI",  "label": "Dow Jones",   "currency": "USD"},
    {"key": "BOVESPA", "ticker": "^BVSP", "label": "Bovespa",     "currency": "BRL"},
    {"key": "IPCMX",   "ticker": "^MXX",  "label": "IPC Mexico",  "currency": "MXN"},
    {"key": "MERVAL",  "ticker": "^MERV", "label": "Merval",      "currency": "ARS"},
]

COMMODITIES = [
    {"key": "WTI",    "ticker": "CL=F", "label": "WTI Crude",   "unit": "USD/bbl"},
    {"key": "BRENT",  "ticker": "BZ=F", "label": "Brent Crude", "unit": "USD/bbl"},
    {"key": "GOLD",   "ticker": "GC=F", "label": "Gold",        "unit": "USD/oz"},
    {"key": "NATGAS", "ticker": "NG=F", "label": "Natural Gas", "unit": "USD/MMBtu"},
    {"key": "COFFEE", "ticker": "KC=F", "label": "Coffee",      "unit": "USD/lb"},
    {"key": "COCOA",  "ticker": "CC=F", "label": "Cocoa",       "unit": "USD/MT"},
]


# ── Fetch helpers ─────────────────────────────────────────────────────────────────

def _closes_for(df, ticker: str) -> list[float]:
    """Extract the clean close series for one ticker out of a yf.download frame.

    yf.download(group_by="ticker") returns a column MultiIndex keyed by ticker;
    a single-ticker frame is flat. Handle both, and never raise — a missing or
    malformed column just yields an empty series so the caller marks it ok=False.
    """
    try:
        if hasattr(df.columns, "levels") and ticker in df.columns.get_level_values(0):
            series = df[ticker]["Close"]
        elif "Close" in getattr(df, "columns", []):
            series = df["Close"]
        else:
            return []
        return [float(c) for c in series.dropna().tolist()]
    except Exception:
        return []


def _result_from_closes(spec: dict, closes: list[float]) -> dict:
    """Build the card payload for one ticker from its close series."""
    if not closes:
        return {**spec, "ok": False, "error": "no data"}
    value = closes[-1]
    prev = closes[-2] if len(closes) > 1 else value
    change_pct = round((value - prev) / abs(prev) * 100, 2) if prev else 0.0
    return {
        **spec,
        "value": value,
        "prev_close": prev,
        "change_pct": change_pct,
        "history": closes[-20:],
        "ok": True,
    }


def _fetch_all() -> dict:
    """Fetch every ticker in ONE batched yf.download request.

    A single batched call (one HTTP request for all ~29 symbols) is the only
    reliable pattern from a Lambda IP: the previous design fired 29 concurrent
    yf.Ticker().history() calls, which Yahoo rate-limits as a burst — every
    ticker then times out and the whole snapshot lands empty. The batched
    endpoint returns all symbols at once in ~2s and stays well under the
    rate limit. `threads` stays on for yfinance's internal chunking but we no
    longer fan out our own thread pool per ticker.
    """
    import yfinance as yf

    all_specs = STOCKS + FX + INDICES + COMMODITIES
    tickers = [s["ticker"] for s in all_specs]

    closes_by_ticker: dict[str, list[float]] = {}
    try:
        df = yf.download(
            tickers,
            period="1mo",
            interval="1d",
            group_by="ticker",
            auto_adjust=True,
            threads=True,
            progress=False,
        )
        for t in tickers:
            closes_by_ticker[t] = _closes_for(df, t)
    except Exception as exc:
        logger.error("batched yf.download failed: %s", exc, exc_info=True)

    def build(specs: list[dict]) -> list[dict]:
        return [_result_from_closes(s, closes_by_ticker.get(s["ticker"], [])) for s in specs]

    return {
        "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "stocks":      build(STOCKS),
        "fx":          build(FX),
        "indices":     build(INDICES),
        "commodities": build(COMMODITIES),
    }


def get_market_data(force: bool = False) -> dict:
    global _CACHE, _CACHE_AT
    if not force and _CACHE and (time.monotonic() - _CACHE_AT) < _CACHE_TTL:
        return _CACHE
    data = _fetch_all()
    _CACHE = data
    _CACHE_AT = time.monotonic()
    logger.info(
        "market_data refresh | stocks=%d fx=%d indices=%d commodities=%d",
        len(data["stocks"]), len(data["fx"]), len(data["indices"]), len(data["commodities"]),
    )
    return data


# ── Lambda handler ─────────────────────────────────────────────────────────────────

def _is_async_refresh(event: dict) -> bool:
    """True when this invocation should do the slow live fetch + S3 write.

    Two triggers reach the writer path, both off the API request path:
      * the async self-invoke fired by a POST (carries `_refresh`)
      * a legacy EventBridge scheduled event (`source: aws.events`)
    """
    return bool(event.get("_refresh")) \
        or event.get("source") == "aws.events" \
        or event.get("detail-type") == "Scheduled Event"


def _http_method(event: dict) -> str:
    """HTTP method for an API Gateway HTTP API (v2) event, or '' for non-API events."""
    return (event.get("requestContext", {}).get("http", {}) or {}).get("method", "")


def _refresh_and_store() -> dict:
    """Live fetch + persist to S3. Slow (35–120s) — only ever runs off the request path."""
    from shared import market_store

    data = get_market_data(force=True)
    market_store.write(DATA_SNAPSHOT_KEY, data)
    return data


def _trigger_async_refresh(context) -> dict:
    """Fire a background self-invoke that runs the slow fetch, return 202 at once.

    The live fetch can take 35–120s, far beyond API Gateway's 30s integration
    timeout, so the request path must never block on it. The frontend polls
    GET /market/data and watches `as_of` to know when the new snapshot lands.
    """
    import boto3

    fn = getattr(context, "function_name", None)
    if not fn:
        # No Lambda context (e.g. local dev) — just refresh synchronously.
        data = _refresh_and_store()
        return _json_response(200, {"status": "refreshed", "as_of": data.get("as_of")})

    boto3.client("lambda").invoke(
        FunctionName=fn,
        InvocationType="Event",  # async — returns immediately
        Payload=json.dumps({"_refresh": True}).encode("utf-8"),
    )
    logger.info("market_data: async refresh triggered on %s", fn)
    return _json_response(202, {"status": "refresh_started"})


def _json_response(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body, ensure_ascii=False, default=str),
    }


def lambda_handler(event: dict, context) -> dict:
    from shared import market_store

    # Writer path: the async self-invoke does the slow live fetch and stores the
    # snapshot to S3. It may take the full Lambda timeout — no client is waiting.
    if _is_async_refresh(event):
        try:
            data = _refresh_and_store()
            return _json_response(200, data)
        except Exception as exc:
            logger.error("market_data refresh failed: %s", exc, exc_info=True)
            return _json_response(500, {"error": str(exc)})

    # POST /market/data — user pressed "Refresh". Kick off the background fetch
    # and return 202 immediately; the frontend then polls the GET below.
    if _http_method(event) == "POST":
        return _trigger_async_refresh(context)

    # Reader path (GET): serve the cached S3 snapshot only. Never fetch live here
    # — a live fetch exceeds API Gateway's 30s cap and returns 503.
    try:
        cached = market_store.read(DATA_SNAPSHOT_KEY)
        if cached:
            return _json_response(200, cached)

        # No snapshot yet (never refreshed). Return an empty-but-valid payload so
        # the UI can prompt the user to press Refresh instead of erroring.
        logger.info("market_data: no cached snapshot yet")
        return _json_response(200, {
            "as_of": None, "stocks": [], "fx": [], "indices": [], "commodities": [],
        })
    except Exception as exc:
        logger.error("market_data handler error: %s", exc, exc_info=True)
        return _json_response(500, {"error": str(exc)})
