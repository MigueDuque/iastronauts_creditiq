"""
llm_reasoning.py

LLM layer for qualitative financial reasoning.
Responsibility: ONLY qualitative analysis — causes, insights, risk levels, NIIF narratives.
All mathematical computations are completed before this module is invoked.

System prompt is loaded from S3 at first use and cached for the Lambda container lifetime.
The local prompts/analyzer_prompt.txt is the inline fallback when S3 is unavailable.
"""

import logging
import json
import os
from dataclasses import dataclass, field

from tenacity import retry, stop_after_attempt, wait_exponential

from shared.llm_provider import LLMProvider
from shared.models.base import MaterialityLevel
from shared.s3_instructions import load_text

from .ratio_engine import AccountVariation

logger = logging.getLogger("financial_analyzer.llm_reasoning")

# S3 key for this agent's prompt
_PROMPT_S3_KEY = "instructions/prompts/02_prompt_agent_financial-analyzer.md"

# Maximum characters of NIIF reference text to include in the user prompt
_NIIF_REF_CAP = 8_000

# ── Local fallback prompt ─────────────────────────────────────────────────────
# Used when the S3 file is not yet uploaded or S3 is unreachable.

_LOCAL_PROMPT_PATH = os.path.join(os.path.dirname(__file__), "prompts", "analyzer_prompt.txt")

try:
    with open(_LOCAL_PROMPT_PATH, encoding="utf-8") as _f:
        _LOCAL_FALLBACK_PROMPT = _f.read()
except FileNotFoundError:
    _LOCAL_FALLBACK_PROMPT = (
        "Eres un analista financiero NIIF. Devuelve ÚNICAMENTE JSON válido sin markdown."
    )

# Lazy-loaded system prompt — populated on first call, cached for container lifetime
_system_prompt_cache: str | None = None


def _get_system_prompt() -> str:
    """Return the system prompt, loading from S3 on first call."""
    global _system_prompt_cache
    if _system_prompt_cache is None:
        _system_prompt_cache = load_text(
            _PROMPT_S3_KEY,
            fallback=_LOCAL_FALLBACK_PROMPT,
        )
        source = "s3" if _system_prompt_cache != _LOCAL_FALLBACK_PROMPT else "local_fallback"
        logger.info("system_prompt_loaded | source=%s chars=%d", source, len(_system_prompt_cache))
    return _system_prompt_cache


# ── Result types ──────────────────────────────────────────────────────────────

@dataclass
class AccountLLMInsight:
    """Qualitative LLM output for a single account."""
    account_id: str
    risk_level: str = "LOW"
    possible_causes: list[str] = field(default_factory=list)
    executive_insight: str = ""
    requires_niif_note: bool = False
    niif_note_references: list[str] = field(default_factory=list)
    anomaly_override: bool = False


@dataclass
class LLMAnalysisResult:
    """Parsed and validated full LLM response."""
    overall_financial_health: str = "STABLE"
    executive_narrative: str = ""
    niif_notes_required: list[str] = field(default_factory=list)
    account_insights: dict[str, AccountLLMInsight] = field(default_factory=dict)


# ── Prompt builder ────────────────────────────────────────────────────────────

def _build_user_prompt(
    company_name: str,
    periods: list[str],
    business_context_snippet: str,
    ratios_dict: dict,
    threshold: float,
    variations: list[AccountVariation],
    materialities: dict[str, MaterialityLevel],
    niif_reference_text: str,
) -> str:
    """Build the structured user prompt from pre-computed math results and NIIF reference."""
    accounts_payload = [
        {
            "account_id": v.account_id,
            "account_name": v.account_name,
            "category": v.category,
            "current_value_cop_mm": v.current_value,
            "previous_value_cop_mm": v.previous_value if v.has_previous_value else None,
            "absolute_variation_cop_mm": v.absolute_variation,
            "variation_pct": v.variation_pct,
            "has_previous_period": v.has_previous_value,
            "materiality": materialities[v.account_id].value,
        }
        for v in variations
    ]

    niif_section = ""
    if niif_reference_text.strip():
        niif_section = (
            f"\nREFERENCIA NORMATIVA NIIF (usar para fundamentar decisiones de compliance):\n"
            f"{niif_reference_text[:_NIIF_REF_CAP]}\n"
        )

    return (
        f"EMPRESA: {company_name}\n"
        f"PERÍODOS ANALIZADOS: {', '.join(periods)}\n"
        f"CONTEXTO DEL NEGOCIO: {business_context_snippet[:600]}\n"
        f"{niif_section}\n"
        f"RATIOS FINANCIEROS GLOBALES (COP MM):\n"
        f"{json.dumps(ratios_dict, indent=2, ensure_ascii=False)}\n\n"
        f"UMBRAL DE MATERIALIDAD: {threshold:.2f} COP MM\n\n"
        f"CUENTAS PARA ANÁLISIS ({len(accounts_payload)} cuentas):\n"
        f"{json.dumps(accounts_payload, indent=2, ensure_ascii=False)}\n"
    )


# ── LLM invocation ────────────────────────────────────────────────────────────

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=8))
def _invoke(
    system_prompt: str,
    user_prompt: str,
    llm: LLMProvider,
    tenant_id: str,
    job_id: str,
) -> dict:
    """Call the LLM with automatic retry on transient errors."""
    result = llm.generate_json(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        temperature=0.2,
        tenant_id=tenant_id,
        job_id=job_id,
    )
    return result if isinstance(result, dict) else {}


# ── Response parser ───────────────────────────────────────────────────────────

def _parse_response(raw: dict) -> LLMAnalysisResult:
    """
    Parse and validate the raw LLM dict into typed LLMAnalysisResult.
    Missing or malformed fields fall back to safe defaults — never raises.
    """
    result = LLMAnalysisResult(
        overall_financial_health=str(raw.get("overall_financial_health", "STABLE")).upper(),
        executive_narrative=str(raw.get("executive_narrative", "")),
        niif_notes_required=list(raw.get("niif_notes_required", [])),
    )

    for item in raw.get("accounts_analysis", []):
        account_id = str(item.get("account_id", "")).strip()
        if not account_id:
            logger.warning("llm_parse_skip | missing account_id in item: %s", item)
            continue
        result.account_insights[account_id] = AccountLLMInsight(
            account_id=account_id,
            risk_level=str(item.get("risk_level", "LOW")).upper(),
            possible_causes=list(item.get("possible_causes", [])),
            executive_insight=str(item.get("executive_insight", "")),
            requires_niif_note=bool(item.get("requires_niif_note", False)),
            niif_note_references=list(item.get("niif_note_references", [])),
            anomaly_override=bool(item.get("anomaly_override", False)),
        )

    return result


# ── Public API ────────────────────────────────────────────────────────────────

def run_llm_analysis(
    company_name: str,
    periods: list[str],
    business_context_snippet: str,
    ratios_dict: dict,
    threshold: float,
    variations: list[AccountVariation],
    materialities: dict[str, MaterialityLevel],
    llm: LLMProvider,
    tenant_id: str,
    job_id: str,
    niif_reference_text: str = "",
) -> LLMAnalysisResult:
    """
    Full LLM analysis pipeline: load prompt → build user prompt → invoke with retry → parse.

    Args:
        niif_reference_text: Raw text of NIIF reference document(s) loaded from S3.
                             Injected into the user prompt so the LLM can ground its
                             NIIF compliance decisions in the actual standard text.

    Returns:
        LLMAnalysisResult with per-account insights and overall narrative.
        On total failure (all retries exhausted), returns a safe empty result.
    """
    system_prompt = _get_system_prompt()
    user_prompt = _build_user_prompt(
        company_name=company_name,
        periods=periods,
        business_context_snippet=business_context_snippet,
        ratios_dict=ratios_dict,
        threshold=threshold,
        variations=variations,
        materialities=materialities,
        niif_reference_text=niif_reference_text,
    )

    logger.info(
        "llm_start | job=%s accounts=%d prompt_chars=%d niif_ref_chars=%d",
        job_id, len(variations), len(user_prompt), len(niif_reference_text),
    )

    try:
        raw = _invoke(system_prompt, user_prompt, llm, tenant_id, job_id)
    except Exception as exc:
        logger.error("llm_failed | job=%s error=%s", job_id, exc)
        return LLMAnalysisResult()

    result = _parse_response(raw)

    logger.info(
        "llm_done | job=%s health=%s insights=%d niif_notes=%d",
        job_id, result.overall_financial_health,
        len(result.account_insights), len(result.niif_notes_required),
    )
    return result
