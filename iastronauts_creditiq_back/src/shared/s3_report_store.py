import json
import re
from datetime import datetime

import boto3

from .models.report import FinalReportOutput, NiifNoteDraft
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


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

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
