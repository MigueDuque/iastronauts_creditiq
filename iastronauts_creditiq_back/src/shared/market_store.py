"""
market_store.py

Cache layer for the AI Market Pulse dashboard.

Unlike job_store (per-job, per-tenant, per-date), market data is a *singleton*:
one global snapshot shared by every tenant, refreshed on a schedule. So the keys
are flat and there is exactly one of each:

    market/raw_snapshot.json   ← Agent 1 (ingestion) output: normalized source data
    market/pulse.json          ← Agent 2 (interpretation) output: card-ready payload

Two cache layers keep reads off the external APIs (GNews/yfinance/TE rate limits)
and fast:

  1. Warm in-memory cache (module-level, per warm container). Same idea as
     llm_provider._cached_anthropic_key / job_store._job_date_cache.
  2. S3 object. Survives container recycles and is shared across Lambdas.

Read endpoints NEVER call the external APIs — they only ever read this cache.
The scheduled refresher (Agent 1 + Agent 2) is the only writer.

S3 is optional: if MAIN_BUCKET is unset (pure-local smoke test), the in-memory
layer alone keeps the dashboard working for the life of the process.
"""

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger("shared.market_store")

BUCKET = os.environ.get("MAIN_BUCKET", "")

RAW_SNAPSHOT_KEY = "market/raw_snapshot.json"
PULSE_KEY = "market/pulse.json"

# How long a pulse is considered "fresh". After this it is still served, but
# flagged stale=True so the UI can show an "as of HH:MM" notice. Drives the
# refresh cadence too (local_server schedules a refresh every REFRESH_SECONDS).
REFRESH_SECONDS = int(os.environ.get("MARKET_REFRESH_SECONDS", "300"))

# Warm in-memory cache: key -> (payload, monotonic_stored_at).
_mem: dict[str, tuple[dict, float]] = {}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _s3():
    import boto3

    return boto3.client("s3")


def _age_seconds(payload: dict) -> Optional[float]:
    """Seconds since the payload's as_of, or None if it has no parseable timestamp."""
    raw = payload.get("as_of")
    if not raw:
        return None
    try:
        ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return max(0.0, (datetime.now(timezone.utc) - ts).total_seconds())


def is_stale(payload: dict) -> bool:
    """A pulse is stale once it is older than the refresh interval."""
    age = _age_seconds(payload)
    return age is None or age > REFRESH_SECONDS


def write(key: str, payload: dict) -> None:
    """Persist to the warm cache and (best-effort) to S3."""
    _mem[key] = (payload, time.monotonic())
    if not BUCKET:
        return
    try:
        _s3().put_object(
            Bucket=BUCKET,
            Key=key,
            Body=json.dumps(payload, ensure_ascii=False, default=str),
            ContentType="application/json",
        )
        logger.info("market_store.write | key=%s as_of=%s", key, payload.get("as_of"))
    except Exception as exc:  # S3 write must never break a refresh
        logger.warning("market_store.write | S3 failed for %s: %s", key, exc)


def read(key: str, max_age: Optional[float] = None) -> Optional[dict]:
    """
    Return the cached payload for `key`, or None if absent.

    Fast path: the warm in-memory copy if it is younger than `max_age` seconds
    (defaults to REFRESH_SECONDS). On a miss/expiry, falls back to S3 and
    repopulates the warm cache. The returned payload may itself be older than
    max_age (S3 holds the last good snapshot) — callers use is_stale() to decide
    how to present it. max_age only governs whether we trust the in-mem copy.
    """
    ttl = REFRESH_SECONDS if max_age is None else max_age
    hit = _mem.get(key)
    if hit and (time.monotonic() - hit[1]) <= ttl:
        return hit[0]

    if not BUCKET:
        # No S3 — the warm copy (even if past ttl) is all we have.
        return hit[0] if hit else None

    try:
        obj = _s3().get_object(Bucket=BUCKET, Key=key)
        payload = json.loads(obj["Body"].read())
        _mem[key] = (payload, time.monotonic())
        return payload
    except Exception as exc:
        logger.info("market_store.read | S3 miss for %s (%s)", key, type(exc).__name__)
        return hit[0] if hit else None


# ── Convenience wrappers ────────────────────────────────────────────────────────

def write_raw_snapshot(payload: dict) -> None:
    write(RAW_SNAPSHOT_KEY, payload)


def read_raw_snapshot() -> Optional[dict]:
    return read(RAW_SNAPSHOT_KEY)


def write_pulse(payload: dict) -> None:
    write(PULSE_KEY, payload)


def read_pulse() -> Optional[dict]:
    """Last-good pulse with a fresh `stale` flag stamped on a shallow copy."""
    payload = read(PULSE_KEY)
    if payload is None:
        return None
    out: dict[str, Any] = dict(payload)
    out["stale"] = is_stale(payload)
    return out
