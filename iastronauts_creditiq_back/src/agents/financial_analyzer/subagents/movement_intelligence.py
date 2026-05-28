"""
Movement Intelligence Agent — internal sub-agent of FinancialAnalyzer.

Detects material movements, portfolio rotations, and capital flow signals.
Answers: "What happened?"
"""

import logging
import os

from tenacity import retry, stop_after_attempt, wait_exponential

from shared.llm_provider import LLMProvider
from shared.models.base import MaterialityLevel
from shared.s3_instructions import load_text

from ..ratio_engine import AccountVariation
from ..contracts.movement_contracts import (
    KeyMovement,
    MovementIntelligenceResult,
    PortfolioRotation,
)

logger = logging.getLogger("financial_analyzer.movement_intelligence")

_PROMPT_S3_KEY = "instructions/prompts/02a_prompt_subagent_movement_intelligence.md"
_LOCAL_PROMPT_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "system_pompts",
                 "02a_prompt_subagent_movement_intelligence.md")
)
_INLINE_FALLBACK = (
    "Eres Movement Intelligence Agent. Detecta movimientos materiales, rotaciones de portafolio "
    "y señales de flujo de capital. Responde ÚNICAMENTE con JSON válido que contenga los campos "
    "key_movements, portfolio_rotations y suspicious_patterns."
)

_prompt_cache: str | None = None
_MAX_ACCOUNTS = 10
_MAX_TOKENS = 5000
_HIGH_PRIORITY = {MaterialityLevel.HIGH, MaterialityLevel.MEDIUM}


def _get_prompt() -> str:
    global _prompt_cache
    if _prompt_cache is not None:
        return _prompt_cache
    s3 = load_text(_PROMPT_S3_KEY, fallback="")
    if s3:
        _prompt_cache = s3
        logger.info("movement_prompt | source=s3 chars=%d", len(s3))
        return _prompt_cache
    try:
        with open(_LOCAL_PROMPT_PATH, encoding="utf-8") as f:
            local = f.read()
        if len(local) > 100:
            _prompt_cache = local
            logger.info("movement_prompt | source=local chars=%d", len(local))
            return _prompt_cache
    except FileNotFoundError:
        pass
    _prompt_cache = _INLINE_FALLBACK
    logger.warning("movement_prompt | source=inline_fallback")
    return _prompt_cache


def _build_digest(
    company_name: str,
    periods: list[str],
    variations: list[AccountVariation],
    materialities: dict[str, MaterialityLevel],
    fund_analysis: dict | None,
    executive_synthesis: dict | None,
    concentration: dict | None,
) -> str:
    lines = [
        f"EMPRESA: {company_name}",
        f"PERÍODOS: {' vs '.join(periods)}",
    ]

    if fund_analysis and fund_analysis.get("is_investment_fund"):
        nav_rec = fund_analysis.get("nav_reconciliation") or {}
        total_assets = nav_rec.get("total_assets_cop_mm", 0) or 0
        net_flow = fund_analysis.get("net_investor_flow_cop_mm", 0) or 0
        cash_ratio = fund_analysis.get("cash_ratio", 0) or 0
        top_positions = fund_analysis.get("top_positions") or []
        top1_pct = fund_analysis.get("top1_position_pct", 0) or 0
        top1_name = top_positions[0].get("name", "") if top_positions else ""
        lines.append(f"TIPO FONDO: {fund_analysis.get('fund_type', '')} | AUM: {total_assets:.0f} COP MM")
        lines.append(f"FLUJO NETO INVERSIONISTAS: {net_flow:+.0f} COP MM")
        lines.append(f"CASH: {cash_ratio:.1f}% | TOP1: {top1_pct:.1f}% ({top1_name})")
        new_pos = fund_analysis.get("new_positions") or []
        closed_pos = fund_analysis.get("closed_positions") or []
        if new_pos:
            lines.append("NUEVAS POSICIONES: " + ", ".join(p.get("name", "") for p in new_pos[:4]))
        if closed_pos:
            lines.append("POSICIONES CERRADAS: " + ", ".join(p.get("name", "") for p in closed_pos[:4]))

    if executive_synthesis:
        theme = executive_synthesis.get("main_portfolio_theme", "")
        signals = executive_synthesis.get("signals", [])
        rotation = executive_synthesis.get("strategic_rotation", "")
        if theme:
            lines.append(f"TEMA: {theme}")
        if signals:
            lines.append("SEÑALES: " + " | ".join(str(s) for s in signals[:6]))
        if rotation:
            lines.append(f"ROTACIÓN ESTRATÉGICA: {str(rotation)[:120]}")

    if concentration and concentration.get("has_concentration"):
        dom = concentration.get("dominant_dimension", "")
        top_item = concentration.get("top_item", "")
        top_pct = concentration.get("top_item_pct", 0) or 0
        lines.append(f"CONCENTRACIÓN: {dom} → {top_item} ({top_pct:.1f}%)")

    priority = sorted(
        [v for v in variations if materialities.get(v.account_id, MaterialityLevel.LOW) in _HIGH_PRIORITY],
        key=lambda v: abs(v.absolute_variation) if v.has_previous_value else abs(v.current_value),
        reverse=True,
    )[:_MAX_ACCOUNTS]

    lines.append(f"\nTOP MOVIMIENTOS MATERIALES ({len(priority)} de {len(variations)} cuentas):")
    for i, v in enumerate(priority, 1):
        mat = materialities.get(v.account_id, MaterialityLevel.LOW).value.upper()
        if v.has_previous_value:
            arrow = "↑" if v.absolute_variation > 0 else "↓"
            pct_str = f" ({v.variation_pct:+.1f}%)" if abs(v.variation_pct) < 10_000 else ""
            lines.append(
                f"{i}. [{v.account_id}] {v.account_name}: {arrow} {abs(v.absolute_variation):.0f} COP MM"
                f"{pct_str} [MAT={mat}] prev={v.previous_value:.0f} curr={v.current_value:.0f}"
            )
        else:
            status = "NUEVA" if v.current_value > 0 else "CERRADA"
            lines.append(
                f"{i}. [{v.account_id}] {v.account_name}: {status} {v.current_value:.0f} COP MM [MAT={mat}]"
            )

    lines.append(
        '\nINSTRUCCIÓN: Devuelve JSON con:\n'
        '{"key_movements":[{"account_id":"...","movement_type":"...","direction":"...","magnitude":0.0,"summary":"...","confidence":0.8}],'
        '"portfolio_rotations":[{"from_assets":[],"to_assets":[],"rationale":"...","confidence":0.7}],'
        '"suspicious_patterns":["..."]}'
    )
    return "\n".join(lines)


@retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=2, max=6))
def _call_llm(system_prompt: str, user_prompt: str, llm: LLMProvider, tenant_id: str, job_id: str) -> dict:
    return llm.generate_json(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        temperature=0.1,
        tenant_id=tenant_id,
        job_id=job_id,
        max_tokens=_MAX_TOKENS,
    )


def _parse(raw: dict) -> MovementIntelligenceResult:
    movements: list[KeyMovement] = []
    for item in raw.get("key_movements", []):
        if not isinstance(item, dict) or not item.get("account_id"):
            continue
        try:
            movements.append(KeyMovement(
                account_id=str(item["account_id"]),
                movement_type=str(item.get("movement_type", "unknown")),
                direction=str(item.get("direction", "unknown")),
                magnitude=float(item.get("magnitude", 0)),
                summary=str(item.get("summary", ""))[:200],
                confidence=min(max(float(item.get("confidence", 0.8)), 0.0), 1.0),
            ))
        except Exception:
            continue

    rotations: list[PortfolioRotation] = []
    for item in raw.get("portfolio_rotations", []):
        if not isinstance(item, dict):
            continue
        try:
            rotations.append(PortfolioRotation(
                from_assets=[str(a) for a in item.get("from_assets", [])],
                to_assets=[str(a) for a in item.get("to_assets", [])],
                rationale=str(item.get("rationale", ""))[:250],
                confidence=min(max(float(item.get("confidence", 0.7)), 0.0), 1.0),
            ))
        except Exception:
            continue

    patterns = [str(p)[:200] for p in raw.get("suspicious_patterns", []) if p]
    return MovementIntelligenceResult(
        key_movements=movements,
        portfolio_rotations=rotations,
        suspicious_patterns=patterns,
    )


def run_movement_intelligence(
    company_name: str,
    periods: list[str],
    variations: list[AccountVariation],
    materialities: dict[str, MaterialityLevel],
    llm: LLMProvider,
    tenant_id: str,
    job_id: str,
    fund_analysis: dict | None = None,
    executive_synthesis: dict | None = None,
    concentration: dict | None = None,
) -> MovementIntelligenceResult:
    """Detect material movements and portfolio rotations. Returns empty result on failure."""
    try:
        system_prompt = _get_prompt()
        user_prompt = _build_digest(
            company_name, periods, variations, materialities,
            fund_analysis, executive_synthesis, concentration,
        )
        logger.info("movement_intelligence_start | job=%s accounts=%d prompt_chars=%d",
                    job_id, len(variations), len(user_prompt))
        raw = _call_llm(system_prompt, user_prompt, llm, tenant_id, job_id)
        result = _parse(raw)
        logger.info("movement_intelligence_done | job=%s movements=%d rotations=%d patterns=%d",
                    job_id, len(result.key_movements), len(result.portfolio_rotations),
                    len(result.suspicious_patterns))
        return result
    except Exception as exc:
        logger.error("movement_intelligence_failed | job=%s error=%s", job_id, exc)
        return MovementIntelligenceResult()
