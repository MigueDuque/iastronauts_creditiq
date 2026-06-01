import io
import json
import logging
import os
import re
from collections import defaultdict
from datetime import datetime

import boto3

from shared.job_store import REPORT_GENERATOR, save as job_save, save_bytes as job_save_bytes
from shared.llm_provider import LLMProvider
from shared.models import ScorerOutput, FinalReportOutput, NiifNoteDraft
from shared.s3_instructions import load_text
from shared.s3_report_store import (
    fetch_historical_reports,
    load_docx_template,
    slugify,
)
from .template_filler import (
    build_deterministic_fields,
    build_narrative_fallbacks,
    is_narrative_field,
)


logger = logging.getLogger("report_generator")
logger.setLevel(logging.INFO)

_RISK_ES = {"LOW": "bajo", "MEDIUM": "medio", "HIGH": "alto", "CRITICAL": "critico"}


def _money(value: float | int | None) -> str:
    if value is None:
        return "-"
    return f"{float(value):,.1f}"


def _pct(value: float | int | None) -> str:
    if value is None:
        return "-"
    sign = "+" if float(value) > 0 else ""
    return f"{sign}{float(value):.1f}%"


def _enum(value) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _material_accounts(payload: ScorerOutput, limit: int = 8) -> list:
    return sorted(
        payload.analysis_results,
        key=lambda a: (
            0 if _enum(a.materiality) == "HIGH" else 1 if _enum(a.materiality) == "MEDIUM" else 2,
            -getattr(a, "impact_score", 0.0),
            -abs(a.variation_pct or 0),
        ),
    )[:limit]


def _build_executive_summary(payload: ScorerOutput) -> str:
    risk = _RISK_ES.get(_enum(payload.overall_risk_score), _enum(payload.overall_risk_score).lower())
    headline = (payload.risk_summary or {}).get("risk_headline")
    top = _material_accounts(payload, 3)
    top_text = "; ".join(f"{a.account_name}: {_pct(a.variation_pct)}" for a in top)
    base = payload.executive_narrative or headline or ""
    if base:
        return (
            f"{base} El perfil de riesgo global es {risk}, con score de validacion "
            f"{payload.validation_score}/100. Las variaciones mas relevantes son {top_text}."
        )
    return (
        f"{payload.company_name} presenta un perfil de riesgo {risk} y score de validacion "
        f"{payload.validation_score}/100. Las variaciones mas relevantes son {top_text}."
    )


def _build_board_summary(payload: ScorerOutput) -> str:
    totals = (payload.financial_ratios or {}).get("totals", {})
    ratios = (payload.financial_ratios or {}).get("ratios", {})
    recs = (payload.risk_summary or {}).get("risk_recommendations") or []
    rec_text = " ".join(recs[:2]) if recs else "Se recomienda mantener monitoreo periodico."
    return (
        f"Activos: COP {_money(totals.get('total_assets'))} MM, "
        f"patrimonio: COP {_money(totals.get('total_equity'))} MM, "
        f"utilidad neta: COP {_money(totals.get('net_income'))} MM. "
        f"Riesgo {_RISK_ES.get(_enum(payload.overall_risk_score), _enum(payload.overall_risk_score).lower())}, "
        f"revision humana: {'si' if payload.requires_human_review else 'no'}. {rec_text}"
    )


def _build_niif_notes(payload: ScorerOutput) -> list[NiifNoteDraft]:
    by_standard: dict[str, list] = defaultdict(list)
    for account in payload.analysis_results:
        if not account.requires_niif_note:
            continue
        refs = account.niif_note_references or payload.niif_notes_required or ["NIIF 7"]
        for ref in refs[:3]:
            by_standard[ref].append(account)

    notes: list[NiifNoteDraft] = []
    for idx, (standard, accounts) in enumerate(sorted(by_standard.items()), start=1):
        top_accounts = accounts[:6]
        names = ", ".join(a.account_name for a in top_accounts)
        content = (
            f"De acuerdo con {standard}, se recomienda revelar las variaciones materiales "
            f"asociadas a {names}. La medicion debe reconciliar saldos del periodo actual y "
            "comparativo, explicar cambios de valor razonable, liquidez o contraparte cuando "
            "aplique, y dejar evidencia de los juicios contables usados por la administracion. "
            "Esta nota se basa exclusivamente en las cuentas marcadas por los agentes previos."
        )
        notes.append(
            NiifNoteDraft(
                note_id=f"note-{idx:03d}",
                niif_reference=standard,
                title=f"Revelacion de variaciones materiales bajo {standard}",
                content=content,
                affected_account_ids=[a.account_id for a in top_accounts],
                requires_disclosure=True,
            )
        )
    return notes


# ---------------------------------------------------------------------------
# .docx template helpers
# ---------------------------------------------------------------------------

_PLACEHOLDER_RE = re.compile(r"\{\{([A-Z0-9_]+)\}\}")
# Instruction markers ~~text~~ are stripped from the final document deterministically.
_INSTRUCTION_RE = re.compile(r"~~.+?~~", re.DOTALL)


def _extract_placeholders(docx_bytes: bytes) -> list[str]:
    """Return ordered unique placeholder names found in a .docx template."""
    try:
        from docx import Document as _DocxDoc
    except ImportError:
        logger.warning("python-docx not installed — docx template flow disabled")
        return []

    doc = _DocxDoc(io.BytesIO(docx_bytes))
    seen: list[str] = []
    seen_set: set[str] = set()

    def _scan_para(para):
        full_text = "".join(r.text for r in para.runs)
        for m in _PLACEHOLDER_RE.finditer(full_text):
            name = m.group(1)
            if name not in seen_set:
                seen_set.add(name)
                seen.append(name)

    def _scan_table(table):
        for row in table.rows:
            for cell in row.cells:
                for para in cell.paragraphs:
                    _scan_para(para)
                for nested in cell.tables:
                    _scan_table(nested)

    def _scan_paragraphs(paragraphs):
        for para in paragraphs:
            _scan_para(para)

    _scan_paragraphs(doc.paragraphs)
    for table in doc.tables:
        _scan_table(table)

    # headers and footers live in separate XML parts
    for section in doc.sections:
        for hf in (
            section.header, section.footer,
            section.first_page_header, section.first_page_footer,
            section.even_page_header, section.even_page_footer,
        ):
            if hf is None:
                continue
            _scan_paragraphs(hf.paragraphs)
            for table in hf.tables:
                _scan_table(table)

    return seen


_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")


def _render_into_paragraph(para, text: str) -> None:
    """Rewrite a paragraph's content with `text`, rendering Markdown **bold** spans
    and `\\n` as real Word line breaks, while preserving the paragraph's original
    run formatting (font, size, color taken from the template's first run)."""
    import copy
    from docx.oxml.ns import qn

    # Capture the template run's formatting so the rewritten text keeps the look.
    template_rpr = None
    if para.runs:
        rpr = para.runs[0]._element.find(qn("w:rPr"))
        if rpr is not None:
            template_rpr = copy.deepcopy(rpr)

    # Drop existing runs (paragraph-level style in pPr is retained).
    for run in list(para.runs):
        run._element.getparent().remove(run._element)

    def _styled_run():
        run = para.add_run()
        if template_rpr is not None:
            run._element.insert(0, copy.deepcopy(template_rpr))
        return run

    def _add(segment: str, *, bold: bool) -> None:
        # The `run.text` setter clears break/tab children, so a line break must be
        # its own run emitted before the text run — never combined with one.
        if not segment:
            return
        run = _styled_run()
        run.text = segment
        if bold:
            run.bold = True

    for line_idx, line in enumerate(text.split("\n")):
        if line_idx > 0:
            _styled_run().add_break()
        pos = 0
        for m in _BOLD_RE.finditer(line):
            if m.start() > pos:
                _add(line[pos:m.start()], bold=False)
            _add(m.group(1), bold=True)
            pos = m.end()
        _add(line[pos:], bold=False)


def _fill_docx_template(docx_bytes: bytes, field_map: dict[str, str]) -> bytes:
    """Replace {{PLACEHOLDER}} markers in a .docx with the provided field values."""
    try:
        from docx import Document as _DocxDoc
    except ImportError:
        return docx_bytes

    doc = _DocxDoc(io.BytesIO(docx_bytes))

    def _replace_paragraph(para):
        full_text = "".join(r.text for r in para.runs)
        has_placeholder = "{{" in full_text
        has_instruction = "~~" in full_text
        if not has_placeholder and not has_instruction:
            return
        modified = full_text
        if has_placeholder:
            modified = _PLACEHOLDER_RE.sub(lambda m: field_map.get(m.group(1), "") or "", modified)
        if has_instruction:
            modified = _INSTRUCTION_RE.sub("", modified)
        if modified != full_text:
            if not modified.strip():
                for run in list(para.runs):
                    run._element.getparent().remove(run._element)
            else:
                _render_into_paragraph(para, modified)

    def _replace_table(table):
        for row in table.rows:
            for cell in row.cells:
                for para in cell.paragraphs:
                    _replace_paragraph(para)
                for nested in cell.tables:
                    _replace_table(nested)

    for para in doc.paragraphs:
        _replace_paragraph(para)
    for table in doc.tables:
        _replace_table(table)

    # headers and footers live in separate XML parts — iterate all sections
    for section in doc.sections:
        for hf in (
            section.header, section.footer,
            section.first_page_header, section.first_page_footer,
            section.even_page_header, section.even_page_footer,
        ):
            if hf is None:
                continue
            for para in hf.paragraphs:
                _replace_paragraph(para)
            for table in hf.tables:
                _replace_table(table)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_PROMPT_S3_KEY = "instructions/prompts/04_prompt_agent_report-generator.md"
_LOCAL_PROMPT_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "system_pompts",
                 "04_prompt_agent_report-generator.md")
)
_INLINE_FALLBACK = (
    "Eres el agente generador de reportes financieros institucionales de CreditIQ. "
    "Recibes una lista de campos de plantilla y datos financieros. "
    "Responde SOLO con un objeto JSON donde cada clave es el nombre del campo "
    "y el valor es el contenido institucional a insertar en ese campo."
)
_prompt_cache: str | None = None


def _get_system_prompt() -> str:
    global _prompt_cache
    if _prompt_cache is not None:
        return _prompt_cache
    s3_text = load_text(_PROMPT_S3_KEY, fallback="")
    if s3_text:
        _prompt_cache = s3_text
        logger.info("report_prompt | source=s3 chars=%d", len(s3_text))
        return _prompt_cache
    try:
        with open(_LOCAL_PROMPT_PATH, encoding="utf-8") as f:
            local_text = f.read()
        if len(local_text) > 100:
            _prompt_cache = local_text
            logger.info("report_prompt | source=local chars=%d", len(local_text))
            return _prompt_cache
    except OSError:
        pass
    logger.warning("report_prompt | source=inline_fallback")
    _prompt_cache = _INLINE_FALLBACK
    return _prompt_cache


# ---------------------------------------------------------------------------
# LLM field generation
# ---------------------------------------------------------------------------

def _build_llm_digest(payload: ScorerOutput, historical: list) -> str:
    """Build a compact text digest for the LLM (~3000 tokens max)."""
    risk = _enum(payload.overall_risk_score)
    health = _enum(payload.overall_financial_health)
    periods_str = " / ".join(payload.periods) if payload.periods else "-"
    conf_label = "Alta" if payload.analysis_confidence >= 0.8 else "Media" if payload.analysis_confidence >= 0.6 else "Baja"

    lines = [
        f"# BRIEFING: {payload.company_name}",
        f"Período: {periods_str} | Moneda: {payload.currency}",
        f"Riesgo Global: {risk} | Salud Financiera: {health}",
        f"Score Validación: {payload.validation_score}/100 | Confianza: {conf_label}",
        f"Anti-Alucinación: {'PASSED' if payload.anti_hallucination_passed else 'FAILED'}",
        "",
    ]

    kpis = payload.executive_kpis or {}
    if kpis:
        lines.append("## KPIs EJECUTIVOS")
        for k, v in list(kpis.items())[:8]:
            lines.append(f"- {k}: {v}")
        lines.append("")

    top = _material_accounts(payload, 10)
    if top:
        lines.append("## CUENTAS MATERIALES (TOP 10)")
        lines.append("| Cuenta | Actual | Anterior | Δ% | Materialidad | Señal |")
        lines.append("|--------|-------:|---------:|----:|:------------:|-------|")
        for a in top:
            signal = getattr(a, "investment_signal", None) or ""
            anomaly = " ⚠" if a.anomaly_detected else ""
            lines.append(
                f"| {a.account_name}{anomaly} | {_money(a.current_value)} "
                f"| {_money(a.previous_value)} | {_pct(a.variation_pct)} "
                f"| {_enum(a.materiality)} | {signal} |"
            )
        lines.append("")

    categories = payload.risk_categories or {}
    narratives = (payload.risk_summary or {}).get("category_narratives") or {}
    if categories:
        lines.append("## PERFIL DE RIESGO")
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

    synthesis = payload.executive_synthesis or {}
    if payload.portfolio_thesis:
        lines.append("## TESIS DE PORTAFOLIO")
        lines.append(payload.portfolio_thesis)
        lines.append("")
    if synthesis.get("portfolio_story"):
        lines.append("## HISTORIA DEL PORTAFOLIO")
        lines.append(synthesis["portfolio_story"])
        lines.append("")
    board_alerts = synthesis.get("board_alerts") or []
    if board_alerts:
        lines.append("## ALERTAS JUNTA")
        for alert in board_alerts[:5]:
            lines.append(f"- {alert}")
        lines.append("")

    sc = payload.sheet_concentration or {}
    if sc.get("asset_available") or sc.get("instrument_available") or sc.get("bank_available"):
        lines.append("## COMPOSICIÓN DEL PORTAFOLIO")
        if sc.get("asset_available"):
            asset_bd = (sc.get("asset_breakdown") or [])[:5]
            lines.append("Activos: " + " | ".join(f"{r.get('name')}: {_pct(r.get('pct'))}" for r in asset_bd))
        if sc.get("instrument_available"):
            inst_bd = (sc.get("instrument_breakdown") or [])[:5]
            lines.append("Instrumentos: " + " | ".join(f"{r.get('instrument_type')}: {_pct(r.get('pct'))}" for r in inst_bd))
        if sc.get("bank_available"):
            bank_bd = (sc.get("bank_breakdown") or [])[:5]
            lines.append("Bancos/Custodios: " + " | ".join(f"{r.get('name')}: {_pct(r.get('pct'))}" for r in bank_bd))
        lines.append("")

    signals = payload.cross_statement_signals or []
    if signals:
        lines.append("## SEÑALES CROSS-STATEMENT")
        for s in signals[:5]:
            desc = s.get("description") or s.get("finding") or ""
            implication = s.get("implication") or ""
            if desc:
                lines.append(f"- {desc} {implication}".strip())
        lines.append("")

    anomalies = [a for a in payload.analysis_results if a.anomaly_detected]
    if anomalies:
        lines.append("## ANOMALÍAS DETECTADAS")
        for a in anomalies[:8]:
            insight = a.executive_insight or ""
            lines.append(f"- **{a.account_name}** ({_pct(a.variation_pct)}): {insight}")
        lines.append("")

    niif_refs = payload.niif_notes_required or []
    if niif_refs:
        lines.append("## REQUERIMIENTOS NIIF")
        lines.append(", ".join(niif_refs[:10]))
        lines.append("")

    macro = payload.macro_context or {}
    if macro:
        lines.append("## CONTEXTO MACROECONÓMICO")
        for k, v in list(macro.items())[:8]:
            lines.append(f"- {k}: {v}")
        lines.append("")

    if historical:
        lines.append("## CONTEXTO HISTÓRICO")
        lines.append(f"Se dispone de {len(historical)} reporte(s) histórico(s) comparables.")
        lines.append("")

    risk_summary = payload.risk_summary or {}
    if risk_summary.get("risk_headline"):
        lines.append("## TITULAR DE RIESGO")
        lines.append(risk_summary["risk_headline"])
        lines.append("")
    recs = risk_summary.get("risk_recommendations") or []
    if recs:
        lines.append("## RECOMENDACIONES")
        for r in recs[:5]:
            lines.append(f"- {r}")
        lines.append("")

    return "\n".join(lines)


def _call_llm_for_fields(
    payload: ScorerOutput,
    digest: str,
    placeholders: list[str],
    llm_model: str | None = None,
) -> dict[str, str]:
    """Ask the LLM to fill each template placeholder. Returns {} on error."""
    system_prompt = _get_system_prompt()
    field_list = "\n".join(f"- {{{{{p}}}}}" for p in placeholders)
    user_prompt = (
        f"## CAMPOS DEL TEMPLATE\n\n{field_list}\n\n"
        f"## DATOS FINANCIEROS\n\n{digest}"
    )
    llm = LLMProvider(model=llm_model)
    try:
        result = llm.generate_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.5,
            tenant_id=payload.tenant_id,
            job_id=payload.job_id,
            max_tokens=12000,
        )
        valid = {k: str(v) for k, v in result.items() if k in set(placeholders)}
        logger.info(
            "llm_for_fields | job=%s placeholders=%d filled=%d",
            payload.job_id, len(placeholders), len(valid),
        )
        return valid
    except Exception as exc:
        logger.error("llm_for_fields failed | job=%s error=%s", payload.job_id, exc)
        return {}


# ---------------------------------------------------------------------------
# Lambda entry point
# ---------------------------------------------------------------------------

_DOCX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"




def lambda_handler(event: dict, context) -> dict:
    """
    Input:  ScorerOutput
    Output: FinalReportOutput — primary deliverable is a filled .docx stored in the
            job S3 folder at jobs/{date}/{job_id}/report_generator_report.docx

    Flow:
      1. Load CreditIQ_Template_EEFF.docx from S3
      2. Extract {{PLACEHOLDER}} fields from the template
      3. Call LLM → JSON mapping of field names to institutional content
      4. Fill template bytes with the field map
      5. Save filled .docx to jobs/{date}/{job_id}/report_generator_report.docx
      6. Store s3://bucket/key in FinalReportOutput.docx_report_url
    """
    llm_model: str | None = event.get("llm_model") or None
    payload = ScorerOutput.model_validate(event)
    bucket = os.environ["MAIN_BUCKET"]
    reference_date = datetime.utcnow()

    logger.info("ReportGenerator start | job=%s company=%s", payload.job_id, payload.company_name)

    historical = fetch_historical_reports(
        tenant_id=payload.tenant_id,
        company_slug=slugify(payload.company_name),
        reference_date=reference_date,
        bucket=bucket,
    )

    digest = _build_llm_digest(payload, historical)

    # ── .docx template flow ──────────────────────────────────────────────────
    docx_s3_url: str | None = None
    template_bytes = load_docx_template(bucket)

    if template_bytes:
        placeholders = _extract_placeholders(template_bytes)
        narrative = [p for p in placeholders if is_narrative_field(p)]
        logger.info(
            "docx_template | job=%s placeholders=%d narrative=%d",
            payload.job_id, len(placeholders), len(narrative),
        )

        # 1. Deterministic calculated cells — the bulk of the template (tables, KPIs).
        det_map = build_deterministic_fields(
            payload, generated_at=reference_date, job_id=payload.job_id
        )
        # 2. Deterministic narrative fallbacks — used if the LLM omits/fails a field.
        fallback_map = build_narrative_fallbacks(payload)
        # 3. LLM authors ONLY the narrative sections (never the data cells).
        llm_map = (
            _call_llm_for_fields(payload, digest, narrative, llm_model=llm_model)
            if narrative else {}
        )

        # Merge per placeholder. Precedence: LLM > deterministic > fallback > "".
        # Every extracted placeholder gets a value, so no raw {{TOKEN}} leaks through
        # and a .docx is always produced once the template loads.
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
            "report_generator_report",
            filled_bytes,
            _DOCX_CONTENT_TYPE,
            "docx",
        )
        docx_s3_url = f"s3://{bucket}/{docx_key}"
        llm_filled = sum(1 for p in narrative if (llm_map.get(p) or "").strip())
        logger.info(
            "docx_saved | job=%s key=%s filled=%d llm_narrative=%d/%d",
            payload.job_id, docx_key, len(field_map), llm_filled, len(narrative),
        )
    else:
        logger.warning("docx_template_not_found | job=%s bucket=%s", payload.job_id, bucket)

    # ── Build FinalReportOutput (flows to RevisorInteligente) ────────────────
    result = FinalReportOutput(
        job_id=payload.job_id,
        tenant_id=payload.tenant_id,
        company_name=payload.company_name,
        periods=payload.periods,
        generated_at=reference_date,
        validation_score=payload.validation_score,
        overall_risk_score=payload.overall_risk_score,
        overall_financial_health=payload.overall_financial_health,
        executive_summary=_build_executive_summary(payload),
        board_summary=_build_board_summary(payload),
        analysis_results=payload.analysis_results,
        niif_note_drafts=_build_niif_notes(payload),
        docx_report_url=docx_s3_url,
        risk_categories=payload.risk_categories,
        risk_summary=payload.risk_summary,
        financial_ratios=payload.financial_ratios,
        fund_analysis=payload.fund_analysis,
        executive_kpis=payload.executive_kpis,
        sheet_concentration=payload.sheet_concentration,
        structured_analysis=payload.structured_analysis,
        cross_statement_signals=payload.cross_statement_signals,
        earnings_sustainability=payload.earnings_sustainability,
    )

    output = result.model_dump(mode="json")
    try:
        job_save(result.job_id, REPORT_GENERATOR, output)
    except Exception as exc:
        logger.error("Failed to persist report generator output: %s", exc)

    logger.info(
        "ReportGenerator done | job=%s docx=%s",
        payload.job_id, docx_s3_url or "none",
    )
    return output
