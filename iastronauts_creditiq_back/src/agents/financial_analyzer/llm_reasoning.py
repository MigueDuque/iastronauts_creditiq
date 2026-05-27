"""
llm_reasoning.py — Multi-agent LLM pipeline for FinancialAnalyzer.

Replaces a single overloaded LLM call with 4 focused internal sub-agents:
  1. Movement Intelligence  — "What happened?"
  2. Causality Agent        — "Why did it happen?"
  3. Financial Thesis       — "What does this mean strategically?"
  4. Executive Narrative    — "How do we communicate the conclusions?"

External contract (run_llm_analysis signature and LLMAnalysisResult type) is
unchanged — all callers in service.py continue to work without modification.
"""

import logging
from dataclasses import dataclass, field

from shared.llm_provider import LLMProvider
from shared.models.base import MaterialityLevel

from .ratio_engine import AccountVariation
from .variation_reliability import ReliabilityResult, VariationReliability
from .materiality_engine import _NIIF_MAP

from .subagents.movement_intelligence import run_movement_intelligence
from .subagents.causality_agent import run_causality_analysis
from .subagents.thesis_agent import run_financial_thesis
from .subagents.narrative_agent import run_executive_narrative

logger = logging.getLogger("financial_analyzer.llm_reasoning")


# ── NIIF whitelist (sourced from codebase, never hardcoded from external knowledge) ──

def _build_niif_whitelist() -> frozenset[str]:
    refs: set[str] = set()
    for category_map in _NIIF_MAP.values():
        for standards in category_map.values():
            refs.update(standards)
    refs.add("NIIF 18")
    return frozenset(refs)

_VALID_NIIF_REFS: frozenset[str] = _build_niif_whitelist()


# ── Result types (public contracts used by service.py) ───────────────────────

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
    llm_confidence_hint: float = 0.8
    llm_evidence_sources: list[str] = field(default_factory=list)
    is_related_party: bool = False
    related_party_counterpart: str | None = None
    investment_signal: str | None = None


@dataclass
class LLMAnalysisResult:
    """Full qualitative analysis result assembled from the 4 sub-agent pipeline."""
    overall_financial_health: str = "STABLE"
    executive_narrative: str = ""
    niif_notes_required: list[str] = field(default_factory=list)
    account_insights: dict[str, AccountLLMInsight] = field(default_factory=dict)
    # Executive intelligence layer
    portfolio_thesis: str = ""
    insight_tiers: dict = field(default_factory=dict)
    narrative_layers: dict = field(default_factory=dict)
    # Institutional analysis layer
    structured_analysis: dict = field(default_factory=dict)
    cross_statement_signals: list[dict] = field(default_factory=list)
    earnings_sustainability: str = ""


# ── Deterministic confidence calculator (used by service.py) ─────────────────

def compute_confidence(
    variation: AccountVariation,
    reliability: ReliabilityResult,
    materiality: MaterialityLevel,
    anomaly_detected: bool,
    llm_hint: float = 0.8,
) -> tuple[float, int, list[str]]:
    """
    Compute confidence score deterministically.
    LLM hint is anchored to deterministic evidence to prevent artificial inflation.
    Returns (confidence, evidence_count, evidence_sources).
    """
    evidence: list[str] = []
    base = 1.0

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

    if materiality == MaterialityLevel.HIGH:
        base += 0.05
        evidence.append("Alta materialidad confirmada")
    elif materiality == MaterialityLevel.LOW:
        base -= 0.05

    if anomaly_detected:
        base += 0.03
        evidence.append("Anomalía detectada deterministicamente")

    llm_adj = (min(max(llm_hint, 0.0), 1.0) - 0.8) * 0.10
    base += llm_adj

    confidence = round(min(max(base, 0.30), 0.99), 2)
    return confidence, len(evidence), evidence


# ── NIIF reference validator ──────────────────────────────────────────────────

def _validate_niif_refs(refs: list) -> list[str]:
    """Drop any NIIF/NIC reference not in the known standard set."""
    valid = [str(r).strip() for r in refs if str(r).strip() in _VALID_NIIF_REFS]
    dropped = len(refs) - len(valid)
    if dropped:
        logger.warning("niif_refs_dropped | count=%d hallucinated=%s", dropped, refs)
    return valid


# ── Result assembly ───────────────────────────────────────────────────────────

_HIGH_MAT = MaterialityLevel.HIGH
_HIGH_PRIORITY = {MaterialityLevel.HIGH, MaterialityLevel.MEDIUM}


def _assemble_result(
    movement_result,
    causality_result,
    thesis_result,
    narrative_result,
    variations: list[AccountVariation],
    materialities: dict[str, MaterialityLevel],
) -> LLMAnalysisResult:
    """
    Assemble LLMAnalysisResult from the 4 sub-agent outputs.

    account_insights are built from causality results, enriched with movement signals.
    Fields not produced by sub-agents default to safe values; service.py applies
    ceiling rules and deterministic overrides on top of these.
    """
    global_niif_refs = _validate_niif_refs(narrative_result.niif_notes_required)

    high_mat_ids = {
        v.account_id for v in variations
        if materialities.get(v.account_id, MaterialityLevel.LOW) == _HIGH_MAT
    }

    movement_map = {m.account_id: m for m in movement_result.key_movements}

    account_insights: dict[str, AccountLLMInsight] = {}

    # Build insights from causality agent output
    for caus in causality_result.account_causality:
        aid = caus.account_id
        movement = movement_map.get(aid)
        is_high = aid in high_mat_ids

        account_insights[aid] = AccountLLMInsight(
            account_id=aid,
            risk_level="LOW",           # ceiling rule applied by service.py
            possible_causes=caus.possible_causes,
            executive_insight=caus.executive_insight or (movement.summary if movement else ""),
            requires_niif_note=is_high,
            niif_note_references=global_niif_refs if is_high else [],
            llm_confidence_hint=caus.confidence,
            llm_evidence_sources=caus.linked_accounts,
            investment_signal=movement.movement_type if movement else None,
        )

    # Ensure HIGH materiality accounts always have an entry (may not have causality data)
    for aid in high_mat_ids:
        if aid not in account_insights:
            movement = movement_map.get(aid)
            account_insights[aid] = AccountLLMInsight(
                account_id=aid,
                risk_level="LOW",
                possible_causes=[movement.summary] if movement else [],
                executive_insight=movement.summary if movement else "",
                requires_niif_note=True,
                niif_note_references=global_niif_refs,
                llm_confidence_hint=0.65,
                investment_signal=movement.movement_type if movement else None,
            )

    # Merge narrative_layers: use sub-agent output; board_summary goes into "board" key
    narrative_layers = dict(narrative_result.narrative_layers)
    if narrative_result.board_summary:
        narrative_layers["board"] = narrative_result.board_summary

    return LLMAnalysisResult(
        overall_financial_health=thesis_result.overall_financial_health,
        executive_narrative=narrative_result.executive_narrative,
        niif_notes_required=global_niif_refs,
        account_insights=account_insights,
        portfolio_thesis=thesis_result.portfolio_thesis,
        insight_tiers=thesis_result.insight_tiers,
        narrative_layers=narrative_layers,
        structured_analysis=thesis_result.structured_analysis,
        cross_statement_signals=thesis_result.cross_statement_signals,
        earnings_sustainability=thesis_result.earnings_sustainability,
    )


# ── Public API (backward-compatible) ─────────────────────────────────────────

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
    macro_context: dict | None = None,
    executive_synthesis: dict | None = None,
    financial_diagnostics: dict | None = None,
) -> LLMAnalysisResult:
    """
    Multi-agent LLM pipeline for qualitative financial reasoning.

    Runs 4 focused sub-agents sequentially:
      Movement → Causality → Thesis → Narrative

    Each sub-agent operates on a compact context digest rather than the full
    raw data payload, which was the root cause of context-window saturation
    and empty narrative fields in the previous single-call architecture.

    On any sub-agent failure the pipeline continues with an empty result for
    that step — the assembly stage handles missing data gracefully.
    """
    logger.info(
        "llm_pipeline_start | job=%s accounts=%d high_medium=%d",
        job_id,
        len(variations),
        sum(1 for v in variations if materialities.get(v.account_id, MaterialityLevel.LOW) in _HIGH_PRIORITY),
    )

    # Step 1 — Movement Intelligence: what happened?
    movement_result = run_movement_intelligence(
        company_name=company_name,
        periods=periods,
        variations=variations,
        materialities=materialities,
        llm=llm,
        tenant_id=tenant_id,
        job_id=job_id,
        fund_analysis=fund_analysis,
        executive_synthesis=executive_synthesis,
        concentration=concentration,
    )

    # Step 2 — Causality: why did it happen?
    causality_result = run_causality_analysis(
        company_name=company_name,
        periods=periods,
        variations=variations,
        materialities=materialities,
        reliabilities=reliabilities,
        movement_result=movement_result,
        llm=llm,
        tenant_id=tenant_id,
        job_id=job_id,
        causality_chains=causality_chains,
        fund_analysis=fund_analysis,
        business_context_snippet=business_context_snippet,
        macro_context=macro_context,
    )

    # Step 3 — Financial Thesis: what does this mean strategically?
    thesis_result = run_financial_thesis(
        company_name=company_name,
        periods=periods,
        movement_result=movement_result,
        causality_result=causality_result,
        llm=llm,
        tenant_id=tenant_id,
        job_id=job_id,
        executive_synthesis=executive_synthesis,
        earnings_quality=earnings_quality,
        concentration=concentration,
        ratios_dict=ratios_dict,
        macro_context=macro_context,
        financial_diagnostics=financial_diagnostics,
    )

    # Step 4 — Executive Narrative: communicate the conclusions
    narrative_result = run_executive_narrative(
        company_name=company_name,
        periods=periods,
        business_context_snippet=business_context_snippet,
        thesis_result=thesis_result,
        movement_result=movement_result,
        causality_result=causality_result,
        llm=llm,
        tenant_id=tenant_id,
        job_id=job_id,
    )

    result = _assemble_result(
        movement_result=movement_result,
        causality_result=causality_result,
        thesis_result=thesis_result,
        narrative_result=narrative_result,
        variations=variations,
        materialities=materialities,
    )

    logger.info(
        "llm_pipeline_done | job=%s health=%s insights=%d niif_notes=%d "
        "movements=%d narrative_len=%d thesis_len=%d",
        job_id,
        result.overall_financial_health,
        len(result.account_insights),
        len(result.niif_notes_required),
        len(movement_result.key_movements),
        len(result.executive_narrative),
        len(result.portfolio_thesis),
    )
    return result
