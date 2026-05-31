"""
GET /market/data — stocks, FX, indices, commodities via yfinance.

Results are cached in memory for 5 minutes so yfinance isn't hammered on every
page navigation. Each ticker fetch runs in a thread pool for ~3s total instead
of ~15s sequential.
"""

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger("api.market_data")
logger.setLevel(logging.INFO)

_CACHE: Optional[dict] = None
_CACHE_AT: float = 0.0
_CACHE_TTL = 300  # 5 minutes

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

def _fetch_one(spec: dict) -> dict:
    """Fetch 1-month daily history for one ticker. Returns a result dict."""
    import yfinance as yf
    ticker = spec["ticker"]
    try:
        hist = yf.Ticker(ticker).history(period="1mo", interval="1d")
        closes = [float(c) for c in hist["Close"].dropna().tolist()]
        if not closes:
            raise ValueError("no closes")
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
    except Exception as exc:
        logger.warning("fetch %s failed: %s", ticker, exc)
        return {**spec, "ok": False, "error": str(exc)}


def _fetch_group(specs: list[dict]) -> list[dict]:
    results_by_key: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=min(len(specs), 12)) as pool:
        futures = {pool.submit(_fetch_one, s): s["key"] for s in specs}
        for future in as_completed(futures):
            r = future.result()
            results_by_key[r["key"]] = r
    return [results_by_key[s["key"]] for s in specs if s["key"] in results_by_key]


def _fetch_all() -> dict:
    """Fetch all four groups concurrently (groups run in parallel threads)."""
    groups: dict[str, list[dict]] = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        fs = {
            pool.submit(_fetch_group, STOCKS):      "stocks",
            pool.submit(_fetch_group, FX):          "fx",
            pool.submit(_fetch_group, INDICES):     "indices",
            pool.submit(_fetch_group, COMMODITIES): "commodities",
        }
        for f in as_completed(fs):
            key = fs[f]
            groups[key] = f.result()

    return {
        "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "stocks":      groups.get("stocks", []),
        "fx":          groups.get("fx", []),
        "indices":     groups.get("indices", []),
        "commodities": groups.get("commodities", []),
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

def lambda_handler(event: dict, context) -> dict:
    force = str((event.get("queryStringParameters") or {}).get("force", "")).lower() == "true"
    try:
        data = get_market_data(force=force)
        return {
            "statusCode": 200,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            },
            "body": json.dumps(data, ensure_ascii=False, default=str),
        }
    except Exception as exc:
        logger.error("market_data handler error: %s", exc, exc_info=True)
        return {
            "statusCode": 500,
            "headers": {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"},
            "body": json.dumps({"error": str(exc)}),
        }
