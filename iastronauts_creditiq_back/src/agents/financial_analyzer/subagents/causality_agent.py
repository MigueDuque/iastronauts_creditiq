"""
Causality Agent — internal sub-agent of FinancialAnalyzer.

Explains WHY financial movements occurred.
Answers: "Why did it happen?"
"""

import json
import logging
import os

from tenacity import retry, stop_after_attempt, wait_exponential

from shared.llm_provider import LLMProvider
from shared.models.base import MaterialityLevel
from shared.s3_instructions import load_text

from ..ratio_engine import AccountVariation
from ..variation_reliability import ReliabilityResult, VariationReliability
from ..contracts.causality_contracts import (
    AccountCausality,
    CausalityAnalysisResult,
    CrossAccountDynamic,
)
from ..contracts.movement_contracts import MovementIntelligenceResult

logger = logging.getLogger("financial_analyzer.causality_agent")

_PROMPT_S3_KEY = "instructions/prompts/02b_prompt_subagent_causality.md"
_LOCAL_PROMPT_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "system_pompts",
                 "02b_prompt_subagent_causality.md")
)
_INLINE_FALLBACK = (
    "Eres Causality Agent. Explica POR QUÉ ocurrieron los movimientos financieros. "
    "Genera causas específicas y estratégicas — nunca repitas porcentajes. "
    "Responde ÚNICAMENTE con JSON válido que contenga account_causality y cross_account_dynamics."
)

_prompt_cache: str | None = None
_MAX_ACCOUNTS = 10
_MAX_TOKENS = 6000
_HIGH_PRIORITY = {MaterialityLevel.HIGH, MaterialityLevel.MEDIUM}


def _get_prompt() -> str:
    global _prompt_cache
    if _prompt_cache is not None:
        return _prompt_cache
    s3 = load_text(_PROMPT_S3_KEY, fallback="")
    if s3:
        _prompt_cache = s3
        logger.info("causality_prompt | source=s3 chars=%d", len(s3))
        return _prompt_cache
    try:
        with open(_LOCAL_PROMPT_PATH, encoding="utf-8") as f:
            local = f.read()
        if len(local) > 100:
            _prompt_cache = local
            logger.info("causality_prompt | source=local chars=%d", len(local))
            return _prompt_cache
    except FileNotFoundError:
        pass
    _prompt_cache = _INLINE_FALLBACK
    logger.warning("causality_prompt | source=inline_fallback")
    return _prompt_cache


def _build_prompt(
    company_name: str,
    periods: list[str],
    variations: list[AccountVariation],
    materialities: dict[str, MaterialityLevel],
    reliabilities: dict[str, ReliabilityResult],
    movement_result: MovementIntelligenceResult,
    causality_chains: list[dict] | None,
    fund_analysis: dict | None,
    business_context_snippet: str,
    macro_context: dict | None,
    policy_clauses: str = "",
    comparative_basis: dict | None = None,
) -> str:
    top_accounts = sorted(
        [v for v in variations if materialities.get(v.account_id, MaterialityLevel.LOW) in _HIGH_PRIORITY],
        key=lambda v: abs(v.absolute_variation) if v.has_previous_value else abs(v.current_value),
        reverse=True,
    )[:_MAX_ACCOUNTS]

    lines = [
        f"EMPRESA: {company_name} | PERÍODOS: {' vs '.join(periods)}",
        f"CONTEXTO: {business_context_snippet[:300]}",
    ]

    # Period-homogeneity warning — critical for not misinterpreting flow accounts
    if comparative_basis:
        is_basis = comparative_basis.get("income_statement", {})
        cf_basis = comparative_basis.get("cash_flow", {})
        if not is_basis.get("periods_homogeneous", True) or not cf_basis.get("periods_homogeneous", True):
            curr_m = is_basis.get("current_period_months") or cf_basis.get("current_period_months", "?")
            comp_m = is_basis.get("comparative_period_months") or cf_basis.get("comparative_period_months", "?")
            factor = is_basis.get("annualization_factor") or cf_basis.get("annualization_factor", "?")
            lines.append(
                f"\n⚠️  ADVERTENCIA CRÍTICA DE PERÍODO: El estado de resultados y flujo de caja "
                f"cubren {curr_m} meses; el período comparativo cubre {comp_m} meses. "
                f"Las cuentas marcadas [WARN:Períodos no homogéneos] NO representan cambios reales "
                f"de actividad — la variación refleja la diferencia de duración. "
                f"Factor de anualización: {factor}x (valor actual × {factor} = equivalente anual). "
                f"OBLIGATORIO: Para estas cuentas, NO uses la variación % como evidencia de tendencia. "
                f"Usa el valor absoluto actual y el factor de anualización como contexto."
            )

    # Movement intelligence output
    if movement_result.key_movements:
        lines.append("\nMOVIMIENTOS DETECTADOS:")
        for m in movement_result.key_movements[:_MAX_ACCOUNTS]:
            lines.append(f"  [{m.account_id}] {m.summary} (tipo={m.movement_type})")
    if movement_result.portfolio_rotations:
        lines.append("ROTACIONES DE PORTAFOLIO:")
        for r in movement_result.portfolio_rotations:
            lines.append(f"  De {r.from_assets} → {r.to_assets}: {r.rationale}")
    if movement_result.suspicious_patterns:
        lines.append("PATRONES SOSPECHOSOS: " + " | ".join(movement_result.suspicious_patterns[:3]))

    # Deterministic causality chains
    if causality_chains:
        lines.append("\nCADENAS DE CAUSALIDAD DETECTADAS (determinístico):")
        for chain in causality_chains[:5]:
            lines.append(f"  {json.dumps(chain, ensure_ascii=False)}")

    # Fund context
    if fund_analysis and fund_analysis.get("is_investment_fund"):
        net_flow = fund_analysis.get("net_investor_flow_cop_mm", 0) or 0
        new_pos = fund_analysis.get("new_positions") or []
        closed_pos = fund_analysis.get("closed_positions") or []
        lines.append(
            f"\nCONTEXTO FONDO: flujo_neto={net_flow:+.0f} COP MM | "
            f"nuevas={len(new_pos)} | cerradas={len(closed_pos)}"
        )
        if new_pos:
            lines.append("  NUEVAS: " + ", ".join(p.get("name", "") for p in new_pos[:3]))
        if closed_pos:
            lines.append("  CERRADAS: " + ", ".join(p.get("name", "") for p in closed_pos[:3]))

    # Fund policy clauses (from tenant policy document — Sprint 3 Item 8)
    if policy_clauses:
        lines.append(
            "\nPOLÍTICA DE INVERSIÓN (RESTRICCIONES REGULATORIAS):\n"
            + policy_clauses[:1_500]
            + "\nINSTRUCCIÓN: Usa estas cláusulas como evidencia de tipo 'policy' (ref=cláusula) "
            "cuando identifiques que una posición respeta o supera un límite definido."
        )

    # Compact macro context
    if macro_context:
        macro_signals = macro_context.get("macro_signals", [])
        macro_ctx = macro_context.get("macro_context", {})
        market_ctx = macro_context.get("market_context", {})
        macro_lines = []
        if macro_signals:
            macro_lines.append("señales: " + " | ".join(str(s) for s in macro_signals[:4]))
        rate_env = macro_ctx.get("interest_rate_environment", "") or market_ctx.get("rate_environment", "")
        if rate_env:
            macro_lines.append(f"tasas: {rate_env}")
        if macro_lines:
            lines.append("\nCONTEXTO MACRO: " + " | ".join(macro_lines))

    # Account list with reliability flags
    lines.append(f"\nCUENTAS A ANALIZAR ({len(top_accounts)}):")
    for v in top_accounts:
        mat = materialities.get(v.account_id, MaterialityLevel.LOW).value.upper()
        rel = reliabilities.get(v.account_id)
        rel_flag = ""
        if rel and rel.reliability != VariationReliability.RELIABLE:
            rel_flag = f" [WARN:{rel.display_label}]"
        if v.has_previous_value:
            lines.append(
                f"  [{v.account_id}] {v.account_name}: "
                f"{v.previous_value:.0f} → {v.current_value:.0f} COP MM "
                f"(Δ{v.absolute_variation:+.0f}) [MAT={mat}]{rel_flag}"
            )
        else:
            status = "NUEVA" if v.current_value > 0 else "CERRADA"
            lines.append(
                f"  [{v.account_id}] {v.account_name}: {status} {v.current_value:.0f} COP MM [MAT={mat}]{rel_flag}"
            )

    lines.append(
        '\nINSTRUCCIÓN — EVIDENCIA PRIMERO (Evidence First):\n'
        'Cada causa debe estar respaldada por evidencia concreta. '
        'Si no existe evidencia suficiente, escribe EXACTAMENTE: '
        '"No existe evidencia suficiente para determinar la causa de esta variación." '
        'NUNCA inventes causas especulativas sin evidencia.\n\n'
        'Devuelve JSON con:\n'
        '{"account_causality":[{"account_id":"...","possible_causes":["causa1 (evidencia: [account_id])"],'
        '"executive_insight":"...","linked_accounts":["act-001"],"confidence":0.8,'
        '"evidence":[{"claim":"causa estratégica","evidence_type":"account","ref":"act-001"}]}],'
        '"cross_account_dynamics":[{"explanation":"...","impacted_accounts":[],"confidence":0.7}]}\n\n'
        'REGLAS:\n'
        '- evidence_type: "account" (ref=account_id) | "variation" (ref=account_id) | '
        '"news" (ref=titular) | "policy" (ref=cláusula) | "note" (ref=NIIF estándar)\n'
        '- ref SIEMPRE debe identificar el dato concreto que sustenta la causa.\n'
        '- possible_causes DEBEN ser causas estratégicas específicas (2-3 por cuenta).\n'
        '- PROHIBIDO repetir porcentajes crudos. OBLIGATORIO referenciar emisores, flujos o contexto.'
    )
    return "\n".join(lines)


@retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=2, max=6))
def _call_llm(system_prompt: str, user_prompt: str, llm: LLMProvider, tenant_id: str, job_id: str) -> dict:
    return llm.generate_json(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        temperature=0.2,
        tenant_id=tenant_id,
        job_id=job_id,
        max_tokens=_MAX_TOKENS,
    )


def _parse(raw: dict) -> CausalityAnalysisResult:
    account_causality: list[AccountCausality] = []
    for item in raw.get("account_causality", []):
        if not isinstance(item, dict) or not item.get("account_id"):
            continue
        try:
            causes = [str(c)[:300] for c in item.get("possible_causes", []) if c][:3]
            evidence = []
            for e in item.get("evidence", []):
                if not isinstance(e, dict):
                    continue
                claim = str(e.get("claim", ""))[:300]
                ev_type = str(e.get("evidence_type", ""))
                ref = str(e.get("ref", ""))[:200]
                if claim and ev_type:
                    evidence.append({"claim": claim, "evidence_type": ev_type, "ref": ref})
            account_causality.append(AccountCausality(
                account_id=str(item["account_id"]),
                possible_causes=causes,
                executive_insight=str(item.get("executive_insight", ""))[:350],
                linked_accounts=[str(a) for a in item.get("linked_accounts", []) if a],
                confidence=min(max(float(item.get("confidence", 0.8)), 0.0), 1.0),
                evidence=evidence,
            ))
        except Exception:
            continue

    dynamics: list[CrossAccountDynamic] = []
    for item in raw.get("cross_account_dynamics", []):
        if not isinstance(item, dict):
            continue
        try:
            dynamics.append(CrossAccountDynamic(
                explanation=str(item.get("explanation", ""))[:400],
                impacted_accounts=[str(a) for a in item.get("impacted_accounts", [])],
                confidence=min(max(float(item.get("confidence", 0.7)), 0.0), 1.0),
            ))
        except Exception:
            continue

    return CausalityAnalysisResult(
        account_causality=account_causality,
        cross_account_dynamics=dynamics,
    )


def run_causality_analysis(
    company_name: str,
    periods: list[str],
    variations: list[AccountVariation],
    materialities: dict[str, MaterialityLevel],
    reliabilities: dict[str, ReliabilityResult],
    movement_result: MovementIntelligenceResult,
    llm: LLMProvider,
    tenant_id: str,
    job_id: str,
    causality_chains: list[dict] | None = None,
    fund_analysis: dict | None = None,
    business_context_snippet: str = "",
    macro_context: dict | None = None,
    policy_clauses: str = "",
    comparative_basis: dict | None = None,
) -> CausalityAnalysisResult:
    """Explain WHY movements occurred. Returns empty result on failure."""
    try:
        system_prompt = _get_prompt()
        user_prompt = _build_prompt(
            company_name, periods, variations, materialities, reliabilities,
            movement_result, causality_chains, fund_analysis, business_context_snippet,
            macro_context, policy_clauses, comparative_basis,
        )
        logger.info("causality_start | job=%s prompt_chars=%d", job_id, len(user_prompt))
        raw = _call_llm(system_prompt, user_prompt, llm, tenant_id, job_id)
        result = _parse(raw)
        logger.info("causality_done | job=%s accounts=%d dynamics=%d",
                    job_id, len(result.account_causality), len(result.cross_account_dynamics))
        return result
    except Exception as exc:
        logger.error("causality_failed | job=%s error=%s", job_id, exc)
        return CausalityAnalysisResult()
