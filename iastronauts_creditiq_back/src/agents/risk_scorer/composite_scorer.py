"""
composite_scorer.py

Aggregates the 5 risk dimension results into a single composite risk score
and determines the overall risk level.

Weights (tuned for investment fund context; auto-adjusted for corporate):
  - Market risk:      35% (dominant for equity/investment funds)
  - Liquidity risk:   25%
  - Credit risk:      15%
  - Solvency risk:    15%
  - Operational risk: 10%

Ceiling rule: if any dimension is HIGH, overall cannot be LOW.
Floor rule: if all dimensions are LOW, overall is LOW regardless of arithmetic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Any

from shared.models.base import RiskLevel

from .liquidity_engine import LiquidityRiskResult
from .credit_engine import CreditRiskResult
from .solvency_engine import SolvencyRiskResult
from .market_risk_engine import MarketRiskResult
from .operational_engine import OperationalRiskResult


@dataclass
class CompositeRiskResult:
    overall_risk_score: RiskLevel
    composite_score: int                    # 0–100 weighted average
    validation_score: int                   # anti-hallucination / data quality check
    requires_human_review: bool
    analysis_confidence: float
    anti_hallucination_passed: bool
    issues_found: list[str] = field(default_factory=list)
    compliance_flags: list[str] = field(default_factory=list)
    dimension_scores: Dict[str, int] = field(default_factory=dict)
    dimension_levels: Dict[str, str] = field(default_factory=dict)


def _level_from_score(score: int) -> RiskLevel:
    if score >= 70:
        return RiskLevel.LOW
    elif score >= 40:
        return RiskLevel.MEDIUM
    return RiskLevel.HIGH


def _run_anti_hallucination_checks(analysis_results: list, financial_ratios: dict) -> tuple[int, list[str]]:
    """
    Checks the FinancialAnalyzer output quality.
    Returns (score 0-100, list of issues found).
    """
    score = 100
    issues = []
    totals = financial_ratios.get("totals", {})

    high_mat_accounts = [
        a for a in analysis_results if a.get("materiality") == "HIGH"
    ]

    # Check 1: HIGH materiality accounts should have ≥2 possible_causes
    for a in high_mat_accounts:
        causes = a.get("possible_causes", [])
        if len(causes) < 2:
            # Allow single-cause if confidence is high and it's not generic template text
            cause_text = causes[0] if causes else ""
            if "Variación de" in cause_text and "Anomalías:" in cause_text:
                # Generic template fallback text — deduct points
                score -= 8
                issues.append(
                    f"Cuenta HIGH materialidad '{a.get('account_name', '')}': causas genéricas (texto de plantilla)."
                )

    # Check 2: executive_insight should contain numbers for HIGH materiality accounts
    for a in high_mat_accounts[:5]:  # check top 5 only
        insight = a.get("executive_insight", "")
        if insight and not any(c.isdigit() for c in insight):
            score -= 5
            issues.append(
                f"Insight ejecutivo de '{a.get('account_name', '')}' no contiene cifras verificables."
            )

    # Check 3: Mathematical consistency spot-check (variation_pct = absolute / previous)
    tolerance = 0.5  # 0.5 percentage points tolerance
    math_errors = 0
    for a in analysis_results[:20]:  # spot-check first 20
        prev = a.get("previous_value")
        curr = a.get("current_value", 0.0)
        reported_pct = a.get("variation_pct", 0.0)
        if prev and prev != 0 and reported_pct != 0:
            expected_pct = (curr - prev) / abs(prev) * 100
            if abs(expected_pct - reported_pct) > tolerance:
                math_errors += 1

    if math_errors > 0:
        deduction = min(math_errors * 5, 20)
        score -= deduction
        issues.append(f"{math_errors} cuentas con variación porcentual inconsistente con los valores absolutos.")

    # Check 4: accounts flagged as anomaly should be HIGH or MEDIUM materiality
    anomaly_low_mat = [
        a for a in analysis_results
        if a.get("anomaly_detected") and a.get("materiality") == "LOW"
    ]
    if len(anomaly_low_mat) > 3:
        score -= 10
        issues.append(f"{len(anomaly_low_mat)} anomalías detectadas en cuentas de materialidad LOW — posible sobredetección.")

    return max(0, min(100, score)), issues


def compute_composite(
    liquidity: LiquidityRiskResult,
    credit: CreditRiskResult,
    solvency: SolvencyRiskResult,
    market: MarketRiskResult,
    operational: OperationalRiskResult,
    analysis_results: list,
    financial_ratios: dict,
    is_investment_fund: bool,
) -> CompositeRiskResult:

    # Weights: adjusted for fund vs corporate
    if is_investment_fund:
        weights = {
            "market": 0.35,
            "liquidity": 0.25,
            "credit": 0.15,
            "solvency": 0.15,
            "operational": 0.10,
        }
    else:
        weights = {
            "liquidity": 0.25,
            "credit": 0.20,
            "solvency": 0.25,
            "market": 0.15,
            "operational": 0.15,
        }

    dimension_scores = {
        "market": market.score,
        "liquidity": liquidity.score,
        "credit": credit.score,
        "solvency": solvency.score,
        "operational": operational.score,
    }

    dimension_levels = {
        "market": market.level.value,
        "liquidity": liquidity.level.value,
        "credit": credit.level.value,
        "solvency": solvency.level.value,
        "operational": operational.level.value,
    }

    composite_score = round(
        sum(dimension_scores[k] * weights[k] for k in weights)
    )

    # Ceiling / floor rules
    levels = list(dimension_levels.values())
    overall_level = _level_from_score(composite_score)

    if overall_level == RiskLevel.LOW and "HIGH" in levels:
        overall_level = RiskLevel.MEDIUM  # floor raised by a HIGH dimension

    if all(lvl == "LOW" for lvl in levels):
        overall_level = RiskLevel.LOW  # floor — unanimous LOW overrides arithmetic

    # Anti-hallucination checks
    validation_score, issues = _run_anti_hallucination_checks(analysis_results, financial_ratios)
    anti_hallucination_passed = validation_score >= 60 and len(issues) == 0

    # Compile compliance flags from all dimensions
    compliance_flags: list[str] = []
    all_drivers: list[str] = (
        liquidity.risk_drivers
        + credit.risk_drivers
        + solvency.risk_drivers
        + market.risk_drivers
        + operational.risk_drivers
    )

    # Determine if human review is warranted
    high_count = sum(1 for lvl in levels if lvl == "HIGH")
    requires_human_review = (
        overall_level == RiskLevel.HIGH
        or high_count >= 2
        or not anti_hallucination_passed
        or composite_score < 35
    )

    # Analysis confidence: average of dimension scores / 100, weighted
    analysis_confidence = round(composite_score / 100, 3)

    return CompositeRiskResult(
        overall_risk_score=overall_level,
        composite_score=composite_score,
        validation_score=validation_score,
        requires_human_review=requires_human_review,
        analysis_confidence=analysis_confidence,
        anti_hallucination_passed=anti_hallucination_passed,
        issues_found=issues,
        compliance_flags=all_drivers,
        dimension_scores=dimension_scores,
        dimension_levels=dimension_levels,
    )
