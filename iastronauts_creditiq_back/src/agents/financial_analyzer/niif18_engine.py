"""
niif18_engine.py

Deterministic NIIF 18 compliance checker.
Returns structured flags and a compliance score — no LLM, no narrative.
All pass/fail decisions are keyword + threshold based.

NIIF 18 "Presentation and Disclosure in Financial Statements"
Mandatory from 2027; replaces NIC 1 for P&L structure, OCI classification,
and MPM (Management Performance Measures) disclosure.
"""

import logging
from dataclasses import dataclass, field
from typing import List

from shared.models import ExtractedAccount

from .ratio_engine import (
    PLCategory, OCIType, NIIF18Subtotals,
    classify_pl_category, classify_oci,
)

logger = logging.getLogger("financial_analyzer.niif18_engine")

# MPM keywords — accounts with these names require a reconciliation note per NIIF 18 §61
_MPM_KEYWORDS = (
    "ebitda", "ebit", "resultado operativo ajustado",
    "utilidad ajustada", "resultado normalizado",
    "utilidad recurrente", "medida de rendimiento",
)


@dataclass
class NIIF18ComplianceResult:
    """Deterministic NIIF 18 compliance check output."""
    has_operating_category: bool = False
    has_investing_category: bool = False
    has_financing_category: bool = False
    has_taxes_category: bool = False
    mandatory_subtotals_present: bool = False
    mpm_disclosure_required: bool = False
    oci_properly_split: bool = False     # OCI items classified into recyclable/non-recyclable
    compliance_score: int = 0            # 0–100 deterministic score
    flags: list[str] = field(default_factory=list)


def check_niif18_compliance(
    accounts: List[ExtractedAccount],
    subtotals: NIIF18Subtotals,
) -> NIIF18ComplianceResult:
    """
    Run deterministic NIIF 18 compliance checks.

    Does NOT call the LLM. Returns binary flags + compliance_score (0–100).
    The service passes this result to the LLM as additional context so the
    narrative can reference concrete compliance gaps.

    Scoring weights:
      has_operating_category      35 pts  — core NIIF 18 requirement
      mandatory_subtotals_present 20 pts  — 4 mandatory subtotals computable
      has_taxes_category          15 pts  — tax line visible and classified
      has_investing_category      10 pts  — investing P&L category present
      has_financing_category      10 pts  — financing P&L category present
      oci_properly_split          10 pts  — OCI items classified recyclable/non
      mpm_disclosure_required     −5 pts  — MPMs detected, adds disclosure risk
    """
    result = NIIF18ComplianceResult()
    has_pl_accounts = False
    mpm_names_found: list[str] = []
    oci_recyclable_count = 0
    oci_non_recyclable_count = 0

    for acc in accounts:
        cat = acc.category.lower()
        name = acc.normalized_account_name

        # P&L category detection
        if cat in ("revenue", "expense", "other"):
            has_pl_accounts = True
            pl_cat = classify_pl_category(name, cat)
            if pl_cat == PLCategory.OPERATING:
                result.has_operating_category = True
            elif pl_cat == PLCategory.INVESTING:
                result.has_investing_category = True
            elif pl_cat == PLCategory.FINANCING:
                result.has_financing_category = True
            elif pl_cat == PLCategory.TAXES:
                result.has_taxes_category = True

        # MPM detection (any account, any category)
        name_lower = name.lower()
        if any(mpm in name_lower for mpm in _MPM_KEYWORDS):
            mpm_names_found.append(name)

        # OCI classification
        oci_type = classify_oci(name)
        if oci_type == OCIType.RECYCLABLE:
            oci_recyclable_count += 1
        elif oci_type == OCIType.NON_RECYCLABLE:
            oci_non_recyclable_count += 1

    result.mpm_disclosure_required = len(mpm_names_found) > 0
    result.oci_properly_split = (oci_recyclable_count + oci_non_recyclable_count) > 0
    result.mandatory_subtotals_present = result.has_operating_category and has_pl_accounts

    # Build flags
    if has_pl_accounts and not result.has_operating_category:
        result.flags.append(
            "No se identificaron cuentas de la categoría OPERATIVA — requerido por NIIF 18"
        )
    if not result.has_investing_category:
        result.flags.append(
            "Categoría INVERSIÓN no detectada — verificar si aplica para esta entidad"
        )
    if not result.has_financing_category:
        result.flags.append(
            "Categoría FINANCIAMIENTO no detectada — verificar si hay instrumentos de deuda"
        )
    if has_pl_accounts and not result.has_taxes_category:
        result.flags.append(
            "Gasto por impuesto de renta no detectado — verificar si la entidad es contribuyente"
        )
    if not result.mandatory_subtotals_present:
        result.flags.append(
            "Los 4 subtotales obligatorios de NIIF 18 no son calculables con las cuentas disponibles"
        )
    if result.mpm_disclosure_required:
        sample = ", ".join(mpm_names_found[:3])
        result.flags.append(
            f"MPMs detectadas que requieren nota de conciliación NIIF 18 §61: {sample}"
        )
    if has_pl_accounts and not result.oci_properly_split:
        result.flags.append(
            "No se detectaron partidas de ORI clasificadas — verificar si aplica para esta entidad"
        )

    # Compliance score
    score = 0
    if result.has_operating_category:
        score += 35
    if result.mandatory_subtotals_present:
        score += 20
    if result.has_taxes_category:
        score += 15
    if result.has_investing_category:
        score += 10
    if result.has_financing_category:
        score += 10
    if result.oci_properly_split:
        score += 10
    if result.mpm_disclosure_required:
        score -= 5
    result.compliance_score = max(0, min(100, score))

    logger.info(
        "niif18_compliance | operating=%s investing=%s financing=%s taxes=%s "
        "subtotals=%s mpm=%s oci_split=%s score=%d flags=%d",
        result.has_operating_category, result.has_investing_category,
        result.has_financing_category, result.has_taxes_category,
        result.mandatory_subtotals_present, result.mpm_disclosure_required,
        result.oci_properly_split, result.compliance_score, len(result.flags),
    )

    return result


def niif18_to_dict(
    subtotals: NIIF18Subtotals,
    tci: dict,
    compliance: NIIF18ComplianceResult,
) -> dict:
    """
    Serialize NIIF 18 results into a dict stored under financial_ratios['niif18'].
    This section is passed verbatim to the LLM user prompt.
    """
    return {
        "subtotals": {
            "resultado_operativo": subtotals.resultado_operativo,
            "resultado_inversion": subtotals.resultado_inversion,
            "resultado_financiamiento": subtotals.resultado_financiamiento,
            "impuestos": subtotals.impuestos,
            "resultado_operaciones_disc": subtotals.resultado_operaciones_disc,
            "resultado_antes_fin_imp": subtotals.resultado_antes_fin_imp,
            "resultado_antes_impuestos": subtotals.resultado_antes_impuestos,
            "resultado_neto": subtotals.resultado_neto,
            "ebitda_niif18": subtotals.ebitda_niif18,
        },
        "comprehensive_income": tci,
        "compliance": {
            "has_operating_category": compliance.has_operating_category,
            "has_investing_category": compliance.has_investing_category,
            "has_financing_category": compliance.has_financing_category,
            "has_taxes_category": compliance.has_taxes_category,
            "mandatory_subtotals_present": compliance.mandatory_subtotals_present,
            "mpm_disclosure_required": compliance.mpm_disclosure_required,
            "oci_properly_split": compliance.oci_properly_split,
            "compliance_score": compliance.compliance_score,
            "flags": compliance.flags,
        },
    }
