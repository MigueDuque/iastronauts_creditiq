"""
Agent 3 (read side) — Market Pulse read endpoint + refresh entrypoint.

Two responsibilities, kept in one module because they share the same cache:

  * lambda_handler  — GET /market/pulse|news|overview|signals. Pure cache hit;
    NEVER calls an external API on the request path. This is what keeps reads
    fast and under the GNews/yfinance rate limits.

  * refresh         — runs Agent 1 (ingest) → Agent 2 (interpret), writing a new
    pulse to the cache. Called by the EventBridge schedule (prod) and the
    local_server background loop (dev).

Fallback ladder when no fresh pulse exists:
  1. fresh pulse (within TTL)            → stale:false
  2. last-good pulse (past TTL)          → stale:true + as_of
  3. nothing cached                      → 503 + empty payload; the frontend then
                                           shows its own hardcoded fallback data.
"""

import json
import logging
from typing import Any

from shared import market_store

logger = logging.getLogger("api.market_read")
logger.setLevel(logging.INFO)

_EMPTY = {"as_of": None, "stale": True, "overview": [], "signals": [], "news": []}


def refresh() -> dict[str, Any]:
    """Run the ingestion → interpretation chain and return the new pulse."""
    from agents.market_ingestion.handler import ingest
    from agents.market_interpreter.handler import interpret

    snapshot = ingest()
    return interpret(snapshot)


def get_pulse() -> tuple[dict[str, Any], bool]:
    """Return (pulse, found). Serves last-good even when stale."""
    pulse = market_store.read_pulse()
    if pulse is None:
        return dict(_EMPTY), False
    return pulse, True


def _http_method(event: dict) -> str:
    return (event.get("requestContext", {}).get("http", {}) or {}).get("method", "")


def _trigger_async_refresh(context) -> dict:
    """Fire a background self-invoke that runs the GNews ingest + LLM interpret.

    This chain takes longer than API Gateway's 30s cap, so the request path must
    not block on it. The frontend polls GET /market/pulse and watches `as_of`.
    """
    import boto3

    fn = getattr(context, "function_name", None)
    if not fn:
        # Local dev — refresh synchronously.
        pulse = refresh()
        return _response(200, {"status": "refreshed", "as_of": pulse.get("as_of")})

    boto3.client("lambda").invoke(
        FunctionName=fn,
        InvocationType="Event",  # async — returns immediately
        Payload=json.dumps({"_refresh": True}).encode("utf-8"),
    )
    logger.info("market_read: async pulse refresh triggered on %s", fn)
    return _response(202, {"status": "refresh_started"})


def lambda_handler(event: dict, context) -> dict:
    """
    GET  /market/pulse     → full payload
    GET  /market/news      → pulse.news
    GET  /market/overview  → pulse.overview
    GET  /market/signals   → pulse.signals
    POST /market/news      → trigger a background pulse refresh (GNews + LLM)

    The slice is taken from rawPath so one Lambda backs all four read routes.
    """
    # Writer path: the async self-invoke runs the slow ingest → interpret chain
    # and writes the new pulse to S3. No client is waiting on it.
    if isinstance(event, dict) and event.get("_refresh"):
        try:
            pulse = refresh()
            return _response(200, {"status": "refreshed", "as_of": pulse.get("as_of")})
        except Exception as exc:
            logger.error("market_read pulse refresh failed: %s", exc, exc_info=True)
            return _response(500, {"error": str(exc)})

    # POST — user pressed "Update". Kick off the background refresh, return 202.
    if _http_method(event) == "POST":
        return _trigger_async_refresh(context)

    path = (event.get("rawPath") or event.get("path") or "/market/pulse").rstrip("/")
    pulse, found = get_pulse()

    if path.endswith("/news"):
        return _response(200, {"news": pulse.get("news", []), "stale": pulse.get("stale", True)})
    if path.endswith("/overview"):
        return _response(200, {"overview": pulse.get("overview", []), "stale": pulse.get("stale", True)})
    if path.endswith("/signals"):
        return _response(200, {"signals": pulse.get("signals", []), "stale": pulse.get("stale", True)})

    # /market/pulse — full payload. 503 only when nothing is cached at all, so the
    # client knows to fall back to its built-in defaults.
    return _response(200 if found else 503, pulse)


def _response(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body, ensure_ascii=False, default=str),
    }
