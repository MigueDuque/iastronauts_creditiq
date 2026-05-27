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
    },
    "revenue": {
        "ingreso": ["NIIF 15"],
        "venta": ["NIIF 15"],
        "dividendo": ["NIIF 9"],
        "intereses recibidos": ["NIIF 9"],
        "contrato": ["NIIF 15"],
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


def infer_niif_references(account_name: str, category: str) -> list[str]:
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

    return sorted(refs)
