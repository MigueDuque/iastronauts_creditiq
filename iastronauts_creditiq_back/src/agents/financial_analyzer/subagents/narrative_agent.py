"""
Executive Narrative Agent — internal sub-agent of FinancialAnalyzer.

Converts structured financial intelligence into executive narratives.
Answers: "How do we communicate these conclusions effectively?"
"""

import logging
import os

from tenacity import retry, stop_after_attempt, wait_exponential

from shared.llm_provider import LLMProvider
from shared.s3_instructions import load_text

from ..contracts.causality_contracts import CausalityAnalysisResult
from ..contracts.movement_contracts import MovementIntelligenceResult
from ..contracts.narrative_contracts import ExecutiveNarrativeResult
from ..contracts.thesis_contracts import FinancialThesisResult

logger = logging.getLogger("financial_analyzer.narrative_agent")

_PROMPT_S3_KEY = "instructions/prompts/02d_prompt_subagent_narrative.md"
_LOCAL_PROMPT_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "system_pompts",
                 "02d_prompt_subagent_narrative.md")
)
_INLINE_FALLBACK = (
    "Eres Executive Narrative Agent. Convierte inteligencia financiera estructurada en narrativas "
    "ejecutivas institucionales. Genera executive_narrative, board_summary, investor_takeaways y "
    "niif_notes_required. Responde ÚNICAMENTE con JSON válido."
)

_prompt_cache: str | None = None
_MAX_TOKENS = 4000


def _get_prompt() -> str:
    global _prompt_cache
    if _prompt_cache is not None:
        return _prompt_cache
    s3 = load_text(_PROMPT_S3_KEY, fallback="")
    if s3:
        _prompt_cache = s3
        logger.info("narrative_prompt | source=s3 chars=%d", len(s3))
        return _prompt_cache
    try:
        with open(_LOCAL_PROMPT_PATH, encoding="utf-8") as f:
            local = f.read()
        if len(local) > 100:
            _prompt_cache = local
            logger.info("narrative_prompt | source=local chars=%d", len(local))
            return _prompt_cache
    except FileNotFoundError:
        pass
    _prompt_cache = _INLINE_FALLBACK
    logger.warning("narrative_prompt | source=inline_fallback")
    return _prompt_cache


def _build_prompt(
    company_name: str,
    periods: list[str],
    business_context_snippet: str,
    thesis_result: FinancialThesisResult,
    movement_result: MovementIntelligenceResult,
    causality_result: CausalityAnalysisResult,
) -> str:
    lines = [
        f"EMPRESA: {company_name} | PERÍODOS: {' vs '.join(periods)}",
        f"CONTEXTO: {business_context_snippet[:300]}",
        f"SALUD FINANCIERA: {thesis_result.overall_financial_health}",
        f"SOSTENIBILIDAD: {thesis_result.earnings_sustainability or 'N/A'}",
    ]

    if thesis_result.portfolio_thesis:
        lines.append(f"\nTESIS DE PORTAFOLIO:\n{thesis_result.portfolio_thesis[:600]}")

    if thesis_result.strategic_shifts:
        lines.append("\nCAMBIOS ESTRATÉGICOS:")
        for shift in thesis_result.strategic_shifts[:4]:
            lines.append(f"  [{shift.shift_type}] {shift.explanation}")

    if thesis_result.investment_implications:
        lines.append("\nIMPLICACIONES DE INVERSIÓN:")
        for impl in thesis_result.investment_implications[:4]:
            lines.append(f"  - {impl}")

    if causality_result.cross_account_dynamics:
        lines.append("\nDINÁMICAS CAUSALES RELEVANTES:")
        for d in causality_result.cross_account_dynamics[:3]:
            lines.append(f"  - {d.explanation}")

    tier1 = (thesis_result.insight_tiers or {}).get("tier1_critical", [])
    if tier1:
        lines.append("\nSEÑALES CRÍTICAS PARA LA JUNTA:")
        for alert in tier1[:3]:
            so_what = alert.get("so_what", "")
            signal = alert.get("signal", "")
            lines.append(f"  [{alert.get('category', '')}] {signal}: {so_what}")

    if movement_result.portfolio_rotations:
        lines.append("\nROTACIONES DE PORTAFOLIO:")
        for r in movement_result.portfolio_rotations[:2]:
            lines.append(f"  - {r.rationale}")

    lines.append(
        '\nINSTRUCCIÓN: Devuelve JSON con:\n'
        '{"executive_narrative":"(3-4 oraciones, nivel junta directiva)",'
        '"board_summary":"(más detallado que executive_narrative, nivel comité de inversiones)",'
        '"investor_takeaways":["...","...","..."],'
        '"niif_notes_required":["NIIF 9","NIC 28"],'
        '"narrative_layers":{"executive":"...","tactical":"...","technical":"..."}}\n'
        'ESTILO: Castellano institucional colombiano. Formal. Accionable. Sin jerga técnica excesiva.\n'
        'niif_notes_required: solo estándares NIIF/NIC reales. Omitir si no aplica.'
    )
    return "\n".join(lines)


@retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=2, max=6))
def _call_llm(system_prompt: str, user_prompt: str, llm: LLMProvider, tenant_id: str, job_id: str) -> dict:
    return llm.generate_json(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        temperature=0.3,
        tenant_id=tenant_id,
        job_id=job_id,
        max_tokens=_MAX_TOKENS,
    )


def _parse(raw: dict) -> ExecutiveNarrativeResult:
    nl_raw = raw.get("narrative_layers") or {}
    narrative_layers: dict = {}
    if isinstance(nl_raw, dict):
        narrative_layers = {
            "executive": str(nl_raw.get("executive", "")),
            "tactical": str(nl_raw.get("tactical", "")),
            "technical": str(nl_raw.get("technical", "")),
        }

    return ExecutiveNarrativeResult(
        executive_narrative=str(raw.get("executive_narrative", ""))[:2000],
        board_summary=str(raw.get("board_summary", ""))[:3000],
        investor_takeaways=[str(t)[:300] for t in raw.get("investor_takeaways", []) if t][:6],
        niif_notes_required=[str(n).strip() for n in raw.get("niif_notes_required", []) if n],
        narrative_layers=narrative_layers,
    )


def run_executive_narrative(
    company_name: str,
    periods: list[str],
    business_context_snippet: str,
    thesis_result: FinancialThesisResult,
    movement_result: MovementIntelligenceResult,
    causality_result: CausalityAnalysisResult,
    llm: LLMProvider,
    tenant_id: str,
    job_id: str,
) -> ExecutiveNarrativeResult:
    """Generate executive narrative from pre-computed intelligence. Returns empty result on failure."""
    try:
        system_prompt = _get_prompt()
        user_prompt = _build_prompt(
            company_name, periods, business_context_snippet,
            thesis_result, movement_result, causality_result,
        )
        logger.info("narrative_start | job=%s prompt_chars=%d", job_id, len(user_prompt))
        raw = _call_llm(system_prompt, user_prompt, llm, tenant_id, job_id)
        result = _parse(raw)
        logger.info("narrative_done | job=%s narrative_len=%d board_len=%d",
                    job_id, len(result.executive_narrative), len(result.board_summary))
        return result
    except Exception as exc:
        logger.error("narrative_failed | job=%s error=%s", job_id, exc)
        return ExecutiveNarrativeResult()
