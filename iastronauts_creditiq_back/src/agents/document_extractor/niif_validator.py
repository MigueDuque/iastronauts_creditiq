"""
niif_validator.py

Deterministic NIIF structural compliance validator for extracted EEFF accounts.
Runs inside DocumentExtractor after LLM normalization, before the output is
passed to FinancialAnalyzer. No LLM — fully rule-based.

Standards covered
─────────────────
  NIC 1  — Presentation of Financial Statements (completeness, equation, equity)
  NIC 2  — Inventories (cost of sales linkage)
  NIC 34 — Interim Financial Reporting (comparative base, required periods)
  CAT    — Category misclassification detection
  EXT    — Extraction quality gates (count, confidence)

Output: NiifValidationResult attached to ExtractorOutput.niif_validation.
"""

import logging

from shared.models.extractor import ExtractedAccount, NiifValidationFlag, NiifValidationResult

logger = logging.getLogger("document_extractor.niif_validator")

# ── Thresholds ────────────────────────────────────────────────────────────────
_MIN_ACCOUNT_COUNT = 5
_MIN_AVG_CONFIDENCE = 0.5
_EQUATION_TOLERANCE = 0.05       # 5% gap acceptable in accounting equation
_MAX_MISCLASSIFICATION_FLAGS = 5 # cap to avoid flooding output with CAT flags

# Score deductions per severity
_DEDUCTIONS = {"ERROR": 20, "WARNING": 5, "INFO": 1}

# ── Category signal keywords ──────────────────────────────────────────────────
# Maps expected category → keywords that strongly imply that category.
# Used to detect accounts whose name contradicts their assigned category.
_CATEGORY_SIGNALS: dict[str, list[str]] = {
    "assets": [
        "activo", "caja y bancos", "efectivo y equivalentes",
        "cartera de clientes", "inventario", "inventarios",
        "propiedad planta", "intangibles", "deudores",
        "inversiones permanentes", "activos diferidos",
    ],
    "liabilities": [
        "pasivo", "obligaciones financieras", "cuentas por pagar",
        "proveedores", "impuesto por pagar", "anticipo de clientes",
        "bonos por pagar",
    ],
    "equity": [
        "patrimonio", "capital suscrito", "capital pagado",
        "reserva legal", "utilidades retenidas", "perdidas acumuladas",
        "superávit de capital", "resultados del ejercicio",
    ],
    "revenue": [
        "ingresos operacionales", "ventas netas", "ingresos por servicios",
        "honorarios recibidos", "ingresos financieros",
    ],
    "expense": [
        "gastos operacionales", "costo de ventas", "gastos administrativos",
        "gastos de personal", "depreciación del período",
        "gasto financiero", "impuesto de renta",
    ],
}

# Named-total keyword patterns per category — used for accounting equation check.
# We only check the equation when we find dedicated "Total X" accounts on all sides,
# avoiding false positives from double-counting when both line items and subtotals exist.
_TOTAL_KEYWORDS: dict[str, list[str]] = {
    "assets":      ["total activo", "total activos", "total del activo"],
    "liabilities": ["total pasivo", "total pasivos", "total del pasivo"],
    "equity":      ["total patrimonio", "total del patrimonio", "total patrimonio neto"],
}

# NIC 2 inventory-related keywords
_INVENTORY_KEYWORDS = (
    "inventar", "existencia", "mercancía", "producto terminado",
    "materia prima", "producto en proceso",
)
_COGS_KEYWORDS = (
    "costo de venta", "costo de operac", "costo directo",
    "costo mercancía vendida",
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _has_category(accounts: list[ExtractedAccount], category: str) -> bool:
    return any(a.category.lower() == category for a in accounts)


def _category_accounts(accounts: list[ExtractedAccount], category: str) -> list[ExtractedAccount]:
    return [a for a in accounts if a.category.lower() == category]


def _avg_confidence(accounts: list[ExtractedAccount]) -> float:
    return sum(a.confidence_score for a in accounts) / len(accounts) if accounts else 0.0


def _find_total_account(accounts: list[ExtractedAccount], category: str) -> float | None:
    """
    Return the current_value of the first account whose normalized name looks
    like a dedicated total line for the given category.
    Returns None when no such account is found — caller must decide whether to
    fall back to a sum or skip the check entirely.
    """
    keywords = _TOTAL_KEYWORDS.get(category, [])
    for acc in accounts:
        if acc.category.lower() != category:
            continue
        name = acc.normalized_account_name.lower()
        if any(k in name for k in keywords):
            return acc.current_value
    return None


# ── Rules ─────────────────────────────────────────────────────────────────────

def _rule_ext001_account_count(accounts: list[ExtractedAccount]) -> NiifValidationFlag | None:
    if len(accounts) < _MIN_ACCOUNT_COUNT:
        return NiifValidationFlag(
            rule_id="EXT-001",
            standard="N/A",
            severity="ERROR",
            message=(
                f"Solo se extrajeron {len(accounts)} cuenta(s); se requieren al menos "
                f"{_MIN_ACCOUNT_COUNT} para un análisis fiable. "
                "Verifique que el archivo EEFF sea legible y esté en formato correcto."
            ),
            affected_accounts=[],
        )
    return None


def _rule_ext002_confidence(accounts: list[ExtractedAccount]) -> NiifValidationFlag | None:
    avg = _avg_confidence(accounts)
    if avg < _MIN_AVG_CONFIDENCE:
        low_conf = [a.account_id for a in accounts if a.confidence_score < _MIN_AVG_CONFIDENCE]
        return NiifValidationFlag(
            rule_id="EXT-002",
            standard="N/A",
            severity="WARNING",
            message=(
                f"Confianza promedio de extracción baja: {avg:.0%}. "
                "Varios valores pueden ser imprecisos o inferidos. "
                "Revise la calidad y legibilidad del archivo fuente."
            ),
            affected_accounts=low_conf,
        )
    return None


def _rule_nic1_001_categories_present(accounts: list[ExtractedAccount]) -> NiifValidationFlag | None:
    """At least assets or revenue must be present; otherwise extraction likely failed."""
    if not _has_category(accounts, "assets") and not _has_category(accounts, "revenue"):
        return NiifValidationFlag(
            rule_id="NIC1-001",
            standard="NIC 1",
            severity="ERROR",
            message=(
                "No se detectaron cuentas de activos ni de ingresos. "
                "El documento podría no ser un estado financiero válido bajo NIC 1, "
                "o la extracción no reconoció la estructura del archivo."
            ),
            affected_accounts=[],
        )
    return None


def _rule_nic1_002_balance_sheet_completeness(
    accounts: list[ExtractedAccount],
) -> list[NiifValidationFlag]:
    """NIC 1.54: a balance sheet must present assets, liabilities, and equity."""
    flags: list[NiifValidationFlag] = []
    if not _has_category(accounts, "assets"):
        return flags  # nothing to check without assets

    if not _has_category(accounts, "liabilities"):
        flags.append(NiifValidationFlag(
            rule_id="NIC1-002",
            standard="NIC 1",
            severity="WARNING",
            message=(
                "Se detectaron activos pero no pasivos. "
                "NIC 1.54 exige la presentación de pasivos en el estado de situación financiera. "
                "Verifique que el archivo incluya la sección de pasivos."
            ),
            affected_accounts=[],
        ))

    if not _has_category(accounts, "equity"):
        flags.append(NiifValidationFlag(
            rule_id="NIC1-003",
            standard="NIC 1",
            severity="WARNING",
            message=(
                "Se detectaron activos pero no patrimonio. "
                "NIC 1.54 exige la presentación del patrimonio en el estado de situación financiera. "
                "Verifique que el archivo incluya la sección de patrimonio."
            ),
            affected_accounts=[],
        ))

    return flags


def _rule_nic1_004_income_statement_completeness(
    accounts: list[ExtractedAccount],
) -> NiifValidationFlag | None:
    """NIC 1.82: if revenue exists, expense accounts must also be presented."""
    if _has_category(accounts, "revenue") and not _has_category(accounts, "expense"):
        return NiifValidationFlag(
            rule_id="NIC1-004",
            standard="NIC 1",
            severity="WARNING",
            message=(
                "Se detectaron ingresos pero no gastos ni costos. "
                "NIC 1.82 exige la presentación de gastos en el estado de resultados. "
                "Verifique que el archivo incluya la sección de gastos y costos."
            ),
            affected_accounts=[],
        )
    return None


def _rule_nic1_005_accounting_equation(
    accounts: list[ExtractedAccount],
) -> tuple[NiifValidationFlag | None, bool, float]:
    """
    NIC 1: Activos = Pasivos + Patrimonio.

    Only fires when dedicated 'Total X' accounts are found on all three sides —
    this avoids false positives from double-counting when both line items and
    subtotals are present in the extracted list.

    Returns (flag_or_None, balanced, gap_pct).
    """
    total_assets = _find_total_account(accounts, "assets")
    total_liab = _find_total_account(accounts, "liabilities")
    total_equity = _find_total_account(accounts, "equity")

    if total_assets is None or total_liab is None or total_equity is None:
        # Cannot reliably verify — skip rather than produce false positives
        return None, True, 0.0

    if total_assets == 0:
        return None, True, 0.0

    expected = total_liab + total_equity
    gap = abs(total_assets - expected)
    gap_pct = round(gap / abs(total_assets), 4)

    if gap_pct > _EQUATION_TOLERANCE:
        return (
            NiifValidationFlag(
                rule_id="NIC1-005",
                standard="NIC 1",
                severity="WARNING",
                message=(
                    f"Ecuación contable desequilibrada: "
                    f"Activos ({total_assets:,.1f} COP MM) ≠ "
                    f"Pasivos ({total_liab:,.1f}) + Patrimonio ({total_equity:,.1f}). "
                    f"Diferencia: {gap_pct:.1%}. "
                    "Puede indicar cuentas faltantes, doble contabilización o errores en el EEFF. "
                    "NIC 1 exige estados financieros internamente coherentes."
                ),
                affected_accounts=[],
            ),
            False,
            gap_pct,
        )

    return None, True, gap_pct


def _rule_nic1_006_negative_equity(
    accounts: list[ExtractedAccount],
) -> NiifValidationFlag | None:
    """NIC 1.25: negative equity is a going concern signal that must be disclosed."""
    equity_accs = _category_accounts(accounts, "equity")
    if not equity_accs:
        return None
    equity_total = sum(a.current_value for a in equity_accs)
    if equity_total < 0:
        negative_ids = [a.account_id for a in equity_accs if a.current_value < 0]
        return NiifValidationFlag(
            rule_id="NIC1-006",
            standard="NIC 1",
            severity="WARNING",
            message=(
                f"Patrimonio total negativo: {equity_total:,.1f} COP MM. "
                "Conforme a NIC 1.25, la empresa debe evaluar y revelar su capacidad "
                "de continuar como negocio en marcha. Este hallazgo requiere atención prioritaria."
            ),
            affected_accounts=negative_ids,
        )
    return None


def _rule_nic1_007_zero_assets(
    accounts: list[ExtractedAccount],
) -> NiifValidationFlag | None:
    """Zero total assets strongly suggests an extraction failure or unit-of-measure issue."""
    asset_accs = _category_accounts(accounts, "assets")
    if not asset_accs:
        return None
    asset_total = sum(a.current_value for a in asset_accs)
    if abs(asset_total) < 1.0:
        return NiifValidationFlag(
            rule_id="NIC1-007",
            standard="NIC 1",
            severity="ERROR",
            message=(
                "Total de activos prácticamente nulo (< 1 COP MM). "
                "Es probable que el archivo no contenga valores numéricos legibles "
                "o que las unidades monetarias no fueron reconocidas correctamente "
                "(verifique si el documento usa miles, millones o billones)."
            ),
            affected_accounts=[],
        )
    return None


def _rule_nic1_008_comparative_periods(periods: list[str]) -> NiifValidationFlag | None:
    """NIC 1.38: financial statements must include comparative information for the prior period."""
    if len(periods) < 2:
        return NiifValidationFlag(
            rule_id="NIC1-008",
            standard="NIC 1",
            severity="INFO",
            message=(
                f"Solo se detectó {len(periods)} período(s) en el documento. "
                "NIC 1.38 exige información comparativa del período inmediatamente anterior. "
                "El análisis de variaciones será limitado sin período de comparación."
            ),
            affected_accounts=[],
        )
    return None


def _rule_nic1_009_period_comparability(periods: list[str]) -> NiifValidationFlag | None:
    """
    Flag non-homogeneous period comparison (e.g. Jun 2025 vs Dec 2024).
    P&L accounts cover different time spans, making direct ratio comparison misleading.
    """
    if len(periods) < 2:
        return None
    try:
        months = [p.split("-")[1] for p in periods if "-" in p]
    except IndexError:
        return None
    if len(months) < 2 or months[0] == months[1]:
        return None  # same month-end — fully comparable
    return NiifValidationFlag(
        rule_id="NIC1-009",
        standard="NIC 1",
        severity="INFO",
        message=(
            f"Los períodos comparados tienen cierres en meses distintos "
            f"({periods[0]} vs {periods[1]}). "
            "Las cuentas del estado de resultados (ingresos y gastos) cubren intervalos de tiempo "
            "diferentes, por lo que las variaciones porcentuales no son directamente comparables. "
            "El análisis debe interpretarse con cautela para cuentas de flujo (ingresos/gastos/flujos de caja)."
        ),
        affected_accounts=[],
    )


def _rule_nic2_001_inventories_without_cogs(
    accounts: list[ExtractedAccount],
) -> NiifValidationFlag | None:
    """NIC 2.34: the cost of inventories recognized as expense must be disclosed."""
    has_inventories = any(
        any(k in a.normalized_account_name.lower() for k in _INVENTORY_KEYWORDS)
        for a in accounts if a.category.lower() == "assets"
    )
    if not has_inventories:
        return None

    has_cogs = any(
        any(k in a.normalized_account_name.lower() for k in _COGS_KEYWORDS)
        for a in accounts if a.category.lower() == "expense"
    )
    if has_cogs:
        return None

    inv_ids = [
        a.account_id for a in accounts
        if a.category.lower() == "assets"
        and any(k in a.normalized_account_name.lower() for k in _INVENTORY_KEYWORDS)
    ]
    return NiifValidationFlag(
        rule_id="NIC2-001",
        standard="NIC 2",
        severity="INFO",
        message=(
            "Se detectaron inventarios pero no cuenta de costo de ventas. "
            "NIC 2.34 requiere la revelación del costo de los inventarios reconocido como gasto. "
            "Verifique que el estado de resultados esté completo."
        ),
        affected_accounts=inv_ids,
    )


# Cash flow line items are correctly classified as "other"; their names contain
# balance-sheet keywords (e.g. "cuentas por pagar") only because they describe
# movements, not positions. Skip misclassification check for these accounts.
_CASH_FLOW_NAME_INDICATORS = (
    "(flujo)", "flujo de efectivo", "aumento de ", "disminución de ",
    "disminucion de ", "efectivo neto", "variación de ", "variacion de ",
)


def _is_cash_flow_item(account) -> bool:
    name_lower = account.normalized_account_name.lower()
    return account.category.lower() == "other" and any(
        k in name_lower for k in _CASH_FLOW_NAME_INDICATORS
    )


def _rule_cat_001_misclassification(
    accounts: list[ExtractedAccount],
) -> list[NiifValidationFlag]:
    """
    Detect accounts whose normalized name strongly signals a different category
    than the one assigned during LLM normalization.
    Capped at _MAX_MISCLASSIFICATION_FLAGS to avoid flooding output.
    """
    flags: list[NiifValidationFlag] = []

    for acc in accounts:
        if len(flags) >= _MAX_MISCLASSIFICATION_FLAGS:
            break
        if _is_cash_flow_item(acc):
            continue  # cash flow adjustments are legitimately "other"
        name_lower = acc.normalized_account_name.lower()
        actual_cat = acc.category.lower()

        for expected_cat, keywords in _CATEGORY_SIGNALS.items():
            if expected_cat == actual_cat:
                continue
            if any(k in name_lower for k in keywords):
                flags.append(NiifValidationFlag(
                    rule_id="CAT-001",
                    standard="NIC 1",
                    severity="WARNING",
                    message=(
                        f"Posible clasificación errónea: '{acc.normalized_account_name}' "
                        f"clasificada como '{actual_cat}' pero su nombre sugiere '{expected_cat}'. "
                        "Revise la clasificación en el EEFF fuente."
                    ),
                    affected_accounts=[acc.account_id],
                ))
                break  # one flag per account

    return flags


# ── NIC 34 helpers ────────────────────────────────────────────────────────────

def _is_interim(periods: list[str]) -> bool:
    """Return True when the most recent period is NOT a December year-end statement."""
    if not periods:
        return False
    try:
        month = int(periods[0].split("-")[1])
        return month != 12
    except (IndexError, ValueError):
        return False


def _rule_nic34_001_requires_comparative(periods: list[str]) -> NiifValidationFlag | None:
    """
    NIC 34.20: an interim financial report must include a comparative period.
    Only fires for non-December (interim) statements — year-end statements are
    already covered by NIC1-008.
    """
    if not _is_interim(periods):
        return None
    if len(periods) < 2:
        return NiifValidationFlag(
            rule_id="NIC34-001",
            standard="NIC 34",
            severity="ERROR",
            message=(
                f"Estado financiero intermedio (período {periods[0] if periods else '?'}) "
                "sin período comparativo. NIC 34.20 exige información financiera comparativa "
                "para todos los estados intermedios. El análisis de variaciones no es posible."
            ),
            affected_accounts=[],
        )
    return None


def _rule_nic34_002_balance_comparative_base(periods: list[str]) -> NiifValidationFlag | None:
    """
    NIC 34.20(b): the interim balance sheet must compare against the prior year-end
    (December), not against the same interim date of the prior year.

    Fires when both detected periods are in the same month (e.g. Jun-25 vs Jun-24)
    AND that month is not December — the balance sheet comparison is non-standard.
    Note: this is an INFO because the document may have deliberately chosen a different
    comparative; the reviewer should confirm.
    """
    if not _is_interim(periods) or len(periods) < 2:
        return None
    try:
        month_current = int(periods[0].split("-")[1])
        month_comparative = int(periods[1].split("-")[1])
    except (IndexError, ValueError):
        return None

    # If comparative is December it's correct; if it matches current month for balance → flag
    if month_comparative == 12:
        return None  # correct IFRS base

    if month_current == month_comparative:
        return NiifValidationFlag(
            rule_id="NIC34-002",
            standard="NIC 34",
            severity="INFO",
            message=(
                f"Estado intermedio ({periods[0]}) comparado contra {periods[1]} "
                f"(mismo mes, año anterior). "
                "NIC 34.20(b) establece que el estado de situación financiera intermedio "
                f"debe compararse contra el cierre del ejercicio anual anterior "
                f"({periods[0][:4]}-12 o {int(periods[0][:4])-1}-12), no contra el intermedio previo. "
                "El estado de resultados sí se compara contra el mismo período del año anterior. "
                "Verifique que las variaciones de balance se interpreten con la base correcta."
            ),
            affected_accounts=[],
        )
    return None


# ── Public API ────────────────────────────────────────────────────────────────

def validate_niif(
    accounts: list[ExtractedAccount],
    periods: list[str],
) -> NiifValidationResult:
    """
    Run all NIIF structural validation rules against the extracted accounts.
    Called by DocumentExtractor after LLM normalization and before building
    ExtractorOutput.  Returns a NiifValidationResult ready to be attached
    to ExtractorOutput.niif_validation.
    """
    flags: list[NiifValidationFlag] = []

    # Extraction quality gates
    if (f := _rule_ext001_account_count(accounts)):
        flags.append(f)
    if (f := _rule_ext002_confidence(accounts)):
        flags.append(f)

    # NIC 1 structural rules
    if (f := _rule_nic1_001_categories_present(accounts)):
        flags.append(f)
    flags.extend(_rule_nic1_002_balance_sheet_completeness(accounts))
    if (f := _rule_nic1_004_income_statement_completeness(accounts)):
        flags.append(f)

    eq_flag, equation_balanced, gap_pct = _rule_nic1_005_accounting_equation(accounts)
    if eq_flag:
        flags.append(eq_flag)

    if (f := _rule_nic1_006_negative_equity(accounts)):
        flags.append(f)
    if (f := _rule_nic1_007_zero_assets(accounts)):
        flags.append(f)
    if (f := _rule_nic1_008_comparative_periods(periods)):
        flags.append(f)
    if (f := _rule_nic1_009_period_comparability(periods)):
        flags.append(f)

    # NIC 2
    if (f := _rule_nic2_001_inventories_without_cogs(accounts)):
        flags.append(f)

    # NIC 34 — Interim Financial Reporting
    if (f := _rule_nic34_001_requires_comparative(periods)):
        flags.append(f)
    if (f := _rule_nic34_002_balance_comparative_base(periods)):
        flags.append(f)

    # Category misclassification
    flags.extend(_rule_cat_001_misclassification(accounts))

    # Score: start at 100, deduct per severity
    score = 100
    for flag in flags:
        score -= _DEDUCTIONS.get(flag.severity, 0)
    score = max(0, score)

    has_errors = any(f.severity == "ERROR" for f in flags)
    is_compliant = score >= 60 and not has_errors

    missing_cats = [
        cat for cat in ("assets", "liabilities", "equity", "revenue", "expense")
        if not _has_category(accounts, cat)
    ]

    logger.info(
        "niif_validation | accounts=%d periods=%d flags=%d errors=%d score=%d compliant=%s",
        len(accounts), len(periods), len(flags),
        sum(1 for f in flags if f.severity == "ERROR"),
        score, is_compliant,
    )
    for flag in flags:
        logger.info(
            "niif_flag | rule=%s severity=%s msg=%.80s",
            flag.rule_id, flag.severity, flag.message,
        )

    return NiifValidationResult(
        is_niif_compliant=is_compliant,
        compliance_score=score,
        flags=flags,
        missing_categories=missing_cats,
        accounting_equation_balanced=equation_balanced,
        equation_gap_pct=gap_pct,
    )
