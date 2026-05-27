"""
llm_reasoning.py  —  Improvements #7 (Constrained Reasoning) + #8 (Confidence Engine)

LLM layer for QUALITATIVE financial reasoning only.
All mathematical computations are completed before this module is invoked.

Constraints:
- LLM may ONLY narrate pre-computed deterministic facts.
- LLM must NOT invent causality or generate unsupported claims.
- Every insight must be grounded in evidence provided in the prompt.
- Confidence is computed deterministically here, not by the LLM.
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
from .variation_reliability import ReliabilityResult, VariationReliability
from .materiality_engine import _NIIF_MAP

logger = logging.getLogger("financial_analyzer.llm_reasoning")

# Derive the valid NIIF/NIC whitelist from the standards this codebase actually
# handles — sourced from materiality_engine._NIIF_MAP and niif18_engine (NIIF 18).
# Never hardcode from external knowledge: the codebase IS the source of truth.
def _build_niif_whitelist() -> frozenset[str]:
    refs: set[str] = set()
    for category_map in _NIIF_MAP.values():
        for standards in category_map.values():
            refs.update(standards)
    refs.add("NIIF 18")  # from niif18_engine.py
    return frozenset(refs)

_VALID_NIIF_REFS: frozenset[str] = _build_niif_whitelist()

_PROMPT_S3_KEY = "instructions/prompts/02_prompt_agent_financial-analyzer.md"
_NIIF_REF_CAP = 8_000

_LOCAL_PROMPT_PATH = os.path.join(os.path.dirname(__file__), "prompts", "analyzer_prompt.txt")

try:
    with open(_LOCAL_PROMPT_PATH, encoding="utf-8") as _f:
        _LOCAL_FALLBACK_PROMPT = _f.read()
except FileNotFoundError:
    _LOCAL_FALLBACK_PROMPT = (
        "Eres un analista financiero NIIF experto. "
        "Tu rol es EXCLUSIVAMENTE narrativo: describes hechos ya calculados. "
        "NUNCA inventes causalidades. NUNCA generes riesgos no soportados por los datos. "
        "Devuelve ÚNICAMENTE JSON válido sin markdown."
    )

_system_prompt_cache: str | None = None


def _get_system_prompt() -> str:
    global _system_prompt_cache
    if _system_prompt_cache is None:
        _system_prompt_cache = load_text(_PROMPT_S3_KEY, fallback=_LOCAL_FALLBACK_PROMPT)
        source = "s3" if _system_prompt_cache != _LOCAL_FALLBACK_PROMPT else "local_fallback"
        logger.info("system_prompt_loaded | source=%s chars=%d", source, len(_system_prompt_cache))
    return _system_prompt_cache


# ── Result types ──────────────────────────────────────────────────────────────

@dataclass
class AccountLLMInsight:
    """Qualitative LLM output for a single account — narrative layer only."""
    account_id: str
    risk_level: str = "LOW"
    possible_causes: list[str] = field(default_factory=list)
    executive_insight: str = ""
    requires_niif_note: bool = False
    niif_note_references: list[str] = field(default_factory=list)
    anomaly_override: bool = False
    # Improvement #8: confidence signals from LLM (validated + clamped deterministically)
    llm_confidence_hint: float = 0.8       # LLM's self-reported confidence (0.0–1.0)
    llm_evidence_sources: list[str] = field(default_factory=list)
    # Related-party detection (NIC 24)
    is_related_party: bool = False
    related_party_counterpart: str | None = None
    # Dashboard signal for asset accounts
    investment_signal: str | None = None


@dataclass
class LLMAnalysisResult:
    """Parsed and validated full LLM response."""
    overall_financial_health: str = "STABLE"
    executive_narrative: str = ""
    niif_notes_required: list[str] = field(default_factory=list)
    account_insights: dict[str, AccountLLMInsight] = field(default_factory=dict)


# ── Deterministic confidence calculator ──────────────────────────────────────

def compute_confidence(
    variation: AccountVariation,
    reliability: ReliabilityResult,
    materiality: MaterialityLevel,
    anomaly_detected: bool,
    llm_hint: float = 0.8,
) -> tuple[float, int, list[str]]:
    """
    Improvement #8: Compute confidence score deterministically.
    Returns (confidence: float, evidence_count: int, evidence_sources: list[str]).

    The LLM hint is used as a soft signal — it's anchored to deterministic evidence
    to prevent the LLM from artificially inflating confidence on weak data.
    """
    evidence: list[str] = []
    base = 1.0

    # Reliability penalty
    if reliability.reliability == VariationReliability.NEW_ACCOUNT:
        base -= 0.30
        evidence.append("Nueva cuenta (sin período anterior)")
    elif reliability.reliability == VariationReliability.INSUFFICIENT_BASELINE:
        base -= 0.45
        evidence.append("Base insuficiente — variación % no confiable")
    elif reliability.reliability == VariationReliability.EXTREME_VARIATION:
        base -= 0.40
        evidence.append("Variación extrema — posible reclasificación contable")
    else:
        evidence.append("Variación confiable con período comparativo")

    # Materiality bonus: high-materiality accounts have more financial significance
    if materiality == MaterialityLevel.HIGH:
        base += 0.05
        evidence.append("Alta materialidad confirmada")
    elif materiality == MaterialityLevel.LOW:
        base -= 0.05

    # Anomaly evidence
    if anomaly_detected:
        base += 0.03
        evidence.append("Anomalía detectada deterministicamente")

    # Incorporate LLM hint as a bounded soft signal
    llm_adj = (min(max(llm_hint, 0.0), 1.0) - 0.8) * 0.10  # ±0.10 range
    base += llm_adj

    confidence = round(min(max(base, 0.30), 0.99), 2)
    return confidence, len(evidence), evidence


# ── Prompt builder ────────────────────────────────────────────────────────────

def _build_user_prompt(
    company_name: str,
    periods: list[str],
    business_context_snippet: str,
    ratios_dict: dict,
    threshold: float,
    variations: list[AccountVariation],
    materialities: dict[str, MaterialityLevel],
    reliabilities: dict[str, ReliabilityResult],
    niif_reference_text: str,
    causality_chains: list[dict] | None = None,
    earnings_quality: dict | None = None,
    concentration: dict | None = None,
    niif_validation_context: str | None = None,
    fund_analysis: dict | None = None,
) -> str:
    """
    Improvement #7: Constrained Financial Reasoning prompt.
    Deterministic facts are presented first. LLM is explicitly instructed to
    ONLY narrate these facts — never invent causality or unsupported claims.
    """
    accounts_payload = []
    for v in variations:
        rel = reliabilities.get(v.account_id)
        entry: dict = {
            "account_id": v.account_id,
            "account_name": v.account_name,
            "category": v.category,
            "current_value_cop_mm": v.current_value,
            "has_previous_period": v.has_previous_value,
            "materiality": materialities[v.account_id].value,
            "variation_reliability": rel.reliability.value if rel else "RELIABLE",
            "reliability_display": rel.display_label if rel else "",
        }
        if rel and not rel.suppress_pct and v.has_previous_value:
            entry["previous_value_cop_mm"] = v.previous_value
            entry["absolute_variation_cop_mm"] = v.absolute_variation
            entry["variation_pct"] = v.variation_pct
        else:
            entry["previous_value_cop_mm"] = None
            entry["absolute_variation_cop_mm"] = v.absolute_variation if v.has_previous_value else None
            entry["variation_pct_note"] = rel.display_label if rel else "Sin datos previos"
        accounts_payload.append(entry)

    niif_section = ""
    if niif_reference_text.strip():
        niif_section = (
            "\nREFERENCIA NORMATIVA NIIF (para decisiones de compliance):\n"
            f"{niif_reference_text[:_NIIF_REF_CAP]}\n"
        )

    extra_context = ""
    if niif_validation_context:
        extra_context += (
            f"\nVALIDACIÓN NIIF ESTRUCTURAL (determinístico — del DocumentExtractor):\n"
            f"{niif_validation_context}\n"
        )
    if earnings_quality:
        extra_context += (
            f"\nCALIDAD DE UTILIDADES (determinístico):\n"
            f"{json.dumps(earnings_quality, indent=2, ensure_ascii=False)}\n"
        )
    if concentration:
        extra_context += (
            f"\nCONCENTRACIÓN DE PORTAFOLIO (determinístico):\n"
            f"{json.dumps(concentration, indent=2, ensure_ascii=False)}\n"
        )
    if causality_chains:
        extra_context += (
            f"\nCADENAS DE CAUSALIDAD DETECTADAS (determinístico):\n"
            f"{json.dumps(causality_chains, indent=2, ensure_ascii=False)}\n"
        )
    if fund_analysis:
        # Include only the narrative-relevant fields; raw position list is too large
        fund_summary = {
            "is_investment_fund": fund_analysis.get("is_investment_fund"),
            "fund_type": fund_analysis.get("fund_type"),
            "fund_type_rationale": fund_analysis.get("fund_type_rationale"),
            "asset_breakdown_pct": fund_analysis.get("asset_breakdown_pct"),
            "cash_ratio": fund_analysis.get("cash_ratio"),
            "top1_position_pct": fund_analysis.get("top1_position_pct"),
            "top3_concentration_pct": fund_analysis.get("top3_concentration_pct"),
            "nav_reconciliation": fund_analysis.get("nav_reconciliation"),
            "net_investor_flow_cop_mm": fund_analysis.get("net_investor_flow_cop_mm"),
            "new_positions": [
                {"name": p["account_name"], "value": p["current_value"], "pct": p["pct_of_portfolio"]}
                for p in (fund_analysis.get("new_positions") or [])
            ],
            "closed_positions": [
                {"name": p["account_name"], "prev_value": p["previous_value"]}
                for p in (fund_analysis.get("closed_positions") or [])
            ],
            "top_positions": [
                {"name": p["account_name"], "value": p["current_value"],
                 "pct": p["pct_of_portfolio"], "status": p["status"], "asset_class": p["asset_class"]}
                for p in (fund_analysis.get("top_positions") or [])
            ],
            "insights": fund_analysis.get("insights"),
        }
        extra_context += (
            f"\nANÁLISIS DE FONDO DE INVERSIÓN (determinístico — fund_engine):\n"
            f"{json.dumps(fund_summary, indent=2, ensure_ascii=False)}\n"
        )

    constraint_block = """
══════════════════════════════════════════════════════
REGLAS DE RAZONAMIENTO RESTRINGIDO (OBLIGATORIO)
══════════════════════════════════════════════════════
1. HECHOS PRIMERO, NARRATIVA DESPUÉS.
   Solo puedes narrar datos ya calculados y provistos en este prompt.

2. VARIACIONES NO CONFIABLES.
   Si variation_reliability ≠ "RELIABLE", NO uses variation_pct como evidencia.
   En cambio, menciona el display_label (ej: "Nueva cuenta / sin período anterior").

3. SIN CAUSALIDADES INVENTADAS.
   Solo puedes establecer relaciones causales que estén en las "cadenas de causalidad
   detectadas" o que sean aritméticamente demostrables con los datos del prompt.

4. SIN CLASIFICACIÓN DE RIESGO FINAL.
   El campo risk_level es solo una señal financiera del analista.
   El scoring de riesgo definitivo es responsabilidad del RiskScorer Agent.

   overall_financial_health: elige el valor que MEJOR describe el estado financiero predominante.
   Valores disponibles: STABLE | DECLINING | GROWING | CRITICAL |
     LIQUID (caja sólida, baja deuda) |
     LEVERAGED (alto endeudamiento relativo) |
     SPECULATIVE (alta volatilidad, baja cobertura) |
     CASH_STRESSED (flujo operativo negativo o deteriorado) |
     VALUATION_DRIVEN (resultados dependen de ganancias por valorización no realizadas) |
     CONCENTRATED (alta concentración en pocas cuentas o emisores)
   Usa los nuevos valores cuando el análisis los justifique; de lo contrario usa los clásicos.

5. POSSIBLE_CAUSES: CAUSAS ESPECÍFICAS, NO REPETICIONES.
   Para cada cuenta, genera 2-3 causas CONCRETAS usando el contexto del fondo/empresa.
   PROHIBIDO: repetir la variación porcentual como causa (ej. "Variación de -40.7%...").
   PROHIBIDO: frases genéricas como "cambios en el mercado" sin datos específicos.
   OBLIGATORIO: referenciar datos del prompt (flujos de inversionistas, posiciones
   nuevas/cerradas, cadenas causales, ratios, eventos corporativos).

6. CONFIDENCE HONESTA.
   En llm_confidence_hint: usa 0.9–0.99 solo si tienes ≥3 evidencias concretas.
   Usa 0.5–0.7 para cuentas nuevas o con variaciones no confiables.
══════════════════════════════════════════════════════
"""

    return (
        f"EMPRESA: {company_name}\n"
        f"PERÍODOS ANALIZADOS: {', '.join(periods)}\n"
        f"CONTEXTO DEL NEGOCIO: {business_context_snippet[:600]}\n"
        f"{niif_section}"
        f"\nRATIOS FINANCIEROS GLOBALES (COP MM, determinísticos):\n"
        f"{json.dumps(ratios_dict, indent=2, ensure_ascii=False)}\n\n"
        f"UMBRAL DE MATERIALIDAD: {threshold:.2f} COP MM\n"
        f"{extra_context}"
        f"{constraint_block}\n"
        f"CUENTAS PARA ANÁLISIS ({len(accounts_payload)} cuentas):\n"
        f"{json.dumps(accounts_payload, indent=2, ensure_ascii=False)}\n"
    )


# ── LLM invocation ────────────────────────────────────────────────────────────

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=8))
def _invoke(system_prompt: str, user_prompt: str, llm: LLMProvider, tenant_id: str, job_id: str) -> dict:
    result = llm.generate_json(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        temperature=0.2,
        tenant_id=tenant_id,
        job_id=job_id,
    )
    return result if isinstance(result, dict) else {}


# ── Response parser ───────────────────────────────────────────────────────────

def _validate_niif_refs(refs: list) -> list[str]:
    """Drop any NIIF/NIC reference the LLM invented that isn't in the known standard set."""
    valid = [str(r).strip() for r in refs if str(r).strip() in _VALID_NIIF_REFS]
    dropped = len(refs) - len(valid)
    if dropped:
        logger.warning("niif_refs_dropped | count=%d hallucinated=%s", dropped, refs)
    return valid


def _parse_response(raw: dict) -> LLMAnalysisResult:
    """Parse and validate raw LLM dict into typed result. Never raises."""
    result = LLMAnalysisResult(
        overall_financial_health=str(raw.get("overall_financial_health", "STABLE")).upper(),
        executive_narrative=str(raw.get("executive_narrative", "")),
        niif_notes_required=_validate_niif_refs(list(raw.get("niif_notes_required", []))),
    )

    for item in raw.get("accounts_analysis", []):
        account_id = str(item.get("account_id", "")).strip()
        if not account_id:
            logger.warning("llm_parse_skip | missing account_id in item: %s", item)
            continue

        llm_conf = float(item.get("llm_confidence_hint", 0.8))
        llm_conf = max(0.0, min(1.0, llm_conf))

        counterpart_raw = item.get("related_party_counterpart")
        investment_signal_raw = item.get("investment_signal")

        result.account_insights[account_id] = AccountLLMInsight(
            account_id=account_id,
            risk_level=str(item.get("risk_level", "LOW")).upper(),
            possible_causes=list(item.get("possible_causes", [])),
            executive_insight=str(item.get("executive_insight", "")),
            requires_niif_note=bool(item.get("requires_niif_note", False)),
            niif_note_references=_validate_niif_refs(list(item.get("niif_note_references", []))),
            anomaly_override=bool(item.get("anomaly_override", False)),
            llm_confidence_hint=llm_conf,
            llm_evidence_sources=list(item.get("evidence_sources", [])),
            is_related_party=bool(item.get("is_related_party", False)),
            related_party_counterpart=str(counterpart_raw) if counterpart_raw else None,
            investment_signal=str(investment_signal_raw) if investment_signal_raw else None,
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
    reliabilities: dict[str, ReliabilityResult],
    llm: LLMProvider,
    tenant_id: str,
    job_id: str,
    niif_reference_text: str = "",
    causality_chains: list[dict] | None = None,
    earnings_quality: dict | None = None,
    concentration: dict | None = None,
    niif_validation_context: str | None = None,
    fund_analysis: dict | None = None,
) -> LLMAnalysisResult:
    """
    Full LLM analysis pipeline with constrained reasoning and reliability context.
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
        reliabilities=reliabilities,
        niif_reference_text=niif_reference_text,
        causality_chains=causality_chains,
        earnings_quality=earnings_quality,
        concentration=concentration,
        niif_validation_context=niif_validation_context,
        fund_analysis=fund_analysis,
    )

    logger.info(
        "llm_start | job=%s accounts=%d prompt_chars=%d",
        job_id, len(variations), len(user_prompt),
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
