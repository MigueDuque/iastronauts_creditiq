"""
Management Report Generator — CreditIQ (Sprint 4 Item 10, isolated trial).

Generates a board/committee-oriented .docx report on demand from a completed
analysis. This is NOT part of the standard pipeline — it is exposed as a separate
opt-in endpoint and must not appear as a mandatory Task state in analysis_workflow.json.

Design intent (from improvement_and_correction_plan.md Item 10):
  - Inputs:  ScorerOutput (carries AnalyzerOutput data + risk scoring + macro context)
  - Output:  A board/governance .docx, stored at jobs/{date}/{job_id}/management_report_response.docx
  - Tone:    Stewardship, mandate compliance, performance vs benchmark, governance framing
  - Reuse:   build_deterministic_fields + build_narrative_fallbacks from report_generator.template_filler
             docx utilities (_extract_placeholders, _expand_all_tables, _fill_docx_template)
             imported directly from report_generator.handler (same package, isolated-trial exception)
  - Template: tries instructions/CreditIQ_Template_Gerencial.docx first, falls back to
              instructions/CreditIQ_Template_EEFF.docx so the feature works without a
              dedicated management template until one is designed.
"""

import io
import json
import logging
import os
from datetime import datetime

from shared.job_store import MANAGEMENT_REPORT, RISK_SCORER, save as job_save, save_bytes as job_save_bytes, load as job_load
from shared.llm_provider import LLMProvider
from shared.models import ScorerOutput
from shared.s3_instructions import load_text
from shared.s3_report_store import load_docx_template, slugify

# Reuse deterministic field builders and narrative classifier from Agent 4.
from agents.report_generator.template_filler import (
    build_deterministic_fields,
    build_narrative_fallbacks,
    is_narrative_field,
)
# Reuse docx byte-level utilities from Agent 4 handler (isolated-trial exception:
# these are module-private but management report is an optional sibling, not a
# separate service, so direct import is acceptable here).
from agents.report_generator.handler import (
    _extract_placeholders,
    _expand_all_tables,
    _fill_docx_template,
    _compute_row_needs,
    _deduplicate_narrative_fields,
    _enum,
    _money,
    _pct,
    _material_accounts,
)
from agents.report_generator.sheet_mapper import match_sheets

logger = logging.getLogger("management_report")
logger.setLevel(logging.INFO)

_MGMT_TEMPLATE_KEY = "instructions/CreditIQ_Template_Gerencial.docx"
_DOCX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

_PROMPT_S3_KEY = "instructions/prompts/05_prompt_management_report.md"
_LOCAL_PROMPT_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "system_pompts",
                 "05_prompt_management_report.md")
)
_INLINE_FALLBACK = (
    "Eres el generador de informes gerenciales de CreditIQ para comités y juntas directivas. "
    "Recibes datos financieros del fondo de inversión y debes redactar secciones de gobierno, "
    "cumplimiento de mandato y recomendaciones estratégicas. "
    "Responde SOLO con un JSON donde cada clave es el nombre del campo y el valor es el contenido."
)
_prompt_cache: str | None = None


def _get_system_prompt() -> str:
    global _prompt_cache
    if _prompt_cache is not None:
        return _prompt_cache
    s3_text = load_text(_PROMPT_S3_KEY, fallback="")
    if s3_text:
        _prompt_cache = s3_text
        logger.info("mgmt_prompt | source=s3 chars=%d", len(s3_text))
        return _prompt_cache
    try:
        with open(_LOCAL_PROMPT_PATH, encoding="utf-8") as f:
            local_text = f.read()
        if len(local_text) > 100:
            _prompt_cache = local_text
            logger.info("mgmt_prompt | source=local chars=%d", len(local_text))
            return _prompt_cache
    except OSError:
        pass
    logger.warning("mgmt_prompt | source=inline_fallback")
    _prompt_cache = _INLINE_FALLBACK
    return _prompt_cache


def _load_extractor_accounts(job_id: str) -> list[dict]:
    try:
        data = job_load(job_id, "extractor_response")
        return data.get("accounts") or []
    except Exception:
        return []


def _build_governance_digest(payload: ScorerOutput) -> str:
    """Build a governance-focused briefing for the board/committee LLM prompt.

    Emphasises mandate compliance, policy limit status, and stewardship framing —
    rather than the technical accounting detail that drives the standard report digest.
    """
    risk = _enum(payload.overall_risk_score)
    health = _enum(payload.overall_financial_health)
    periods_str = " / ".join(payload.periods) if payload.periods else "-"

    lines = [
        f"# INFORME GERENCIAL — {payload.company_name}",
        f"Período: {periods_str} | Moneda: {payload.currency}",
        f"Perfil de riesgo: {risk} | Salud financiera: {health}",
        f"Score de validación: {payload.validation_score}/100",
        "",
    ]

    # ── Fund identity & mandate ──────────────────────────────────────────────
    fa = payload.fund_analysis or {}
    if fa.get("is_fund"):
        fund_type = fa.get("fund_type") or "N/A"
        policy = fa.get("investment_policy_summary") or ""
        recon = fa.get("nav_reconciliation") or {}
        lines += [
            "## IDENTIDAD DEL FONDO",
            f"- Tipo: {fund_type}",
            f"- Política de inversión: {policy}" if policy else "",
            f"- NAV apertura: {_money(recon.get('opening_nav'))}",
            f"- NAV cierre: {_money(recon.get('closing_nav'))}",
            f"- Flujo neto de inversores: {_money(recon.get('net_investor_flow'))}",
            "",
        ]

    # ── KPIs ─────────────────────────────────────────────────────────────────
    kpis = payload.executive_kpis or {}
    if kpis:
        lines.append("## KPIs DEL PERÍODO")
        for k, v in list(kpis.items())[:8]:
            lines.append(f"- {k}: {v}")
        lines.append("")

    # ── Fund policy — mandate compliance (highest governance priority) ────────
    fpa = payload.fund_policy_assessment or {}
    if fpa and fpa.get("assessments"):
        lines.append("## CUMPLIMIENTO DE MANDATO — LÍMITES DE POLÍTICA")
        lines.append(
            f"Tipo de fondo: {fpa.get('fund_type', 'N/A')} | "
            f"Fuente de límites: {fpa.get('policy_source', 'default')}"
        )
        limits = fpa.get("limits") or {}
        if limits:
            lines.append(
                f"Límites aplicables: por emisor {limits.get('per_issuer_pct', '?')}% | "
                f"sector {limits.get('per_sector_pct', '?')}% | "
                f"contraparte {limits.get('per_counterparty_pct', '?')}%"
            )
        breaches = [a for a in fpa.get("assessments", []) if a.get("status") == "breach"]
        nears = [a for a in fpa.get("assessments", []) if a.get("status") == "near"]
        within = [a for a in fpa.get("assessments", []) if a.get("status") == "within"]
        if breaches:
            lines.append(f"INCUMPLIMIENTOS ({len(breaches)}) — requieren atención inmediata del comité:")
            for b in breaches[:5]:
                lines.append(
                    f"  ✗ {b.get('dimension')} / {b.get('name')}: "
                    f"{b.get('exposure_pct', 0):.1f}% vs límite {b.get('limit_pct', 0):.1f}% "
                    f"(exceso: {abs(b.get('headroom_pct', 0)):.1f}%)"
                )
        if nears:
            lines.append(f"CERCA DEL LÍMITE ({len(nears)}) — requieren monitoreo reforzado:")
            for n in nears[:5]:
                lines.append(
                    f"  △ {n.get('dimension')} / {n.get('name')}: "
                    f"{n.get('exposure_pct', 0):.1f}% vs límite {n.get('limit_pct', 0):.1f}% "
                    f"(margen: {n.get('headroom_pct', 0):.1f}%)"
                )
        lines.append(
            f"Posiciones dentro de límites: {len(within)}. "
            "INSTRUCCIÓN: Al mencionar concentraciones SIEMPRE incluye el % de exposición y el límite permitido."
        )
        lines.append("")

    # ── Risk profile ─────────────────────────────────────────────────────────
    categories = payload.risk_categories or {}
    narratives = (payload.risk_summary or {}).get("category_narratives") or {}
    recs = (payload.risk_summary or {}).get("risk_recommendations") or []
    if categories:
        lines.append("## PERFIL DE RIESGO PARA EL COMITÉ")
        for key in ("credito", "mercado", "financiero"):
            cat = categories.get(key)
            if not cat:
                continue
            label = cat.get("label", key)
            level = cat.get("level", "N/A")
            score = cat.get("score", 0)
            narrative = narratives.get(key, "")
            findings = cat.get("key_findings") or []
            lines.append(f"### {label}: {level} ({score}/100)")
            if narrative:
                lines.append(narrative)
            for f in findings[:3]:
                lines.append(f"- {f}")
        lines.append("")

    if recs:
        lines.append("## RECOMENDACIONES AL COMITÉ")
        for r in recs[:5]:
            lines.append(f"- {r}")
        lines.append("")

    # ── Portfolio composition ─────────────────────────────────────────────────
    sc = payload.sheet_concentration or {}
    if sc.get("asset_available") or sc.get("instrument_available") or sc.get("bank_available"):
        lines.append("## COMPOSICIÓN DEL PORTAFOLIO")
        if sc.get("instrument_available"):
            inst_bd = (sc.get("instrument_breakdown") or [])[:8]
            lines.append("Por instrumento:")
            for r in inst_bd:
                lines.append(f"  - {r.get('instrument_type')}: {_pct(r.get('pct'))}")
        if sc.get("bank_available"):
            bank_bd = (sc.get("bank_breakdown") or [])[:8]
            lines.append("Por custodio/banco:")
            for r in bank_bd:
                lines.append(f"  - {r.get('name')}: {_pct(r.get('pct'))}")
        lines.append("")

    # ── Portfolio thesis ──────────────────────────────────────────────────────
    synthesis = payload.executive_synthesis or {}
    if payload.portfolio_thesis:
        lines.append("## TESIS DEL PORTAFOLIO")
        lines.append(payload.portfolio_thesis)
        lines.append("")
    board_alerts = synthesis.get("board_alerts") or []
    if board_alerts:
        lines.append("## ALERTAS PARA LA JUNTA")
        for alert in board_alerts[:5]:
            lines.append(f"- {alert}")
        lines.append("")

    # ── Top material accounts (governance view) ───────────────────────────────
    top = _material_accounts(payload, 8)
    if top:
        lines.append("## POSICIONES MATERIALES")
        lines.append("| Cuenta | Actual | Anterior | Δ% | Materialidad |")
        lines.append("|--------|-------:|---------:|----:|:------------:|")
        for a in top:
            anomaly = " ⚠" if a.anomaly_detected else ""
            lines.append(
                f"| {a.account_name}{anomaly} | {_money(a.current_value)} "
                f"| {_money(a.previous_value)} | {_pct(a.variation_pct)} "
                f"| {_enum(a.materiality)} |"
            )
        lines.append("")

    # ── Comparative basis (period labels) ─────────────────────────────────────
    cb = payload.comparative_basis or {}
    if cb:
        lines.append("## BASES COMPARATIVAS (IFRS)")
        lines.append("REGLA: al mencionar variaciones, nombra siempre ambos períodos explícitamente.")
        for stmt, entry in cb.items():
            cur = entry.get("current", "?")
            comp = entry.get("comparative", "?")
            rule = entry.get("rule", "?")
            lines.append(f"- {stmt}: {cur} vs {comp} [{rule}]")
        lines.append("")

    # ── Macro context ─────────────────────────────────────────────────────────
    macro = payload.macro_context or {}
    if macro:
        lines.append("## CONTEXTO MACROECONÓMICO")
        for k, v in list(macro.items())[:8]:
            lines.append(f"- {k}: {v}")
        lines.append("")

    lines.append(
        "NOTA AL LLM: Este es un informe para el comité directivo / junta de administración. "
        "El tono debe ser de gobierno corporativo: stewardship, cumplimiento de mandato, "
        "recomendaciones de política. Evita tecnicismos contables; usa lenguaje de comité de inversión."
    )
    return "\n".join(line for line in lines if line is not None)


def generate_management_report(payload: ScorerOutput, *, llm_model: str | None = None) -> dict:
    """Pure generator function (no event/Lambda ceremony). Can be called directly from tests."""
    bucket = os.environ["MAIN_BUCKET"]
    reference_date = datetime.utcnow()

    logger.info("ManagementReport start | job=%s company=%s", payload.job_id, payload.company_name)

    extractor_accounts = _load_extractor_accounts(payload.job_id)
    actual_sheets = sorted({
        acc["source_sheet"]
        for acc in extractor_accounts
        if acc.get("source_sheet")
    })
    sheet_mapping = match_sheets(
        actual_sheets,
        llm=LLMProvider(model=llm_model),
        tenant_id=payload.tenant_id,
        job_id=payload.job_id,
    ) if actual_sheets else {}

    row_needs = _compute_row_needs(extractor_accounts, sheet_mapping) if extractor_accounts else {}
    digest = _build_governance_digest(payload)

    # Try dedicated management template first; fall back to the standard one.
    template_bytes = load_docx_template(bucket, template_key=_MGMT_TEMPLATE_KEY)
    if not template_bytes:
        logger.info("mgmt_template | no dedicated template found, using standard EEFF template")
        template_bytes = load_docx_template(bucket)

    docx_s3_url: str | None = None

    if template_bytes:
        if row_needs:
            template_bytes = _expand_all_tables(template_bytes, row_needs)

        placeholders = _extract_placeholders(template_bytes)
        narrative = [p for p in placeholders if is_narrative_field(p)]
        logger.info(
            "mgmt_template | job=%s placeholders=%d narrative=%d",
            payload.job_id, len(placeholders), len(narrative),
        )

        det_map, _audit = build_deterministic_fields(
            payload, generated_at=reference_date, job_id=payload.job_id,
            row_needs=row_needs, extractor_accounts=extractor_accounts,
            sheet_mapping=sheet_mapping,
        )
        fallback_map = build_narrative_fallbacks(payload)

        if narrative:
            system_prompt = _get_system_prompt()
            field_list = "\n".join(f"- {{{{{p}}}}}" for p in narrative)
            user_prompt = (
                f"## CAMPOS DEL TEMPLATE\n\n{field_list}\n\n"
                f"## DATOS DEL FONDO\n\n{digest}"
            )
            llm = LLMProvider(model=llm_model)
            try:
                llm_map_raw = llm.generate_json(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    temperature=0.5,
                    tenant_id=payload.tenant_id,
                    job_id=payload.job_id,
                    max_tokens=10000,
                )
                llm_map = {k: str(v) for k, v in llm_map_raw.items() if k in set(narrative)}
            except Exception as exc:
                logger.error("mgmt_llm_failed | job=%s error=%s", payload.job_id, exc)
                llm_map = {}
        else:
            llm_map = {}

        llm_map = _deduplicate_narrative_fields(llm_map)

        field_map: dict[str, str] = {}
        for ph in placeholders:
            llm_val = llm_map.get(ph)
            if llm_val and llm_val.strip():
                field_map[ph] = llm_val
            elif ph in det_map:
                field_map[ph] = det_map[ph]
            elif ph in fallback_map:
                field_map[ph] = fallback_map[ph]
            else:
                field_map[ph] = ""

        filled_bytes = _fill_docx_template(template_bytes, field_map)
        docx_key = job_save_bytes(
            payload.job_id,
            "management_report_report",
            filled_bytes,
            _DOCX_CONTENT_TYPE,
            "docx",
        )
        docx_s3_url = f"s3://{bucket}/{docx_key}"
        logger.info("ManagementReport done | job=%s docx_key=%s", payload.job_id, docx_key)
    else:
        logger.warning("ManagementReport | no template found, skipping docx | job=%s", payload.job_id)

    result = {
        "job_id": payload.job_id,
        "tenant_id": payload.tenant_id,
        "company_name": payload.company_name,
        "periods": payload.periods,
        "generated_at": reference_date.isoformat(),
        "docx_management_report_url": docx_s3_url,
        "overall_risk_score": _enum(payload.overall_risk_score),
        "validation_score": payload.validation_score,
    }
    job_save(payload.job_id, MANAGEMENT_REPORT, result)
    return result


def lambda_handler(event: dict, context) -> dict:
    """
    Lambda entry point for on-demand management report generation.
    Input:  ScorerOutput (same as Agent 4 input — pass from risk_scorer_response.json)
    Output: dict with docx_management_report_url pointing to the generated .docx
    """
    llm_model: str | None = event.get("llm_model") or None
    payload = ScorerOutput.model_validate(event)
    return generate_management_report(payload, llm_model=llm_model)
