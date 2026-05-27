"""
concentration_engine.py  —  Improvement #4: Portfolio Concentration Engine

Detects account and category concentration relative to total financial base.
Relevant for investment funds, holding companies, and diversified portfolios.
Pure deterministic — no LLM.
"""

from dataclasses import dataclass, field

from .ratio_engine import AccountVariation, FinancialTotals


@dataclass
class ConcentrationResult:
    top_account_name: str
    top_account_pct: float            # % of total assets for the single largest account
    top3_concentration_pct: float     # combined % of top 3 accounts
    category_concentration: dict      # { category: pct_of_total_base }
    concentration_label: str          # "LOW" | "MEDIUM" | "HIGH" | "CRITICAL"
    insight: str
    top_accounts: list[dict] = field(default_factory=list)   # [{name, value, pct, category}]


def analyze_concentration(
    variations: list[AccountVariation],
    totals: FinancialTotals,
) -> ConcentrationResult:
    """
    Analyse account and category concentration relative to total financial base.
    Uses total_assets as denominator; falls back to total_revenue for P&L-only statements.
    """
    base = totals.total_assets if totals.total_assets > 0 else totals.total_revenue
    base = max(base, 0.001)

    # Sort by absolute current value descending
    sorted_vars = sorted(variations, key=lambda v: abs(v.current_value), reverse=True)

    top_accounts = [
        {
            "name": v.account_name,
            "value_cop_mm": round(v.current_value, 2),
            "pct_of_total": round(abs(v.current_value) / base * 100, 2),
            "category": v.category,
        }
        for v in sorted_vars[:10]
    ]

    top1_pct = top_accounts[0]["pct_of_total"] if top_accounts else 0.0
    top3_pct = sum(a["pct_of_total"] for a in top_accounts[:3])

    # Category concentration
    cat_totals: dict[str, float] = {}
    for v in variations:
        cat = v.category.lower()
        cat_totals[cat] = cat_totals.get(cat, 0.0) + abs(v.current_value)

    category_concentration = {
        cat: round(val / base * 100, 1)
        for cat, val in sorted(cat_totals.items(), key=lambda x: -x[1])
    }

    # Concentration label thresholds
    if top1_pct >= 50 or top3_pct >= 80:
        label = "CRITICAL"
    elif top1_pct >= 30 or top3_pct >= 60:
        label = "HIGH"
    elif top1_pct >= 15 or top3_pct >= 40:
        label = "MEDIUM"
    else:
        label = "LOW"

    top_name = top_accounts[0]["name"] if top_accounts else "N/A"

    if top1_pct >= 40:
        insight = (
            f"Concentración CRÍTICA: la cuenta '{top_name}' representa el {top1_pct:.1f}% "
            f"del total. Las 3 mayores posiciones concentran el {top3_pct:.1f}%."
        )
    elif top3_pct >= 60:
        insight = (
            f"Concentración ALTA: las 3 principales cuentas acumulan el {top3_pct:.1f}% "
            f"del total. Mayor posición: '{top_name}' con {top1_pct:.1f}%."
        )
    else:
        insight = (
            f"Concentración {label}: cuenta principal '{top_name}' representa "
            f"el {top1_pct:.1f}% del total. Top 3: {top3_pct:.1f}%."
        )

    return ConcentrationResult(
        top_account_name=top_name,
        top_account_pct=round(top1_pct, 2),
        top3_concentration_pct=round(top3_pct, 2),
        category_concentration=category_concentration,
        concentration_label=label,
        insight=insight,
        top_accounts=top_accounts,
    )
