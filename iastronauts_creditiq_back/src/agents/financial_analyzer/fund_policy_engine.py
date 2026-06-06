"""
fund_policy_engine.py — Fund Policy Engine

Pure deterministic function: no LLM, no S3, no side effects.

Compares measured concentration views against fund policy limits and produces
per-dimension status (within | near | breach) with headroom and context-aware
phrasing. Removes false-positive concentration alerts by tying every flag to a
defined limit.

Usage pattern matches risk_scorer/scoring.py::compute_risk so the eval harness
can call apply_fund_limits() directly without any Lambda infrastructure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from shared.models.fund_policy import FundPolicy, get_default_policy

ConcentrationStatus = Literal["within", "near", "breach"]

# ≥ 90 % of the limit → "near" (early-warning band)
_NEAR_THRESHOLD: float = 0.90


@dataclass
class ConcentrationAssessment:
    dimension: str              # "issuer" | "sector" | "counterparty" | "asset_class"
    name: str                   # issuer name, sector label, bank name, instrument type
    exposure_pct: float         # measured concentration (%)
    limit_pct: float            # policy maximum (%)
    status: ConcentrationStatus
    headroom_pct: float         # > 0 = below limit; < 0 = over limit


@dataclass
class FundPolicyAssessment:
    fund_type: str
    policy_source: str                                              # "default" | "policy_document"
    limits: dict                                                    # raw limit values
    assessments: list[ConcentrationAssessment] = field(default_factory=list)
    breach_count: int = 0
    near_count: int = 0
    within_count: int = 0
    summary: str = ""


def _compute_status(exposure_pct: float, limit_pct: float) -> ConcentrationStatus:
    if exposure_pct >= limit_pct:
        return "breach"
    if limit_pct > 0 and exposure_pct >= limit_pct * _NEAR_THRESHOLD:
        return "near"
    return "within"


def apply_fund_limits(
    concentration_views: dict,
    fund_type: str,
    policy: FundPolicy | None = None,
) -> FundPolicyAssessment:
    """
    Compare measured concentration against fund policy limits.

    Args:
        concentration_views: merged dict containing:
            - top_accounts       list[dict]  {name, pct_of_total, ...}   (from concentration_engine)
            - category_concentration dict   {category: pct}               (from concentration_engine)
            - bank_breakdown     list[dict]  {bank, pct, ...}             (from sheet_concentration)
            - instrument_breakdown list[dict] {instrument_type, pct, ...} (from sheet_concentration)
        fund_type: fund_engine.py detect_fund_type() return value
        policy: explicit FundPolicy override; defaults to SFC Colombia limits when None

    Returns:
        FundPolicyAssessment with per-dimension status, headroom and breach tallies.
    """
    if policy is None:
        policy = get_default_policy(fund_type)

    assessments: list[ConcentrationAssessment] = []

    # 1. Per-issuer concentration (individual positions as % of total)
    for acc in concentration_views.get("top_accounts") or []:
        exposure = float(acc.get("pct_of_total", 0.0))
        status = _compute_status(exposure, policy.per_issuer_pct)
        assessments.append(ConcentrationAssessment(
            dimension="issuer",
            name=acc.get("name", ""),
            exposure_pct=round(exposure, 2),
            limit_pct=policy.per_issuer_pct,
            status=status,
            headroom_pct=round(policy.per_issuer_pct - exposure, 2),
        ))

    # 2. Per-sector / category concentration
    cat_concentration: dict = concentration_views.get("category_concentration") or {}
    for cat, pct in cat_concentration.items():
        exposure = float(pct)
        status = _compute_status(exposure, policy.per_sector_pct)
        assessments.append(ConcentrationAssessment(
            dimension="sector",
            name=str(cat),
            exposure_pct=round(exposure, 2),
            limit_pct=policy.per_sector_pct,
            status=status,
            headroom_pct=round(policy.per_sector_pct - exposure, 2),
        ))

    # 3. Per-counterparty (custodian bank) concentration
    for bank in concentration_views.get("bank_breakdown") or []:
        exposure = float(bank.get("pct", 0.0))
        name = bank.get("bank", bank.get("name", ""))
        status = _compute_status(exposure, policy.per_counterparty_pct)
        assessments.append(ConcentrationAssessment(
            dimension="counterparty",
            name=str(name),
            exposure_pct=round(exposure, 2),
            limit_pct=policy.per_counterparty_pct,
            status=status,
            headroom_pct=round(policy.per_counterparty_pct - exposure, 2),
        ))

    # 4. Per-asset-class concentration (instrument types from Inversiones sheet)
    for inst in concentration_views.get("instrument_breakdown") or []:
        exposure = float(inst.get("pct", 0.0))
        name = inst.get("instrument_type", "")
        status = _compute_status(exposure, policy.per_asset_class_pct)
        assessments.append(ConcentrationAssessment(
            dimension="asset_class",
            name=str(name),
            exposure_pct=round(exposure, 2),
            limit_pct=policy.per_asset_class_pct,
            status=status,
            headroom_pct=round(policy.per_asset_class_pct - exposure, 2),
        ))

    breach_count = sum(1 for a in assessments if a.status == "breach")
    near_count = sum(1 for a in assessments if a.status == "near")
    within_count = sum(1 for a in assessments if a.status == "within")

    if breach_count > 0:
        dims = {a.name for a in assessments if a.status == "breach"}
        summary = (
            f"{breach_count} dimensión(es) con incumplimiento de límite permitido: "
            + ", ".join(sorted(dims))
            + f". Límite máximo por emisor: {policy.per_issuer_pct}%."
        )
    elif near_count > 0:
        summary = (
            f"Sin incumplimientos. {near_count} dimensión(es) cerca del límite "
            f"(≥{_NEAR_THRESHOLD*100:.0f}% del máximo permitido) — monitorear."
        )
    else:
        summary = (
            f"Todas las dimensiones dentro de límites permitidos. "
            f"Fuente de política: {policy.source}."
        )

    return FundPolicyAssessment(
        fund_type=policy.fund_type,
        policy_source=policy.source,
        limits={
            "per_issuer_pct": policy.per_issuer_pct,
            "per_sector_pct": policy.per_sector_pct,
            "per_asset_class_pct": policy.per_asset_class_pct,
            "per_counterparty_pct": policy.per_counterparty_pct,
        },
        assessments=assessments,
        breach_count=breach_count,
        near_count=near_count,
        within_count=within_count,
        summary=summary,
    )


def policy_assessment_to_dict(assessment: FundPolicyAssessment) -> dict:
    """Serialize FundPolicyAssessment to a plain dict for model storage."""
    return {
        "fund_type": assessment.fund_type,
        "policy_source": assessment.policy_source,
        "limits": assessment.limits,
        "assessments": [
            {
                "dimension": a.dimension,
                "name": a.name,
                "exposure_pct": a.exposure_pct,
                "limit_pct": a.limit_pct,
                "status": a.status,
                "headroom_pct": a.headroom_pct,
            }
            for a in assessment.assessments
        ],
        "breach_count": assessment.breach_count,
        "near_count": assessment.near_count,
        "within_count": assessment.within_count,
        "summary": assessment.summary,
    }


def worst_issuer_status(assessment_dict: dict) -> ConcentrationStatus:
    """
    Return the worst status among all issuer-dimension assessments.
    Used by risk engines to adjust scoring without recomputing limits.
    Returns 'within' when no issuer assessments exist.
    """
    rank = {"breach": 2, "near": 1, "within": 0}
    worst = "within"
    for a in assessment_dict.get("assessments") or []:
        if a.get("dimension") == "issuer":
            s = a.get("status", "within")
            if rank.get(s, 0) > rank.get(worst, 0):
                worst = s
    return worst  # type: ignore[return-value]


def worst_counterparty_status(assessment_dict: dict) -> ConcentrationStatus:
    """Return the worst status among counterparty-dimension assessments."""
    rank = {"breach": 2, "near": 1, "within": 0}
    worst = "within"
    for a in assessment_dict.get("assessments") or []:
        if a.get("dimension") == "counterparty":
            s = a.get("status", "within")
            if rank.get(s, 0) > rank.get(worst, 0):
                worst = s
    return worst  # type: ignore[return-value]
