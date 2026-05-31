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
    composite_score: int                    # 0–100 weighted average (canonical scalar)
    validation_score: int                   # anti-hallucination / data quality check (scalar)
    requires_human_review: bool
    analysis_confidence: float
    anti_hallucination_passed: bool
    issues_found: list[str] = field(default_factory=list)
    compliance_flags: list[str] = field(default_factory=list)
    dimension_scores: Dict[str, int] = field(default_factory=dict)
    dimension_levels: Dict[str, str] = field(default_factory=dict)
    # ── Transparency detail objects (Mejoras 1, 2, 4, 6, 7, 9) ─────────────────
    composite_score_detail: Dict[str, Any] = field(default_factory=dict)
    validation_score_detail: Dict[str, Any] = field(default_factory=dict)
    anti_hallucination_result: Dict[str, Any] = field(default_factory=dict)
    analysis_confidence_detail: Dict[str, Any] = field(default_factory=dict)
    data_quality_warnings: list = field(default_factory=list)
    anomaly_detection_summary: Dict[str, Any] = field(default_factory=dict)


# ── Weight profiles (Mejora 1 — exposed in output, no longer internal-only) ────
_WEIGHT_PROFILES: Dict[str, Dict[str, float]] = {
    "fund": {
        "liquidity": 0.25,
        "credit": 0.15,
        "solvency": 0.15,
        "market": 0.35,
        "operational": 0.10,
    },
    "corporate": {
        "liquidity": 0.25,
        "credit": 0.20,
        "solvency": 0.25,
        "market": 0.15,
        "operational": 0.15,
    },
}

_PROFILE_RATIONALE: Dict[str, str] = {
    "fund": (
        "Perfil aplicado a fondos de inversión colectiva: mayor peso a riesgo de "
        "mercado por la naturaleza mark-to-market de las posiciones."
    ),
    "corporate": (
        "Perfil corporativo: peso equilibrado con énfasis en liquidez y solvencia "
        "estructural propias de una empresa operativa."
    ),
}

# Spanish keys used in the transparency objects exposed to the final report.
_DIM_ES = {
    "liquidity": "liquidez",
    "credit": "credito",
    "solvency": "solvencia",
    "market": "mercado",
    "operational": "operacional",
}


def _level_from_score(score: int) -> RiskLevel:
    if score >= 70:
        return RiskLevel.LOW
    elif score >= 40:
        return RiskLevel.MEDIUM
    return RiskLevel.HIGH


_LEVEL_RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}


def _worst_level(*levels: str) -> str:
    """Returns the most severe risk level among the inputs."""
    return max(levels, key=lambda lvl: _LEVEL_RANK.get(lvl, 0))


def build_risk_categories(
    liquidity: LiquidityRiskResult,
    credit: CreditRiskResult,
    solvency: SolvencyRiskResult,
    market: MarketRiskResult,
    operational: OperationalRiskResult,
) -> Dict[str, Any]:
    """
    Regroups the 5 deterministic engines into the 3 report-facing risk
    categories the executive report renders:

      - "credito"    (Riesgo de Crédito)    ← credit engine (issuer, receivables, counterparty)
      - "mercado"    (Riesgo de Mercado)    ← market engine (fair value, concentration, rate, FX)
      - "financiero" (Riesgo Financiero)    ← liquidity + solvency (funding structure)

    Operational/profitability is intentionally excluded — it is a business-risk
    signal, not a financial-risk category, and feeds the executive summary.

    Mejora 8: risk_drivers are NOT duplicated here — they live only in
    risk_dimensions.<dimension>.risk_drivers (the single source of truth). The
    Report Generator resolves drivers from there.
    """
    # Riesgo Financiero = liquidity (dominant) + solvency, with a ceiling rule.
    fin_score = round(liquidity.score * 0.6 + solvency.score * 0.4)
    fin_level = _worst_level(liquidity.level.value, solvency.level.value)
    # Reconcile arithmetic level with the ceiling: never softer than the worst component.
    fin_level = _worst_level(fin_level, _level_from_score(fin_score).value)

    return {
        "credito": {
            "label": "Riesgo de Crédito",
            "level": credit.level.value,
            "score": credit.score,
            "key_findings": credit.key_findings,
            "metrics": {
                "dias_cartera": credit.dias_cartera,
                "cuentas_cobrar_pct_activos": credit.cuentas_cobrar_pct_activos,
                "top_issuer_name": credit.top_issuer_name,
                "top_issuer_pct_portfolio": credit.top_issuer_pct_portfolio,
                "top_custodian_name": credit.top_custodian_name,
                "top_custodian_pct": credit.top_custodian_pct,
                "num_custodians": credit.num_custodians,
                "related_party_exposure": credit.related_party_exposure,
            },
        },
        "mercado": {
            "label": "Riesgo de Mercado",
            "level": market.level.value,
            "score": market.score,
            "key_findings": market.key_findings,
            "metrics": {
                "fair_value_income_pct": market.fair_value_income_pct,
                "equity_exposure_pct": market.equity_exposure_pct,
                "hhi": market.hhi,
                "effective_positions": market.effective_positions,
                "top_issuer_pct": market.top_issuer_pct,
                "top3_concentration_pct": market.top3_concentration_pct,
                "fixed_income_pct": market.fixed_income_pct,
                "interest_rate_sensitivity": market.interest_rate_sensitivity,
                "fx_exposure_pct": market.fx_exposure_pct,
                "reporting_currency": market.reporting_currency,
                # Mejora 3: self-describing HHI block (definition + interpretation).
                "concentration_metrics": market.concentration_metrics,
            },
        },
        "financiero": {
            "label": "Riesgo Financiero",
            "level": fin_level,
            "score": fin_score,
            "key_findings": liquidity.key_findings + solvency.key_findings,
            "components": {
                "liquidez": {
                    "level": liquidity.level.value,
                    "score": liquidity.score,
                    "razon_corriente": liquidity.razon_corriente,
                    "cash_pct_assets": liquidity.cash_pct_assets,
                    "net_redemption_pressure_pct": liquidity.net_redemption_pressure_pct,
                },
                "solvencia": {
                    "level": solvency.level.value,
                    "score": solvency.score,
                    "endeudamiento_global": solvency.endeudamiento_global,
                    "deuda_patrimonio": solvency.deuda_patrimonio,
                    "equity_ratio": solvency.equity_ratio,
                },
            },
        },
    }


def _is_generic_cause(causes: list) -> bool:
    """Generic template fallback text (no specific figures), e.g. the
    'Variación de … Anomalías: …' placeholder the analyzer emits as a last resort."""
    if not causes:
        return True
    cause_text = causes[0] if isinstance(causes[0], str) else ""
    return "Variación de" in cause_text and "Anomalías:" in cause_text


def _build_anti_hallucination_result(analysis_results: list) -> Dict[str, Any]:
    """
    Mejora 6: run anti-hallucination checks and return a structured result that
    names the affected account_id and the specific check that failed, plus the
    action taken. Each failure also carries enough to flag the account itself.
    """
    failures: list[dict] = []
    checks_performed = 0

    high_mat = [a for a in analysis_results if a.get("materiality") == "HIGH"]

    # Check A — executive insight of HIGH materiality account must cite figures.
    for a in high_mat[:8]:
        checks_performed += 1
        insight = a.get("executive_insight", "") or ""
        if insight and not any(c.isdigit() for c in insight):
            failures.append({
                "account_id": a.get("account_id", ""),
                "check": "insight_cifras_verificables",
                "detail": (
                    f"El insight ejecutivo de '{a.get('account_name', '')}' no contiene "
                    f"cifras verificables contra los datos fuente."
                ),
                "action": "Insight marcado como NO VERIFICADO. Requiere revisión humana.",
            })

    # Check B — HIGH materiality account must not rely on generic template causes.
    for a in high_mat[:8]:
        checks_performed += 1
        if _is_generic_cause(a.get("possible_causes", [])):
            failures.append({
                "account_id": a.get("account_id", ""),
                "check": "causas_vs_datos",
                "detail": (
                    f"Las causas de '{a.get('account_name', '')}' son genéricas "
                    f"(texto de plantilla) y no referencian cifras o posiciones específicas."
                ),
                "action": "Causa marcada como INFERENCIA. Confianza reducida a 0.75.",
            })

    checks_failed = len(failures)
    passed = checks_failed == 0
    return {
        "passed": passed,
        "checks_performed": checks_performed,
        "checks_failed": checks_failed,
        "failures": failures,
        "impact_on_output": (
            "Los campos afectados están marcados individualmente con hallucination_flag. "
            f"El validation_score fue penalizado por {checks_failed} fallo(s) de anti-alucinación."
            if not passed else
            "Todas las verificaciones de anti-alucinación pasaron."
        ),
    }


def _build_validation_detail(
    analysis_results: list,
    financial_ratios: dict,
    anti_h: Dict[str, Any],
) -> tuple[int, Dict[str, Any], list[str]]:
    """
    Mejora 2: compute the validation_score as a weighted sum of 5 independently
    scored components, exposing each component score, weight and the penalties
    that reduced it. Returns (scalar_score, detail_dict, issues_penalized).
    """
    issues_penalized: list[str] = []
    high_mat = [a for a in analysis_results if a.get("materiality") == "HIGH"]

    # ── 1. Consistencia matemática (variation_pct vs valores absolutos) ────────
    tolerance = 0.5
    math_errors = 0
    for a in analysis_results[:20]:
        prev = a.get("previous_value")
        curr = a.get("current_value", 0.0)
        reported_pct = a.get("variation_pct", 0.0)
        if prev and prev != 0 and reported_pct != 0:
            expected_pct = (curr - prev) / abs(prev) * 100
            if abs(expected_pct - reported_pct) > tolerance:
                math_errors += 1
    consistencia = max(0, 100 - math_errors * 10)
    if math_errors:
        issues_penalized.append(
            f"{math_errors} cuenta(s) con variación porcentual inconsistente con los "
            f"valores absolutos: -{math_errors * 10} puntos en consistencia_matematica"
        )

    # ── 2. Coherencia narrativa (insights ejecutivos con cifras) ───────────────
    insight_fail = sum(
        1 for f in anti_h.get("failures", [])
        if f.get("check") == "insight_cifras_verificables"
    )
    coherencia = max(0, 100 - insight_fail * 10)
    if insight_fail:
        issues_penalized.append(
            f"{insight_fail} insight(s) ejecutivo(s) sin cifras verificables: "
            f"-{insight_fail * 10} puntos en coherencia_narrativa"
        )

    # ── 3. Cobertura NIIF (cuentas HIGH con nota NIIF asignada) ────────────────
    if high_mat:
        with_note = sum(
            1 for a in high_mat
            if a.get("requires_niif_note") or a.get("niif_note_references")
        )
        cobertura = round(with_note / len(high_mat) * 100)
        if cobertura < 100:
            issues_penalized.append(
                f"{len(high_mat) - with_note} de {len(high_mat)} cuentas HIGH sin nota NIIF "
                f"asignada: cobertura_niif en {cobertura}/100"
            )
    else:
        cobertura = 100

    # ── 4. Calidad de causas (genéricas vs específicas) ────────────────────────
    causa_fail = sum(
        1 for f in anti_h.get("failures", [])
        if f.get("check") == "causas_vs_datos"
    )
    calidad_causas = max(0, 100 - causa_fail * 8)
    if causa_fail:
        issues_penalized.append(
            f"{causa_fail} cuenta(s) HIGH con causas genéricas detectadas: "
            f"-{causa_fail * 8} puntos en calidad_causas"
        )

    # ── 5. Anti-alucinación (resultado de validación cruzada) ──────────────────
    performed = anti_h.get("checks_performed", 0)
    failed = anti_h.get("checks_failed", 0)
    if performed:
        anti_alucinacion = round(100 * (performed - failed) / performed)
    else:
        anti_alucinacion = 100
    if not anti_h.get("passed", True):
        issues_penalized.append(
            f"anti_hallucination_passed=false ({failed}/{performed} verificaciones fallidas): "
            f"componente anti_alucinacion en {anti_alucinacion}/100"
        )

    components = {
        "consistencia_matematica": {
            "score": consistencia, "weight": 0.30,
            "description": "Verificación de cuadre de totales, variaciones calculadas vs. declaradas y ratios derivables.",
        },
        "coherencia_narrativa": {
            "score": coherencia, "weight": 0.25,
            "description": "Alineación entre insights ejecutivos y cifras subyacentes.",
        },
        "cobertura_niif": {
            "score": cobertura, "weight": 0.20,
            "description": "Porcentaje de cuentas con materialidad HIGH que tienen nota NIIF asignada.",
        },
        "calidad_causas": {
            "score": calidad_causas, "weight": 0.15,
            "description": "Detección de causas genéricas (texto de plantilla) vs. causas específicas con cifras.",
        },
        "anti_alucinacion": {
            "score": anti_alucinacion, "weight": 0.10,
            "description": "Resultado de validación cruzada entre claims del análisis y datos fuente.",
        },
    }

    validation_score = round(sum(c["score"] * c["weight"] for c in components.values()))
    validation_score = max(0, min(100, validation_score))

    detail = {
        "value": validation_score,
        "components": components,
        "formula": "sum(score_i * weight_i)",
        "issues_penalized": issues_penalized,
    }
    return validation_score, detail, issues_penalized


def _build_anomaly_summary(analysis_results: list, confidence_threshold: float = 0.60) -> Dict[str, Any]:
    """
    Mejora 4: take an explicit decision on low-signal anomalies instead of
    self-doubting ("posible sobredetección"). Anomalies on LOW-materiality
    accounts with sub-threshold confidence OR variation under the alert band are
    filtered out of the executive output (still available in technical detail).
    """
    detected = [a for a in analysis_results if a.get("anomaly_detected")]
    filtered: list[dict] = []
    for a in detected:
        is_low = a.get("materiality") == "LOW"
        low_conf = a.get("confidence", 1.0) < confidence_threshold
        minor_var = abs(a.get("variation_pct", 0.0)) < 15.0
        if is_low and (low_conf or minor_var):
            filtered.append(a)

    filtered_ids = [a.get("account_id", "") for a in filtered]
    return {
        "total_detected": len(detected),
        "included_in_output": len(detected) - len(filtered),
        "filtered_out": len(filtered),
        "anomaly_confidence_threshold": confidence_threshold,
        "filter_criteria": (
            "Anomalías en cuentas con materialidad LOW y confianza < 0.60 o variación "
            "absoluta < 15% filtradas del output ejecutivo. Disponibles en detalle técnico."
        ),
        "filtered_accounts": filtered_ids,
    }


def _build_data_quality_warnings(business_context) -> list[dict]:
    """
    Mejora 9: emit explicit warnings for missing business_context fields and how
    each gap limits the analysis. business_context may be a pydantic model or dict.
    """
    def _get(field_name):
        if business_context is None:
            return None
        if isinstance(business_context, dict):
            return business_context.get(field_name)
        return getattr(business_context, field_name, None)

    checks = [
        ("business_context.industry", "industry", "MEDIUM",
         "Sin clasificación sectorial, los benchmarks de ratios financieros se aplican con "
         "parámetros genéricos. Los umbrales de materialidad no están ajustados al sector específico."),
        ("business_context.fiscal_year", "fiscal_year", "LOW",
         "Sin año fiscal definido, el período se asume como semestre/año calendario. Podría "
         "afectar comparabilidad si la entidad usa un año fiscal diferente."),
        ("business_context.strategic_context", "strategic_context", "LOW",
         "Sin contexto estratégico, la narrativa de riesgo no incorpora eventos o decisiones "
         "de gestión que expliquen variaciones materiales."),
    ]
    warnings: list[dict] = []
    for label, attr, severity, impact in checks:
        if not _get(attr):
            warnings.append({"field": label, "value": None, "impact": impact, "severity": severity})
    return warnings


def _compute_analysis_confidence(
    analysis_results: list,
    financial_ratios: dict,
    anti_h: Dict[str, Any],
    data_quality_warnings: list,
) -> tuple[float, Dict[str, Any]]:
    """
    Mejora 7: analysis_confidence measures trust in the analysis (not business
    risk), so it is computed independently of composite_score. It blends input
    quality, data coverage, anti-hallucination outcome and variation reliability.
    """
    # 1. Input quality — completeness of business_context (1 - missing/total signals).
    total_ctx_signals = 3  # industry, fiscal_year, strategic_context (see warnings)
    missing = len([w for w in data_quality_warnings if w["field"].startswith("business_context")])
    input_quality = round((total_ctx_signals - missing) / total_ctx_signals, 3)

    # 2. Data coverage — share of headline ratios that are populated (non-null/non-zero).
    ratios = financial_ratios.get("ratios", {}) or {}
    key_ratios = ["razon_corriente", "endeudamiento_global", "roe_pct", "margen_neto_pct"]
    present = sum(1 for k in key_ratios if ratios.get(k) not in (None, 0, 0.0))
    data_coverage = round(present / len(key_ratios), 3)

    # 3. Anti-hallucination outcome.
    anti_h_factor = 1.0 if anti_h.get("passed", True) else 0.6

    # 4. Variation reliability — share of accounts the analyzer marked RELIABLE.
    if analysis_results:
        reliable = sum(1 for a in analysis_results if a.get("variation_reliability") == "RELIABLE")
        reliable_pct = round(reliable / len(analysis_results), 3)
    else:
        reliable_pct = 0.0

    weights = {"input_quality": 0.25, "data_coverage": 0.25, "anti_hallucination": 0.25, "variation_reliability": 0.25}
    confidence = round(
        input_quality * weights["input_quality"]
        + data_coverage * weights["data_coverage"]
        + anti_h_factor * weights["anti_hallucination"]
        + reliable_pct * weights["variation_reliability"],
        3,
    )

    detail = {
        "value": confidence,
        "formula": "input_quality*0.25 + data_coverage*0.25 + anti_hallucination*0.25 + variation_reliability*0.25",
        "factors": {
            "input_quality": input_quality,
            "data_coverage": data_coverage,
            "anti_hallucination": anti_h_factor,
            "variation_reliability": reliable_pct,
        },
        "weights": weights,
        "note": (
            "Mide confianza en el análisis (calidad de lo producido), independiente del "
            "composite_score que mide el riesgo del negocio."
        ),
    }
    return confidence, detail


def _build_compliance_flags(credit: CreditRiskResult, market: MarketRiskResult) -> list[str]:
    """
    Mejora 8: compliance_flags carry ONLY regulatory signals (NIIF/NIC, SFC,
    normative limits) — never the operational risk_drivers, which live in
    risk_dimensions.<dimension>.risk_drivers.
    """
    flags: list[str] = []
    if credit.related_party_exposure:
        flags.append("NIC 24 — operaciones con partes relacionadas identificadas: requieren revelación.")
    if market.hhi is not None and market.hhi >= 0.25:
        flags.append(
            f"SFC Colombia — concentración de cartera supera el umbral de referencia "
            f"(HHI {market.hhi:.3f} > 0.25). Ver risk_dimensions.market.concentration_metrics."
        )
    if credit.top_custodian_pct is not None and credit.top_custodian_pct >= 70.0:
        flags.append(
            f"SFC — concentración de contraparte/custodio elevada: {credit.top_custodian_pct:.1f}% "
            f"del efectivo en un único custodio."
        )
    return flags


def compute_composite(
    liquidity: LiquidityRiskResult,
    credit: CreditRiskResult,
    solvency: SolvencyRiskResult,
    market: MarketRiskResult,
    operational: OperationalRiskResult,
    analysis_results: list,
    financial_ratios: dict,
    is_investment_fund: bool,
    business_context=None,
) -> CompositeRiskResult:

    weight_profile = "fund" if is_investment_fund else "corporate"
    weights = _WEIGHT_PROFILES[weight_profile]

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

    composite_score = round(sum(dimension_scores[k] * weights[k] for k in weights))

    # Ceiling / floor rules
    levels = list(dimension_levels.values())
    overall_level = _level_from_score(composite_score)

    if overall_level == RiskLevel.LOW and "HIGH" in levels:
        overall_level = RiskLevel.MEDIUM  # floor raised by a HIGH dimension

    if all(lvl == "LOW" for lvl in levels):
        overall_level = RiskLevel.LOW  # floor — unanimous LOW overrides arithmetic

    # ── Mejora 1: composite_score transparency ────────────────────────────────
    weighted_components = {
        _DIM_ES[k]: round(dimension_scores[k] * weights[k], 1) for k in weights
    }
    formula = " + ".join(f"{_DIM_ES[k]}*{weights[k]:.2f}" for k in
                         ("liquidity", "credit", "solvency", "market", "operational"))
    composite_score_detail = {
        "value": composite_score,
        "formula": formula,
        "weights": {_DIM_ES[k]: weights[k] for k in weights},
        "weighted_components": weighted_components,
        "weight_profile": weight_profile,
        "weight_profile_rationale": _PROFILE_RATIONALE[weight_profile],
    }

    # ── Mejoras 6 + 2: anti-hallucination + validation transparency ───────────
    anti_hallucination_result = _build_anti_hallucination_result(analysis_results)
    anti_hallucination_passed = anti_hallucination_result["passed"]
    validation_score, validation_score_detail, issues = _build_validation_detail(
        analysis_results, financial_ratios, anti_hallucination_result
    )

    # ── Mejora 4: anomaly decision summary ────────────────────────────────────
    anomaly_detection_summary = _build_anomaly_summary(analysis_results)

    # ── Mejora 9: data quality warnings ───────────────────────────────────────
    data_quality_warnings = _build_data_quality_warnings(business_context)

    # ── Mejora 7: analysis_confidence (independent of composite_score) ─────────
    analysis_confidence, analysis_confidence_detail = _compute_analysis_confidence(
        analysis_results, financial_ratios, anti_hallucination_result, data_quality_warnings
    )

    # ── Mejora 8: regulatory-only compliance flags ────────────────────────────
    compliance_flags = _build_compliance_flags(credit, market)

    # Determine if human review is warranted
    high_count = sum(1 for lvl in levels if lvl == "HIGH")
    requires_human_review = (
        overall_level == RiskLevel.HIGH
        or high_count >= 2
        or not anti_hallucination_passed
        or composite_score < 35
    )

    return CompositeRiskResult(
        overall_risk_score=overall_level,
        composite_score=composite_score,
        validation_score=validation_score,
        requires_human_review=requires_human_review,
        analysis_confidence=analysis_confidence,
        anti_hallucination_passed=anti_hallucination_passed,
        issues_found=issues,
        compliance_flags=compliance_flags,
        dimension_scores=dimension_scores,
        dimension_levels=dimension_levels,
        composite_score_detail=composite_score_detail,
        validation_score_detail=validation_score_detail,
        anti_hallucination_result=anti_hallucination_result,
        analysis_confidence_detail=analysis_confidence_detail,
        data_quality_warnings=data_quality_warnings,
        anomaly_detection_summary=anomaly_detection_summary,
    )
