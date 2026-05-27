"""
fund_engine.py  —  Investment Fund Analysis Engine

Detects whether the EEFF belongs to an investment fund (CIV / FIC) and, if so,
performs fund-specific analysis that generic financial engines cannot handle:

  1. Fund type detection   — equity / fixed-income / balanced / money-market
  2. NAV reconciliation    — opening NAV + contributions − redemptions + return = closing NAV
  3. Asset class breakdown — equities, fixed-income, cash, investment-funds, other
  4. Portfolio positions   — individual holdings with status (new / closed / increased /
                             decreased / stable) and weight in portfolio
  5. Fund-specific causal  — redemption pressure, cash deployment, rebalancing chains
     chains

All logic is purely deterministic. No LLM calls.
"""

import logging
from dataclasses import dataclass, field
from enum import Enum

from shared.models import ExtractedAccount
from .ratio_engine import AccountVariation, FinancialTotals
from .causality_engine import CausalChain

logger = logging.getLogger("financial_analyzer.fund_engine")


# ── Fund type taxonomy ────────────────────────────────────────────────────────

class FundType(str, Enum):
    EQUITY          = "equity_fund"          # predominantly listed stocks
    FIXED_INCOME    = "fixed_income_fund"    # predominantly bonds / debt securities
    BALANCED        = "balanced_fund"        # mixed equities + fixed-income
    MONEY_MARKET    = "money_market_fund"    # short-term / liquidity instruments
    ALTERNATIVES    = "alternatives_fund"    # private equity, real estate, commodities
    GENERAL         = "general"              # not an investment fund


class AssetClass(str, Enum):
    EQUITIES         = "equities"
    FIXED_INCOME     = "fixed_income"
    CASH             = "cash"
    INVESTMENT_FUNDS = "investment_funds"
    ALTERNATIVES     = "alternatives"
    OTHER            = "other"


# ── Keyword matchers ──────────────────────────────────────────────────────────

_FUND_SIGNALS = (
    "aportes de inversionistas", "aporte inversionistas", "retiros de inversionistas",
    "retiro inversionistas", "activos netos iniciales", "patrimonio inicial",
    "clase p", "clase a", "clase b", "clase i", "clase f",
    "derechos o suscripciones", "patrimonio de inversionistas",
    "total activo neto", "fondo de inversión",
)

_OPENING_NAV_KW  = ("activos netos iniciales", "patrimonio inicial", "saldo inicial nav")
_CONTRIBUTIONS_KW = ("aportes de inversionistas", "aporte inversionistas", "suscripciones")
_REDEMPTIONS_KW  = ("retiros de inversionistas", "retiro inversionistas", "redenciones")
_PERIOD_RETURN_KW = ("ganancia del período", "utilidad del período", "resultado del período",
                     "pérdida del período", "resultado neto del período")

_EQUITY_ASSET_KW    = ("acciones", "renta variable", "equity", "acción")
_FIXED_INCOME_KW    = ("bono", "tes", "cdt", "título de deuda", "renta fija",
                       "título de renta", "deuda pública", "deuda privada")
_CASH_KW            = ("efectivo", "banco ", "caja", "bancos", "banco de", "bancolombia",
                       "banco occidente", "btg pactual")  # named cash accounts
_INV_FUND_KW        = ("fondo de inversión", "fondo liquidez", "etf", "btg fondo",
                       "inversión en fondo", "fondos de inversión")
_ALTERNATIVES_KW    = ("inmobiliario", "real estate", "private equity", "commodities",
                       "infraestructura", "activos alternativos")

# Keywords that suggest an account is an individual position (not a subtotal/total)
_POSITION_EXCLUDE_KW = (
    "total", "subtotal", "suma", "activos financieros", "inversiones",
    "instrumentos financieros", "portafolio",
)


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class PortfolioPosition:
    account_id: str
    account_name: str
    asset_class: AssetClass
    current_value: float
    previous_value: float | None
    pct_of_portfolio: float       # % of total investable assets
    status: str                   # "new" | "closed" | "increased" | "decreased" | "stable"
    absolute_change: float        # current − previous (0 if no prior)


@dataclass
class NavReconciliation:
    opening_nav: float | None
    contributions: float | None
    redemptions: float | None     # stored as signed value (negative for outflows)
    investment_return: float | None
    closing_nav: float
    net_investor_flow: float | None   # contributions + redemptions
    reconciles: bool
    gap_cop_mm: float
    narrative: str


@dataclass
class FundAnalysis:
    is_investment_fund: bool
    fund_type: FundType
    fund_type_rationale: str

    # Asset class breakdown
    asset_breakdown_pct: dict[str, float]    # AssetClass.value → % of investable assets
    asset_breakdown_cop_mm: dict[str, float] # AssetClass.value → COP MM

    # NAV mechanics
    nav_reconciliation: NavReconciliation | None
    net_investor_flow_cop_mm: float | None   # positive = net inflows, negative = net outflows

    # Portfolio positions
    portfolio_positions: list[PortfolioPosition]
    new_positions: list[PortfolioPosition]
    closed_positions: list[PortfolioPosition]
    top_positions: list[PortfolioPosition]   # top 5 by current_value

    # Risk indicators
    cash_ratio: float                        # cash / total investable assets
    top1_position_pct: float                 # largest single position % of portfolio
    top3_concentration_pct: float            # top 3 positions combined %

    # Human-readable summary
    insights: list[str] = field(default_factory=list)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _hit(name: str, keywords: tuple) -> bool:
    return any(k in name for k in keywords)


def _classify_asset(name: str) -> AssetClass:
    if _hit(name, _CASH_KW):
        return AssetClass.CASH
    if _hit(name, _INV_FUND_KW):
        return AssetClass.INVESTMENT_FUNDS
    if _hit(name, _FIXED_INCOME_KW):
        return AssetClass.FIXED_INCOME
    if _hit(name, _EQUITY_ASSET_KW):
        return AssetClass.EQUITIES
    if _hit(name, _ALTERNATIVES_KW):
        return AssetClass.ALTERNATIVES
    return AssetClass.OTHER


def _is_individual_position(acc: ExtractedAccount) -> bool:
    """
    True when the account represents a single named holding (not a subtotal/total/category line).
    Used to populate the portfolio positions list.
    """
    if acc.category not in ("assets", "other"):
        return False
    name = acc.normalized_account_name.lower()
    if _hit(name, _POSITION_EXCLUDE_KW):
        return False
    # Must have a positive value and look like a named instrument
    if acc.current_value <= 0 and (acc.previous_value is None or acc.previous_value <= 0):
        return False
    return True


def _position_status(current: float, previous: float | None) -> tuple[str, float]:
    """Returns (status_label, absolute_change)."""
    if previous is None:
        return "new", current
    change = current - previous
    if abs(previous) < 0.001:
        return "new", current
    if abs(current) < 0.001:
        return "closed", change
    if change > 0:
        return "increased", change
    if change < 0:
        return "decreased", change
    return "stable", 0.0


# ── Fund type detection ───────────────────────────────────────────────────────

def _detect_fund_type(
    accounts: list[ExtractedAccount],
    asset_breakdown_cop_mm: dict[str, float],
) -> tuple[FundType, str]:
    total = sum(asset_breakdown_cop_mm.values()) or 1.0
    equities_pct  = asset_breakdown_cop_mm.get(AssetClass.EQUITIES.value, 0) / total * 100
    fi_pct        = asset_breakdown_cop_mm.get(AssetClass.FIXED_INCOME.value, 0) / total * 100
    cash_pct      = asset_breakdown_cop_mm.get(AssetClass.CASH.value, 0) / total * 100
    inv_fund_pct  = asset_breakdown_cop_mm.get(AssetClass.INVESTMENT_FUNDS.value, 0) / total * 100
    alt_pct       = asset_breakdown_cop_mm.get(AssetClass.ALTERNATIVES.value, 0) / total * 100

    if cash_pct + inv_fund_pct > 70:
        return FundType.MONEY_MARKET, (
            f"El {cash_pct + inv_fund_pct:.0f}% de los activos está en efectivo e instrumentos "
            f"de corto plazo → fondo de mercado monetario / liquidez."
        )
    if equities_pct > 60:
        return FundType.EQUITY, (
            f"El {equities_pct:.0f}% de los activos son acciones listadas → fondo de renta variable."
        )
    if fi_pct > 60:
        return FundType.FIXED_INCOME, (
            f"El {fi_pct:.0f}% de los activos son títulos de deuda → fondo de renta fija."
        )
    if alt_pct > 40:
        return FundType.ALTERNATIVES, (
            f"El {alt_pct:.0f}% corresponde a activos alternativos → fondo de inversión alternativa."
        )
    if equities_pct > 20 and fi_pct > 20:
        return FundType.BALANCED, (
            f"Mezcla acciones ({equities_pct:.0f}%) y renta fija ({fi_pct:.0f}%) → fondo balanceado."
        )
    return FundType.EQUITY, (
        "No se detectó una clase de activo dominante clara; "
        "clasificado como renta variable por defecto."
    )


# ── NAV reconciliation ────────────────────────────────────────────────────────

def _compute_nav_reconciliation(
    accounts: list[ExtractedAccount],
    closing_nav: float,
) -> NavReconciliation | None:
    opening_nav = contributions = redemptions = period_return = None

    for acc in accounts:
        name = acc.normalized_account_name.lower()
        val = acc.current_value
        if _hit(name, _OPENING_NAV_KW):
            opening_nav = val
        elif _hit(name, _CONTRIBUTIONS_KW):
            contributions = val
        elif _hit(name, _REDEMPTIONS_KW):
            # Stored as signed — redemptions are typically negative in the EEFF
            redemptions = val if val < 0 else -abs(val)
        elif _hit(name, _PERIOD_RETURN_KW):
            period_return = val

    if opening_nav is None:
        return None

    net_flow = (contributions or 0) + (redemptions or 0)
    implied_closing = opening_nav + (contributions or 0) + (redemptions or 0) + (period_return or 0)
    gap = abs(implied_closing - closing_nav)
    reconciles = gap < max(1.0, abs(closing_nav) * 0.001)  # within 0.1% or 1 COP MM

    if reconciles:
        narrative = (
            f"NAV reconciliado: apertura {opening_nav:,.1f} + aportes "
            f"{contributions or 0:+,.1f} + retiros {redemptions or 0:+,.1f} + "
            f"rendimiento {period_return or 0:+,.1f} = cierre {closing_nav:,.1f} COP MM. "
            f"Diferencia: {gap:,.2f} COP MM ({gap/max(abs(closing_nav),1)*100:.3f}%)."
        )
    else:
        narrative = (
            f"Reconciliación de NAV con diferencia de {gap:,.1f} COP MM "
            f"({gap/max(abs(closing_nav),1)*100:.1f}%). "
            f"Verifique si hay cuentas de ajuste no capturadas."
        )

    return NavReconciliation(
        opening_nav=opening_nav,
        contributions=contributions,
        redemptions=redemptions,
        investment_return=period_return,
        closing_nav=closing_nav,
        net_investor_flow=net_flow if (contributions or redemptions) else None,
        reconciles=reconciles,
        gap_cop_mm=round(gap, 3),
        narrative=narrative,
    )


# ── Fund-specific causal chains ───────────────────────────────────────────────

def _fund_causal_chains(
    analysis: "FundAnalysis",
    nav: NavReconciliation | None,
) -> list[CausalChain]:
    chains: list[CausalChain] = []

    # Chain A: Net redemptions → portfolio de-risking / asset sales
    if nav and nav.net_investor_flow is not None and nav.net_investor_flow < -500:
        outflow = abs(nav.net_investor_flow)
        chains.append(CausalChain(
            chain_id="fund_redemption_pressure",
            driver_account="Retiros de inversionistas",
            driver_direction="up",
            effects=["Reducción de activos", "Necesidad de liquidez", "Ventas de portafolio"],
            narrative=(
                f"Flujo neto de inversionistas negativo ({nav.net_investor_flow:,.1f} COP MM): "
                f"retiros ({nav.redemptions or 0:,.1f}) superan aportes ({nav.contributions or 0:,.1f}). "
                f"El fondo debió liquidar posiciones para cubrir la salida de capital. "
                f"Cadena: retiros ↑ → ventas portafolio → AUM ↓."
            ),
            confidence=0.93,
            evidence=[
                f"Aportes: {nav.contributions or 0:+,.1f} COP MM",
                f"Retiros: {nav.redemptions or 0:+,.1f} COP MM",
                f"Flujo neto: {nav.net_investor_flow:+,.1f} COP MM",
            ],
        ))

    # Chain B: Net inflows → cash deployment into portfolio positions
    if nav and nav.net_investor_flow is not None and nav.net_investor_flow > 500:
        cash_ratio = analysis.cash_ratio
        if cash_ratio < 0.05:
            chains.append(CausalChain(
                chain_id="fund_cash_deployment",
                driver_account="Aportes de inversionistas",
                driver_direction="up",
                effects=["Incremento de posiciones", "Baja caja residual"],
                narrative=(
                    f"Flujo neto positivo de {nav.net_investor_flow:+,.1f} COP MM desplegado "
                    f"en portafolio. Caja residual: {cash_ratio*100:.1f}% — el fondo invirtió "
                    f"eficientemente los nuevos capitales. Cadena: aportes ↑ → compras ↑ → caja ↓."
                ),
                confidence=0.85,
                evidence=[f"Cash ratio: {cash_ratio:.1%}", f"Flujo neto: {nav.net_investor_flow:+,.1f} COP MM"],
            ))

    # Chain C: Portfolio rebalancing — new + closed positions
    if analysis.new_positions and analysis.closed_positions:
        new_val = sum(p.current_value for p in analysis.new_positions)
        closed_val = sum(abs(p.previous_value or 0) for p in analysis.closed_positions)
        chains.append(CausalChain(
            chain_id="fund_portfolio_rebalancing",
            driver_account=analysis.new_positions[0].account_name,
            driver_direction="up",
            effects=[p.account_name for p in analysis.closed_positions[:2]],
            narrative=(
                f"Rebalanceo estratégico del portafolio: "
                f"{len(analysis.new_positions)} posición(es) nueva(s) "
                f"({new_val:,.1f} COP MM) y {len(analysis.closed_positions)} "
                f"posición(es) liquidada(s) ({closed_val:,.1f} COP MM). "
                f"Cadena: desinversión de posiciones antiguas → reinversión en nuevas tesis. "
                f"Nueva posición más grande: {analysis.new_positions[0].account_name} "
                f"({analysis.new_positions[0].current_value:,.1f} COP MM, "
                f"{analysis.new_positions[0].pct_of_portfolio:.1f}% del portafolio)."
            ),
            confidence=0.88,
            evidence=[
                f"Nuevas: {', '.join(p.account_name for p in analysis.new_positions[:3])}",
                f"Cerradas: {', '.join(p.account_name for p in analysis.closed_positions[:3])}",
            ],
        ))

    # Chain D: Valuation return vs investor flows — which drove AUM more?
    if nav and nav.investment_return is not None and nav.net_investor_flow is not None:
        total_change = abs(nav.investment_return) + abs(nav.net_investor_flow)
        if total_change > 0:
            return_pct = abs(nav.investment_return) / total_change * 100
            flow_pct = abs(nav.net_investor_flow) / total_change * 100
            dominant = "rendimiento de inversiones" if return_pct > flow_pct else "flujo de inversionistas"
            chains.append(CausalChain(
                chain_id="fund_aum_drivers",
                driver_account="Utilidad del período / Flujos de inversionistas",
                driver_direction="up" if nav.investment_return > 0 else "down",
                effects=["AUM / Patrimonio total"],
                narrative=(
                    f"Variación del AUM explicada por: "
                    f"rendimiento de inversiones ({nav.investment_return:+,.1f} COP MM, {return_pct:.0f}%) "
                    f"y flujo neto de inversionistas ({nav.net_investor_flow:+,.1f} COP MM, {flow_pct:.0f}%). "
                    f"Factor dominante: {dominant}. "
                    f"Cadena: {dominant} ↑/↓ → patrimonio ↑/↓ → AUM {nav.closing_nav:,.1f} COP MM."
                ),
                confidence=0.91,
                evidence=[
                    f"Rendimiento: {nav.investment_return:+,.1f} COP MM ({return_pct:.0f}% del cambio)",
                    f"Flujo neto: {nav.net_investor_flow:+,.1f} COP MM ({flow_pct:.0f}% del cambio)",
                ],
            ))

    # Chain E: Concentration in a single new position — strategic bet flag
    if analysis.top_positions and analysis.top_positions[0].status == "new":
        top = analysis.top_positions[0]
        if top.pct_of_portfolio > 30:
            chains.append(CausalChain(
                chain_id="fund_new_dominant_position",
                driver_account=top.account_name,
                driver_direction="up",
                effects=["Concentración del portafolio", "Riesgo idiosincrático"],
                narrative=(
                    f"Entrada de nueva posición dominante: '{top.account_name}' "
                    f"({top.current_value:,.1f} COP MM = {top.pct_of_portfolio:.1f}% del portafolio). "
                    f"Esta posición no existía en el período anterior. "
                    f"Cadena: apuesta estratégica nueva → concentración ↑ → "
                    f"riesgo idiosincrático ↑ → exposición a evento específico."
                ),
                confidence=0.90,
                evidence=[
                    f"Posición: {top.account_name}",
                    f"Peso en portafolio: {top.pct_of_portfolio:.1f}%",
                    f"Status: nueva (no existía en período anterior)",
                ],
            ))

    return chains


# ── Public API ────────────────────────────────────────────────────────────────

def analyze_fund(
    accounts: list[ExtractedAccount],
    variations: list[AccountVariation],
    totals: FinancialTotals,
) -> tuple["FundAnalysis", list[CausalChain]]:
    """
    Detect whether this EEFF belongs to an investment fund and perform fund-specific
    analysis if so.

    Returns (FundAnalysis, list[CausalChain]) — the chains are additional fund-specific
    chains that service.py should merge with the generic causality chains.
    """
    # Detect if this is an investment fund
    all_names = " ".join(acc.normalized_account_name.lower() for acc in accounts)
    is_fund = any(kw in all_names for kw in _FUND_SIGNALS)

    if not is_fund:
        logger.info("fund_engine | not_an_investment_fund")
        analysis = FundAnalysis(
            is_investment_fund=False,
            fund_type=FundType.GENERAL,
            fund_type_rationale="No se detectaron indicadores de fondo de inversión colectivo.",
            asset_breakdown_pct={},
            asset_breakdown_cop_mm={},
            nav_reconciliation=None,
            net_investor_flow_cop_mm=None,
            portfolio_positions=[],
            new_positions=[],
            closed_positions=[],
            top_positions=[],
            cash_ratio=0.0,
            top1_position_pct=0.0,
            top3_concentration_pct=0.0,
            insights=["EEFF no corresponde a un fondo de inversión colectivo."],
        )
        return analysis, []

    # Build portfolio positions — only individual named holdings (not subtotals)
    investable_total = 0.0
    asset_breakdown_cop_mm: dict[str, float] = {}
    positions: list[PortfolioPosition] = []

    # Map account_id → previous_value from variations (enriched)
    prev_map = {v.account_id: v.previous_value for v in variations}

    for acc in accounts:
        if not _is_individual_position(acc):
            continue
        name = acc.normalized_account_name.lower()
        asset_class = _classify_asset(name)
        val = abs(acc.current_value)
        prev = prev_map.get(acc.account_id, acc.previous_value)

        asset_breakdown_cop_mm[asset_class.value] = (
            asset_breakdown_cop_mm.get(asset_class.value, 0.0) + val
        )
        investable_total += val

        status, change = _position_status(acc.current_value, prev)
        positions.append(PortfolioPosition(
            account_id=acc.account_id,
            account_name=acc.normalized_account_name,
            asset_class=asset_class,
            current_value=acc.current_value,
            previous_value=prev,
            pct_of_portfolio=0.0,  # filled after total is known
            status=status,
            absolute_change=change,
        ))

    # Compute weights
    base = max(investable_total, 0.001)
    for pos in positions:
        pos.pct_of_portfolio = round(abs(pos.current_value) / base * 100, 2)

    # Asset class % breakdown
    asset_breakdown_pct = {
        cls: round(val / base * 100, 1)
        for cls, val in asset_breakdown_cop_mm.items()
    }

    # Fund type
    fund_type, fund_type_rationale = _detect_fund_type(accounts, asset_breakdown_cop_mm)

    # NAV reconciliation — closing NAV is the total equity/patrimonio
    closing_nav = totals.total_equity if totals.total_equity > 0 else totals.total_assets
    nav = _compute_nav_reconciliation(accounts, closing_nav)

    # Derived metrics
    cash_cop_mm = asset_breakdown_cop_mm.get(AssetClass.CASH.value, 0.0)
    cash_ratio = cash_cop_mm / base if base > 0 else 0.0

    sorted_positions = sorted(positions, key=lambda p: abs(p.current_value), reverse=True)
    top_positions = sorted_positions[:5]
    new_positions = [p for p in sorted_positions if p.status == "new"]
    closed_positions = [p for p in sorted_positions if p.status == "closed"]

    top1_pct = sorted_positions[0].pct_of_portfolio if sorted_positions else 0.0
    top3_pct = sum(p.pct_of_portfolio for p in sorted_positions[:3])

    # Human-readable insights
    insights: list[str] = []

    if nav and nav.reconciles:
        insights.append(f"NAV reconciliado correctamente. {nav.narrative}")
    elif nav and not nav.reconciles:
        insights.append(f"Diferencia en reconciliación de NAV: {nav.gap_cop_mm:,.1f} COP MM.")

    if nav and nav.net_investor_flow is not None:
        flow_dir = "salida neta" if nav.net_investor_flow < 0 else "entrada neta"
        insights.append(
            f"Flujo de inversionistas: {flow_dir} de {abs(nav.net_investor_flow):,.1f} COP MM "
            f"(aportes {nav.contributions or 0:+,.1f}, retiros {nav.redemptions or 0:+,.1f})."
        )

    if new_positions:
        insights.append(
            f"{len(new_positions)} posición(es) nueva(s): "
            + ", ".join(f"{p.account_name} ({p.current_value:,.1f} COP MM)" for p in new_positions[:3])
        )
    if closed_positions:
        insights.append(
            f"{len(closed_positions)} posición(es) liquidada(s): "
            + ", ".join(
                f"{p.account_name} ({abs(p.previous_value or 0):,.1f} COP MM)"
                for p in closed_positions[:3]
            )
        )

    if top1_pct > 30:
        insights.append(
            f"Alta concentración: '{sorted_positions[0].account_name}' representa "
            f"el {top1_pct:.1f}% del portafolio."
        )

    dom_class = max(asset_breakdown_pct.items(), key=lambda x: x[1], default=("N/A", 0))
    insights.append(
        f"Composición: clase dominante '{dom_class[0]}' con {dom_class[1]:.1f}%. "
        f"Tipo de fondo detectado: {fund_type.value}."
    )

    net_flow = nav.net_investor_flow if nav else None

    analysis = FundAnalysis(
        is_investment_fund=True,
        fund_type=fund_type,
        fund_type_rationale=fund_type_rationale,
        asset_breakdown_pct=asset_breakdown_pct,
        asset_breakdown_cop_mm={k: round(v, 2) for k, v in asset_breakdown_cop_mm.items()},
        nav_reconciliation=nav,
        net_investor_flow_cop_mm=round(net_flow, 2) if net_flow is not None else None,
        portfolio_positions=sorted_positions,
        new_positions=new_positions,
        closed_positions=closed_positions,
        top_positions=top_positions,
        cash_ratio=round(cash_ratio, 4),
        top1_position_pct=round(top1_pct, 2),
        top3_concentration_pct=round(top3_pct, 2),
        insights=insights,
    )

    logger.info(
        "fund_engine | type=%s nav_reconciles=%s positions=%d new=%d closed=%d "
        "cash_ratio=%.2f%% top1=%.1f%% top3=%.1f%%",
        fund_type.value,
        nav.reconciles if nav else "N/A",
        len(positions), len(new_positions), len(closed_positions),
        cash_ratio * 100, top1_pct, top3_pct,
    )

    fund_chains = _fund_causal_chains(analysis, nav)
    return analysis, fund_chains


def fund_analysis_to_dict(analysis: FundAnalysis) -> dict:
    """Serialize FundAnalysis to a JSON-safe dict for AnalyzerOutput."""
    def pos_to_dict(p: PortfolioPosition) -> dict:
        return {
            "account_id": p.account_id,
            "account_name": p.account_name,
            "asset_class": p.asset_class.value,
            "current_value": p.current_value,
            "previous_value": p.previous_value,
            "pct_of_portfolio": p.pct_of_portfolio,
            "status": p.status,
            "absolute_change": p.absolute_change,
        }

    nav = analysis.nav_reconciliation
    nav_dict = None
    if nav:
        nav_dict = {
            "opening_nav": nav.opening_nav,
            "contributions": nav.contributions,
            "redemptions": nav.redemptions,
            "investment_return": nav.investment_return,
            "closing_nav": nav.closing_nav,
            "net_investor_flow": nav.net_investor_flow,
            "reconciles": nav.reconciles,
            "gap_cop_mm": nav.gap_cop_mm,
            "narrative": nav.narrative,
        }

    return {
        "is_investment_fund": analysis.is_investment_fund,
        "fund_type": analysis.fund_type.value,
        "fund_type_rationale": analysis.fund_type_rationale,
        "asset_breakdown_pct": analysis.asset_breakdown_pct,
        "asset_breakdown_cop_mm": analysis.asset_breakdown_cop_mm,
        "nav_reconciliation": nav_dict,
        "net_investor_flow_cop_mm": analysis.net_investor_flow_cop_mm,
        "portfolio_positions": [pos_to_dict(p) for p in analysis.portfolio_positions],
        "new_positions": [pos_to_dict(p) for p in analysis.new_positions],
        "closed_positions": [pos_to_dict(p) for p in analysis.closed_positions],
        "top_positions": [pos_to_dict(p) for p in analysis.top_positions],
        "cash_ratio": analysis.cash_ratio,
        "top1_position_pct": analysis.top1_position_pct,
        "top3_concentration_pct": analysis.top3_concentration_pct,
        "insights": analysis.insights,
    }
