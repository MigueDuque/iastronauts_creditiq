"""
fund_policy_loader.py

Loads tenant-specific fund policy documents from S3 and uses the LLM to extract
structured numeric limits into a FundPolicy model.

S3 layout:
    rag/{tenant_id}/fund_policies/{fund_slug}/
        - regulation.md / regulation.pdf (fund regulation)
        - investment_policy.md           (investment policy document)
        - risk_policy.md                 (risk management policy)
        - limits.md / limits.txt         (explicit limit schedules)

When one or more policy docs are found, the LLM extracts per-issuer, per-sector,
per-asset-class, and per-counterparty percentage limits into the FundPolicy model
and sets source = "policy_document". When no docs are found the caller falls back
to the sensible SFC Colombia defaults in fund_policy.py.

Never raises — a failed load or extraction returns None so the caller can fall
back to defaults gracefully.

Three-tier fallback mirrors niif_notes.py:
    S3  →  (no local fallback for policy docs — they are tenant-private)  →  None
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

logger = logging.getLogger("shared.fund_policy_loader")

# Maximum total characters read from policy docs (stay within LLM context budget)
_MAX_POLICY_CHARS: int = 8_000
_S3_PREFIX_TEMPLATE = "rag/{tenant_id}/fund_policies/"

# LLM system prompt for limit extraction
_EXTRACTION_SYSTEM = (
    "You are a regulatory compliance analyst. Extract investment concentration limits "
    "from the provided fund policy document. Return ONLY valid JSON, no markdown, "
    "no explanation."
)

_EXTRACTION_SCHEMA = """{
  "per_issuer_pct":       <number | null>,  // max % in a single issuer
  "per_sector_pct":       <number | null>,  // max % in a single sector
  "per_asset_class_pct":  <number | null>,  // max % in a single asset class
  "per_counterparty_pct": <number | null>   // max % with a single counterparty/custodian
}"""


def _s3_prefix(tenant_id: str) -> str:
    return _S3_PREFIX_TEMPLATE.format(tenant_id=tenant_id)


def load_policy_text(
    tenant_id: str,
    s3_client: Any,
    bucket: str,
) -> str | None:
    """
    Download and concatenate all text objects under rag/{tenant_id}/fund_policies/.
    Returns the combined text (capped at _MAX_POLICY_CHARS) or None when the
    prefix is empty or on any S3 error.

    Callers must assert_s3_key() before calling this function when operating
    inside a tenant-boundary context.
    """
    if not tenant_id or not s3_client or not bucket:
        return None

    prefix = _s3_prefix(tenant_id)
    try:
        resp = s3_client.list_objects_v2(Bucket=bucket, Prefix=prefix)
        objects = resp.get("Contents") or []
    except Exception as exc:
        logger.info("fund_policy_loader | list failed tenant=%s: %s", tenant_id, exc)
        return None

    if not objects:
        logger.info("fund_policy_loader | no policy docs found tenant=%s prefix=%s", tenant_id, prefix)
        return None

    parts: list[str] = []
    total = 0
    for obj in objects:
        key = obj.get("Key", "")
        # Only read text-based formats; skip binaries we can't decode cheaply
        if not any(key.endswith(ext) for ext in (".md", ".txt", ".json", ".csv")):
            continue
        try:
            body = s3_client.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8", errors="replace")
            header = f"\n\n--- {os.path.basename(key)} ---\n"
            chunk = (header + body)[: _MAX_POLICY_CHARS - total]
            parts.append(chunk)
            total += len(chunk)
            if total >= _MAX_POLICY_CHARS:
                break
        except Exception as exc:
            logger.warning("fund_policy_loader | read failed key=%s: %s", key, exc)

    if not parts:
        return None

    combined = "".join(parts)
    logger.info(
        "fund_policy_loader | loaded tenant=%s docs=%d chars=%d",
        tenant_id, len(parts), len(combined),
    )
    return combined


def extract_limits_from_text(
    policy_text: str,
    fund_type: str,
    llm_provider: Any,
    tenant_id: str = "",
    job_id: str = "",
) -> dict[str, float | None]:
    """
    Use the LLM to extract concentration limits from raw policy text.
    Returns a dict with keys: per_issuer_pct, per_sector_pct,
    per_asset_class_pct, per_counterparty_pct (float or None when not found).
    Returns an empty dict on any LLM error.
    """
    if not policy_text or not llm_provider:
        return {}

    user_prompt = (
        f"Fund type: {fund_type}\n\n"
        f"Policy document (extract concentration limits from this):\n"
        f"{policy_text[:_MAX_POLICY_CHARS]}\n\n"
        f"Return only the JSON object matching this schema:\n{_EXTRACTION_SCHEMA}"
    )
    try:
        result = llm_provider.generate_json(
            system_prompt=_EXTRACTION_SYSTEM,
            user_prompt=user_prompt,
            temperature=0.0,
            tenant_id=tenant_id,
            job_id=job_id,
            max_tokens=512,
        )
        if not isinstance(result, dict):
            logger.warning("fund_policy_loader | LLM returned non-dict: %s", type(result))
            return {}
        # Normalise: coerce to float or None
        out: dict[str, float | None] = {}
        for key in ("per_issuer_pct", "per_sector_pct", "per_asset_class_pct", "per_counterparty_pct"):
            val = result.get(key)
            if val is None:
                out[key] = None
            else:
                try:
                    out[key] = float(val)
                except (TypeError, ValueError):
                    # Try to parse "35%" → 35.0
                    if isinstance(val, str):
                        m = re.search(r"[\d.]+", val)
                        out[key] = float(m.group()) if m else None
                    else:
                        out[key] = None
        logger.info(
            "fund_policy_loader | extracted limits tenant=%s: %s",
            tenant_id, {k: v for k, v in out.items() if v is not None},
        )
        return out
    except Exception as exc:
        logger.warning("fund_policy_loader | LLM extraction failed: %s", exc)
        return {}


def load_fund_policy(
    tenant_id: str,
    fund_type: str,
    s3_client: Any,
    bucket: str,
    llm_provider: Any,
    job_id: str = "",
):
    """
    High-level loader: load policy text from S3, extract limits, return a FundPolicy
    with source = 'policy_document' if any limits were found, else return None.

    Returns:
        FundPolicy with source='policy_document' when at least one limit was extracted.
        None when no policy docs exist or extraction yields nothing (caller uses defaults).
    """
    from shared.models.fund_policy import FundPolicy, get_default_policy

    policy_text = load_policy_text(tenant_id, s3_client, bucket)
    if not policy_text:
        return None

    limits = extract_limits_from_text(
        policy_text, fund_type, llm_provider, tenant_id=tenant_id, job_id=job_id
    )
    if not limits or all(v is None for v in limits.values()):
        logger.info(
            "fund_policy_loader | no limits extracted — using defaults tenant=%s", tenant_id
        )
        return None

    # Start from defaults, override with any extracted values
    default = get_default_policy(fund_type)
    return FundPolicy(
        fund_type=fund_type,
        per_issuer_pct=limits.get("per_issuer_pct") or default.per_issuer_pct,
        per_sector_pct=limits.get("per_sector_pct") or default.per_sector_pct,
        per_asset_class_pct=limits.get("per_asset_class_pct") or default.per_asset_class_pct,
        per_counterparty_pct=limits.get("per_counterparty_pct") or default.per_counterparty_pct,
        source="policy_document",
        notes=f"Limits extracted from tenant policy documents. Fund type: {fund_type}.",
    )


def extract_policy_clauses(policy_text: str, max_chars: int = 2_000) -> str:
    """
    Return the most policy-relevant passages from the raw policy text for injection
    into LLM prompts as evidence. Heuristic: paragraphs mentioning limit keywords.
    """
    if not policy_text:
        return ""
    limit_keywords = (
        "límite", "limite", "máximo", "maximo", "porcentaje", "%",
        "concentración", "concentracion", "emisor", "sector", "contraparte",
        "custodio", "restricción", "restriccion",
    )
    paragraphs = [p.strip() for p in policy_text.split("\n\n") if p.strip()]
    relevant = [
        p for p in paragraphs
        if any(kw in p.lower() for kw in limit_keywords)
    ]
    combined = "\n\n".join(relevant)
    return combined[:max_chars]
