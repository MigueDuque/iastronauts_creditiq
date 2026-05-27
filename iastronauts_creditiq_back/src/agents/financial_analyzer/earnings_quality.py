"""
earnings_quality.py  —  Improvement #3: Earnings Quality Analyzer

Distinguishes real operational profitability from valuation-driven income.
Pure deterministic analysis — no LLM.
"""

from dataclasses import dataclass, field

from .ratio_engine import AccountVariation, FinancialTotals

_VALUATION_KW = (
    "valorizacion", "valor razonable", "ganancia en venta", "rendimiento de inversiones",
    "resultado de inversiones", "valoracion", "participacion en subsidiarias",
    "metodo de participacion", "ganancia por valoracion", "cambios en valor razonable",
)
_OPERATING_KW = (
    "venta", "prestacion de servicio", "ingreso operativo", "ingreso por contratos",
    "cuota de administracion", "honorario",
)
_NON_RECURRING_KW = (
    "ganancia por disposicion", "venta de activo", "indemnizacion",
    "resultado extraordinario", "recuperacion de cartera castigada",
    "subsidio", "condonacion",
)


def _hit(name: str, kw: tuple) -> bool:
    return any(k in name for k in kw)


@dataclass
class EarningsQualityResult:
    quality_score: float              # 0–100; higher = better quality
    quality_label: str                # "HIGH" | "MEDIUM" | "LOW"
    fair_value_income_ratio: float    # % of income from unrealized valuations
    operating_income_ratio: float     # % of income from core recurring operations
    non_recurring_income_ratio: float # % of income from one-time items
    key_insight: str
    recommendations: list[str] = field(default_factory=list)


def analyze_earnings_quality(
    variations: list[AccountVariation],
    totals: FinancialTotals,
) -> EarningsQualityResult:
    """
    Assess earnings quality: separates real operational income from
    valuation gains and non-recurring items.
    """
    base = max(totals.total_revenue, 0.001)

    valuation_income = 0.0
    operating_income = 0.0
    non_recurring_income = 0.0

    for v in variations:
        name = v.account_name.lower()
        cat = v.category.lower()
        val = max(v.current_value, 0.0)  # only positive contributions

        if cat not in ("revenue", "other"):
            continue

        if _hit(name, _VALUATION_KW):
            valuation_income += val
        elif _hit(name, _NON_RECURRING_KW):
            non_recurring_income += val
        elif _hit(name, _OPERATING_KW):
            operating_income += val

    fair_value_ratio = round(min(valuation_income / base * 100, 100.0), 1)
    operating_ratio = round(min(operating_income / base * 100, 100.0), 1)
    non_recurring_ratio = round(min(non_recurring_income / base * 100, 100.0), 1)

    # Score: 100 base, penalise non-cash and non-recurring income
    score = 100.0
    score -= fair_value_ratio * 0.60      # up to -60 pts for full valuation dependency
    score -= non_recurring_ratio * 0.35   # up to -35 pts for fully non-recurring
    score += min(operating_ratio * 0.25, 25.0)  # up to +25 pts for high operational base
    score = max(0.0, min(100.0, round(score, 1)))

    label = "HIGH" if score >= 70 else ("MEDIUM" if score >= 40 else "LOW")

    # Key insight
    if fair_value_ratio > 50:
        insight = (
            f"Resultado neto impulsado principalmente por ganancias de valorización "
            f"({fair_value_ratio:.0f}% de los ingresos). "
            f"Calidad baja — los resultados dependen del mercado, no de la operación recurrente."
        )
    elif non_recurring_ratio > 30:
        insight = (
            f"Alta proporción de ingresos no recurrentes ({non_recurring_ratio:.0f}%). "
            f"Los resultados actuales podrían no sostenerse en períodos futuros."
        )
    elif operating_ratio > 70:
        insight = (
            f"Alta calidad de utilidades — {operating_ratio:.0f}% de los ingresos proviene "
            f"de actividades operativas recurrentes."
        )
    else:
        insight = (
            f"Estructura de ingresos mixta: operativo {operating_ratio:.0f}%, "
            f"valorización {fair_value_ratio:.0f}%, no recurrente {non_recurring_ratio:.0f}%."
        )

    recs: list[str] = []
    if fair_value_ratio > 30:
        recs.append(
            "Separar ingresos por valorización de ingresos operativos en el análisis de tendencias."
        )
    if non_recurring_ratio > 20:
        recs.append(
            "Identificar y excluir partidas no recurrentes para construir proyecciones confiables."
        )
    if operating_ratio < 30 and totals.total_revenue > 0:
        recs.append(
            "Evaluar la dependencia estructural en ingresos no operativos para proyecciones a largo plazo."
        )

    return EarningsQualityResult(
        quality_score=score,
        quality_label=label,
        fair_value_income_ratio=fair_value_ratio,
        operating_income_ratio=operating_ratio,
        non_recurring_income_ratio=non_recurring_ratio,
        key_insight=insight,
        recommendations=recs,
    )
