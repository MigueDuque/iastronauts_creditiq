"""
causality_engine.py  —  Improvement #2: Financial Causality Engine

Detects cross-account causal chains from deterministic signals.
Produces structured evidence for the LLM to narrate as financial storytelling.
No LLM invocation — pure rule-based logic.
"""

from dataclasses import dataclass, field

from .ratio_engine import AccountVariation, FinancialTotals

# ── Keyword matchers ──────────────────────────────────────────────────────────

_VALUATION_KW = (
    "valorizacion", "valor razonable", "ganancia en venta", "rendimiento de inversiones",
    "resultado de inversiones", "valoracion", "participacion en subsidiarias",
    "metodo de participacion", "ganancia por valoracion",
)
_FIN_EXPENSE_KW = (
    "gasto financiero", "gastos financieros", "intereses pagados", "costo financiero",
    "intereses de deuda", "costos de financiamiento",
)
_LIABILITY_KW = (
    "prestamo", "obligacion financiera", "deuda", "credito bancario", "bonos",
    "pasivo financiero",
)
_REVENUE_KW = (
    "ingreso", "venta", "rendimiento", "prestacion de servicio", "contrato",
)
_OPEX_KW = (
    "gasto operativo", "gasto de personal", "costo operativo",
    "administracion", "nomina",
)
_FIXED_ASSET_KW = (
    "propiedad", "planta", "equipo", "activo fijo", "inversion permanente",
)
_DEPRECIATION_KW = ("depreciacion", "amortizacion")
_EQUITY_KW = ("patrimonio", "capital", "reserva", "utilidad retenida", "resultado acumulado")


def _hit(name: str, keywords: tuple) -> bool:
    return any(k in name for k in keywords)


@dataclass
class CausalChain:
    chain_id: str
    driver_account: str           # account that triggers the chain
    driver_direction: str         # "up" | "down"
    effects: list[str]            # affected metrics or accounts
    narrative: str                # human-readable causal story
    confidence: float             # 0.0–1.0 — based on strength of deterministic evidence
    evidence: list[str] = field(default_factory=list)


def detect_causality_chains(
    variations: list[AccountVariation],
    totals: FinancialTotals,
) -> list[CausalChain]:
    """
    Identify cross-account causal relationships from variation data and financial totals.
    Returns chains ordered by confidence descending.
    """
    chains: list[CausalChain] = []

    def find(keywords: tuple) -> list[AccountVariation]:
        return [v for v in variations if _hit(v.account_name.lower(), keywords)]

    valuations   = find(_VALUATION_KW)
    liabilities  = find(_LIABILITY_KW)
    revenues     = find(_REVENUE_KW)
    fin_expenses = find(_FIN_EXPENSE_KW)
    fixed_assets = find(_FIXED_ASSET_KW)
    depreciation = find(_DEPRECIATION_KW)

    net_income = totals.net_income

    # ── Chain 1: Valuation gains → Net income → Equity ───────────────────────
    rising_val = [v for v in valuations if v.absolute_variation > 0]
    if rising_val and net_income > 0:
        total_val_gain = sum(v.absolute_variation for v in rising_val)
        val_pct = (total_val_gain / net_income * 100) if net_income else 0
        if val_pct > 15:
            confidence = min(0.97, 0.60 + val_pct / 250)
            chains.append(CausalChain(
                chain_id="valuation_income_equity",
                driver_account=rising_val[0].account_name,
                driver_direction="up",
                effects=["Resultado del ejercicio", "Patrimonio neto"],
                narrative=(
                    f"Ganancias por valorización ({total_val_gain:+,.1f} COP MM) "
                    f"explican el {val_pct:.0f}% del resultado neto. "
                    f"Cadena: valorización ↑ → ingreso ↑ → patrimonio ↑ → "
                    f"exposición a concentración ↑."
                ),
                confidence=round(confidence, 2),
                evidence=[
                    f"{v.account_name}: {v.absolute_variation:+,.1f} COP MM"
                    for v in rising_val
                ],
            ))

    falling_val = [v for v in valuations if v.absolute_variation < 0]
    if falling_val and net_income < 0:
        total_val_loss = sum(v.absolute_variation for v in falling_val)
        chains.append(CausalChain(
            chain_id="valuation_loss_equity",
            driver_account=falling_val[0].account_name,
            driver_direction="down",
            effects=["Resultado del ejercicio", "Patrimonio neto"],
            narrative=(
                f"Pérdidas por desvalorización ({total_val_loss:,.1f} COP MM) "
                f"deterioran el resultado del período y reducen el patrimonio. "
                f"Cadena: valorización ↓ → pérdida ↑ → patrimonio ↓."
            ),
            confidence=0.87,
            evidence=[
                f"{v.account_name}: {v.absolute_variation:+,.1f} COP MM"
                for v in falling_val
            ],
        ))

    # ── Chain 2: Liability growth → Financial expense pressure ───────────────
    growing_liab = [v for v in liabilities if v.absolute_variation > 0 and v.has_previous_value]
    growing_finexp = [v for v in fin_expenses if v.absolute_variation > 0 and v.has_previous_value]
    if growing_liab and growing_finexp:
        liab_delta = sum(v.absolute_variation for v in growing_liab)
        exp_delta = sum(v.absolute_variation for v in growing_finexp)
        chains.append(CausalChain(
            chain_id="liability_financial_expense",
            driver_account=growing_liab[0].account_name,
            driver_direction="up",
            effects=[v.account_name for v in growing_finexp],
            narrative=(
                f"Crecimiento en obligaciones financieras ({liab_delta:+,.1f} COP MM) "
                f"correlaciona con aumento en gastos financieros ({exp_delta:+,.1f} COP MM). "
                f"Cadena: deuda ↑ → costo financiero ↑ → resultado operativo ↓."
            ),
            confidence=0.78,
            evidence=[
                f"Deuda: {growing_liab[0].account_name} {growing_liab[0].variation_pct:+.1f}%",
                f"Intereses: {growing_finexp[0].account_name} {growing_finexp[0].variation_pct:+.1f}%",
            ],
        ))

    # ── Chain 3: Revenue growth → operating leverage ─────────────────────────
    growing_rev = [
        v for v in revenues
        if v.absolute_variation > 0 and v.has_previous_value and v.variation_pct > 10
    ]
    if growing_rev:
        rev_delta = sum(v.absolute_variation for v in growing_rev)
        chains.append(CausalChain(
            chain_id="revenue_growth_leverage",
            driver_account=growing_rev[0].account_name,
            driver_direction="up",
            effects=["Margen operativo", "Resultado neto"],
            narrative=(
                f"Crecimiento de ingresos ({rev_delta:+,.1f} COP MM) "
                f"mejora el apalancamiento operativo. "
                f"Cadena: ingresos ↑ → margen operativo ↑ → resultado neto ↑."
            ),
            confidence=0.70,
            evidence=[f"{v.account_name}: {v.variation_pct:+.1f}%" for v in growing_rev[:3]],
        ))

    declining_rev = [
        v for v in revenues
        if v.absolute_variation < 0 and v.has_previous_value and v.variation_pct < -10
    ]
    if declining_rev:
        rev_delta = sum(v.absolute_variation for v in declining_rev)
        chains.append(CausalChain(
            chain_id="revenue_decline",
            driver_account=declining_rev[0].account_name,
            driver_direction="down",
            effects=["Margen operativo", "Cobertura de gastos fijos"],
            narrative=(
                f"Caída en ingresos ({rev_delta:,.1f} COP MM) "
                f"comprime márgenes y reduce cobertura de gastos fijos. "
                f"Cadena: ingresos ↓ → margen ↓ → presión sobre liquidez ↑."
            ),
            confidence=0.72,
            evidence=[f"{v.account_name}: {v.variation_pct:+.1f}%" for v in declining_rev[:3]],
        ))

    # ── Chain 4: Fixed asset growth → future depreciation ────────────────────
    growing_assets = [v for v in fixed_assets if v.absolute_variation > 0 and v.has_previous_value]
    if growing_assets and depreciation:
        asset_delta = sum(v.absolute_variation for v in growing_assets)
        chains.append(CausalChain(
            chain_id="asset_depreciation_pressure",
            driver_account=growing_assets[0].account_name,
            driver_direction="up",
            effects=["Depreciación y amortización futura"],
            narrative=(
                f"Incremento en activos fijos ({asset_delta:+,.1f} COP MM) "
                f"anticipa mayor gasto de depreciación en períodos futuros, "
                f"reduciendo el margen operativo. Cadena: CAPEX ↑ → D&A futura ↑ → EBIT ↓."
            ),
            confidence=0.65,
            evidence=[
                f"{v.account_name}: {v.current_value:,.1f} COP MM"
                for v in growing_assets[:2]
            ],
        ))

    return sorted(chains, key=lambda c: c.confidence, reverse=True)
