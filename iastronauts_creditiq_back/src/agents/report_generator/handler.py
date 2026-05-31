import logging
import os
from collections import defaultdict
from datetime import datetime

from shared.job_store import REPORT_GENERATOR, save as job_save
from shared.models import ScorerOutput, FinalReportOutput, NiifNoteDraft
from shared.s3_report_store import (
    build_pdf_s3_key,
    build_s3_key,
    fetch_historical_reports,
    save_pdf_report,
    save_report,
    slugify,
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
    rec_text = " ".join(recs[:2]) if recs else "Se recomienda mantener monitoreo periodico de liquidez, concentracion y cumplimiento NIIF."
    return (
        f"Para Junta Directiva, el fondo reporta activos por COP {_money(totals.get('total_assets'))} MM, "
        f"patrimonio por COP {_money(totals.get('total_equity'))} MM y utilidad neta por COP "
        f"{_money(totals.get('net_income'))} MM. La razon corriente es "
        f"{_money(ratios.get('razon_corriente'))}x y el endeudamiento global es "
        f"{_pct((ratios.get('endeudamiento_global') or 0) * 100)}. "
        f"El reporte identifica riesgo {_RISK_ES.get(_enum(payload.overall_risk_score), _enum(payload.overall_risk_score).lower())}, "
        f"requiere revision humana: {'si' if payload.requires_human_review else 'no'}, y conserva trazabilidad "
        f"cuenta a cuenta para soportar las conclusiones. {rec_text}"
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
            f"De acuerdo con {standard}, se recomienda revelar las variaciones materiales asociadas a {names}. "
            "La medicion debe reconciliar saldos del periodo actual y comparativo, explicar cambios de valor razonable, "
            "liquidez o contraparte cuando aplique, y dejar evidencia de los juicios contables usados por la administracion. "
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


def _build_table_analyses(payload: ScorerOutput) -> list[dict]:
    out: list[dict] = []
    sc = payload.sheet_concentration or {}
    if sc.get("asset_available"):
        top = (sc.get("asset_breakdown") or [])[:3]
        detail = "; ".join(f"{r.get('name')}: {_pct(r.get('pct'))}" for r in top)
        out.append({"title": "Tabla de activos", "analysis": f"La composicion de activos muestra {detail}. Este bloque soporta el analisis de concentracion y liquidez del fondo."})
    if sc.get("instrument_available"):
        instruments = sc.get("instrument_breakdown") or []
        detail = "; ".join(f"{r.get('instrument_type')}: {_pct(r.get('pct'))}" for r in instruments[:3])
        out.append({"title": "Tabla de instrumentos", "analysis": f"La exposicion por instrumentos se concentra en {detail}. La lectura es clave para riesgo de mercado, valor razonable y diversificacion."})
    if sc.get("bank_available"):
        banks = sc.get("bank_breakdown") or []
        detail = "; ".join(f"{r.get('name')}: {_pct(r.get('pct'))}" for r in banks[:3])
        out.append({"title": "Tabla de bancos y custodios", "analysis": f"La liquidez/custodia esta distribuida en {detail}. La concentracion por entidad debe revisarse frente a politicas internas de contraparte."})

    structured = payload.structured_analysis or {}
    labels = {
        "revenue_profitability": "Resultado y rentabilidad",
        "balance_sheet_strength": "Balance y solvencia",
        "cash_flow_quality": "Flujo de caja",
        "equity_movement": "Movimiento patrimonial",
        "risk_signals": "Senales de riesgo",
        "forward_outlook": "Perspectiva",
    }
    for key, title in labels.items():
        if structured.get(key):
            out.append({"title": title, "analysis": structured[key]})
    return out


def _build_global_analysis(payload: ScorerOutput, historical: list[FinalReportOutput]) -> list[str]:
    lines: list[str] = []
    synthesis = payload.executive_synthesis or {}
    if synthesis.get("portfolio_story"):
        lines.append(synthesis["portfolio_story"])
    if payload.portfolio_thesis:
        lines.append(payload.portfolio_thesis)
    for item in (synthesis.get("board_alerts") or [])[:3]:
        lines.append(f"Alerta para Junta: {item}")
    for signal in (payload.cross_statement_signals or [])[:4]:
        desc = signal.get("description") or signal.get("finding")
        implication = signal.get("implication")
        if desc:
            lines.append(f"{desc} {implication or ''}".strip())
    if historical:
        lines.append(f"Se consultaron {len(historical)} reporte(s) historico(s) comparables para contexto de tendencia.")
    if not lines:
        risk_summary = payload.risk_summary or {}
        for key in ("risk_narrative_paragraph1", "risk_narrative_paragraph2", "risk_narrative_paragraph3"):
            if risk_summary.get(key):
                lines.append(risk_summary[key])
    return lines


def _build_sections(payload: ScorerOutput, historical: list[FinalReportOutput]) -> dict:
    anomalies = [a for a in payload.analysis_results if a.anomaly_detected]
    drivers = []
    for a in _material_accounts(payload, 8):
        drivers.append(f"{a.account_name}: {_pct(a.variation_pct)} ({a.trend_label or 'variacion material'}).")
    for cat in (payload.risk_categories or {}).values():
        drivers.extend((cat.get("key_findings") or [])[:2])

    confidence_label = "Alta" if payload.validation_score >= 80 else "Media" if payload.validation_score >= 60 else "Baja"
    return {
        "drivers": drivers[:10],
        "recommendations": (payload.risk_summary or {}).get("risk_recommendations", []),
        "table_analyses": _build_table_analyses(payload),
        "global_analysis": _build_global_analysis(payload, historical),
        "anomaly_summary": (
            f"Se identificaron {len(anomalies)} anomalia(s) que requieren seguimiento: "
            + ", ".join(a.account_name for a in anomalies[:8])
            if anomalies
            else "No se identificaron anomalias criticas en las cuentas analizadas."
        ),
        "requires_human_review": payload.requires_human_review,
        "analysis_confidence_label": confidence_label,
    }


def _historical_summary(historical: list[FinalReportOutput]) -> list[dict]:
    return [
        {
            "job_id": r.job_id,
            "generated_at": r.generated_at.isoformat(),
            "validation_score": r.validation_score,
            "overall_risk_score": _enum(r.overall_risk_score),
            "overall_financial_health": _enum(r.overall_financial_health),
        }
        for r in historical
    ]


def lambda_handler(event: dict, context) -> dict:
    """
    Input:  ScorerOutput
    Output: FinalReportOutput with Markdown and PDF deliverables.
    """
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
        markdown_report_url="",
        pdf_report_url=None,
        risk_categories=payload.risk_categories,
        risk_summary=payload.risk_summary,
        report_sections=_build_sections(payload, historical),
        financial_ratios=payload.financial_ratios,
        fund_analysis=payload.fund_analysis,
        executive_kpis=payload.executive_kpis,
        sheet_concentration=payload.sheet_concentration,
        structured_analysis=payload.structured_analysis,
        cross_statement_signals=payload.cross_statement_signals,
        earnings_sustainability=payload.earnings_sustainability,
        historical_context=_historical_summary(historical),
    )

    company_slug = slugify(result.company_name)
    md_key = build_s3_key(result.tenant_id, company_slug, result.generated_at, result.job_id)
    pdf_key = build_pdf_s3_key(result.tenant_id, company_slug, result.generated_at, result.job_id)
    result.markdown_report_url = f"s3://{bucket}/{md_key}"
    result.pdf_report_url = f"s3://{bucket}/{pdf_key}"
    save_report(result, bucket, expected_tenant_id=payload.tenant_id)
    save_pdf_report(result, bucket, expected_tenant_id=payload.tenant_id)

    output = result.model_dump(mode="json")
    try:
        job_save(result.job_id, REPORT_GENERATOR, output)
    except Exception as exc:
        logger.error("Failed to persist report generator output to S3 job store: %s", exc)

    logger.info("ReportGenerator done | job=%s md=%s pdf=%s", payload.job_id, md_key, pdf_key)
    return output
