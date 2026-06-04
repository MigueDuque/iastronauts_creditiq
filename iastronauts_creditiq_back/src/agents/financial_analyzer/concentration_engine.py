"""
concentration_engine.py  —  Improvement #4: Portfolio Concentration Engine

Detects account and category concentration relative to total financial base.
Relevant for investment funds, holding companies, and diversified portfolios.
Pure deterministic — no LLM.
"""

from dataclasses import dataclass, field

from .ratio_engine import AccountVariation, FinancialTotals

# Cash-flow and flow-statement accounts contaminate portfolio concentration metrics.
# Flows are NOT positions — excluding them gives a correct picture of holdings.
_CASH_FLOW_KEYWORDS: tuple[str, ...] = (
    "flujo de efectivo",
    "flujo neto",
    "cash flow",
    "efectivo inicial",
    "efectivo final",
    "aumento neto de efectivo",
    "net cash",
    "variación de efectivo",
    "flujo de caja",
    "flujo neto de",
    "aportes de inversionistas",
    "retiros de inversionistas",
    "patrimonio neto inicial",
    "patrimonio neto final",
    "utilidad del período",
    "ganancia del período",
    "pérdida del período",
    "resultado del período",
)


def _is_portfolio_position(variation: AccountVariation) -> bool:
    """Return True only for real asset holdings; exclude flow/P&L accounts."""
    # Subtotal/total rows (e.g. "Total activos") are not positions — counting them
    # double-counts and, combined with liability/equity rows, pushes top-N above 100%.
    if variation.is_total:
        return False
    # Concentration is measured within asset holdings. Mixing liabilities/equity in
    # (which by the accounting identity sum to ≈ assets) is what produced top-3 > 100%.
    if variation.category.lower() != "assets":
        return False
    name = variation.account_name.lower()
    if any(kw in name for kw in _CASH_FLOW_KEYWORDS):
        return False
    # Negative values are typical of flows/expenses, not balance-sheet positions
    if variation.current_value < 0:
        return False
    return True


@dataclass
class ConcentrationResult:
    top_account_name: str
    top_account_pct: float            # % of total assets for the single largest account
    top3_concentration_pct: float     # combined % of top 3 accounts
    category_concentration: dict      # { category: pct_of_total_base }
    concentration_label: str          # "LOW" | "MEDIUM" | "HIGH" | "CRITICAL"
    insight: str
    top_accounts: list[dict] = field(default_factory=list)   # [{name, value, pct, category}]
    hhi: float = 0.0                  # Herfindahl-Hirschman Index = Σ(wi²); range 0–1
    effective_positions: float = 0.0  # 1/HHI — equivalent number of equal-weight positions


def analyze_concentration(
    variations: list[AccountVariation],
    totals: FinancialTotals,
    leaf_ids: set[str] | None = None,
) -> ConcentrationResult:
    """
    Analyse account and category concentration relative to total financial base.
    Uses total_assets as denominator; falls back to total_revenue for P&L-only statements.

    `leaf_ids` (from hierarchy_engine) restricts the universe to leaf positions, so a
    portfolio *summary line* never competes with its own per-instrument breakdown — the
    double-count that previously inflated HHI / top-N. When omitted (e.g. PDF/CSV with no
    sheet structure) behaviour is unchanged.
    """
    base = totals.total_assets if totals.total_assets > 0 else totals.total_revenue
    base = max(base, 0.001)

    # Drop non-leaf rows (summary lines that are broken down elsewhere) up front so a
    # parent and its children are never both ranked.
    universe = variations
    if leaf_ids is not None:
        leaves = [v for v in variations if v.account_id in leaf_ids]
        if leaves:
            universe = leaves

    # Filter to real portfolio positions only before computing concentration
    portfolio_vars = [v for v in universe if _is_portfolio_position(v)]
    # Fall back to all accounts if filtering leaves nothing (non-fund statements)
    if not portfolio_vars:
        portfolio_vars = universe

    # Sort by absolute current value descending
    sorted_vars = sorted(portfolio_vars, key=lambda v: abs(v.current_value), reverse=True)

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

    # Herfindahl-Hirschman Index: Σ(wi²) where wi = value/base (as a fraction 0–1)
    hhi = sum((abs(v.current_value) / base) ** 2 for v in portfolio_vars)
    hhi = round(min(hhi, 1.0), 4)
    effective_positions = round(1.0 / hhi, 2) if hhi > 0 else 0.0

    # Category concentration
    cat_totals: dict[str, float] = {}
    for v in portfolio_vars:
        cat = v.category.lower()
        cat_totals[cat] = cat_totals.get(cat, 0.0) + abs(v.current_value)

    category_concentration = {
        cat: round(val / base * 100, 1)
        for cat, val in sorted(cat_totals.items(), key=lambda x: -x[1])
    }

    # Concentration label — driven by HHI first, top-N as tiebreaker
    # HHI > 0.25 → CRITICAL; 0.15–0.25 → HIGH; 0.10–0.15 → MEDIUM; < 0.10 → LOW
    if hhi > 0.25 or top1_pct >= 50 or top3_pct >= 80:
        label = "CRITICAL"
    elif hhi > 0.15 or top1_pct >= 30 or top3_pct >= 60:
        label = "HIGH"
    elif hhi > 0.10 or top1_pct >= 15 or top3_pct >= 40:
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
        hhi=hhi,
        effective_positions=effective_positions,
    )
