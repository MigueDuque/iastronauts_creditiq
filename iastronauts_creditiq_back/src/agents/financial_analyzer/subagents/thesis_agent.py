"""
Financial Thesis Agent — internal sub-agent of FinancialAnalyzer.

Transforms causal findings into strategic interpretation and portfolio thesis.
Answers: "What does this mean strategically?"
"""

import json
import logging
import os

from tenacity import retry, stop_after_attempt, wait_exponential

from shared.llm_provider import LLMProvider
from shared.s3_instructions import load_text

from ..contracts.causality_contracts import CausalityAnalysisResult
from ..contracts.movement_contracts import MovementIntelligenceResult
from ..contracts.thesis_contracts import FinancialThesisResult, StrategicShift

logger = logging.getLogger("financial_analyzer.thesis_agent")

_PROMPT_S3_KEY = "instructions/prompts/02c_prompt_subagent_thesis.md"
_LOCAL_PROMPT_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "system_pompts",
                 "02c_prompt_subagent_thesis.md")
)
_INLINE_FALLBACK = (
    "Eres Financial Thesis Agent. Transforma hallazgos causales en interpretación estratégica. "
    "Genera portfolio_thesis, strategic_shifts, investment_implications, overall_financial_health "
    "y earnings_sustainability. Responde ÚNICAMENTE con JSON válido."
)

_prompt_cache: str | None = None
_MAX_TOKENS = 5000

_VALID_HEALTH = {
    "STABLE", "DECLINING", "GROWING", "CRITICAL",
    "LIQUID", "LEVERAGED", "SPECULATIVE", "CASH_STRESSED",
    "VALUATION_DRIVEN", "CONCENTRATED",
}
_VALID_SUSTAINABILITY = {"STRONG", "MODERATE", "WEAK"}
_VALID_SEVERITY = {"HIGH", "MEDIUM", "LOW"}


def _get_prompt() -> str:
    global _prompt_cache
    if _prompt_cache is not None:
        return _prompt_cache
    s3 = load_text(_PROMPT_S3_KEY, fallback="")
    if s3:
        _prompt_cache = s3
        logger.info("thesis_prompt | source=s3 chars=%d", len(s3))
        return _prompt_cache
    try:
        with open(_LOCAL_PROMPT_PATH, encoding="utf-8") as f:
            local = f.read()
        if len(local) > 100:
            _prompt_cache = local
            logger.info("thesis_prompt | source=local chars=%d", len(local))
            return _prompt_cache
    except FileNotFoundError:
        pass
    _prompt_cache = _INLINE_FALLBACK
    logger.warning("thesis_prompt | source=inline_fallback")
    return _prompt_cache


def _build_prompt(
    company_name: str,
    periods: list[str],
    movement_result: MovementIntelligenceResult,
    causality_result: CausalityAnalysisResult,
    executive_synthesis: dict | None,
    earnings_quality: dict | None,
    concentration: dict | None,
    ratios_dict: dict,
    macro_context: dict | None,
    financial_diagnostics: dict | None,
) -> str:
    lines = [f"EMPRESA: {company_name} | PERÍODOS: {' vs '.join(periods)}"]

    if executive_synthesis:
        theme = executive_synthesis.get("main_portfolio_theme", "")
        signals = executive_synthesis.get("signals", [])
        conclusions = executive_synthesis.get("executive_conclusions", [])
        board_alerts = executive_synthesis.get("board_alerts", [])
        if theme:
            lines.append(f"TEMA PRE-CALCULADO: {theme}")
        if signals:
            lines.append("SEÑALES: " + " | ".join(str(s) for s in signals[:8]))
        if conclusions:
            lines.append("CONCLUSIONES: " + " / ".join(str(c)[:80] for c in conclusions[:3]))
        if board_alerts:
            lines.append("ALERTAS: " + " / ".join(str(a)[:80] for a in board_alerts[:2]))

    if movement_result.key_movements:
        lines.append("\nMOVIMIENTOS CLAVE:")
        for m in movement_result.key_movements[:8]:
            lines.append(f"  [{m.movement_type}] {m.summary}")
    if movement_result.portfolio_rotations:
        lines.append("ROTACIONES:")
        for r in movement_result.portfolio_rotations[:3]:
            lines.append(f"  {r.rationale}")
    if movement_result.suspicious_patterns:
        lines.append("PATRONES: " + " | ".join(movement_result.suspicious_patterns[:3]))

    if causality_result.cross_account_dynamics:
        lines.append("\nDINÁMICAS CAUSALES:")
        for d in causality_result.cross_account_dynamics[:4]:
            lines.append(f"  {d.explanation}")

    if earnings_quality:
        score = earnings_quality.get("earnings_quality_score", 0) or earnings_quality.get("quality_score", 0)
        label = earnings_quality.get("quality_label", "")
        fv_ratio = earnings_quality.get("fair_value_income_ratio", 0) or 0
        lines.append(f"\nCALIDAD UTILIDADES: score={score} | {label} | fv_ratio={fv_ratio:.1f}%")

    if concentration and concentration.get("has_concentration"):
        dom = concentration.get("dominant_dimension", "")
        top_item = concentration.get("top_item", "")
        top_pct = concentration.get("top_item_pct", 0) or 0
        lines.append(f"CONCENTRACIÓN: {dom} → {top_item} ({top_pct:.1f}%)")

    key_ratios = {k: ratios_dict.get("ratios", {}).get(k) for k in
                  ["deuda_patrimonio", "endeudamiento_global", "margen_neto", "razon_corriente"]
                  if ratios_dict.get("ratios", {}).get(k) is not None}
    if key_ratios:
        lines.append(f"\nRATIOS CLAVE: {json.dumps(key_ratios, ensure_ascii=False)}")

    # Compact macro context for strategic alignment
    if macro_context:
        macro_signals = macro_context.get("macro_signals", [])
        macro_ctx = macro_context.get("macro_context", {})
        rate_env = macro_ctx.get("interest_rate_environment", "")
        sector_ctx = macro_context.get("sector_context", [])
        macro_parts = []
        if rate_env:
            macro_parts.append(f"tasas: {rate_env}")
        if macro_signals:
            macro_parts.append("señales: " + " | ".join(str(s) for s in macro_signals[:4]))
        if sector_ctx:
            macro_parts.append("sectores: " + ", ".join(str(s)[:40] for s in sector_ctx[:2]))
        if macro_parts:
            lines.append("\nCONTEXTO MACRO: " + " | ".join(macro_parts))

    # Pre-computed cross-statement diagnostic signals (inputs for cross_statement_signals output)
    if financial_diagnostics:
        diag_signals = financial_diagnostics.get("signals", [])
        if diag_signals:
            sorted_signals = sorted(
                diag_signals,
                key=lambda s: {"HIGH": 0, "MEDIUM": 1, "LOW": 2}.get(s.get("severity", "LOW"), 2),
            )
            lines.append("\nDIAGNÓSTICOS CROSS-STATEMENT (determinístico — usa para cross_statement_signals):")
            for s in sorted_signals[:4]:
                lines.append(
                    f"  [{s.get('severity', 'LOW')}] {s.get('signal_id', '')}: "
                    f"{s.get('finding', '')} → {s.get('implication', '')}"
                )

    lines.append(
        '\nINSTRUCCIÓN: Devuelve JSON con:\n'
        '{"portfolio_thesis":"...","strategic_shifts":[{"shift_type":"...","explanation":"...","confidence":0.8}],'
        '"investment_implications":["..."],"overall_financial_health":"STABLE","earnings_sustainability":"STRONG",'
        '"insight_tiers":{"tier1_critical":[{"signal":"...","so_what":"...","category":"..."}],"tier2_material":[]},'
        '"structured_analysis":{"revenue_profitability":"...","balance_sheet_strength":"...","cash_flow_quality":"...",'
        '"equity_movement":"...","risk_signals":"...","forward_outlook":"..."},'
        '"cross_statement_signals":[{"pattern":"...","description":"...","implication":"...","severity":"HIGH"}]}\n'
        f'VALORES VÁLIDOS overall_financial_health: {" | ".join(sorted(_VALID_HEALTH))}\n'
        'VALORES VÁLIDOS earnings_sustainability: STRONG | MODERATE | WEAK'
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


def _parse(raw: dict) -> FinancialThesisResult:
    shifts: list[StrategicShift] = []
    for item in raw.get("strategic_shifts", []):
        if not isinstance(item, dict):
            continue
        try:
            shifts.append(StrategicShift(
                shift_type=str(item.get("shift_type", "unknown")),
                explanation=str(item.get("explanation", ""))[:350],
                confidence=min(max(float(item.get("confidence", 0.8)), 0.0), 1.0),
            ))
        except Exception:
            continue

    health = str(raw.get("overall_financial_health", "STABLE")).upper()
    if health not in _VALID_HEALTH:
        health = "STABLE"

    sustainability = str(raw.get("earnings_sustainability", "")).upper()
    if sustainability not in _VALID_SUSTAINABILITY:
        sustainability = ""

    # insight_tiers
    tiers_raw = raw.get("insight_tiers") or {}
    insight_tiers: dict = {}
    if isinstance(tiers_raw, dict):
        t1 = tiers_raw.get("tier1_critical") or []
        t2 = tiers_raw.get("tier2_material") or []
        insight_tiers = {
            "tier1_critical": [
                {"signal": str(t.get("signal", "")), "so_what": str(t.get("so_what", "")),
                 "category": str(t.get("category", ""))}
                for t in t1 if isinstance(t, dict)
            ],
            "tier2_material": [
                {"account_id": str(t.get("account_id", "")), "signal": str(t.get("signal", "")),
                 "so_what": str(t.get("so_what", ""))}
                for t in t2 if isinstance(t, dict)
            ],
        }

    # structured_analysis
    sa_raw = raw.get("structured_analysis") or {}
    structured_analysis: dict = {}
    if isinstance(sa_raw, dict):
        for k in ["revenue_profitability", "balance_sheet_strength", "cash_flow_quality",
                  "equity_movement", "risk_signals", "forward_outlook"]:
            if sa_raw.get(k):
                structured_analysis[k] = str(sa_raw[k])

    # cross_statement_signals
    css_raw = raw.get("cross_statement_signals") or []
    cross_signals: list[dict] = []
    if isinstance(css_raw, list):
        for item in css_raw:
            if not isinstance(item, dict):
                continue
            sev = str(item.get("severity", "LOW")).upper()
            cross_signals.append({
                "pattern": str(item.get("pattern", "")),
                "description": str(item.get("description", "")),
                "implication": str(item.get("implication", "")),
                "severity": sev if sev in _VALID_SEVERITY else "LOW",
            })

    return FinancialThesisResult(
        portfolio_thesis=str(raw.get("portfolio_thesis", ""))[:1200],
        strategic_shifts=shifts,
        investment_implications=[str(i)[:300] for i in raw.get("investment_implications", []) if i][:6],
        overall_financial_health=health,
        earnings_sustainability=sustainability,
        insight_tiers=insight_tiers,
        structured_analysis=structured_analysis,
        cross_statement_signals=cross_signals,
    )


def run_financial_thesis(
    company_name: str,
    periods: list[str],
    movement_result: MovementIntelligenceResult,
    causality_result: CausalityAnalysisResult,
    llm: LLMProvider,
    tenant_id: str,
    job_id: str,
    executive_synthesis: dict | None = None,
    earnings_quality: dict | None = None,
    concentration: dict | None = None,
    ratios_dict: dict | None = None,
    macro_context: dict | None = None,
    financial_diagnostics: dict | None = None,
) -> FinancialThesisResult:
    """Transform causal findings into strategic interpretation. Returns safe default on failure."""
    try:
        system_prompt = _get_prompt()
        user_prompt = _build_prompt(
            company_name, periods, movement_result, causality_result,
            executive_synthesis, earnings_quality, concentration, ratios_dict or {},
            macro_context, financial_diagnostics,
        )
        logger.info("thesis_start | job=%s prompt_chars=%d", job_id, len(user_prompt))
        raw = _call_llm(system_prompt, user_prompt, llm, tenant_id, job_id)
        result = _parse(raw)
        logger.info("thesis_done | job=%s health=%s shifts=%d implications=%d",
                    job_id, result.overall_financial_health, len(result.strategic_shifts),
                    len(result.investment_implications))
        return result
    except Exception as exc:
        logger.error("thesis_failed | job=%s error=%s", job_id, exc)
        return FinancialThesisResult()
