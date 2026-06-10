"""
synthesis_engine.py — Executive Synthesis Engine (Phase 1 of Roadmap)

Aggregates outputs from all deterministic engines into a portfolio-level
executive story BEFORE the LLM call.

This removes the burden of pattern discovery from the LLM.  Instead the LLM
receives pre-formed, labeled conclusions and only needs to narrativize them —
which it does far better than discovering them from 60+ raw account rows.

All logic is deterministic.  No LLM calls.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger("financial_analyzer.synthesis_engine")


# ─────────────────────────────────────────────────────────────────────────────
# Sector classification helpers
# ─────────────────────────────────────────────────────────────────────────────

_SECTOR_MAP: dict[str, str] = {
    # Banking / Financial
    "bancolombia": "banking",
    "bac holding": "banking",
    "grupo sura": "financial_holding",
    "davivienda": "banking",
    "banco": "banking",
    # Energy / Infrastructure
    "isa": "infrastructure",
    "grupo energía bogotá": "energy",
    "ecopetrol": "energy",
    "celsia": "energy",
    "emgesa": "energy",
    "isagen": "energy",
    # Holdings / Conglomerates
    "grupo argos": "construction_holding",
    "cementos argos": "cement_construction",
    "grupo cibest": "holding_conglomerate",
    "grupo nutresa": "consumer_food",
    "grupo empresarial": "holding_conglomerate",
    # Retail / Consumer
    "almacenes éxito": "retail",
    "almacenes exito": "retail",
    "éxito": "retail",
    # Funds / Liquidity
    "btg fondo": "money_market_fund",
    "fondo liquidez": "money_market_fund",
    "fondo de inversión": "investment_fund",
}

_SECTOR_LABELS: dict[str, str] = {
    "banking": "Sector Bancario",
    "financial_holding": "Holding Financiero",
    "infrastructure": "Infraestructura",
    "energy": "Energía / Oil&Gas",
    "construction_holding": "Holding Constructor",
    "cement_construction": "Cemento / Construcción",
    "holding_conglomerate": "Holding Conglomerado",
    "consumer_food": "Consumo / Alimentos",
    "retail": "Comercio Minorista",
    "money_market_fund": "Fondo Monetario",
    "investment_fund": "Fondo de Inversión",
    "other": "Otras Posiciones",
}


def _classify_sector(account_name: str) -> str:
    name_lower = account_name.lower()
    for keyword, sector in _SECTOR_MAP.items():
        if keyword in name_lower:
            return sector
    return "other"


# ─────────────────────────────────────────────────────────────────────────────
# Result dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ExecutiveSynthesis:
    # Single-line characterization
    main_portfolio_theme: str = ""

    # Deterministic narrative segments (Spanish, board-ready)
    portfolio_story: str = ""
    strategic_rotation: str = ""
    investor_flow_story: str = ""
    earnings_quality_story: str = ""
    concentration_story: str = ""

    # Board-level structured outputs
    executive_conclusions: list[str] = field(default_factory=list)
    board_alerts: list[str] = field(default_factory=list)
    top_risks: list[str] = field(default_factory=list)
    top_strengths: list[str] = field(default_factory=list)

    # Machine-readable signals for LLM context and dashboard
    signals: list[str] = field(default_factory=list)

    # AUM / NAV metrics
    aum_opening_cop_mm: float | None = None
    aum_closing_cop_mm: float | None = None
    aum_change_cop_mm: float | None = None
    aum_change_pct: float | None = None
    net_investor_flow_cop_mm: float | None = None
    contributions_cop_mm: float | None = None
    redemptions_cop_mm: float | None = None

    # Portfolio rotation data
    new_positions: list[dict] = field(default_factory=list)
    closed_positions: list[dict] = field(default_factory=list)
    sector_rotation: dict = field(default_factory=dict)

    # Concentration
    top_issuer: str = ""
    top_issuer_pct: float = 0.0
    concentration_increased: bool = False

    # Earnings quality
    valuation_dependency_pct: float = 0.0
    recurring_income_pct: float = 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Signal generation
# ─────────────────────────────────────────────────────────────────────────────

def _generate_signals(
    *,
    net_flow: float | None,
    aum_change_pct: float | None,
    top_issuer_pct: float,
    top3_pct: float,
    valuation_dep_pct: float,
    eq_score: float,
    new_positions: list[dict],
    closed_positions: list[dict],
    is_fund: bool,
) -> list[str]:
    signals: list[str] = []

    # Investor flow signals
    if is_fund and net_flow is not None:
        if net_flow < -1_000:
            signals.append("investor_net_outflows_material")
        elif net_flow < -200:
            signals.append("investor_net_outflows_moderate")
        elif net_flow > 1_000:
            signals.append("investor_net_inflows_material")

    # AUM direction
    if aum_change_pct is not None:
        if aum_change_pct < -20:
            signals.append("aum_decline_severe")
        elif aum_change_pct < -10:
            signals.append("aum_decline_moderate")
        elif aum_change_pct > 20:
            signals.append("aum_growth_strong")
        elif aum_change_pct > 5:
            signals.append("aum_growth_moderate")

    # Concentration signals
    if top_issuer_pct > 35:
        signals.append("single_issuer_concentration_critical")
    elif top_issuer_pct > 25:
        signals.append("single_issuer_concentration_high")

    if top3_pct > 70:
        signals.append("top3_concentration_critical")
    elif top3_pct > 55:
        signals.append("top3_concentration_high")

    # Earnings quality signals
    if valuation_dep_pct > 70:
        signals.append("valuation_dependency_critical")
    elif valuation_dep_pct > 50:
        signals.append("valuation_dependency_high")
    elif valuation_dep_pct < 20:
        signals.append("earnings_quality_strong")

    if eq_score < 40:
        signals.append("earnings_quality_low")
    elif eq_score > 70:
        signals.append("earnings_quality_high")

    # Portfolio rotation signals
    if new_positions:
        total_new = sum(p.get("current_value", 0) for p in new_positions)
        if total_new > 5_000:
            signals.append("major_new_positions_entered")
        elif new_positions:
            signals.append("new_positions_entered")

    if closed_positions:
        total_closed = sum(abs(p.get("previous_value") or 0) for p in closed_positions)
        if total_closed > 5_000:
            signals.append("major_positions_liquidated")
        elif closed_positions:
            signals.append("positions_liquidated")

    if new_positions and closed_positions:
        signals.append("portfolio_strategic_rebalancing")

    # Sector rotation signals — computed from position name keywords
    all_new_names = [p.get("account_name", "") for p in new_positions]
    all_closed_names = [p.get("account_name", "") for p in closed_positions]

    new_sectors = {_classify_sector(n) for n in all_new_names}
    closed_sectors = {_classify_sector(n) for n in all_closed_names}

    for sector in new_sectors - closed_sectors:
        if sector != "other":
            signals.append(f"sector_exposure_increased_{sector}")

    for sector in closed_sectors - new_sectors:
        if sector != "other":
            signals.append(f"sector_exposure_reduced_{sector}")

    return signals


# ─────────────────────────────────────────────────────────────────────────────
# Sector rotation analysis
# ─────────────────────────────────────────────────────────────────────────────

def _compute_sector_rotation(
    top_positions: list[dict],
    new_positions: list[dict],
    closed_positions: list[dict],
) -> dict:
    """
    Classify all top positions by sector and compute what changed.
    Returns {increased: [...], decreased: [...], entered: [...], exited: [...]}.
    """
    entered = [
        {
            "name": p.get("account_name", ""),
            "sector": _classify_sector(p.get("account_name", "")),
            "sector_label": _SECTOR_LABELS.get(_classify_sector(p.get("account_name", "")), ""),
            "value_cop_mm": p.get("current_value", 0),
            "pct_portfolio": p.get("pct_of_portfolio", 0),
        }
        for p in new_positions
    ]

    exited = [
        {
            "name": p.get("account_name", ""),
            "sector": _classify_sector(p.get("account_name", "")),
            "sector_label": _SECTOR_LABELS.get(_classify_sector(p.get("account_name", "")), ""),
            "prev_value_cop_mm": abs(p.get("previous_value") or 0),
        }
        for p in closed_positions
    ]

    # Positions that moved significantly
    increased = []
    decreased = []
    for p in top_positions:
        if p.get("status") == "increased" and abs(p.get("absolute_change", 0)) > 200:
            increased.append({
                "name": p.get("account_name", ""),
                "sector": _classify_sector(p.get("account_name", "")),
                "change_cop_mm": p.get("absolute_change", 0),
                "pct_portfolio": p.get("pct_of_portfolio", 0),
            })
        elif p.get("status") == "decreased" and abs(p.get("absolute_change", 0)) > 200:
            decreased.append({
                "name": p.get("account_name", ""),
                "sector": _classify_sector(p.get("account_name", "")),
                "change_cop_mm": p.get("absolute_change", 0),
                "pct_portfolio": p.get("pct_of_portfolio", 0),
            })

    return {
        "entered": entered,
        "exited": exited,
        "increased": increased,
        "decreased": decreased,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Narrative builders
# ─────────────────────────────────────────────────────────────────────────────

def _build_investor_flow_story(
    aum_opening: float | None,
    aum_closing: float | None,
    aum_change: float | None,
    aum_change_pct: float | None,
    net_flow: float | None,
    contributions: float | None,
    redemptions: float | None,
    is_fund: bool,
) -> str:
    if not is_fund or aum_closing is None:
        return ""

    parts: list[str] = []

    if aum_opening and aum_change is not None and aum_change_pct is not None:
        direction = "disminuyó" if aum_change < 0 else "aumentó"
        parts.append(
            f"El AUM {direction} de {aum_opening:,.1f} a {aum_closing:,.1f} COP MM "
            f"({aum_change_pct:+.1f}%)"
        )

    if net_flow is not None:
        flow_dir = "salidas netas" if net_flow < 0 else "entradas netas"
        flow_parts: list[str] = [f"{flow_dir} de {abs(net_flow):,.1f} COP MM de inversionistas"]
        if contributions:
            flow_parts.append(f"aportes {contributions:+,.1f}")
        if redemptions:
            flow_parts.append(f"retiros {redemptions:+,.1f}")
        parts.append("impulsado por " + " y ".join(flow_parts[:2]) + " COP MM")

    if not parts:
        return f"AUM al cierre: {aum_closing:,.1f} COP MM."

    return ". ".join(parts) + "."


def _build_rotation_narrative(
    new_positions: list[dict],
    closed_positions: list[dict],
    sector_rotation: dict,
) -> str:
    if not new_positions and not closed_positions:
        return ""

    parts: list[str] = []

    entered = sector_rotation.get("entered", [])
    exited = sector_rotation.get("exited", [])

    if exited:
        exited_desc = ", ".join(
            f"{p['name']} ({p['prev_value_cop_mm']:,.1f} COP MM)" for p in exited[:3]
        )
        parts.append(f"Se liquidaron posiciones en {exited_desc}")

    if entered:
        entered_desc = ", ".join(
            f"{p['name']} ({p['value_cop_mm']:,.1f} COP MM, {p['pct_portfolio']:.1f}% del portafolio)"
            for p in entered[:3]
        )
        parts.append(f"se ingresaron nuevas posiciones en {entered_desc}")

    # Sector shift summary
    exited_sectors = list({p["sector_label"] for p in exited if p.get("sector_label") and p["sector_label"] != "Otras Posiciones"})
    entered_sectors = list({p["sector_label"] for p in entered if p.get("sector_label") and p["sector_label"] != "Otras Posiciones"})

    if exited_sectors and entered_sectors:
        parts.append(
            f"reflejando una rotación sectorial desde {', '.join(exited_sectors)} "
            f"hacia {', '.join(entered_sectors)}"
        )
    elif exited_sectors:
        parts.append(f"reduciendo exposición a {', '.join(exited_sectors)}")
    elif entered_sectors:
        parts.append(f"incrementando exposición a {', '.join(entered_sectors)}")

    return ". ".join(p.capitalize() if i == 0 else p for i, p in enumerate(parts)) + "." if parts else ""


def _build_earnings_story(
    valuation_dep_pct: float,
    recurring_pct: float,
    eq_score: float,
    eq_label: str,
    earnings_quality: dict,
) -> str:
    insight = earnings_quality.get("key_insight", "")
    if insight:
        return insight

    if valuation_dep_pct > 60:
        sustainability = "alta dependencia de ganancias no realizadas"
        risk_note = "La sostenibilidad de estos resultados está condicionada a la continuidad del ciclo de valorización."
    elif valuation_dep_pct > 30:
        sustainability = "dependencia moderada de valorización a valor razonable"
        risk_note = "Los resultados son parcialmente sensibles a reversiones de mercado."
    else:
        sustainability = "base de ingresos recurrente sólida"
        risk_note = ""

    parts = [
        f"La rentabilidad del período muestra {sustainability} "
        f"({valuation_dep_pct:.0f}% de utilidades provienen de valorización de inversiones, "
        f"{recurring_pct:.0f}% de ingresos recurrentes)."
    ]
    if risk_note:
        parts.append(risk_note)
    if eq_label:
        parts.append(f"Calidad de utilidades: {eq_label} (score {eq_score:.0f}/100).")

    return " ".join(parts)


def _build_concentration_story(
    top_issuer: str,
    top_issuer_pct: float,
    top3_pct: float,
    conc_label: str,
    new_positions: list[dict],
    closed_positions: list[dict],
) -> str:
    if not top_issuer:
        return ""

    parts = [
        f"La mayor concentración corresponde a '{top_issuer}' con {top_issuer_pct:.1f}% del portafolio "
        f"(top 3 posiciones: {top3_pct:.1f}%)."
    ]

    if new_positions:
        largest_new = max(new_positions, key=lambda p: p.get("current_value", 0), default=None)
        if largest_new and largest_new.get("pct_of_portfolio", 0) > 20:
            parts.append(
                f"La nueva posición en '{largest_new['account_name']}' "
                f"({largest_new['pct_of_portfolio']:.1f}% del portafolio) "
                f"incrementó significativamente el riesgo idiosincrático."
            )

    if conc_label in ("HIGH", "CRITICAL"):
        parts.append(
            "El perfil de concentración actual amerita seguimiento activo por parte de la junta directiva."
        )

    return " ".join(parts)


def _build_portfolio_story(
    investor_flow_story: str,
    rotation_story: str,
    earnings_story: str,
) -> str:
    segments = [s for s in [investor_flow_story, rotation_story, earnings_story] if s]
    return " ".join(segments)


def _detect_main_theme(
    signals: list[str],
    valuation_dep_pct: float,
    top_issuer_pct: float,
    net_flow: float | None,
) -> str:
    if "major_positions_liquidated" in signals and "major_new_positions_entered" in signals:
        return "Rotación estratégica de portafolio con recomposición de posiciones"
    if "investor_net_outflows_material" in signals and "aum_decline_severe" in signals:
        return "Reducción de AUM por salida neta de inversionistas"
    if "single_issuer_concentration_critical" in signals or "single_issuer_concentration_high" in signals:
        return f"Portafolio altamente concentrado — riesgo idiosincrático elevado"
    if "valuation_dependency_critical" in signals:
        return "Rentabilidad predominantemente dependiente de valorización no realizada"
    if "investor_net_inflows_material" in signals:
        return "Crecimiento de AUM impulsado por nuevas suscripciones de inversionistas"
    return "Período de transición con cambios estructurales en composición del portafolio"


# ─────────────────────────────────────────────────────────────────────────────
# Board-level outputs
# ─────────────────────────────────────────────────────────────────────────────

def _build_executive_conclusions(
    aum_opening: float | None,
    aum_closing: float | None,
    aum_change_pct: float | None,
    net_flow: float | None,
    new_positions: list[dict],
    closed_positions: list[dict],
    valuation_dep_pct: float,
    eq_score: float,
    top_issuer: str,
    top_issuer_pct: float,
    signals: list[str],
) -> list[str]:
    conclusions: list[str] = []

    # AUM / Flow conclusion
    if aum_opening and aum_closing and aum_change_pct is not None:
        if net_flow is not None and abs(net_flow) > 1_000:
            driver = "salidas netas de inversionistas" if net_flow < 0 else "entradas netas de inversionistas"
            conclusions.append(
                f"El AUM se redujo de {aum_opening:,.0f} a {aum_closing:,.0f} COP MM ({aum_change_pct:+.1f}%), "
                f"impulsado principalmente por {driver} de {abs(net_flow):,.0f} COP MM."
            )
        else:
            conclusions.append(
                f"El AUM varió de {aum_opening:,.0f} a {aum_closing:,.0f} COP MM ({aum_change_pct:+.1f}%)."
            )

    # Portfolio rotation conclusion
    if new_positions and closed_positions:
        entered_names = [p.get("account_name", "") for p in new_positions[:2]]
        exited_names = [p.get("account_name", "") for p in closed_positions[:2]]
        conclusions.append(
            f"El portafolio ejecutó una rotación estratégica: se liquidaron posiciones en "
            f"{', '.join(exited_names)} y se ingresaron {', '.join(entered_names)}."
        )
    elif new_positions:
        entered_names = [p.get("account_name", "") for p in new_positions[:2]]
        conclusions.append(f"Se ingresaron nuevas posiciones: {', '.join(entered_names)}.")
    elif closed_positions:
        exited_names = [p.get("account_name", "") for p in closed_positions[:2]]
        conclusions.append(f"Se liquidaron posiciones en: {', '.join(exited_names)}.")

    # Earnings quality conclusion
    if valuation_dep_pct > 50:
        conclusions.append(
            f"El {valuation_dep_pct:.0f}% de la rentabilidad proviene de valorización no realizada "
            f"(calidad de utilidades: {eq_score:.0f}/100) — los resultados dependen de la continuidad "
            f"del ciclo de apreciación de mercado."
        )

    # Concentration conclusion
    if top_issuer and top_issuer_pct > 25:
        conclusions.append(
            f"La concentración en '{top_issuer}' ({top_issuer_pct:.1f}% del portafolio) "
            f"representa el principal riesgo idiosincrático del fondo."
        )

    return conclusions[:5]


def _build_board_alerts(
    signals: list[str],
    top_issuer_pct: float,
    valuation_dep_pct: float,
    net_flow: float | None,
    aum_change_pct: float | None,
    eq_score: float,
    new_positions: list[dict],
) -> list[str]:
    alerts: list[str] = []

    if "single_issuer_concentration_critical" in signals:
        alerts.append(
            f"CONCENTRACIÓN CRÍTICA: La posición individual supera el 35% del portafolio. "
            f"Revisar política de límites de concentración."
        )

    if "valuation_dependency_critical" in signals:
        alerts.append(
            f"DEPENDENCIA DE VALORIZACIÓN: Más del 70% de la rentabilidad proviene de "
            f"ganancias no realizadas. Alta sensibilidad ante correcciones de mercado."
        )

    if "investor_net_outflows_material" in signals and net_flow is not None:
        alerts.append(
            f"SALIDAS DE CAPITAL: Flujo neto de inversionistas negativo ({net_flow:,.0f} COP MM). "
            f"Monitorear impacto sobre capacidad de despliegue y necesidad de liquidez."
        )

    if "aum_decline_severe" in signals and aum_change_pct is not None:
        alerts.append(
            f"REDUCCIÓN SIGNIFICATIVA DE AUM: {aum_change_pct:.1f}% en el período. "
            f"Evaluar sustentabilidad de la estructura de costos."
        )

    if new_positions:
        largest_new = max(new_positions, key=lambda p: p.get("pct_of_portfolio", 0), default=None)
        if largest_new and largest_new.get("pct_of_portfolio", 0) > 30:
            alerts.append(
                f"NUEVA POSICIÓN DOMINANTE: '{largest_new['account_name']}' representa "
                f"{largest_new['pct_of_portfolio']:.1f}% del portafolio. "
                f"Validar tesis de inversión y límites de concentración."
            )

    return alerts


def _build_risk_signals(
    signals: list[str],
    top_issuer_pct: float,
    valuation_dep_pct: float,
    net_flow: float | None,
) -> list[str]:
    risks: list[str] = []

    if "single_issuer_concentration_critical" in signals or "single_issuer_concentration_high" in signals:
        risks.append(f"Riesgo de concentración: posición individual {top_issuer_pct:.0f}% del portafolio")

    if "valuation_dependency_critical" in signals or "valuation_dependency_high" in signals:
        risks.append(f"Dependencia de valorización: {valuation_dep_pct:.0f}% de utilidades son no realizadas")

    if "investor_net_outflows_material" in signals and net_flow is not None:
        risks.append(f"Presión de redenciones: salida neta de {abs(net_flow):,.0f} COP MM de inversionistas")

    if "aum_decline_severe" in signals:
        risks.append("Reducción severa de AUM con impacto potencial en estructura de costos")

    if "top3_concentration_critical" in signals:
        risks.append("Top 3 posiciones superan el 70% del portafolio — diversificación insuficiente")

    return risks


def _build_strength_signals(
    signals: list[str],
    eq_score: float,
    valuation_dep_pct: float,
    net_flow: float | None,
) -> list[str]:
    strengths: list[str] = []

    if "earnings_quality_high" in signals or eq_score > 70:
        strengths.append(f"Alta calidad de utilidades (score {eq_score:.0f}/100) con base recurrente sólida")

    if "investor_net_inflows_material" in signals and net_flow is not None:
        strengths.append(f"Entradas netas de inversionistas ({net_flow:+,.0f} COP MM) reflejan confianza en el fondo")

    if "portfolio_strategic_rebalancing" in signals:
        strengths.append("Recomposición estratégica activa del portafolio con gestión de posiciones")

    if valuation_dep_pct < 30:
        strengths.append("Rentabilidad predominantemente operacional — menor exposición a riesgo de mercado")

    if "aum_growth_strong" in signals or "aum_growth_moderate" in signals:
        strengths.append("Crecimiento positivo de AUM en el período")

    return strengths


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def synthesize(
    fund_analysis: dict,
    concentration: dict,
    earnings_quality: dict,
) -> ExecutiveSynthesis:
    """
    Main entry point.  Takes serialized engine outputs and produces a structured
    ExecutiveSynthesis ready for the AnalyzerOutput and as pre-computed context
    for the LLM narrative step.
    """
    is_fund = fund_analysis.get("is_investment_fund", False)

    # ── NAV / AUM ─────────────────────────────────────────────────────────
    nav = fund_analysis.get("nav_reconciliation") or {}
    aum_opening = nav.get("opening_nav")
    aum_closing = nav.get("closing_nav")
    net_flow = fund_analysis.get("net_investor_flow_cop_mm")
    contributions = nav.get("contributions")
    redemptions = nav.get("redemptions")

    aum_change = (
        (aum_closing - aum_opening) if (aum_closing is not None and aum_opening is not None) else None
    )
    aum_change_pct = (
        round((aum_change / aum_opening) * 100, 2)
        if (aum_change is not None and aum_opening and aum_opening != 0)
        else None
    )

    # ── Portfolio positions ───────────────────────────────────────────────
    new_positions = fund_analysis.get("new_positions", [])
    closed_positions = fund_analysis.get("closed_positions", [])
    top_positions = fund_analysis.get("top_positions", [])

    # ── Concentration ─────────────────────────────────────────────────────
    top_issuer = concentration.get("top_account_name", "")
    top_issuer_pct = float(concentration.get("top_account_pct", 0) or 0)
    top3_pct = float(concentration.get("top3_concentration_pct", 0) or 0)
    conc_label = concentration.get("concentration_label", "")

    # ── Earnings quality ──────────────────────────────────────────────────
    fair_value_ratio = float(earnings_quality.get("fair_value_income_ratio", 0) or 0)
    eq_score = float(earnings_quality.get("quality_score", 100) or 100)
    eq_label = earnings_quality.get("quality_label", "")

    # Stored as 0–1 or 0–100 depending on how the engine outputs it
    valuation_dep_pct = (fair_value_ratio * 100) if fair_value_ratio <= 1.0 else fair_value_ratio
    recurring_pct = max(0.0, 100.0 - valuation_dep_pct)

    # ── Sector rotation ───────────────────────────────────────────────────
    sector_rotation = _compute_sector_rotation(top_positions, new_positions, closed_positions)

    # ── Signals ───────────────────────────────────────────────────────────
    signals = _generate_signals(
        net_flow=net_flow,
        aum_change_pct=aum_change_pct,
        top_issuer_pct=top_issuer_pct,
        top3_pct=top3_pct,
        valuation_dep_pct=valuation_dep_pct,
        eq_score=eq_score,
        new_positions=new_positions,
        closed_positions=closed_positions,
        is_fund=is_fund,
    )

    # ── Narrative segments ────────────────────────────────────────────────
    investor_flow_story = _build_investor_flow_story(
        aum_opening, aum_closing, aum_change, aum_change_pct,
        net_flow, contributions, redemptions, is_fund,
    )
    rotation_narrative = _build_rotation_narrative(new_positions, closed_positions, sector_rotation)
    earnings_story = _build_earnings_story(valuation_dep_pct, recurring_pct, eq_score, eq_label, earnings_quality)
    concentration_story = _build_concentration_story(
        top_issuer, top_issuer_pct, top3_pct, conc_label, new_positions, closed_positions,
    )
    portfolio_story = _build_portfolio_story(investor_flow_story, rotation_narrative, earnings_story)
    main_theme = _detect_main_theme(signals, valuation_dep_pct, top_issuer_pct, net_flow)

    # ── Board-level outputs ───────────────────────────────────────────────
    executive_conclusions = _build_executive_conclusions(
        aum_opening, aum_closing, aum_change_pct, net_flow,
        new_positions, closed_positions,
        valuation_dep_pct, eq_score, top_issuer, top_issuer_pct, signals,
    )
    board_alerts = _build_board_alerts(
        signals, top_issuer_pct, valuation_dep_pct, net_flow,
        aum_change_pct, eq_score, new_positions,
    )
    top_risks = _build_risk_signals(signals, top_issuer_pct, valuation_dep_pct, net_flow)
    top_strengths = _build_strength_signals(signals, eq_score, valuation_dep_pct, net_flow)

    # ── Concentration change flag ─────────────────────────────────────────
    # Concentration increased if a large new position was added and/or major one closed
    conc_increased = bool(
        new_positions and top_issuer_pct > 25
        and any(p.get("pct_of_portfolio", 0) > 20 for p in new_positions)
    )

    logger.info(
        "synthesis | theme=%r signals=%d conclusions=%d alerts=%d",
        main_theme[:60], len(signals), len(executive_conclusions), len(board_alerts),
    )

    return ExecutiveSynthesis(
        main_portfolio_theme=main_theme,
        portfolio_story=portfolio_story,
        strategic_rotation=rotation_narrative,
        investor_flow_story=investor_flow_story,
        earnings_quality_story=earnings_story,
        concentration_story=concentration_story,
        executive_conclusions=executive_conclusions,
        board_alerts=board_alerts,
        top_risks=top_risks,
        top_strengths=top_strengths,
        signals=signals,
        aum_opening_cop_mm=aum_opening,
        aum_closing_cop_mm=aum_closing,
        aum_change_cop_mm=round(aum_change, 2) if aum_change is not None else None,
        aum_change_pct=aum_change_pct,
        net_investor_flow_cop_mm=net_flow,
        contributions_cop_mm=contributions,
        redemptions_cop_mm=redemptions,
        new_positions=[
            {
                "name": p.get("account_name", ""),
                "value_cop_mm": p.get("current_value", 0),
                "pct_portfolio": p.get("pct_of_portfolio", 0),
            }
            for p in new_positions
        ],
        closed_positions=[
            {
                "name": p.get("account_name", ""),
                "prev_value_cop_mm": abs(p.get("previous_value") or 0),
            }
            for p in closed_positions
        ],
        sector_rotation=sector_rotation,
        top_issuer=top_issuer,
        top_issuer_pct=round(top_issuer_pct, 1),
        concentration_increased=conc_increased,
        valuation_dependency_pct=round(valuation_dep_pct, 1),
        recurring_income_pct=round(recurring_pct, 1),
    )


def synthesis_to_dict(s: ExecutiveSynthesis) -> dict:
    """Serialize ExecutiveSynthesis to a JSON-safe dict."""
    return {
        "main_portfolio_theme": s.main_portfolio_theme,
        "portfolio_story": s.portfolio_story,
        "strategic_rotation": s.strategic_rotation,
        "investor_flow_story": s.investor_flow_story,
        "earnings_quality_story": s.earnings_quality_story,
        "concentration_story": s.concentration_story,
        "executive_conclusions": s.executive_conclusions,
        "board_alerts": s.board_alerts,
        "top_risks": s.top_risks,
        "top_strengths": s.top_strengths,
        "signals": s.signals,
        "aum_opening_cop_mm": s.aum_opening_cop_mm,
        "aum_closing_cop_mm": s.aum_closing_cop_mm,
        "aum_change_cop_mm": s.aum_change_cop_mm,
        "aum_change_pct": s.aum_change_pct,
        "net_investor_flow_cop_mm": s.net_investor_flow_cop_mm,
        "contributions_cop_mm": s.contributions_cop_mm,
        "redemptions_cop_mm": s.redemptions_cop_mm,
        "new_positions": s.new_positions,
        "closed_positions": s.closed_positions,
        "sector_rotation": s.sector_rotation,
        "top_issuer": s.top_issuer,
        "top_issuer_pct": s.top_issuer_pct,
        "concentration_increased": s.concentration_increased,
        "valuation_dependency_pct": s.valuation_dependency_pct,
        "recurring_income_pct": s.recurring_income_pct,
    }
