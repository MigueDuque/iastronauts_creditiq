"""
kpi_engine.py — Executive KPI Consolidation

Converts pre-computed deterministic outputs (ratios_dict, earnings_quality,
concentration, fund_analysis) into a structured executive KPI dict for
UI dashboard display and LLM context enrichment.

All arithmetic is deterministic — never calls the LLM.
"""

from __future__ import annotations


def calculate_executive_kpis(
    ratios_dict: dict,
    earnings_quality: dict,
    concentration: dict,
    fund_analysis: dict | None = None,
) -> dict:
    """
    Build executive_kpis from pre-computed deterministic outputs.
    Returns a dict with 'profitability', 'liquidity', 'leverage',
    'earnings_quality', 'concentration', optionally 'fund', and
    'dashboard_metrics' (top 6 UI cards).
    """
    totals  = ratios_dict.get("totals", {})
    ratios  = ratios_dict.get("ratios", {})
    niif18  = ratios_dict.get("niif18", {})
    subtotals = niif18.get("subtotals", {}) if niif18 else {}

    net_income    = float(totals.get("net_income",    0) or 0)
    total_equity  = float(totals.get("total_equity",  0) or 0)
    total_assets  = float(totals.get("total_assets",  0) or 0)
    total_revenue = float(totals.get("total_revenue", 0) or 0)

    roe_pct = round((net_income / total_equity) * 100, 2) if total_equity > 0 else 0.0
    roa_pct = round((net_income / total_assets) * 100, 2) if total_assets > 0 else 0.0

    resultado_operativo = subtotals.get("resultado_operativo")
    operating_margin_pct = 0.0
    if resultado_operativo is not None and total_revenue > 0:
        operating_margin_pct = round((float(resultado_operativo) / total_revenue) * 100, 2)

    eq_score    = float(earnings_quality.get("quality_score",          0) or 0)
    eq_label    = str(earnings_quality.get("quality_label",           ""))
    fair_val_rt = float(earnings_quality.get("fair_value_income_ratio", 0) or 0)
    # Stored as 0–1 ratio; convert to percentage for display
    unrealized_dep_pct = round(fair_val_rt * 100, 1) if fair_val_rt <= 1.0 else round(fair_val_rt, 1)

    top1_pct    = float(concentration.get("top_account_pct",         0) or 0)
    top3_pct    = float(concentration.get("top3_concentration_pct",  0) or 0)
    conc_label  = str(concentration.get("concentration_label", ""))

    kpis: dict = {
        "profitability": {
            "roe_pct":              roe_pct,
            "roa_pct":              roa_pct,
            "net_margin_pct":       float(ratios.get("margen_neto_pct",   0) or 0),
            "ebitda_margin_pct":    float(ratios.get("margen_ebitda_pct", 0) or 0),
            "operating_margin_pct": operating_margin_pct,
            "gross_margin_pct":     float(ratios.get("margen_bruto_pct",  0) or 0),
        },
        "liquidity": {
            "razon_corriente":  float(ratios.get("razon_corriente",  0) or 0),
            "prueba_acida":     float(ratios.get("prueba_acida",     0) or 0),
            "capital_trabajo":  float(ratios.get("capital_trabajo",  0) or 0),
        },
        "leverage": {
            "deuda_patrimonio":     float(ratios.get("deuda_patrimonio",     0) or 0),
            "endeudamiento_global": float(ratios.get("endeudamiento_global", 0) or 0),
        },
        "earnings_quality": {
            "quality_score":               eq_score,
            "quality_label":               eq_label,
            "unrealized_gain_dependency_pct": unrealized_dep_pct,
        },
        "concentration": {
            "top1_concentration_pct":  top1_pct,
            "top3_concentration_pct":  top3_pct,
            "concentration_label":     conc_label,
        },
    }

    if fund_analysis and fund_analysis.get("is_investment_fund"):
        kpis["fund"] = _build_fund_kpis(fund_analysis)

    kpis["dashboard_metrics"] = _build_dashboard_metrics(kpis, fund_analysis, total_assets)
    return kpis


def _build_fund_kpis(fund: dict) -> dict:
    aum_growth   = fund.get("aum_change_pct")
    net_flow     = fund.get("net_investor_flow_cop_mm")
    cash_ratio   = fund.get("cash_ratio")
    top1_pos_pct = fund.get("top1_position_pct")
    top3_pos_pct = fund.get("top3_concentration_pct")

    # Redemption ratio: |outflows| / previous AUM
    redemption_ratio: float | None = None
    nav = fund.get("nav_reconciliation") or {}
    prev_aum = nav.get("previous_aum")
    if prev_aum and prev_aum > 0 and net_flow is not None and net_flow < 0:
        redemption_ratio = round(abs(float(net_flow)) / float(prev_aum) * 100, 2)

    return {
        "aum_growth_pct":             aum_growth,
        "net_investor_flow_cop_mm":   net_flow,
        "redemption_ratio_pct":       redemption_ratio,
        "cash_ratio":                 cash_ratio,
        "top1_position_pct":          top1_pos_pct,
        "top3_position_pct":          top3_pos_pct,
    }


def _signal(value: float, high_good: float, low_bad: float) -> str:
    """Return 'positive' / 'neutral' / 'negative' for a metric where higher is better."""
    if value >= high_good:
        return "positive"
    if value <= low_bad:
        return "negative"
    return "neutral"


def _signal_inv(value: float, low_good: float, high_bad: float) -> str:
    """Return signal for a metric where lower is better (e.g. leverage, concentration)."""
    if value <= low_good:
        return "positive"
    if value >= high_bad:
        return "negative"
    return "neutral"


def _build_dashboard_metrics(kpis: dict, fund_analysis: dict | None, total_assets: float = 0.0) -> list[dict]:
    """
    Build ordered list of ≤6 dashboard KPI cards for UI display.
    Each card has: key, label, value (formatted string), signal ('positive'|'neutral'|'negative').
    """
    cards: list[dict] = []
    is_fund = bool(fund_analysis and fund_analysis.get("is_investment_fund"))

    # 1. AUM Growth (funds) or Net Margin (non-funds) — first position
    if is_fund:
        fund_kpis = kpis.get("fund", {})
        aum_growth = fund_kpis.get("aum_growth_pct")
        if aum_growth is not None:
            cards.append({
                "key":    "aum_growth",
                "label":  "AUM Growth",
                "value":  f"{float(aum_growth):+.1f}%",
                "signal": _signal(float(aum_growth), 5.0, -5.0),
            })
        net_flow = fund_kpis.get("net_investor_flow_cop_mm")
        if net_flow is not None:
            cards.append({
                "key":    "net_flow",
                "label":  "Net Investor Flow",
                "value":  f"COP {float(net_flow):+,.0f} MM",
                "signal": "positive" if float(net_flow) > 0 else "negative",
            })
    else:
        profit = kpis.get("profitability", {})
        net_margin = float(profit.get("net_margin_pct", 0) or 0)
        cards.append({
            "key":    "net_margin",
            "label":  "Net Margin",
            "value":  f"{net_margin:.1f}%",
            "signal": _signal(net_margin, 10.0, 0.0),
        })
        ebitda_margin = float(profit.get("ebitda_margin_pct", 0) or 0)
        cards.append({
            "key":    "ebitda_margin",
            "label":  "EBITDA Margin",
            "value":  f"{ebitda_margin:.1f}%",
            "signal": _signal(ebitda_margin, 15.0, 0.0),
        })

    # 2. ROE — always shown
    roe = float(kpis.get("profitability", {}).get("roe_pct", 0) or 0)
    cards.append({
        "key":    "roe",
        "label":  "ROE",
        "value":  f"{roe:.1f}%",
        "signal": _signal(roe, 12.0, 0.0),
    })

    # 3. Earnings Quality score
    eq_score = float(kpis.get("earnings_quality", {}).get("quality_score", 0) or 0)
    cards.append({
        "key":    "earnings_quality",
        "label":  "Earnings Quality",
        "value":  f"{eq_score:.0f}/100",
        "signal": _signal(eq_score, 70.0, 40.0),
    })

    # 4. Top position concentration
    top1 = float(kpis.get("concentration", {}).get("top1_concentration_pct", 0) or 0)
    if is_fund:
        fund_top1 = float((kpis.get("fund") or {}).get("top1_position_pct") or top1 or 0)
        top1 = fund_top1 if fund_top1 > 0 else top1
    cards.append({
        "key":    "concentration",
        "label":  "Top Concentration",
        "value":  f"{top1:.1f}%",
        "signal": _signal_inv(top1, 20.0, 40.0),
    })

    # 5. AUM — for funds use closing NAV; for others use total assets
    nav = (fund_analysis or {}).get("nav_reconciliation") or {}
    closing_nav = nav.get("closing_nav")
    if is_fund and closing_nav is not None:
        aum_val = float(closing_nav)
    else:
        aum_val = total_assets
    cards.append({
        "key":    "aum",
        "label":  "AUM",
        "value":  f"{aum_val:,.0f} MM",
        "signal": "positive" if aum_val > 0 else "neutral",
    })

    return cards[:6]
