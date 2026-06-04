"""
materiality_engine.py

Classifies account variations as LOW / MEDIUM / HIGH materiality
following Colombian NIIF audit standards.
Also infers applicable NIIF references from account name and category.
Computes economic impact_score (0–100) for relevance ranking.
No LLM.
"""

import logging

from shared.models.base import MaterialityLevel

from .ratio_engine import AccountVariation, FinancialTotals
from .variation_reliability import VariationReliability

logger = logging.getLogger("financial_analyzer.materiality_engine")

# Standard NIIF-Colombia materiality: 1 % of max(total_assets, total_revenue)
_MATERIALITY_RATE: float = 0.01
# MEDIUM threshold: half of materiality threshold
_MEDIUM_MULTIPLIER: float = 0.5
# Minimum floor in COP MM
_FLOOR_COP_MM: float = 1.0

# ── NIIF reference lookup by category and account keyword ─────────────────────
# Structure: { category: { keyword: [applicable_standards] } }
_NIIF_MAP: dict[str, dict[str, list[str]]] = {
    "assets": {
        "propiedad": ["NIC 16"],
        "planta": ["NIC 16"],
        "equipo": ["NIC 16"],
        "maquinaria": ["NIC 16"],
        "intangible": ["NIC 38"],
        "goodwill": ["NIIF 3"],
        "plusvalia": ["NIIF 3"],
        "cartera": ["NIIF 9"],
        "clientes": ["NIIF 9"],
        "cuentas por cobrar": ["NIIF 9"],
        "deudores": ["NIIF 9"],
        "arrendamiento": ["NIIF 16"],
        "inventar": ["NIC 2"],
        "existencia": ["NIC 2"],
        "instrumento financiero": ["NIIF 7", "NIIF 9", "NIC 32"],
        "activo por impuesto diferido": ["NIC 12"],
        "impuesto diferido": ["NIC 12"],
        "inversiones": ["NIIF 9", "NIIF 7"],
        # Investment fund / portfolio keywords
        "valor razonable": ["NIIF 9", "NIIF 13"],
        "activos financieros": ["NIIF 9", "NIIF 7"],
        "acciones": ["NIIF 9", "NIIF 13"],
        "portafolio": ["NIIF 9", "NIIF 7"],
        "fondo de inversión": ["NIIF 9"],
        "fondo liquidez": ["NIIF 9"],
        "efectivo": ["NIC 7"],
        "banco": ["NIC 7"],
    },
    "liabilities": {
        "prestamo": ["NIIF 7", "NIC 32"],
        "obligacion financiera": ["NIIF 7", "NIC 32"],
        "instrumento financiero": ["NIIF 7", "NIC 32"],
        "arrendamiento": ["NIIF 16"],
        "provision": ["NIC 37"],
        "pasivo por impuesto diferido": ["NIC 12"],
        "impuesto diferido": ["NIC 12"],
        "beneficio empleado": ["NIC 19"],
        "pension": ["NIC 19"],
        "bonos": ["NIIF 7", "NIC 32"],
    },
    "equity": {
        "capital": ["NIC 1"],
        "reserva": ["NIC 1"],
        "utilidad retenida": ["NIC 1"],
        "perdida acumulada": ["NIC 1"],
        "patrimonio": ["NIC 32", "NIC 1"],
        "suscripciones": ["NIC 32"],
        "partícipes": ["NIC 32", "NIC 1"],
        "inversionistas": ["NIC 32", "NIC 24"],
    },
    "revenue": {
        "ingreso": ["NIIF 15"],
        "venta": ["NIIF 15"],
        "dividendo": ["NIIF 9"],
        "intereses recibidos": ["NIIF 9"],
        "contrato": ["NIIF 15"],
        "valorización": ["NIIF 9", "NIIF 13"],
        "valoración": ["NIIF 9", "NIIF 13"],
        "valor razonable": ["NIIF 9", "NIIF 13"],
    },
    "expense": {
        "depreciacion": ["NIC 16"],
        "amortizacion": ["NIC 38"],
        "impuesto": ["NIC 12"],
        "beneficio empleado": ["NIC 19"],
        "deterioro": ["NIC 36"],
        "provision": ["NIC 37"],
        "intereses pagados": ["NIIF 7"],
        "costo financiero": ["NIIF 7"],
    },
}


def determine_threshold(totals: FinancialTotals) -> float:
    """
    Compute the global materiality threshold.
    Standard: 1 % of max(total_assets, total_revenue), minimum 1 COP MM.
    """
    base = max(totals.total_assets, totals.total_revenue)
    threshold = base * _MATERIALITY_RATE
    return max(threshold, _FLOOR_COP_MM)


def classify(variation: AccountVariation, threshold: float) -> MaterialityLevel:
    """
    Classify materiality based on the absolute variation against the threshold.
    HIGH  ≥ threshold
    MEDIUM ≥ threshold × 0.5
    LOW   < threshold × 0.5
    """
    magnitude = abs(variation.absolute_variation)
    if magnitude >= threshold:
        return MaterialityLevel.HIGH
    if magnitude >= threshold * _MEDIUM_MULTIPLIER:
        return MaterialityLevel.MEDIUM
    return MaterialityLevel.LOW


def calculate_impact_score(
    variation: AccountVariation,
    threshold: float,
    totals: FinancialTotals,
    reliability: VariationReliability,
    anomaly_detected: bool,
) -> float:
    """
    Improvement #5: Economic Relevance Ranking.
    Returns an impact_score in [0, 100] — higher means more economically relevant.

    Components:
    - 40 pts  materiality level (HIGH=40, MEDIUM=20, LOW=5)
    - 25 pts  absolute value as % of financial base (assets or revenue)
    - 20 pts  variation magnitude (only when RELIABLE baseline)
    - 15 pts  anomaly bonus
    """
    base = max(totals.total_assets, totals.total_revenue, 0.001)
    mat = classify(variation, threshold)

    mat_pts = {MaterialityLevel.HIGH: 40.0, MaterialityLevel.MEDIUM: 20.0, MaterialityLevel.LOW: 5.0}[mat]

    # Absolute value as fraction of total base, capped at 25 pts
    size_ratio = abs(variation.current_value) / base
    size_pts = min(size_ratio * 250.0, 25.0)

    # Variation magnitude (only when reliable)
    if reliability == VariationReliability.RELIABLE and variation.has_previous_value:
        var_ratio = abs(variation.absolute_variation) / base
        var_pts = min(var_ratio * 200.0, 20.0)
    else:
        var_pts = 0.0

    anomaly_pts = 15.0 if anomaly_detected else 0.0

    return round(min(mat_pts + size_pts + var_pts + anomaly_pts, 100.0), 2)


def build_account_trace(
    variation: AccountVariation,
    threshold: float,
    totals: FinancialTotals,
    materiality: MaterialityLevel,
    impact_score: float,
    reliability: VariationReliability,
    anomaly_detected: bool,
) -> dict:
    """Build the deterministic computation_trace for a single account.

    All values are taken from already-computed objects so no re-calculation
    occurs — the trace is a faithful record of what the pipeline actually produced.

    Returns a dict with keys:
      absolute_variation, variation_pct, materiality_threshold,
      materiality_classification, impact_score.
    Each entry: {formula, inputs, result, [note], [components]}.
    """
    magnitude = abs(variation.absolute_variation)
    base = max(totals.total_assets, totals.total_revenue, 0.001)

    mat_pts = {
        MaterialityLevel.HIGH: 40.0,
        MaterialityLevel.MEDIUM: 20.0,
        MaterialityLevel.LOW: 5.0,
    }[materiality]
    size_ratio = abs(variation.current_value) / base
    size_pts = min(size_ratio * 250.0, 25.0)
    if reliability == VariationReliability.RELIABLE and variation.has_previous_value:
        var_ratio = abs(variation.absolute_variation) / base
        var_pts = min(var_ratio * 200.0, 20.0)
    else:
        var_pts = 0.0
    anomaly_pts = 15.0 if anomaly_detected else 0.0

    # The exposed variation_pct field is None whenever reliability suppresses it
    # (no baseline, near-zero baseline, extreme reclassification). The trace must
    # report the same value the field exposes so the two never diverge (§5.4).
    pct_suppressed = reliability != VariationReliability.RELIABLE
    if not variation.has_previous_value:
        pct_formula = "N/A — sin período anterior (cuenta nueva)"
    elif variation.previous_value == 0:
        pct_formula = "N/A — previous_value = 0"
    elif pct_suppressed:
        pct_formula = f"N/A — % suprimido (confiabilidad = {reliability.value})"
    else:
        pct_formula = "(absolute_variation / previous_value) * 100"
    pct_result = None if pct_suppressed else variation.variation_pct
    if not variation.has_previous_value:
        pct_note = "No baseline period available"
    elif pct_suppressed:
        pct_note = (
            f"Suprimido — confiabilidad {reliability.value}; "
            f"valor crudo calculado = {variation.variation_pct:+.2f}%"
        )
    else:
        pct_note = None

    return {
        "absolute_variation": {
            "formula": "current_value - previous_value",
            "inputs": {
                "current_value": variation.current_value,
                "previous_value": variation.previous_value,
            },
            "result": variation.absolute_variation,
        },
        "variation_pct": {
            "formula": pct_formula,
            "inputs": {
                "absolute_variation": variation.absolute_variation,
                "previous_value": variation.previous_value,
                "raw_computed_pct": variation.variation_pct,
            },
            "result": pct_result,
            "note": pct_note,
        },
        "materiality_threshold": {
            "formula": f"max(max(total_assets, total_revenue) * {_MATERIALITY_RATE}, {_FLOOR_COP_MM})",
            "inputs": {
                "total_assets": totals.total_assets,
                "total_revenue": totals.total_revenue,
                "rate": _MATERIALITY_RATE,
                "floor_cop_mm": _FLOOR_COP_MM,
            },
            "result": threshold,
        },
        "materiality_classification": {
            "formula": (
                f"|absolute_variation| >= threshold → HIGH; "
                f">= threshold * {_MEDIUM_MULTIPLIER} → MEDIUM; else LOW"
            ),
            "inputs": {
                "magnitude": magnitude,
                "threshold": threshold,
                "medium_threshold": round(threshold * _MEDIUM_MULTIPLIER, 3),
            },
            "result": materiality.value,
        },
        "impact_score": {
            "formula": "min(mat_pts + size_pts + var_pts + anomaly_pts, 100)",
            "inputs": {
                "base": round(base, 3),
                "mat_pts": mat_pts,
                "size_pts": round(size_pts, 2),
                "var_pts": round(var_pts, 2),
                "anomaly_pts": anomaly_pts,
            },
            "components": {
                "materiality": f"{mat_pts} pts  (materiality = {materiality.value})",
                "size": (
                    f"{size_pts:.2f} pts  "
                    f"= min(|{variation.current_value:.2f}| / {base:.2f} * 250, 25)"
                ),
                "variation": (
                    f"{var_pts:.2f} pts"
                    if reliability == VariationReliability.RELIABLE
                    else f"0.00 pts  (suppressed — reliability = {reliability.value})"
                ),
                "anomaly_bonus": f"{anomaly_pts} pts  ({'detected' if anomaly_detected else 'none'})",
            },
            "result": impact_score,
        },
    }


def infer_niif_references(
    account_name: str,
    category: str,
    materiality: MaterialityLevel | None = None,
) -> list[str]:
    """
    Keyword-based NIIF standard lookup.
    Returns a sorted, deduplicated list of applicable NIIF/NIC references.
    """
    name_lower = account_name.lower()
    cat_lower = category.lower()

    refs: set[str] = set()
    keyword_map = _NIIF_MAP.get(cat_lower, {})
    for keyword, standards in keyword_map.items():
        if keyword in name_lower:
            refs.update(standards)

    # Cross-category keywords
    if "partes relacionadas" in name_lower or "comisión de administración" in name_lower:
        refs.add("NIC 24")
    if "impuesto" in name_lower:
        refs.add("NIC 12")
    if "flujo de efectivo" in name_lower or "flujo neto" in name_lower:
        refs.add("NIC 7")

    # HIGH materiality always warrants NIC 1 disclosure
    if materiality == MaterialityLevel.HIGH and refs:
        refs.add("NIC 1")

    return sorted(refs)
