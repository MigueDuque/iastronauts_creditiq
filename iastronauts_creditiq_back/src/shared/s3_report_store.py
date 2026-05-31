import json
import re
from datetime import datetime

import boto3

from .models.report import FinalReportOutput, NiifNoteDraft
from .pdf_writer import markdown_to_pdf_bytes
from .tenant_context import TenantBoundaryViolation

_METADATA_RE = re.compile(r"<!--\s*CREDITIQ_REPORT\s*([\s\S]*?)-->", re.DOTALL)


def slugify(text: str) -> str:
    """Converts a company name to a filesystem-safe slug for S3 keys."""
    slug = text.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    return slug.strip("-") or "unknown"


def _trimester_months(month: int) -> list[int]:
    """Returns the three months of the calendar quarter containing *month*."""
    q = (month - 1) // 3
    return [q * 3 + 1, q * 3 + 2, q * 3 + 3]


def build_s3_key(
    tenant_id: str,
    company_slug: str,
    generated_at: datetime,
    job_id: str,
) -> str:
    return (
        f"reports/{tenant_id}/{company_slug}"
        f"/{generated_at.year}/{generated_at.month:02d}"
        f"/report_{job_id}.md"
    )


def build_pdf_s3_key(
    tenant_id: str,
    company_slug: str,
    generated_at: datetime,
    job_id: str,
) -> str:
    return (
        f"reports/{tenant_id}/{company_slug}"
        f"/{generated_at.year}/{generated_at.month:02d}"
        f"/report_{job_id}.pdf"
    )


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

_RISK_EMOJI = {"LOW": "🟢", "MEDIUM": "🟡", "HIGH": "🔴"}
_RISK_ES = {"LOW": "BAJO", "MEDIUM": "MEDIO", "HIGH": "ALTO"}


_RISK_EMOJI = {"LOW": "🟢", "MEDIUM": "🟡", "HIGH": "🔴", "CRITICAL": "🔴"}
_RISK_ES = {"LOW": "BAJO", "MEDIUM": "MEDIO", "HIGH": "ALTO", "CRITICAL": "CRITICO"}
_HEALTH_ES = {
    "GROWING": "CRECIENTE",
    "STABLE": "ESTABLE",
    "DECLINING": "DECRECIENTE",
    "CRITICAL": "CRITICO",
    "LIQUID": "LIQUIDA",
    "LEVERAGED": "APALANCADA",
    "SPECULATIVE": "ESPECULATIVA",
    "CASH_STRESSED": "ESTRES DE CAJA",
    "VALUATION_DRIVEN": "DEPENDIENTE DE VALORACION",
    "CONCENTRATED": "CONCENTRADA",
}
_MAT_ES = {"LOW": "BAJA", "MEDIUM": "MEDIA", "HIGH": "ALTA"}


def _enum_value(value) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _money(value: float | int | None) -> str:
    if value is None:
        return "-"
    return f"{float(value):,.1f}"


def _pct(value: float | int | None) -> str:
    if value is None:
        return "-"
    sign = "+" if float(value) > 0 else ""
    return f"{sign}{float(value):.1f}%"


def _top_accounts(report: FinalReportOutput, limit: int = 5) -> list:
    return sorted(
        report.analysis_results,
        key=lambda a: (
            0 if _enum_value(a.materiality) == "HIGH" else 1 if _enum_value(a.materiality) == "MEDIUM" else 2,
            -abs(a.variation_pct or 0),
            -abs(a.current_value or 0),
        ),
    )[:limit]


def _render_risk_section(report: FinalReportOutput) -> str:
    """Renders the 'Análisis de Riesgo' section with the 3 report-facing
    categories (Crédito, Mercado, Financiero). Returns '' when no data."""
    categories = report.risk_categories or {}
    summary = report.risk_summary or {}
    if not categories and not summary:
        return ""

    headline = summary.get("risk_headline", "")
    narratives = summary.get("category_narratives") or {}

    out = "## Análisis de Riesgo\n\n"
    if headline:
        out += f"**{headline}**\n\n"

    for key in ("credito", "mercado", "financiero"):
        cat = categories.get(key)
        if not cat:
            continue
        label = cat.get("label", key)
        level = cat.get("level", "N/A")
        score = cat.get("score", 0)
        emoji = _RISK_EMOJI.get(level, "⚪")
        level_es = _RISK_ES.get(level, level)
        out += f"### {emoji} {label} — {level_es} ({score}/100)\n\n"

        narrative = narratives.get(key)
        if narrative:
            out += f"{narrative}\n\n"

        findings = cat.get("key_findings") or []
        for f in findings[:5]:
            out += f"- {f}\n"
        if findings:
            out += "\n"

    recs = summary.get("risk_recommendations") or []
    if recs:
        out += "### Recomendaciones\n\n"
        for r in recs:
            out += f"- {r}\n"
        out += "\n"

    return out + "---\n\n"


def serialize_to_markdown(report: FinalReportOutput) -> str:
    """Serializes a FinalReportOutput to a structured .md file.

    The file embeds all structured data as JSON inside an HTML comment so it
    can be round-tripped by `deserialize_from_markdown`.  The markdown body
    below the comment is the human-readable rendering.
    """
    metadata = report.model_dump(mode="json")

    risk = (
        report.overall_risk_score.value
        if hasattr(report.overall_risk_score, "value")
        else report.overall_risk_score
    )

    periods_str = " — ".join(report.periods) if report.periods else "—"

    accounts_rows = ""
    for a in report.analysis_results:
        mat = a.materiality.value if hasattr(a.materiality, "value") else a.materiality
        rlv = a.risk_level.value if hasattr(a.risk_level, "value") else a.risk_level
        sign = "+" if a.variation_pct >= 0 else ""
        accounts_rows += (
            f"| {a.account_name} "
            f"| {a.current_value:,.0f} "
            f"| {a.previous_value:,.0f} "
            f"| {sign}{a.variation_pct:.1f}% "
            f"| {mat} "
            f"| {rlv} |\n"
        )

    niif_sections = ""
    for note in report.niif_note_drafts:
        niif_sections += f"\n### {note.title} ({note.niif_reference})\n\n{note.content}\n"
        if note.affected_account_ids:
            niif_sections += (
                f"\n*Cuentas afectadas: {', '.join(note.affected_account_ids)}*\n"
            )
        if note.requires_disclosure:
            niif_sections += "*Requiere revelación obligatoria.*\n"

    health = (
        report.overall_financial_health.value
        if hasattr(report.overall_financial_health, "value")
        else report.overall_financial_health
    )

    return (
        f"<!-- CREDITIQ_REPORT\n"
        f"{json.dumps(metadata, ensure_ascii=False, indent=2)}\n"
        f"-->\n\n"
        f"# {report.company_name} — Reporte Financiero CreditIQ\n\n"
        f"*Período: {periods_str} "
        f"| Generado: {report.generated_at.strftime('%Y-%m-%d %H:%M UTC')} "
        f"| Score de Validación: {report.validation_score}/100 "
        f"| Riesgo Global: {risk}*\n\n"
        f"---\n\n"
        f"## Resumen Ejecutivo\n\n"
        f"{report.executive_summary or '_Por generar_'}\n\n"
        f"---\n\n"
        f"## Resumen para Junta Directiva\n\n"
        f"{report.board_summary or '_Por generar_'}\n\n"
        f"---\n\n"
        f"{_render_risk_section(report)}"
        f"## Análisis de Variaciones por Cuenta\n\n"
        f"| Cuenta | Valor Actual | Valor Anterior | Variación % | Materialidad | Riesgo |\n"
        f"|--------|-------------:|---------------:|------------:|:------------:|:------:|\n"
        f"{accounts_rows or '_Sin cuentas analizadas_'}\n"
        f"---\n\n"
        f"## Notas NIIF Requeridas\n"
        f"{niif_sections or '_Sin notas requeridas_'}\n\n"
        f"---\n\n"
        f"## Indicadores de Cumplimiento\n\n"
        f"- **Score de validación:** {report.validation_score}/100\n"
        f"- **Salud financiera:** {health}\n"
        f"- **Riesgo global:** {risk}\n"
    )


def deserialize_from_markdown(content: str) -> FinalReportOutput:
    """Reconstructs a FinalReportOutput from a .md file produced by serialize_to_markdown."""
    match = _METADATA_RE.search(content)
    if not match:
        raise ValueError("El archivo no contiene metadatos CREDITIQ_REPORT válidos.")
    data = json.loads(match.group(1).strip())
    return FinalReportOutput.model_validate(data)


def serialize_to_markdown(report: FinalReportOutput) -> str:
    """Render the final client-facing report in a corporate Markdown structure."""
    metadata = report.model_dump(mode="json")
    risk = _enum_value(report.overall_risk_score)
    health = _enum_value(report.overall_financial_health)
    periods_str = " - ".join(report.periods) if report.periods else "-"
    sections = report.report_sections or {}

    drivers = sections.get("drivers") or []
    recommendations = sections.get("recommendations") or (report.risk_summary or {}).get("risk_recommendations") or []
    table_analyses = sections.get("table_analyses") or []
    global_analysis = sections.get("global_analysis") or []
    anomaly_summary = sections.get("anomaly_summary") or "Sin anomalias criticas."
    human_review = sections.get("requires_human_review")
    if human_review is None:
        human_review = report.validation_score < 80 or any(a.anomaly_detected for a in report.analysis_results)

    accounts_rows = ""
    for a in _top_accounts(report, limit=len(report.analysis_results)):
        accounts_rows += (
            f"| {a.account_name} "
            f"| {_money(a.current_value)} "
            f"| {_money(a.previous_value)} "
            f"| {_pct(a.variation_pct)} "
            f"| {_MAT_ES.get(_enum_value(a.materiality), _enum_value(a.materiality))} "
            f"| {_RISK_ES.get(_enum_value(a.risk_level), _enum_value(a.risk_level))} "
            f"| {a.executive_insight} |\n"
        )

    board_rows = ""
    for a in _top_accounts(report, 3):
        board_rows += f"| {a.account_name} | COP {_money(a.current_value)} MM ({_pct(a.variation_pct)}) |\n"
    board_rows += f"| Riesgo Global | {_RISK_ES.get(risk, risk)} |\n"
    board_rows += f"| Score de Validacion | {report.validation_score}/100 |\n"
    board_rows += f"| Requiere Revision Humana | {'Si' if human_review else 'No'} |\n"

    niif_sections = ""
    account_names = {a.account_id: a.account_name for a in report.analysis_results}
    for i, note in enumerate(report.niif_note_drafts, start=1):
        affected = [account_names.get(aid, aid) for aid in note.affected_account_ids]
        niif_sections += f"\n### Nota {i}: {note.title} ({note.niif_reference})\n\n{note.content}\n\n"
        if affected:
            niif_sections += f"*Cuentas afectadas: {', '.join(affected)}*\n"
        niif_sections += "*Requiere revelacion obligatoria.*\n" if note.requires_disclosure else "*Nota informativa interna.*\n"

    drivers_md = "".join(f"- {d}\n" for d in drivers[:8]) or "- Sin drivers materiales adicionales.\n"
    recs_md = "".join(f"- {r}\n" for r in recommendations[:8]) or "- Mantener monitoreo periodico de riesgos, liquidez y concentracion.\n"
    global_md = "".join(f"- {g}\n" for g in global_analysis[:10]) or "- Sin analisis global adicional disponible.\n"
    table_md = "".join(
        f"### {t.get('title', 'Tabla analizada')}\n\n{t.get('analysis', '')}\n\n"
        for t in table_analyses
    )

    return (
        f"<!-- CREDITIQ_REPORT\n"
        f"{json.dumps(metadata, ensure_ascii=False, indent=2)}\n"
        f"-->\n\n"
        f"# {report.company_name} - Reporte Financiero CreditIQ\n\n"
        f"*Periodo: {periods_str} | Generado: {report.generated_at.strftime('%Y-%m-%d %H:%M UTC')} "
        f"| Score de Validacion: {report.validation_score}/100 | Riesgo Global: {_RISK_ES.get(risk, risk)}*\n\n"
        f"---\n\n"
        f"## Resumen Ejecutivo\n\n"
        f"{report.executive_summary or '_Por generar_'}\n\n"
        f"**Principales drivers del periodo:**\n\n"
        f"{drivers_md}\n"
        f"{anomaly_summary}\n\n"
        f"---\n\n"
        f"## Resumen para Junta Directiva\n\n"
        f"{report.board_summary or '_Por generar_'}\n\n"
        f"| Indicador | Resultado |\n"
        f"|-----------|-----------|\n"
        f"{board_rows}\n"
        f"**Recomendaciones:**\n\n"
        f"{recs_md}\n"
        f"---\n\n"
        f"## Analisis Global del Fondo\n\n"
        f"{global_md}\n"
        f"{table_md}"
        f"---\n\n"
        f"{_render_risk_section(report)}"
        f"## Analisis de Variaciones por Cuenta\n\n"
        f"| Cuenta | Valor Actual (COP MM) | Valor Anterior (COP MM) | Variacion % | Materialidad | Riesgo | Analisis ejecutivo |\n"
        f"|--------|----------------------:|------------------------:|------------:|:------------:|:------:|--------------------|\n"
        f"{accounts_rows or '_Sin cuentas analizadas_'}\n"
        f"**Anomalias detectadas:** {anomaly_summary}\n\n"
        f"---\n\n"
        f"## Notas NIIF Requeridas\n"
        f"{niif_sections or '_Sin notas requeridas_'}\n\n"
        f"---\n\n"
        f"## Indicadores de Cumplimiento\n\n"
        f"- **Score de validacion:** {report.validation_score}/100\n"
        f"- **Salud financiera:** {_HEALTH_ES.get(health, health)}\n"
        f"- **Riesgo global:** {_RISK_ES.get(risk, risk)}\n"
        f"- **Requiere revision humana:** {'Si' if human_review else 'No'}\n"
        f"- **Confianza del analisis:** {sections.get('analysis_confidence_label', 'Alta' if report.validation_score >= 80 else 'Media' if report.validation_score >= 60 else 'Baja')}\n"
    )


# ---------------------------------------------------------------------------
# S3 operations
# ---------------------------------------------------------------------------

def assert_report_tenant(report: FinalReportOutput, expected_tenant_id: str) -> None:
    """
    Raises TenantBoundaryViolation if the report's tenant_id does not match
    the expected tenant_id from the pipeline context.

    WHY: A bug in any agent could silently drop or corrupt tenant_id.
    The last write before S3 is the final gate — if tenant_id drifted anywhere
    in the pipeline, this catches it before data is persisted under the wrong
    tenant prefix.
    """
    if report.tenant_id != expected_tenant_id:
        raise TenantBoundaryViolation(
            f"Report tenant_id '{report.tenant_id}' does not match "
            f"pipeline tenant_id '{expected_tenant_id}'. "
            f"This indicates a tenant_id mutation somewhere in the agent pipeline."
        )


def save_report(
    report: FinalReportOutput,
    bucket: str,
    *,
    s3_client=None,
    expected_tenant_id: str = None,
) -> str:
    """Uploads *report* as a .md file to S3 and returns the S3 key.

    The key encodes tenant, company, year, and month so that
    `fetch_historical_reports` can reconstruct it with prefix listings.

    expected_tenant_id: when provided, asserts that the report's tenant_id
    has not drifted from the pipeline's original tenant context.
    """
    if expected_tenant_id:
        assert_report_tenant(report, expected_tenant_id)

    client = s3_client or boto3.client("s3")
    company_slug = slugify(report.company_name)
    key = build_s3_key(report.tenant_id, company_slug, report.generated_at, report.job_id)
    client.put_object(
        Bucket=bucket,
        Key=key,
        Body=serialize_to_markdown(report).encode("utf-8"),
        ContentType="text/markdown; charset=utf-8",
    )
    return key


def save_pdf_report(
    report: FinalReportOutput,
    bucket: str,
    *,
    s3_client=None,
    expected_tenant_id: str = None,
) -> str:
    """Uploads a dependency-free PDF rendering of the final report to S3."""
    if expected_tenant_id:
        assert_report_tenant(report, expected_tenant_id)

    client = s3_client or boto3.client("s3")
    company_slug = slugify(report.company_name)
    key = build_pdf_s3_key(report.tenant_id, company_slug, report.generated_at, report.job_id)
    markdown = serialize_to_markdown(report)
    pdf_bytes = markdown_to_pdf_bytes(markdown, title=f"{report.company_name} - CreditIQ")
    client.put_object(
        Bucket=bucket,
        Key=key,
        Body=pdf_bytes,
        ContentType="application/pdf",
    )
    return key


def fetch_historical_reports(
    tenant_id: str,
    company_slug: str,
    reference_date: datetime,
    bucket: str,
    *,
    s3_client=None,
) -> list[FinalReportOutput]:
    """Returns historical reports relevant to *reference_date*:

    - Every report from the same calendar trimester one year ago.
    - Every report from December one year ago (year-end baseline),
      unless December already falls in that trimester (Q4).

    Uses S3 prefix listing, so only the required month folders are read.
    """
    client = s3_client or boto3.client("s3")
    prev_year = reference_date.year - 1
    trimester = _trimester_months(reference_date.month)

    prefixes: set[str] = set()
    for month in trimester:
        prefixes.add(
            f"reports/{tenant_id}/{company_slug}/{prev_year}/{month:02d}/"
        )
    if 12 not in trimester:
        prefixes.add(
            f"reports/{tenant_id}/{company_slug}/{prev_year}/12/"
        )

    reports: list[FinalReportOutput] = []
    paginator = client.get_paginator("list_objects_v2")

    for prefix in sorted(prefixes):
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if not key.endswith(".md"):
                    continue
                body = client.get_object(Bucket=bucket, Key=key)["Body"].read()
                try:
                    reports.append(deserialize_from_markdown(body.decode("utf-8")))
                except (ValueError, KeyError, json.JSONDecodeError):
                    pass  # skip malformed files; don't halt the pipeline

    return reports
