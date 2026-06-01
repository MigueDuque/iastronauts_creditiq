import json
import logging
import os
import re
from datetime import datetime, timezone

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

        # 2.1 absolute_variation = current_value - previous_value
        expected_abs = account.current_value - account.previous_value
        if abs(expected_abs - account.absolute_variation) > ABS_TOLERANCE:
            flags.append(ValidationFlag(
                check_id="2.1",
                category=ValidationCategory.MATHEMATICAL,
                severity=ValidationSeverity.ERROR,
                message=f"absolute_variation incorrecto en '{account.account_name}'",
                affected_field=f"{field}.absolute_variation",
                expected_value=f"{expected_abs:.2f}",
                actual_value=f"{account.absolute_variation:.2f}",
            ))

        # 2.2 / 2.3 variation_pct
        if account.previous_value == 0:
            flags.append(ValidationFlag(
                check_id="2.3",
                category=ValidationCategory.MATHEMATICAL,
                severity=ValidationSeverity.WARNING,
                message=f"previous_value es 0 en '{account.account_name}' — variation_pct no puede calcularse",
                affected_field=f"{field}.previous_value",
                actual_value="0",
            ))
        else:
            expected_pct = (account.absolute_variation / account.previous_value) * 100
            if abs(expected_pct - account.variation_pct) > PCT_TOLERANCE:
                flags.append(ValidationFlag(
                    check_id="2.2",
                    category=ValidationCategory.MATHEMATICAL,
                    severity=ValidationSeverity.ERROR,
                    message=f"variation_pct incorrecto en '{account.account_name}'",
                    affected_field=f"{field}.variation_pct",
                    expected_value=f"{expected_pct:.1f}",
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

    return flags


# ---------------------------------------------------------------------------
# Category 3 — Cross-reference integrity
# ---------------------------------------------------------------------------

def _check_cross_references(report: FinalReportOutput) -> list[ValidationFlag]:
    flags: list[ValidationFlag] = []

    account_ids = {a.account_id for a in report.analysis_results}
    note_ids = {n.note_id for n in report.niif_note_drafts}
    account_by_id = {a.account_id: a for a in report.analysis_results}

    for i, account in enumerate(report.analysis_results):
        # 3.1 niif_note_references → must exist in niif_note_drafts
        for ref in account.niif_note_references:
            if ref not in note_ids:
                flags.append(ValidationFlag(
                    check_id="3.1",
                    category=ValidationCategory.CROSS_REFERENCE,
                    severity=ValidationSeverity.ERROR,
                    message=f"'{account.account_name}' referencia nota '{ref}' que no existe en niif_note_drafts",
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

            # 3.4 Bidirectionality: nota cita cuenta → cuenta debe citar nota
            if note.note_id not in account.niif_note_references:
                flags.append(ValidationFlag(
                    check_id="3.4",
                    category=ValidationCategory.CROSS_REFERENCE,
                    severity=ValidationSeverity.WARNING,
                    message=(
                        f"Nota '{note.note_id}' cita cuenta '{account.account_name}' "
                        f"pero la cuenta no referencia la nota — asimetría en referencias cruzadas"
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

    # 4.3 anomaly_detected → risk_level must not be LOW
    for i, account in enumerate(report.analysis_results):
        if account.anomaly_detected and account.risk_level == RiskLevel.LOW:
            flags.append(ValidationFlag(
                check_id="4.3",
                category=ValidationCategory.BUSINESS_LOGIC,
                severity=ValidationSeverity.ERROR,
                message=f"'{account.account_name}' tiene anomaly_detected=true pero risk_level=LOW — inconsistente",
                affected_field=f"analysis_results[{i}].risk_level",
                expected_value="MEDIUM o HIGH",
                actual_value="LOW",
            ))

    # 4.4 Large variation → HIGH materiality
    for i, account in enumerate(report.analysis_results):
        if (
            account.previous_value != 0
            and abs(account.variation_pct) > 100
            and abs(account.current_value) > 10
            and account.materiality != MaterialityLevel.HIGH
        ):
            flags.append(ValidationFlag(
                check_id="4.4",
                category=ValidationCategory.BUSINESS_LOGIC,
                severity=ValidationSeverity.WARNING,
                message=(
                    f"'{account.account_name}' tiene variación de {account.variation_pct:.1f}% "
                    f"pero materialidad '{account.materiality.value}' — debería ser HIGH"
                ),
                affected_field=f"analysis_results[{i}].materiality",
                expected_value="HIGH",
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
    if profit_accounts:
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
    # 6.2 executive_summary sentence count
    sentences = [s.strip() for s in report.executive_summary.split(".") if s.strip()]
    if len(sentences) > 3:
        flags.append(ValidationFlag(
            check_id="6.2",
            category=ValidationCategory.NARRATIVE,
            severity=ValidationSeverity.WARNING,
            message=f"executive_summary tiene {len(sentences)} oraciones — máximo permitido: 3",
            affected_field="executive_summary",
            expected_value="≤3 oraciones",
            actual_value=str(len(sentences)),
        ))

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

def _compute_adjusted_score(base_score: int, flags: list[ValidationFlag]) -> int:
    penalty = sum(
        _PENALTY_ERROR if f.severity == ValidationSeverity.ERROR else
        _PENALTY_WARNING if f.severity == ValidationSeverity.WARNING else 0
        for f in flags
    )
    return max(0, base_score - penalty)


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

    return result.model_dump(mode="json")
