"""
s3_instructions.py

Cached loader for instruction and prompt files stored in S3 under the instructions/ prefix.

Files are loaded once per Lambda container (cold start) and cached in memory.
Warm invocations reuse the cached value — no repeated S3 round-trips.
Falls back to the provided fallback string on any error so the pipeline never blocks.
"""

import logging
import os

import boto3

logger = logging.getLogger("shared.s3_instructions")

BUCKET = os.environ.get("MAIN_BUCKET", "")

# In-process cache: full S3 URI → file content
_cache: dict[str, str] = {}


def load_text(s3_key: str, bucket: str = "", *, fallback: str = "") -> str:
    """
    Load a UTF-8 text file from S3 and cache the result in memory.

    Args:
        s3_key:   S3 object key, e.g. "instructions/prompts/02_prompt_agent_financial-analyzer.md"
        bucket:   Override bucket name. Defaults to MAIN_BUCKET env var.
        fallback: Returned as-is when the S3 load fails (missing key, no credentials, etc.)

    Returns:
        File contents as a string, or `fallback` on any error.
    """
    effective_bucket = bucket or BUCKET
    cache_key = f"{effective_bucket}/{s3_key}"

    if cache_key in _cache:
        return _cache[cache_key]

    try:
        client = boto3.client("s3")
        body = client.get_object(Bucket=effective_bucket, Key=s3_key)["Body"].read()
        text = body.decode("utf-8")
        _cache[cache_key] = text
        logger.info("s3_instruction_loaded | key=%s chars=%d", s3_key, len(text))
        return text
    except Exception as exc:
        logger.warning("s3_instruction_load_failed | key=%s error=%s — using fallback", s3_key, exc)
        return fallback


def clear_cache() -> None:
    """Evict all cached entries. Intended for tests only."""
    _cache.clear()
