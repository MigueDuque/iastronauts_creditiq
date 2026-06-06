import json
import logging
import os
import re
from datetime import datetime, timezone

from botocore.exceptions import ClientError

from shared.job_store import (
    load as job_load, save as job_save, load_text as job_load_text,
    EXTRACTOR, FINANCIAL_ANALYZER, RISK_SCORER, REPORT_GENERATOR, REVISOR,
    CHAT_LOG, ANALYSIS_SUMMARY,
)
from shared.llm_provider import LLMProvider
from shared.models import FinalReportOutput
from shared.models.base import FinancialHealth, MaterialityLevel, RiskLevel
from shared.models.revisor import (
    RevisorOutput,
    ValidationCategory,
    ValidationFlag,
    ValidationSeverity,
)
from shared.s3_instructions import load_text

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Narrative system prompt — S3 → local → inline fallback
# ---------------------------------------------------------------------------

_PROMPT_S3_KEY = "instructions/prompts/05_prompt_agent_revisor-inteligente.md"
_LOCAL_PROMPT_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "system_pompts",
                 "05_prompt_agent_revisor-inteligente.md")
)
_INLINE_FALLBACK = (
    "Eres un revisor de calidad de reportes financieros en español. "
    "Verifica coherencia entre narrativa y datos. Devuelve un JSON array de flags."
)
_prompt_cache: str | None = None

_CHAT_PROMPT_S3_KEY = "instructions/prompts/05b_prompt_chat_revisor-inteligente.md"
_LOCAL_CHAT_PROMPT_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "system_pompts",
                 "05b_prompt_chat_revisor-inteligente.md")
)
_chat_prompt_cache: str | None = None


def _get_narrative_prompt() -> str:
    global _prompt_cache
    if _prompt_cache is not None:
        return _prompt_cache
    s3_text = load_text(_PROMPT_S3_KEY, fallback="")
    if s3_text:
        _prompt_cache = s3_text
        logger.info("revisor_prompt | source=s3 chars=%d", len(s3_text))
        return _prompt_cache
    try:
        with open(_LOCAL_PROMPT_PATH, encoding="utf-8") as f:
            local_text = f.read()
        if len(local_text) > 100:
            _prompt_cache = local_text
            logger.info("revisor_prompt | source=local chars=%d", len(local_text))
            return _prompt_cache
    except OSError:
        pass
    logger.warning("revisor_prompt | source=inline_fallback")
    _prompt_cache = _INLINE_FALLBACK
    return _prompt_cache


def _get_chat_prompt() -> str:
    global _chat_prompt_cache
    if _chat_prompt_cache is not None:
        return _chat_prompt_cache
    s3_text = load_text(_CHAT_PROMPT_S3_KEY, fallback="")
    if s3_text:
        _chat_prompt_cache = s3_text
        logger.info("chat_prompt | source=s3 chars=%d", len(s3_text))
        return _chat_prompt_cache
    try:
        with open(_LOCAL_CHAT_PROMPT_PATH, encoding="utf-8") as f:
            local_text = f.read()
        if len(local_text) > 100:
            _chat_prompt_cache = local_text
            logger.info("chat_prompt | source=local chars=%d", len(local_text))
            return _chat_prompt_cache
    except OSError:
        pass
    logger.warning("chat_prompt | source=inline_fallback")
    _chat_prompt_cache = _CHAT_SYSTEM_PROMPT
    return _chat_prompt_cache


_PENALTY_ERROR = 10
_PENALTY_WARNING = 3


# ---------------------------------------------------------------------------
# Category 1 — Structural
# ---------------------------------------------------------------------------

def _check_structural(report: FinalReportOutput) -> list[ValidationFlag]:
    flags: list[ValidationFlag] = []

    # 1.2 Required string fields non-empty
    required_strings = [
        ("job_id", report.job_id),
        ("tenant_id", report.tenant_id),
        ("company_name", report.company_name),
        ("executive_summary", report.executive_summary),
        ("board_summary", report.board_summary),
        ("docx_report_url", report.docx_report_url),
    ]
    for field_name, value in required_strings:
        if not value or not value.strip():
            flags.append(ValidationFlag(
                check_id="1.2",
                category=ValidationCategory.STRUCTURAL,
                severity=ValidationSeverity.ERROR,
                message=f"Campo requerido vacío: {field_name}",
                affected_field=field_name,
                actual_value=repr(value),
            ))

    # 1.3 periods must have exactly 2 entries
    if len(report.periods) != 2:
        flags.append(ValidationFlag(
            check_id="1.3",
            category=ValidationCategory.STRUCTURAL,
            severity=ValidationSeverity.ERROR,
            message=f"periods debe tener exactamente 2 entradas, tiene {len(report.periods)}",
            affected_field="periods",
            expected_value="2",
            actual_value=str(len(report.periods)),
        ))

    # 1.4 periods[0] more recent than periods[1]
    if len(report.periods) == 2:
        try:
            p0 = datetime.strptime(report.periods[0], "%Y-%m")
            p1 = datetime.strptime(report.periods[1], "%Y-%m")
            if p0 <= p1:
                flags.append(ValidationFlag(
                    check_id="1.4",
                    category=ValidationCategory.STRUCTURAL,
                    severity=ValidationSeverity.ERROR,
                    message="periods[0] debe ser posterior a periods[1] (período actual > período anterior)",
                    affected_field="periods",
                    expected_value=f"{report.periods[0]} > {report.periods[1]}",
                    actual_value=f"{report.periods[0]} ≤ {report.periods[1]}",
                ))
        except ValueError:
            flags.append(ValidationFlag(
                check_id="1.4",
                category=ValidationCategory.STRUCTURAL,
                severity=ValidationSeverity.ERROR,
                message="periods no tienen formato YYYY-MM válido",
                affected_field="periods",
                actual_value=str(report.periods),
            ))

    # 1.5 generated_at must be reasonable
    now = datetime.now(timezone.utc)
    gen = report.generated_at
    gen_aware = gen.replace(tzinfo=timezone.utc) if gen.tzinfo is None else gen
    if gen_aware > now:
        flags.append(ValidationFlag(
            check_id="1.5",
            category=ValidationCategory.STRUCTURAL,
            severity=ValidationSeverity.WARNING,
            message="generated_at es una fecha futura",
            affected_field="generated_at",
            actual_value=str(report.generated_at),
        ))
    elif gen_aware.year < 2020:
        flags.append(ValidationFlag(
            check_id="1.5",
            category=ValidationCategory.STRUCTURAL,
            severity=ValidationSeverity.WARNING,
            message="generated_at es anterior a 2020 — posible error de timestamp",
            affected_field="generated_at",
            actual_value=str(report.generated_at),
        ))

    # 1.8 analysis_results not empty
    if not report.analysis_results:
        flags.append(ValidationFlag(
            check_id="1.8",
            category=ValidationCategory.STRUCTURAL,
            severity=ValidationSeverity.ERROR,
            message="analysis_results está vacío — no hay cuentas analizadas",
            affected_field="analysis_results",
        ))

    return flags


# ---------------------------------------------------------------------------
# Category 2 — Mathematical
# ---------------------------------------------------------------------------

def _check_mathematical(report: FinalReportOutput) -> list[ValidationFlag]:
    flags: list[ValidationFlag] = []
    ABS_TOLERANCE = 0.15   # COP MM rounding tolerance for absolute_variation
    PCT_TOLERANCE = 0.2    # percentage points tolerance for variation_pct

    for i, account in enumerate(report.analysis_results):
        field = f"analysis_results[{i}]"

        # 2.1 absolute_variation — reliability-aware (§4.1).
        # When there is no baseline period, the absolute movement is the full
        # current value (a brand-new position), so the strict
        # current − previous equality does not apply.
        if not account.has_previous_value:
            expected_abs = account.current_value
            abs_explanation = "current_value (cuenta nueva, sin período anterior)"
        else:
            expected_abs = account.current_value - account.previous_value
            abs_explanation = "current_value - previous_value"
        # Relative tolerance for large magnitudes so 40,000 COP-MM rows don't trip
        # on rounding (§4.3).
        abs_tol = max(ABS_TOLERANCE, abs(expected_abs) * 1e-4)
        if abs(expected_abs - account.absolute_variation) > abs_tol:
            flags.append(ValidationFlag(
                check_id="2.1",
                category=ValidationCategory.MATHEMATICAL,
                severity=ValidationSeverity.ERROR,
                message=f"absolute_variation incorrecto en '{account.account_name}' ({abs_explanation})",
                affected_field=f"{field}.absolute_variation",
                expected_value=f"{expected_abs:.2f}",
                actual_value=f"{account.absolute_variation:.2f}",
            ))

        # 2.2 / 2.3 variation_pct — null-aware (§4.2).
        # A None percentage is intentional (suppressed: no/near-zero baseline or
        # extreme reclassification) — never a math error. Only validate a value
        # that is actually present, and only when a real baseline exists.
        if account.variation_pct is None:
            continue
        if account.previous_value == 0:
            # Present % with a zero baseline is contradictory but low-severity:
            # surface as INFO, not a scored WARNING.
            flags.append(ValidationFlag(
                check_id="2.3",
                category=ValidationCategory.MATHEMATICAL,
                severity=ValidationSeverity.INFO,
                message=f"'{account.account_name}' reporta variation_pct con previous_value=0",
                affected_field=f"{field}.variation_pct",
                actual_value=f"{account.variation_pct:.1f}",
            ))
            continue
        # Prefer the engine's own traced number over recomputing, so the validator
        # stays consistent with the engine's rounding/suppression decisions (§4.2).
        traced_pct = None
        trace = account.computation_trace or {}
        if isinstance(trace, dict):
            traced_pct = (trace.get("variation_pct") or {}).get("result")
        reference_pct = (
            traced_pct
            if isinstance(traced_pct, (int, float))
            else (account.absolute_variation / account.previous_value) * 100
        )
        if abs(reference_pct - account.variation_pct) > PCT_TOLERANCE:
            flags.append(ValidationFlag(
                check_id="2.2",
                category=ValidationCategory.MATHEMATICAL,
                severity=ValidationSeverity.ERROR,
                message=f"variation_pct incorrecto en '{account.account_name}'",
                affected_field=f"{field}.variation_pct",
                expected_value=f"{reference_pct:.1f}",
                actual_value=f"{account.variation_pct:.1f}",
            ))

    # 2.4 Scale outlier — flag if any account is 1000× the median
    if len(report.analysis_results) >= 3:
        nonzero = sorted(abs(a.current_value) for a in report.analysis_results if a.current_value != 0)
        median = nonzero[len(nonzero) // 2]
        if median > 0:
            for i, account in enumerate(report.analysis_results):
                if abs(account.current_value) > median * 1_000:
                    flags.append(ValidationFlag(
                        check_id="2.4",
                        category=ValidationCategory.MATHEMATICAL,
                        severity=ValidationSeverity.WARNING,
                        message=(
                            f"'{account.account_name}' tiene un valor 1000× mayor que la mediana del reporte "
                            f"({median:.0f} COP MM) — posible error de escala o unidades"
                        ),
                        affected_field=f"analysis_results[{i}].current_value",
                        expected_value=f"~{median:.0f} (orden de magnitud)",
                        actual_value=f"{account.current_value:.0f}",
                    ))

    # 2.5 Balance-sheet sanity: equity cannot exceed total assets (§4.5).
    # Pulls the canonical totals computed by ratio_engine rather than re-summing
    # line items (which double-counts subtotals). Catches the headline impossibility
    # of "Patrimonio > Activos" that no rule previously detected.
    totals = (report.financial_ratios or {}).get("totals") or {}
    total_assets = totals.get("total_assets")
    total_equity = totals.get("total_equity")
    if isinstance(total_assets, (int, float)) and isinstance(total_equity, (int, float)):
        # Relative tolerance for rounding on large magnitudes.
        equity_tol = max(0.15, abs(total_assets) * 1e-4)
        if total_equity > total_assets + equity_tol and total_assets > 0:
            flags.append(ValidationFlag(
                check_id="2.5",
                category=ValidationCategory.MATHEMATICAL,
                severity=ValidationSeverity.ERROR,
                message=(
                    f"Patrimonio (COP {total_equity:,.1f} MM) excede el total de activos "
                    f"(COP {total_assets:,.1f} MM) — imposible en un balance cuadrado; "
                    f"revisar doble conteo de subtotales o la base de las cifras de portada"
                ),
                affected_field="financial_ratios.totals.total_equity",
                expected_value=f"≤ {total_assets:,.1f}",
                actual_value=f"{total_equity:,.1f}",
            ))

    # 2.6 P&L sanity: net income cannot exceed total revenue (a >100% net margin is
    # impossible for ordinary operations). This is the safety net for cross-sheet
    # double-counting / expense-sign errors that ratio_engine's reported-total
    # reconciliation could not resolve (e.g. a statement with no reported net-income row).
    net_income = totals.get("net_income")
    total_revenue = totals.get("total_revenue")
    if (
        isinstance(net_income, (int, float))
        and isinstance(total_revenue, (int, float))
        and total_revenue > 0
        and net_income > total_revenue * 1.01
    ):
        flags.append(ValidationFlag(
            check_id="2.6",
            category=ValidationCategory.MATHEMATICAL,
            severity=ValidationSeverity.ERROR,
            message=(
                f"Utilidad neta (COP {net_income:,.1f} MM) excede los ingresos totales "
                f"(COP {total_revenue:,.1f} MM) — margen neto >100% es imposible; "
                f"probable doble conteo entre hojas o error de signo en gastos"
            ),
            affected_field="financial_ratios.totals.net_income",
            expected_value=f"≤ {total_revenue:,.1f}",
            actual_value=f"{net_income:,.1f}",
        ))

    return flags


# ---------------------------------------------------------------------------
# Category 3 — Cross-reference integrity
# ---------------------------------------------------------------------------

def _check_cross_references(report: FinalReportOutput) -> list[ValidationFlag]:
    flags: list[ValidationFlag] = []

    account_ids = {a.account_id for a in report.analysis_results}
    account_by_id = {a.account_id: a for a in report.analysis_results}
    # Accounts reference NIIF *standards* (e.g. "NIIF 9", "NIC 1"), while notes are
    # keyed by note_id ("note-001") but carry the standard in `niif_reference`.
    # Cross-references must therefore be checked against the set of drafted
    # standards, NOT the note_ids — comparing the two always mismatched and drove
    # the score to the floor with false positives (§2).
    available_standards = {n.niif_reference for n in report.niif_note_drafts}

    for i, account in enumerate(report.analysis_results):
        # 3.1 niif_note_references → the standard must have been drafted as a note
        for ref in account.niif_note_references:
            if ref not in available_standards:
                flags.append(ValidationFlag(
                    check_id="3.1",
                    category=ValidationCategory.CROSS_REFERENCE,
                    severity=ValidationSeverity.ERROR,
                    message=f"'{account.account_name}' referencia el estándar '{ref}' que no tiene nota en niif_note_drafts",
                    affected_field=f"analysis_results[{i}].niif_note_references",
                    actual_value=ref,
                ))

        # 3.3 requires_niif_note: true → must have at least one reference
        if account.requires_niif_note and not account.niif_note_references:
            flags.append(ValidationFlag(
                check_id="3.3",
                category=ValidationCategory.CROSS_REFERENCE,
                severity=ValidationSeverity.ERROR,
                message=f"'{account.account_name}' tiene requires_niif_note=true pero niif_note_references está vacío",
                affected_field=f"analysis_results[{i}].niif_note_references",
            ))

    for j, note in enumerate(report.niif_note_drafts):
        for aid in note.affected_account_ids:
            # 3.2 affected_account_ids → must exist in analysis_results
            if aid not in account_ids:
                flags.append(ValidationFlag(
                    check_id="3.2",
                    category=ValidationCategory.CROSS_REFERENCE,
                    severity=ValidationSeverity.ERROR,
                    message=f"Nota '{note.note_id}' referencia cuenta '{aid}' que no existe en analysis_results",
                    affected_field=f"niif_note_drafts[{j}].affected_account_ids",
                    actual_value=aid,
                ))
                continue

            account = account_by_id[aid]

            # 3.4 Bidirectionality: nota cita cuenta → cuenta debe citar el estándar.
            # Compared by standard (niif_reference), not note_id — accounts never
            # carry note_ids, only standards.
            if note.niif_reference not in account.niif_note_references:
                flags.append(ValidationFlag(
                    check_id="3.4",
                    category=ValidationCategory.CROSS_REFERENCE,
                    severity=ValidationSeverity.WARNING,
                    message=(
                        f"Nota '{note.note_id}' ({note.niif_reference}) cita la cuenta "
                        f"'{account.account_name}' pero la cuenta no referencia el estándar "
                        f"{note.niif_reference} — asimetría en referencias cruzadas"
                    ),
                    affected_field=f"analysis_results[account_id={aid}].niif_note_references",
                ))

            # 3.5 Nota cita cuenta que no requiere revelación
            if not account.requires_niif_note:
                flags.append(ValidationFlag(
                    check_id="3.5",
                    category=ValidationCategory.CROSS_REFERENCE,
                    severity=ValidationSeverity.WARNING,
                    message=(
                        f"Nota '{note.note_id}' cita cuenta '{account.account_name}' "
                        f"que tiene requires_niif_note=false"
                    ),
                    affected_field=f"niif_note_drafts[{j}].affected_account_ids",
                ))

    # 3.6 Hierarchy/duplication integrity. The analyzer tags each row's role so no
    # consumer mixes a summary line with its own breakdown on a detail sheet. Verify:
    #   (a) every breakdown_detail's parent exists, and
    #   (b) a parent's breakdown children reconcile to the parent's value.
    # A material mismatch means the breakdown is incomplete or duplicated — exactly the
    # "same money in more than one table" hazard the hierarchy model exists to prevent.
    children_by_parent: dict[str, list] = {}
    for a in report.analysis_results:
        pid = getattr(a, "parent_account_id", None)
        if getattr(a, "statement_role", "") == "breakdown_detail" and pid:
            if pid not in account_ids:
                flags.append(ValidationFlag(
                    check_id="3.6",
                    category=ValidationCategory.CROSS_REFERENCE,
                    severity=ValidationSeverity.WARNING,
                    message=(
                        f"'{a.account_name}' es un detalle (breakdown) cuyo parent "
                        f"'{pid}' no existe en analysis_results"
                    ),
                    affected_field=f"analysis_results[account_id={a.account_id}].parent_account_id",
                ))
                continue
            children_by_parent.setdefault(pid, []).append(a)

    for pid, kids in children_by_parent.items():
        parent = account_by_id.get(pid)
        if parent is None:
            continue
        child_sum = sum(c.current_value for c in kids)
        tol = max(0.5, abs(parent.current_value) * 0.02)  # 2% relative, 0.5 COP-MM floor
        if abs(child_sum - parent.current_value) > tol:
            flags.append(ValidationFlag(
                check_id="3.6",
                category=ValidationCategory.CROSS_REFERENCE,
                severity=ValidationSeverity.WARNING,
                message=(
                    f"El desglose de '{parent.account_name}' suma "
                    f"{child_sum:,.1f} COP MM pero la línea resumen reporta "
                    f"{parent.current_value:,.1f} COP MM — desglose incompleto o "
                    f"duplicado (riesgo de doble conteo)"
                ),
                affected_field=f"analysis_results[account_id={pid}].current_value",
                expected_value=f"{parent.current_value:,.1f}",
                actual_value=f"{child_sum:,.1f}",
            ))

    return flags


# ---------------------------------------------------------------------------
# Category 4 — Business logic
# ---------------------------------------------------------------------------

def _check_business_logic(report: FinalReportOutput) -> list[ValidationFlag]:
    flags: list[ValidationFlag] = []

    anomaly_count = sum(1 for a in report.analysis_results if a.anomaly_detected)

    # 4.1 validation_score vs anomalies
    if anomaly_count > 0 and report.validation_score >= 80:
        flags.append(ValidationFlag(
            check_id="4.1",
            category=ValidationCategory.BUSINESS_LOGIC,
            severity=ValidationSeverity.WARNING,
            message=(
                f"Se detectaron {anomaly_count} anomalía(s) pero validation_score es "
                f"{report.validation_score} (≥80) — score debería reflejar las anomalías"
            ),
            affected_field="validation_score",
            actual_value=str(report.validation_score),
        ))

    # 4.2 overall_risk_score vs individual account risk levels
    high_risk_names = [a.account_name for a in report.analysis_results if a.risk_level == RiskLevel.HIGH]
    if high_risk_names and report.overall_risk_score == RiskLevel.LOW:
        flags.append(ValidationFlag(
            check_id="4.2",
            category=ValidationCategory.BUSINESS_LOGIC,
            severity=ValidationSeverity.WARNING,
            message=(
                f"overall_risk_score es LOW pero {len(high_risk_names)} cuenta(s) tienen risk_level HIGH: "
                f"{high_risk_names}"
            ),
            affected_field="overall_risk_score",
            actual_value="LOW",
        ))

    # 4.3 anomaly_detected with risk_level=LOW — INFORMATIONAL only.
    # anomaly_detected is a *statistical variation outlier*; account risk_level is
    # intentionally LOW because deterministic risk is owned by Agent 3 (the "LLM
    # cannot override deterministic risk levels" rule). These are different axes,
    # so this is surfaced as INFO (no score impact) rather than an ERROR (§4.4).
    for i, account in enumerate(report.analysis_results):
        if account.anomaly_detected and account.risk_level == RiskLevel.LOW:
            flags.append(ValidationFlag(
                check_id="4.3",
                category=ValidationCategory.BUSINESS_LOGIC,
                severity=ValidationSeverity.INFO,
                message=(
                    f"'{account.account_name}' tiene anomaly_detected=true con risk_level=LOW "
                    f"(variación atípica; el riesgo determinístico lo asigna el Agente 3)"
                ),
                affected_field=f"analysis_results[{i}].risk_level",
                actual_value="LOW",
            ))

    # 4.4 Large variation vs materiality — INFORMATIONAL only.
    # Materiality is an *absolute-magnitude* judgement (Agent 2 classifies it as |Δ| vs the
    # 1%-of-base threshold, per Colombian NIIF audit standard), while variation_pct is a
    # *relative* axis. A small line that swings >100% (e.g. a minor cash-flow row) is correctly
    # MEDIUM/LOW materiality even though its percentage is large — the two are different axes,
    # exactly like anomaly_detected vs risk_level in 4.3. Surfacing this as a scored WARNING
    # contradicted the deterministic engine and floored the score with false positives, so it
    # is reported as INFO (no score impact) for visibility only.
    for i, account in enumerate(report.analysis_results):
        if (
            account.variation_pct is not None
            and account.previous_value != 0
            and abs(account.variation_pct) > 100
            and abs(account.current_value) > 10
            and account.materiality != MaterialityLevel.HIGH
        ):
            flags.append(ValidationFlag(
                check_id="4.4",
                category=ValidationCategory.BUSINESS_LOGIC,
                severity=ValidationSeverity.INFO,
                message=(
                    f"'{account.account_name}' tiene variación de {account.variation_pct:.1f}% "
                    f"con materialidad '{account.materiality.value}' (la materialidad es por "
                    f"magnitud absoluta, no por porcentaje; eje distinto)"
                ),
                affected_field=f"analysis_results[{i}].materiality",
                actual_value=account.materiality.value,
            ))

    # 4.5 HIGH materiality → at least 2 possible_causes
    for i, account in enumerate(report.analysis_results):
        if account.materiality == MaterialityLevel.HIGH and len(account.possible_causes) < 2:
            flags.append(ValidationFlag(
                check_id="4.5",
                category=ValidationCategory.BUSINESS_LOGIC,
                severity=ValidationSeverity.WARNING,
                message=f"'{account.account_name}' es HIGH materiality pero tiene menos de 2 possible_causes",
                affected_field=f"analysis_results[{i}].possible_causes",
                expected_value="≥2 causas",
                actual_value=str(len(account.possible_causes)),
            ))

    # 4.6 Financial health must be coherent with profit trend
    profit_keywords = ("utilidad", "resultado", "ganancia", "pérdida", "profit", "income")
    profit_accounts = [
        a for a in report.analysis_results
        if any(kw in a.account_name.lower() for kw in profit_keywords)
    ]
    if profit_accounts and profit_accounts[0].variation_pct is not None:
        profit = profit_accounts[0]
        if profit.variation_pct > 10 and report.overall_financial_health == FinancialHealth.DECLINING:
            flags.append(ValidationFlag(
                check_id="4.6",
                category=ValidationCategory.BUSINESS_LOGIC,
                severity=ValidationSeverity.WARNING,
                message=(
                    f"overall_financial_health es DECLINING pero '{profit.account_name}' "
                    f"creció {profit.variation_pct:.1f}% — revisar coherencia"
                ),
                affected_field="overall_financial_health",
                actual_value="DECLINING",
            ))
        elif profit.variation_pct < -10 and report.overall_financial_health == FinancialHealth.GROWING:
            flags.append(ValidationFlag(
                check_id="4.6",
                category=ValidationCategory.BUSINESS_LOGIC,
                severity=ValidationSeverity.WARNING,
                message=(
                    f"overall_financial_health es GROWING pero '{profit.account_name}' "
                    f"cayó {profit.variation_pct:.1f}% — revisar coherencia"
                ),
                affected_field="overall_financial_health",
                actual_value="GROWING",
            ))

    # 4.7 At least one account must be present
    if len(report.analysis_results) < 1:
        flags.append(ValidationFlag(
            check_id="4.7",
            category=ValidationCategory.BUSINESS_LOGIC,
            severity=ValidationSeverity.ERROR,
            message="analysis_results está vacío — no hay cuentas para analizar",
            affected_field="analysis_results",
        ))

    return flags


# ---------------------------------------------------------------------------
# Category 5 — Structural consistency
# ---------------------------------------------------------------------------

_REPORT_URL_RE = re.compile(
    r"^s3://[^/]+/jobs/\d{4}-\d{2}-\d{2}/[^/]+/.+\.docx$"
)


def _check_consistency(report: FinalReportOutput) -> list[ValidationFlag]:
    flags: list[ValidationFlag] = []

    # 5.1 analysis_results must be non-empty for the markdown table to have rows
    if not report.analysis_results:
        flags.append(ValidationFlag(
            check_id="5.1",
            category=ValidationCategory.CONSISTENCY,
            severity=ValidationSeverity.ERROR,
            message="La tabla de variaciones no tendrá filas porque analysis_results está vacío",
            affected_field="analysis_results",
        ))

    # 5.4 docx_report_url follows expected S3 URL pattern
    if report.docx_report_url and not _REPORT_URL_RE.match(report.docx_report_url):
        flags.append(ValidationFlag(
            check_id="5.4",
            category=ValidationCategory.CONSISTENCY,
            severity=ValidationSeverity.WARNING,
            message=(
                "docx_report_url no sigue el patrón esperado: "
                "s3://{bucket}/jobs/{YYYY-MM-DD}/{job_id}/{nombre}.docx"
            ),
            affected_field="docx_report_url",
            actual_value=report.docx_report_url,
        ))

    return flags


# ---------------------------------------------------------------------------
# Category 6 — Narrative quality (LLM)
# ---------------------------------------------------------------------------

def _check_narrative(report: FinalReportOutput, llm: LLMProvider) -> list[ValidationFlag]:
    flags: list[ValidationFlag] = []

    # Deterministic sub-checks before spending tokens
    # 6.2 executive_summary sentence count.
    # A naive `.split(".")` counts every decimal and abbreviation as a sentence break
    # ("+1.179 MM", "17.0%", "76/100" → many false sentences), which inflated the count to
    # ~12 and produced a false WARNING. Count sentence terminators that are followed by
    # whitespace + a capital/EOL only, so "1.179" stays one token. The cap is 6 because the
    # field is now an LLM executive *narrative* (a multi-sentence thesis) plus an appended
    # risk/variation line — not the legacy 3-sentence blurb.
    sentence_end = re.compile(r"[.!?]+(?:\s+(?=[A-ZÁÉÍÓÚÑ])|\s*$)")
    sentences = [s for s in sentence_end.split(report.executive_summary) if s.strip()]
    if len(sentences) > 6:
        flags.append(ValidationFlag(
            check_id="6.2",
            category=ValidationCategory.NARRATIVE,
            severity=ValidationSeverity.WARNING,
            message=f"executive_summary tiene {len(sentences)} oraciones — máximo permitido: 6",
            affected_field="executive_summary",
            expected_value="≤6 oraciones",
            actual_value=str(len(sentences)),
        ))

    # 6.7 Score self-consistency (§4.6): the prose must not cite a validation score
    # that contradicts the report's own validation_score field. Surfaced as INFO so
    # a stale "NN/100" in the narrative is visible without penalising the score.
    _SCORE_RE = re.compile(r"(\d{1,3})\s*(?:/\s*100|\s+de\s+100|/100)")
    for src_name, prose in (("executive_summary", report.executive_summary),
                            ("board_summary", report.board_summary)):
        for m in _SCORE_RE.finditer(prose or ""):
            cited = int(m.group(1))
            if cited <= 100 and abs(cited - report.validation_score) > 5:
                flags.append(ValidationFlag(
                    check_id="6.7",
                    category=ValidationCategory.NARRATIVE,
                    severity=ValidationSeverity.INFO,
                    message=(
                        f"La narrativa cita un score de {cited}/100 que difiere del "
                        f"validation_score del reporte ({report.validation_score}/100)"
                    ),
                    affected_field=src_name,
                    expected_value=f"{report.validation_score}/100",
                    actual_value=f"{cited}/100",
                ))
                break  # one flag per field is enough

    # 6.3 board_summary must be longer than executive_summary
    if len(report.board_summary) <= len(report.executive_summary):
        flags.append(ValidationFlag(
            check_id="6.3",
            category=ValidationCategory.NARRATIVE,
            severity=ValidationSeverity.WARNING,
            message="board_summary no es más detallado que executive_summary",
            affected_field="board_summary",
            expected_value=f">{len(report.executive_summary)} caracteres",
            actual_value=str(len(report.board_summary)),
        ))

    # LLM call for semantic checks (6.1, 6.4, 6.5, 6.6)
    report_subset = {
        "company_name": report.company_name,
        "executive_summary": report.executive_summary,
        "board_summary": report.board_summary,
        "analysis_results": [
            {
                "account_name": a.account_name,
                "current_value": a.current_value,
                "previous_value": a.previous_value,
                "variation_pct": a.variation_pct,
                "executive_insight": a.executive_insight,
                "possible_causes": a.possible_causes,
            }
            for a in report.analysis_results
        ],
        "niif_note_drafts": [
            {
                "note_id": n.note_id,
                "niif_reference": n.niif_reference,
                "content": n.content,
                "requires_disclosure": n.requires_disclosure,
            }
            for n in report.niif_note_drafts
        ],
    }

    raw = llm.generate_json(
        system_prompt=_get_narrative_prompt(),
        user_prompt=json.dumps(report_subset, ensure_ascii=False, indent=2),
        temperature=0.1,
        tenant_id=report.tenant_id,
        job_id=report.job_id,
    )

    llm_items = raw if isinstance(raw, list) else raw.get("flags", [])
    for item in llm_items:
        try:
            flags.append(ValidationFlag(
                check_id=item.get("check_id", "6.x"),
                category=ValidationCategory.NARRATIVE,
                severity=ValidationSeverity(item.get("severity", "WARNING")),
                message=item.get("message", ""),
                affected_field=item.get("affected_field"),
            ))
        except (ValueError, KeyError):
            pass

    return flags


# ---------------------------------------------------------------------------
# Score adjustment
# ---------------------------------------------------------------------------

_PENALTY_CAP_PER_CHECK = 30  # a single check_id can subtract at most this many points


def _compute_adjusted_score(base_score: int, flags: list[ValidationFlag]) -> int:
    """Adjust the score by penalising findings, fairly.

    The penalty is capped *per check_id* so that one systemic mismatch touching
    dozens of rows (e.g. a single broken cross-reference rule) cannot floor the
    score on its own. The result reflects how many *kinds* of problems exist and
    their severity, not how many rows a single bug happened to touch (§6.4).
    INFO findings never affect the score.
    """
    per_check: dict[str, int] = {}
    for f in flags:
        weight = (
            _PENALTY_ERROR if f.severity == ValidationSeverity.ERROR else
            _PENALTY_WARNING if f.severity == ValidationSeverity.WARNING else 0
        )
        if weight:
            per_check[f.check_id] = per_check.get(f.check_id, 0) + weight

    penalty = sum(min(p, _PENALTY_CAP_PER_CHECK) for p in per_check.values())
    return max(0, base_score - penalty)


# ---------------------------------------------------------------------------
# Category 7 — Evidence Binding (Evidence First)
# ---------------------------------------------------------------------------

_REFUSAL_PHRASE = "No existe evidencia suficiente para determinar la causa de esta variación."

def _check_evidence_binding(report: FinalReportOutput) -> list[ValidationFlag]:
    """
    Verify that causal claims in HIGH-materiality accounts are backed by
    machine-checkable evidence entries. Three checks:
      7.1 HIGH-mat account with possible_causes and no evidence → ERROR  −10
      7.2 evidence entry with empty ref → WARNING −3
      7.3 evidence entry whose ref doesn't match any known account_id → WARNING −3
    """
    flags: list[ValidationFlag] = []
    known_ids = {a.account_id for a in report.analysis_results}

    for acct in report.analysis_results:
        if acct.materiality.value != "HIGH":
            continue
        # Ignore accounts that explicitly declared insufficient evidence
        has_refusal = any(_REFUSAL_PHRASE in c for c in acct.possible_causes)
        if has_refusal:
            continue

        has_causes = bool(acct.possible_causes)
        has_evidence = bool(getattr(acct, "evidence", []))

        # 7.1 HIGH-mat with causes but no evidence
        if has_causes and not has_evidence:
            flags.append(ValidationFlag(
                check_id="7.1",
                category=ValidationCategory.EVIDENCE_BINDING,
                severity=ValidationSeverity.ERROR,
                message=(
                    f"Cuenta de alta materialidad '{acct.account_id}' tiene causas "
                    f"declaradas pero ninguna entrada de evidencia (Evidence First)."
                ),
                affected_field=f"analysis_results[{acct.account_id}].evidence",
                expected_value="≥1 entrada de evidencia",
                actual_value="[]",
            ))

        for ev in getattr(acct, "evidence", []):
            ref = (ev.get("ref") or "").strip()
            ev_type = ev.get("evidence_type", "")

            # 7.2 evidence entry with empty ref
            if not ref:
                flags.append(ValidationFlag(
                    check_id="7.2",
                    category=ValidationCategory.EVIDENCE_BINDING,
                    severity=ValidationSeverity.WARNING,
                    message=(
                        f"Evidencia en '{acct.account_id}' tiene ref vacío "
                        f"(claim: '{ev.get('claim', '')[:60]}')."
                    ),
                    affected_field=f"analysis_results[{acct.account_id}].evidence[].ref",
                    expected_value="account_id, headline, cláusula o estándar NIIF",
                    actual_value="''",
                ))

            # 7.3 account-type evidence whose ref is not a real account_id
            elif ev_type == "account" and ref not in known_ids:
                flags.append(ValidationFlag(
                    check_id="7.3",
                    category=ValidationCategory.EVIDENCE_BINDING,
                    severity=ValidationSeverity.WARNING,
                    message=(
                        f"Referencia de evidencia '{ref}' en '{acct.account_id}' "
                        f"no corresponde a ningún account_id conocido."
                    ),
                    affected_field=f"analysis_results[{acct.account_id}].evidence[].ref",
                    expected_value=f"uno de {len(known_ids)} account_ids conocidos",
                    actual_value=ref,
                ))

    return flags


# ---------------------------------------------------------------------------
# Lambda handler
# ---------------------------------------------------------------------------

def lambda_handler(event: dict, context) -> dict:
    """
    Input:  FinalReportOutput
    Output: RevisorOutput
    """
    payload = FinalReportOutput.model_validate(event)
    llm = LLMProvider()

    flags: list[ValidationFlag] = []
    flags += _check_structural(payload)
    flags += _check_mathematical(payload)
    flags += _check_cross_references(payload)
    flags += _check_business_logic(payload)
    flags += _check_consistency(payload)

    # Cat 6 only runs when there are no structural ERRORs
    errors_so_far = [f for f in flags if f.severity == ValidationSeverity.ERROR]
    if not errors_so_far:
        flags += _check_narrative(payload, llm)

    # Cat 7 — Evidence Binding: always runs (independent of structural errors)
    flags += _check_evidence_binding(payload)

    errors_count = sum(1 for f in flags if f.severity == ValidationSeverity.ERROR)
    warnings_count = sum(1 for f in flags if f.severity == ValidationSeverity.WARNING)
    adjusted_score = _compute_adjusted_score(payload.validation_score, flags)

    base = payload.model_dump(mode="json")
    base["validation_score"] = adjusted_score

    result = RevisorOutput(
        **base,
        validation_flags=flags,
        errors_count=errors_count,
        warnings_count=warnings_count,
        validation_passed=(errors_count == 0),
    )

    result_dict = result.model_dump(mode="json")

    # Persist the REVISOR artifact ourselves. As the terminal SFN state (End:true)
    # nothing downstream saves our output, so GET /analyses/{id}/revisor would 404
    # and the GUI would render nothing even though the execution succeeded. Agents 2
    # and 3 self-persist for the same reason; local_server's _run_agent5 wrapper saved
    # it for us in local dev, but the cloud path has no such wrapper. Best-effort —
    # a failed save must not fail the agent (the report itself is already complete).
    try:
        job_save(payload.job_id, REVISOR, result_dict)
        logger.info("revisor | job=%s saved REVISOR artifact errors=%d warnings=%d",
                    payload.job_id, errors_count, warnings_count)
    except Exception as exc:
        logger.error("revisor | job=%s failed to save REVISOR artifact: %s",
                     payload.job_id, exc)

    return result_dict


# ---------------------------------------------------------------------------
# Interactive chat
# ---------------------------------------------------------------------------

_CHAT_SYSTEM_PROMPT = """\
Eres el Revisor Inteligente de CreditIQ — experto analista financiero de IA especializado \
en fondos de inversión y estados financieros bajo NIIF.

Tienes acceso al análisis completo generado por el pipeline de CreditIQ para esta empresa: \
cuentas extraídas, variaciones materiales, scoring de riesgo, síntesis ejecutiva, y \
observaciones de calidad del revisor. Ese contexto se entrega al inicio de la conversación.

## Rol
- Responde preguntas del analista humano con precisión, clareza y rigor financiero.
- Explica cuentas, variaciones, alertas y recomendaciones presentes en los datos.
- Interpreta scores de riesgo (crédito, mercado, financiero) y hallazgos NIIF.
- Señala coherencias o inconsistencias que el analista debería revisar.
- Responde siempre en español con terminología financiera profesional.

## Formato de respuesta — IMPORTANTE
La interfaz gráfica renderiza Markdown completo. Úsalo siempre para mejorar la legibilidad:
- **Negrita** para cifras clave, nombres de cuentas y términos técnicos importantes.
- Encabezados (`##`, `###`) para separar secciones cuando la respuesta tenga más de un tema.
- Listas con viñetas o numeradas para enumerar hallazgos, causas o recomendaciones.
- Tablas Markdown para comparar valores entre períodos, cuentas o categorías de riesgo.
- Bloques de código (` ``` `) solo si muestras fórmulas o JSON estructurado.
- Citas (`>`) para resaltar conclusiones o alertas relevantes.
- Emojis de apoyo visual donde aporten claridad: ⚠️ alertas, ✅ positivo, 📉 caída, 📈 alza, 🔴🟡🟢 semáforo de riesgo.

No uses Markdown innecesariamente en respuestas cortas de una sola oración.

## Restricciones
- Cíñete estrictamente a los datos del contexto provisto; no inventes cifras.
- Si algo no está en los datos, dilo explícitamente.
- No hagas recomendaciones de inversión — solo análisis descriptivo e interpretativo.\
"""


def _build_context_digest(job_id: str) -> str:
    """Return the analysis context for this job.

    Fast path: loads analysis_summary.md written by ReportGenerator — a single S3
    GET that returns a pre-built, human-readable markdown with all key data.
    Fallback: reconstructs the digest by fetching each agent output individually
    (used for jobs completed before analysis_summary.md was introduced).
    """
    try:
        summary = job_load_text(job_id, ANALYSIS_SUMMARY, extension="md")
        logger.info("context_digest | job=%s source=analysis_summary.md chars=%d", job_id, len(summary))
        return summary
    except ClientError:
        logger.info("context_digest | job=%s source=agent_outputs (summary not found)", job_id)

    # ── Legacy fallback: reconstruct from individual agent outputs ───────────
    lines: list[str] = ["=== CONTEXTO DE ANÁLISIS CREDITIQ ===\n"]

    # ── Agent 1: DocumentExtractor ──────────────────────────────────────────
    try:
        ext = job_load(job_id, EXTRACTOR)
        lines.append(
            f"EMPRESA: {ext.get('company_name')} | MONEDA: {ext.get('currency')} "
            f"| PERÍODOS: {' / '.join(ext.get('periods') or [])}"
        )
        lines.append(f"CONFIANZA EXTRACCIÓN: {(ext.get('extraction_confidence') or 0) * 100:.0f}%")
        accounts = (ext.get("accounts") or [])[:30]
        if accounts:
            lines.append(f"\n## CUENTAS EXTRAÍDAS ({len(ext.get('accounts', []))} total, mostrando {len(accounts)})")
            for a in accounts:
                cur = a.get("current_value") or 0
                prev = a.get("previous_value")
                name = a.get("normalized_account_name") or a.get("raw_account_name") or "—"
                sheet = f" [{a['source_sheet']}]" if a.get("source_sheet") else ""
                prev_s = f" / prev {prev:,.1f}" if prev is not None else ""
                lines.append(f"  - {name}{sheet}: {cur:,.1f}{prev_s}")
    except Exception as exc:
        lines.append(f"[Extractor no disponible: {exc}]")

    # ── Agent 2: FinancialAnalyzer ──────────────────────────────────────────
    try:
        ana = job_load(job_id, FINANCIAL_ANALYZER)
        lines.append("\n## ANÁLISIS FINANCIERO (Agente 2)")
        kpis = ana.get("executive_kpis") or {}
        for k, v in list(kpis.items())[:8]:
            lines.append(f"  KPI {k}: {v}")
        if ana.get("portfolio_thesis"):
            lines.append(f"  TESIS: {ana['portfolio_thesis']}")
        story = (ana.get("executive_synthesis") or {}).get("portfolio_story")
        if story:
            lines.append(f"  HISTORIA: {story[:300]}")
        material = [a for a in (ana.get("analysis_results") or []) if a.get("materiality") == "HIGH"][:8]
        if material:
            lines.append(f"  CUENTAS ALTA MATERIALIDAD ({len(material)}):")
            for a in material:
                insight = (a.get("executive_insight") or "")[:120]
                lines.append(f"    • {a.get('account_name')}: Δ{a.get('variation_pct', 0):.1f}% — {insight}")
        anomalies = [a for a in (ana.get("analysis_results") or []) if a.get("anomaly_detected")][:5]
        if anomalies:
            lines.append(f"  ANOMALÍAS ({len(anomalies)}):")
            for a in anomalies:
                lines.append(f"    ⚠ {a.get('account_name')}: Δ{a.get('variation_pct', 0):.1f}%")
    except Exception as exc:
        lines.append(f"[FinancialAnalyzer no disponible: {exc}]")

    # ── Agent 3: RiskScorer ─────────────────────────────────────────────────
    try:
        sco = job_load(job_id, RISK_SCORER)
        lines.append("\n## SCORING DE RIESGO (Agente 3)")
        lines.append(
            f"  RIESGO GLOBAL: {sco.get('overall_risk_score')} "
            f"| SALUD: {sco.get('overall_financial_health')} "
            f"| SCORE VALIDACIÓN: {sco.get('validation_score')}/100"
        )
        cats = sco.get("risk_categories") or {}
        for k, cat in cats.items():
            findings = (cat.get("key_findings") or [""])[0][:100]
            lines.append(f"  {k.upper()}: {cat.get('level')} ({cat.get('score')}/100) — {findings}")
        rs = sco.get("risk_summary") or {}
        if rs.get("risk_headline"):
            lines.append(f"  TITULAR: {rs['risk_headline']}")
        for rec in (rs.get("risk_recommendations") or [])[:3]:
            lines.append(f"  REC: {rec}")
    except Exception as exc:
        lines.append(f"[RiskScorer no disponible: {exc}]")

    # ── Agent 4: ReportGenerator ────────────────────────────────────────────
    try:
        rep = job_load(job_id, REPORT_GENERATOR)
        lines.append("\n## REPORTE EJECUTIVO (Agente 4)")
        lines.append(f"  RESUMEN EJECUTIVO: {(rep.get('executive_summary') or '')[:400]}")
        lines.append(f"  RESUMEN JUNTA: {(rep.get('board_summary') or '')[:400]}")
    except Exception as exc:
        lines.append(f"[ReportGenerator no disponible: {exc}]")

    # ── Agent 5: RevisorInteligente ─────────────────────────────────────────
    try:
        rev = job_load(job_id, REVISOR)
        lines.append("\n## REVISIÓN DE CALIDAD (Agente 5)")
        lines.append(
            f"  VALIDACIÓN: {'APROBADA' if rev.get('validation_passed') else 'CON OBSERVACIONES'} "
            f"| SCORE: {rev.get('validation_score')}/100 "
            f"| ERRORES: {rev.get('errors_count')} | ALERTAS: {rev.get('warnings_count')}"
        )
        for f in (rev.get("validation_flags") or [])[:6]:
            lines.append(f"  [{f.get('severity')}] {f.get('message', '')[:120]}")
    except Exception as exc:
        lines.append(f"[Revisor no disponible: {exc}]")

    lines.append("\n=== FIN DEL CONTEXTO ===")
    return "\n".join(lines)


def chat_handler(event: dict, _ctx) -> dict:
    """
    POST /analyses/{job_id}/chat
    Body: { "job_id": "...", "message": "...", "tenant_id": "..." }
    Returns: { "reply": "...", "conversation_id": "...", "turn": N }
    """
    job_id = (event.get("job_id") or "").strip()
    tenant_id = (event.get("tenant_id") or "").strip()
    user_message = (event.get("message") or "").strip()

    if not job_id or not user_message:
        return {"error": "job_id and message are required", "statusCode": 400}

    # Load or initialise chat log
    try:
        chat_data = job_load(job_id, CHAT_LOG)
        history: list[dict] = chat_data.get("turns") or []
        context_digest: str = chat_data.get("context_digest") or ""
    except ClientError:
        history = []
        context_digest = ""

    # Build context digest once (first turn) and persist it
    if not context_digest:
        context_digest = _build_context_digest(job_id)

    system_prompt = _get_chat_prompt() + "\n\n" + context_digest

    # Reconstruct message list for multi-turn
    messages: list[dict] = []
    for turn in history:
        messages.append({"role": "user", "content": turn["user"]})
        messages.append({"role": "assistant", "content": turn["assistant"]})
    messages.append({"role": "user", "content": user_message})

    llm = LLMProvider()
    try:
        reply = llm.generate_chat(
            system_prompt=system_prompt,
            messages=messages,
            temperature=0.7,
            tenant_id=tenant_id,
            job_id=job_id,
            max_tokens=2048,
        )
    except Exception as exc:
        logger.error("chat_llm_failed | job=%s error=%s", job_id, exc)
        return {"error": f"LLM error: {exc}", "statusCode": 500}

    turn_n = len(history) + 1
    history.append({
        "turn": turn_n,
        "user": user_message,
        "assistant": reply,
        "timestamp": datetime.utcnow().isoformat(),
    })

    try:
        job_save(job_id, CHAT_LOG, {
            "job_id": job_id,
            "tenant_id": tenant_id,
            "context_digest": context_digest,
            "turns": history,
        })
    except Exception as exc:
        logger.warning("chat_save_failed | job=%s error=%s", job_id, exc)

    logger.info("chat_turn | job=%s turn=%d tokens_approx=%d", job_id, turn_n, len(reply))
    return {"reply": reply, "conversation_id": job_id, "turn": turn_n}
