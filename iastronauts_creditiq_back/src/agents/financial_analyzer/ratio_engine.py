"""
ratio_engine.py

Deterministic financial math engine for the FinancialAnalyzer agent.
No LLM. No external dependencies beyond Python stdlib.
All monetary values are in COP MM (millions of Colombian pesos).
"""

import logging
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from typing import List

from shared.models import ExtractedAccount

logger = logging.getLogger("financial_analyzer.ratio_engine")


def _norm_name(s: str) -> str:
    """Lowercase, strip accents and collapse whitespace for robust name matching."""
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return " ".join(s.lower().split())


# Canonical *grand-total* row names (accent-free, exact match) that financial
# statements report directly. Multi-sheet workbooks re-expand a single statement line
# (e.g. "Activos financieros a valor razonable") into its own detail sheet, so blind
# cross-sheet summation double-counts. When the statement reports the total we trust
# it and rescale the summed sub-components to preserve their composition.
# (§ cross-sheet double-count fix, 2026-06-02)
_REPORTED_TOTAL_NAMES: dict[str, set[str]] = {
    "assets": {
        "total activos", "total de activos", "total del activo",
        "activos totales", "total activo",
    },
    "liabilities": {
        "total pasivos", "total de pasivos", "total del pasivo",
        "pasivos totales", "total pasivo",
    },
    "equity": {
        "total patrimonio", "patrimonio total", "total del patrimonio",
        "total patrimonio neto", "total activos netos",
        "total activos netos de los inversionistas",
    },
    "revenue": {
        "ingresos operacionales", "total ingresos", "ingresos totales",
        "total de ingresos", "total ingresos operacionales",
    },
    "net_income": {
        "utilidad del periodo", "perdida del periodo", "ganancia del periodo",
        "resultado del periodo", "resultado integral del periodo",
        "resultado integral total del periodo",
    },
}

# P&L running-total / subtotal lines that some statements report *inside* the
# revenue/expense columns (e.g. "Utilidad operacional", "Utilidad del período").
# They are cumulative results, NOT line items, so summing them with their own
# components double-counts. Excluded from calculate_niif18_subtotals (accent-free).
_PL_SUBTOTAL_NAMES: set[str] = {
    "utilidad operacional", "utilidad de operacion", "perdida operacional",
    "resultado operativo", "resultado de operacion", "resultado operacional",
    "utilidad del periodo", "perdida del periodo", "ganancia del periodo",
    "resultado del periodo", "resultado neto del periodo",
    "utilidad neta", "perdida neta", "resultado neto",
    "utilidad antes de impuestos", "perdida antes de impuestos",
    "resultado antes de impuestos", "utilidad antes de impuesto",
    "resultado integral", "resultado integral del periodo",
    "total ingresos", "total ingresos operacionales", "ingresos operacionales",
    "total gastos", "total gastos operacionales", "total gastos administrativos",
}

# ── Keyword lists for account classification ──────────────────────────────────

_NON_CURRENT_ASSET_KEYWORDS = (
    "no corriente", "no_corriente", "propiedad", "planta", "equipo",
    "intangible", "largo plazo", "diferido", "valorizac", "goodwill",
    "plusvalia", "arrendamiento por cobrar", "activo por impuesto diferido",
    "inversiones permanentes", "inversiones lp",
)
_NON_CURRENT_LIABILITY_KEYWORDS = (
    "no corriente", "no_corriente", "largo plazo", "largo_plazo",
    "pasivo diferido", "deuda largo", "obligacion financiera lp",
    "impuesto diferido", "beneficio empleado lp",
)
_INVENTORY_KEYWORDS = (
    "inventar", "existencia", "mercanc", "producto terminado",
    "materia prima", "producto en proceso",
)
_COGS_KEYWORDS = (
    "costo de venta", "costo de operac", "costo directo", "costo de produccion",
    "costo mercancia vendida",
)
_DEPRECIATION_KEYWORDS = ("depreciac", "amortizac", "agotamiento")
_RECEIVABLE_KEYWORDS = (
    "cartera", "clientes", "cuentas por cobrar", "deudores comerciales",
    "deudores varios",
)
_PAYABLE_KEYWORDS = (
    "proveedores", "cuentas por pagar", "obligaciones con proveedores",
    "acreedores comerciales",
)


# ── Intermediate data structures ──────────────────────────────────────────────

@dataclass
class AccountVariation:
    """Per-account computed variation — intermediate result bridging extraction and analysis."""
    account_id: str
    account_name: str
    category: str
    source_file: str
    confidence_score: float
    current_value: float
    previous_value: float       # 0.0 when has_previous_value is False
    has_previous_value: bool
    absolute_variation: float   # current − previous, COP MM
    variation_pct: float        # percentage change; 0.0 if no prior period
    source_sheet: str | None = None    # Excel sheet name (None for PDF/CSV)
    statement_type: str | None = None  # "balance_sheet" | "income_statement" | "cash_flow" | "equity_changes"
    is_total: bool = False             # True when this row is a sum/subtotal row


@dataclass
class FinancialTotals:
    """Aggregated balance-sheet and P&L sums derived from the full accounts list."""
    total_assets: float = 0.0
    total_liabilities: float = 0.0
    total_equity: float = 0.0
    total_revenue: float = 0.0
    total_expense: float = 0.0
    current_assets: float = 0.0
    non_current_assets: float = 0.0
    current_liabilities: float = 0.0
    non_current_liabilities: float = 0.0
    inventories: float = 0.0
    cost_of_sales: float = 0.0
    depreciation_amortization: float = 0.0
    receivables: float = 0.0
    payables: float = 0.0
    # Audit trail of which axes were overridden by a statement-reported grand total
    # (vs. summed from detail rows) and by how much — consumed by RevisorInteligente.
    reconciliation: dict = field(default_factory=dict)

    @property
    def net_income(self) -> float:
        return round(self.total_revenue - self.total_expense, 3)

    @property
    def ebitda(self) -> float:
        return round(self.net_income + self.depreciation_amortization, 3)


@dataclass
class FinancialRatios:
    """Standard NIIF financial ratios — all values deterministic."""
    # Liquidity
    razon_corriente: float = 0.0        # Current ratio
    prueba_acida: float = 0.0           # Quick ratio
    capital_trabajo: float = 0.0        # Net working capital (COP MM)
    # Solvency / Leverage
    endeudamiento_global: float = 0.0   # Total liabilities / total assets
    deuda_patrimonio: float = 0.0       # Total liabilities / equity
    equity_ratio: float = 0.0           # Equity / total assets
    # Profitability
    margen_bruto_pct: float = 0.0       # (Revenue − COGS) / Revenue × 100
    margen_neto_pct: float = 0.0        # Net income / Revenue × 100
    margen_ebitda_pct: float = 0.0      # EBITDA / Revenue × 100
    roe: float = 0.0                    # Return on Equity (%)
    roa: float = 0.0                    # Return on Assets (%)
    # Activity (rotation) — days
    dias_cartera: float = 0.0           # Receivables days
    dias_proveedores: float = 0.0       # Payables days


# ── Per-account math ──────────────────────────────────────────────────────────

def calculate_account_variation(account: ExtractedAccount) -> AccountVariation:
    """Compute absolute and percentage variation for a single extracted account."""
    prev = account.previous_value
    has_prev = prev is not None
    prev_val = float(prev) if has_prev else 0.0
    curr_val = float(account.current_value)

    if has_prev and prev_val != 0.0:
        abs_var = curr_val - prev_val
        pct_var = (abs_var / prev_val) * 100.0
    elif has_prev:
        # Previous value exists but is exactly zero — the position appeared this
        # period. The absolute movement is the full current value; the % is undefined.
        abs_var = curr_val
        pct_var = 0.0
    else:
        # No baseline period at all. A brand-new position is a +current_value
        # movement, NOT zero. The % is undefined (no denominator); reliability
        # downstream suppresses it. pct_var stays 0.0 here only because the
        # AccountVariation dataclass field is non-Optional — engines that read it
        # (anomaly/trend) early-return before reaching it for no-baseline accounts,
        # and the output-layer variation_pct is set to None when suppressed.
        abs_var = curr_val
        pct_var = 0.0

    return AccountVariation(
        account_id=account.account_id,
        account_name=account.normalized_account_name,
        category=account.category.lower(),
        source_file=account.source_file,
        confidence_score=account.confidence_score,
        current_value=round(curr_val, 3),
        previous_value=round(prev_val, 3),
        has_previous_value=has_prev,
        absolute_variation=round(abs_var, 3),
        variation_pct=round(pct_var, 2),
        source_sheet=account.source_sheet,
        statement_type=account.statement_type,
        is_total=account.is_total,
    )


# ── Global totals ─────────────────────────────────────────────────────────────

def _matches(name: str, keywords: tuple) -> bool:
    return any(k in name for k in keywords)


def calculate_financial_totals(accounts: List[ExtractedAccount]) -> FinancialTotals:
    """Aggregate all accounts into a FinancialTotals summary.

    is_total=True rows are skipped to prevent double-counting: their value is
    already the sum of the detail rows in the same sheet/section.
    """
    t = FinancialTotals()

    for acc in accounts:
        if acc.is_total:
            continue  # skip sum/subtotal rows — their value is already captured by detail rows
        cat = acc.category.lower()
        name = acc.normalized_account_name.lower()
        val = float(acc.current_value)

        if cat == "assets":
            t.total_assets += val
            if _matches(name, _NON_CURRENT_ASSET_KEYWORDS):
                t.non_current_assets += val
            else:
                t.current_assets += val
            if _matches(name, _INVENTORY_KEYWORDS):
                t.inventories += val
            if _matches(name, _RECEIVABLE_KEYWORDS):
                t.receivables += val

        elif cat == "liabilities":
            t.total_liabilities += val
            if _matches(name, _NON_CURRENT_LIABILITY_KEYWORDS):
                t.non_current_liabilities += val
            else:
                t.current_liabilities += val
            if _matches(name, _PAYABLE_KEYWORDS):
                t.payables += val

        elif cat == "equity":
            # Exclude flow/statement-of-changes accounts that double-count equity.
            # These are already reflected in closing balance-sheet equity accounts.
            if not _matches(name, (
                "aportes de inversionistas", "aporte inversionistas",
                "retiros de inversionistas", "retiro inversionistas",
                "patrimonio neto inicial", "patrimonio neto final",
                "saldo inicial", "suscripciones", "redenciones",
                "utilidad del período", "ganancia del período",
                "pérdida del período", "resultado del período",
            )):
                t.total_equity += val

        elif cat == "revenue":
            t.total_revenue += val

        elif cat == "expense":
            # Expense lines arrive with inconsistent sign (often negative on the source
            # statement, e.g. "Gastos operacionales" = -25.26). net_income is defined as
            # `total_revenue - total_expense`, so total_expense must be a POSITIVE cost
            # magnitude or the bottom line gets the cost *added back* (the 56.6 → 226.4
            # inflation that produced an impossible 133% net margin). Normalise to abs.
            cost = abs(val)
            t.total_expense += cost
            if _matches(name, _DEPRECIATION_KEYWORDS):
                t.depreciation_amortization += cost
            if _matches(name, _COGS_KEYWORDS):
                t.cost_of_sales += cost

    # ── Trust statement-reported grand totals over cross-sheet summation ─────────
    # Detect the explicitly-reported grand-total rows (exact, accent-free name match;
    # largest magnitude wins so a grand total beats a same-named subtotal). When found,
    # override the summed figure and rescale its sub-components proportionally so the
    # composition is preserved but the absolute scale is correct. This removes the
    # double-count that multi-sheet workbooks introduce by expanding one balance-sheet
    # line into a dedicated detail sheet.
    reported: dict[str, float] = {}
    for acc in accounts:
        nm = _norm_name(acc.normalized_account_name)
        for axis, names in _REPORTED_TOTAL_NAMES.items():
            if nm in names:
                v = float(acc.current_value)
                if axis not in reported or abs(v) > abs(reported[axis]):
                    reported[axis] = v

    summed_net_income = t.net_income  # capture before any override mutates the fields

    def _record(axis: str, summed: float, reported_val: float) -> None:
        t.reconciliation[axis] = {
            "summed": round(summed, 3),
            "reported": round(reported_val, 3),
            "overridden": True,
        }

    if "assets" in reported and t.total_assets > 0:
        summed = t.total_assets
        factor = reported["assets"] / summed
        t.total_assets = reported["assets"]
        for fld in ("current_assets", "non_current_assets", "receivables", "inventories"):
            setattr(t, fld, getattr(t, fld) * factor)
        _record("total_assets", summed, reported["assets"])

    if "liabilities" in reported and t.total_liabilities > 0:
        summed = t.total_liabilities
        factor = reported["liabilities"] / summed
        t.total_liabilities = reported["liabilities"]
        for fld in ("current_liabilities", "non_current_liabilities", "payables"):
            setattr(t, fld, getattr(t, fld) * factor)
        _record("total_liabilities", summed, reported["liabilities"])

    if "equity" in reported:
        _record("total_equity", t.total_equity, reported["equity"])
        t.total_equity = reported["equity"]

    if "revenue" in reported and t.total_revenue > 0:
        summed = t.total_revenue
        factor = reported["revenue"] / summed
        t.total_revenue = reported["revenue"]
        t.cost_of_sales = t.cost_of_sales * factor
        _record("total_revenue", summed, reported["revenue"])
        # net_income is a property (revenue − expense). When the bottom line is also
        # reported, back out the expense so the reported net result is preserved exactly;
        # otherwise rescale expense by the same factor to stay consistent with revenue.
        if "net_income" in reported:
            t.total_expense = round(t.total_revenue - reported["net_income"], 3)
            _record("net_income", summed_net_income, reported["net_income"])
        else:
            t.total_expense = t.total_expense * factor

    # Round all plain float fields
    for fname in (
        "total_assets", "total_liabilities", "total_equity",
        "total_revenue", "total_expense",
        "current_assets", "non_current_assets",
        "current_liabilities", "non_current_liabilities",
        "inventories", "cost_of_sales", "depreciation_amortization",
        "receivables", "payables",
    ):
        setattr(t, fname, round(getattr(t, fname), 3))

    return t


# ── Ratio computation ─────────────────────────────────────────────────────────

def calculate_ratios(totals: FinancialTotals) -> FinancialRatios:
    """Compute all financial ratios from aggregated totals. Safe against division by zero."""
    r = FinancialRatios()

    # Liquidity
    if totals.current_liabilities > 0:
        r.razon_corriente = round(totals.current_assets / totals.current_liabilities, 4)
        r.prueba_acida = round(
            (totals.current_assets - totals.inventories) / totals.current_liabilities, 4
        )
    r.capital_trabajo = round(totals.current_assets - totals.current_liabilities, 3)

    # Solvency
    if totals.total_assets > 0:
        r.endeudamiento_global = round(totals.total_liabilities / totals.total_assets, 4)
        r.equity_ratio = round(totals.total_equity / totals.total_assets, 4)
        if totals.net_income != 0:
            r.roa = round(totals.net_income / totals.total_assets * 100, 4)
    if totals.total_equity > 0:
        r.deuda_patrimonio = round(totals.total_liabilities / totals.total_equity, 4)
        if totals.net_income != 0:
            r.roe = round(totals.net_income / totals.total_equity * 100, 4)

    # Profitability
    if totals.total_revenue > 0:
        gross_profit = totals.total_revenue - totals.cost_of_sales
        r.margen_bruto_pct = round(gross_profit / totals.total_revenue * 100, 4)
        r.margen_neto_pct = round(totals.net_income / totals.total_revenue * 100, 4)
        r.margen_ebitda_pct = round(totals.ebitda / totals.total_revenue * 100, 4)

    # Activity — days
    if totals.total_revenue > 0 and totals.receivables > 0:
        r.dias_cartera = round(totals.receivables / totals.total_revenue * 365, 1)
    if totals.cost_of_sales > 0 and totals.payables > 0:
        r.dias_proveedores = round(totals.payables / totals.cost_of_sales * 365, 1)

    return r


# ── Serialization ─────────────────────────────────────────────────────────────

def ratios_to_dict(totals: FinancialTotals, ratios: FinancialRatios) -> dict:
    """Serialize totals + ratios for the LLM prompt and AnalyzerOutput.financial_ratios.

    The _computation_trace key documents every formula, its exact numeric inputs,
    and the result actually stored so any value can be audited or re-derived
    without re-running the pipeline.
    """
    gross_profit = totals.total_revenue - totals.cost_of_sales

    return {
        "totals": {
            "total_assets": totals.total_assets,
            "total_liabilities": totals.total_liabilities,
            "total_equity": totals.total_equity,
            "total_revenue": totals.total_revenue,
            "total_expense": totals.total_expense,
            "current_assets": totals.current_assets,
            "non_current_assets": totals.non_current_assets,
            "current_liabilities": totals.current_liabilities,
            "non_current_liabilities": totals.non_current_liabilities,
            "net_income": totals.net_income,
            "ebitda": totals.ebitda,
        },
        "ratios": {
            "razon_corriente": ratios.razon_corriente,
            "prueba_acida": ratios.prueba_acida,
            "capital_trabajo_cop_mm": ratios.capital_trabajo,
            "endeudamiento_global": ratios.endeudamiento_global,
            "deuda_patrimonio": ratios.deuda_patrimonio,
            "equity_ratio": ratios.equity_ratio,
            "margen_bruto_pct": ratios.margen_bruto_pct,
            "margen_neto_pct": ratios.margen_neto_pct,
            "margen_ebitda_pct": ratios.margen_ebitda_pct,
            "roe_pct": ratios.roe,
            "roa_pct": ratios.roa,
            "dias_cartera": ratios.dias_cartera,
            "dias_proveedores": ratios.dias_proveedores,
        },
        "_computation_trace": {
            "derived": {
                "net_income": {
                    "formula": "total_revenue - total_expense",
                    "inputs": {
                        "total_revenue": totals.total_revenue,
                        "total_expense": totals.total_expense,
                    },
                    "result": totals.net_income,
                },
                "gross_profit": {
                    "formula": "total_revenue - cost_of_sales",
                    "inputs": {
                        "total_revenue": totals.total_revenue,
                        "cost_of_sales": totals.cost_of_sales,
                    },
                    "result": round(gross_profit, 3),
                },
                "ebitda": {
                    "formula": "net_income + depreciation_amortization",
                    "inputs": {
                        "net_income": totals.net_income,
                        "depreciation_amortization": totals.depreciation_amortization,
                    },
                    "result": totals.ebitda,
                },
            },
            "ratios": {
                "razon_corriente": {
                    "formula": "current_assets / current_liabilities",
                    "inputs": {
                        "current_assets": totals.current_assets,
                        "current_liabilities": totals.current_liabilities,
                    },
                    "result": ratios.razon_corriente,
                    "computed": totals.current_liabilities > 0,
                },
                "prueba_acida": {
                    "formula": "(current_assets - inventories) / current_liabilities",
                    "inputs": {
                        "current_assets": totals.current_assets,
                        "inventories": totals.inventories,
                        "current_liabilities": totals.current_liabilities,
                    },
                    "result": ratios.prueba_acida,
                    "computed": totals.current_liabilities > 0,
                },
                "capital_trabajo": {
                    "formula": "current_assets - current_liabilities",
                    "inputs": {
                        "current_assets": totals.current_assets,
                        "current_liabilities": totals.current_liabilities,
                    },
                    "result": ratios.capital_trabajo,
                },
                "endeudamiento_global": {
                    "formula": "total_liabilities / total_assets",
                    "inputs": {
                        "total_liabilities": totals.total_liabilities,
                        "total_assets": totals.total_assets,
                    },
                    "result": ratios.endeudamiento_global,
                    "computed": totals.total_assets > 0,
                },
                "deuda_patrimonio": {
                    "formula": "total_liabilities / total_equity",
                    "inputs": {
                        "total_liabilities": totals.total_liabilities,
                        "total_equity": totals.total_equity,
                    },
                    "result": ratios.deuda_patrimonio,
                    "computed": totals.total_equity > 0,
                },
                "equity_ratio": {
                    "formula": "total_equity / total_assets",
                    "inputs": {
                        "total_equity": totals.total_equity,
                        "total_assets": totals.total_assets,
                    },
                    "result": ratios.equity_ratio,
                    "computed": totals.total_assets > 0,
                },
                "roe_pct": {
                    "formula": "(net_income / total_equity) * 100",
                    "inputs": {
                        "net_income": totals.net_income,
                        "total_equity": totals.total_equity,
                    },
                    "result": ratios.roe,
                    "computed": totals.total_equity > 0 and totals.net_income != 0,
                },
                "roa_pct": {
                    "formula": "(net_income / total_assets) * 100",
                    "inputs": {
                        "net_income": totals.net_income,
                        "total_assets": totals.total_assets,
                    },
                    "result": ratios.roa,
                    "computed": totals.total_assets > 0 and totals.net_income != 0,
                },
                "margen_bruto_pct": {
                    "formula": "((total_revenue - cost_of_sales) / total_revenue) * 100",
                    "inputs": {
                        "total_revenue": totals.total_revenue,
                        "cost_of_sales": totals.cost_of_sales,
                        "gross_profit": round(gross_profit, 3),
                    },
                    "result": ratios.margen_bruto_pct,
                    "computed": totals.total_revenue > 0,
                },
                "margen_neto_pct": {
                    "formula": "(net_income / total_revenue) * 100",
                    "inputs": {
                        "net_income": totals.net_income,
                        "total_revenue": totals.total_revenue,
                    },
                    "result": ratios.margen_neto_pct,
                    "computed": totals.total_revenue > 0,
                },
                "margen_ebitda_pct": {
                    "formula": "(ebitda / total_revenue) * 100",
                    "inputs": {
                        "ebitda": totals.ebitda,
                        "total_revenue": totals.total_revenue,
                    },
                    "result": ratios.margen_ebitda_pct,
                    "computed": totals.total_revenue > 0,
                },
                "dias_cartera": {
                    "formula": "(receivables / total_revenue) * 365",
                    "inputs": {
                        "receivables": totals.receivables,
                        "total_revenue": totals.total_revenue,
                    },
                    "result": ratios.dias_cartera,
                    "computed": totals.total_revenue > 0 and totals.receivables > 0,
                },
                "dias_proveedores": {
                    "formula": "(payables / cost_of_sales) * 365",
                    "inputs": {
                        "payables": totals.payables,
                        "cost_of_sales": totals.cost_of_sales,
                    },
                    "result": ratios.dias_proveedores,
                    "computed": totals.cost_of_sales > 0 and totals.payables > 0,
                },
            },
        },
    }


# ── NIIF 18 — P&L category classification ────────────────────────────────────

class PLCategory(str, Enum):
    """NIIF 18 mandatory P&L categories — keyword-based, deterministic."""
    OPERATING = "operating"
    INVESTING = "investing"
    FINANCING = "financing"
    TAXES = "taxes"
    DISCONTINUED = "discontinued"


class OCIType(str, Enum):
    """Other Comprehensive Income (ORI) classification per NIIF 18."""
    RECYCLABLE = "recyclable"         # Reclassified to P&L in a future period
    NON_RECYCLABLE = "non_recyclable" # Permanently stays in OCI
    NOT_OCI = "not_oci"              # Not an OCI item


# Keyword lists — ordered most-specific to least-specific inside each category.
# classify_pl_category() checks in this same order: TAXES → DISCONTINUED → INVESTING → FINANCING → OPERATING.

_TAXES_KEYWORDS = (
    "impuesto de renta", "impuesto sobre la renta",
    "impuesto corriente", "gasto por impuesto",
    "provision de impuesto", "impuesto diferido por resultados",
)
_DISCONTINUED_KEYWORDS = (
    "operacion discontinuada", "operaciones discontinuadas",
    "componente discontinuado", "actividad discontinuada",
)
_INVESTING_KEYWORDS = (
    "dividendos recibidos", "intereses de inversiones",
    "ingresos por inversiones", "ganancia por disposicion",
    "ganancia en venta de activo", "perdida por disposicion",
    "perdida en venta de activo", "resultado de inversiones",
    "rendimiento de inversiones", "valorizacion de inversiones",
    "participacion en subsidiarias", "metodo de participacion",
)
_FINANCING_KEYWORDS = (
    "gasto financiero", "gastos financieros", "intereses de deuda",
    "intereses pagados", "costos de financiamiento", "costo financiero",
    "gastos por intereses", "intereses por pagar",
    "ingreso financiero", "intereses ganados de deuda",
)
_OCI_RECYCLABLE_KEYWORDS = (
    "diferencia de conversion", "cobertura de flujos de efectivo",
    "activos financieros a valor razonable con cambios en ori",
    "instrumento de cobertura", "coberturas de inversion neta",
    "ganancia o perdida de cobertura",
)
_OCI_NON_RECYCLABLE_KEYWORDS = (
    "remedicion de planes de beneficio definido", "ganancias actuariales",
    "cambios en valor razonable de instrumentos de patrimonio",
    "opcion de valor razonable", "superavit de revaluacion",
    "revaluacion de propiedad planta y equipo",
)


@dataclass
class NIIF18Subtotals:
    """
    NIIF 18 mandatory P&L subtotals — all in COP MM.
    The four subtotals are cumulative from top to bottom of the P&L.
    """
    # Per-category net results (revenue positive, expense negative)
    resultado_operativo: float = 0.0          # NIIF 18 mandatory subtotal 1
    resultado_inversion: float = 0.0          # Net investing result
    resultado_financiamiento: float = 0.0     # Net financing result
    impuestos: float = 0.0                    # Income tax expense (positive = expense)
    resultado_operaciones_disc: float = 0.0   # Discontinued operations
    # 4 mandatory cumulative subtotals (NIIF 18 paragraphs 60-65)
    resultado_antes_fin_imp: float = 0.0      # = operativo + inversion
    resultado_antes_impuestos: float = 0.0    # = antes_fin_imp + financiamiento
    resultado_neto: float = 0.0               # = antes_impuestos − impuestos + disc.
    # NIIF 18 EBITDA MPM — uses resultado_operativo as base (not net_income)
    ebitda_niif18: float = 0.0


def classify_pl_category(account_name: str, category: str) -> PLCategory:
    """
    Classify a P&L account into one of the 5 NIIF 18 mandatory categories.

    Uses keyword matching in specificity order: TAXES → DISCONTINUED →
    INVESTING → FINANCING → OPERATING (default).
    Operates only on names; category param is kept for future extension.
    """
    name = account_name.lower()
    if _matches(name, _TAXES_KEYWORDS):
        return PLCategory.TAXES
    if _matches(name, _DISCONTINUED_KEYWORDS):
        return PLCategory.DISCONTINUED
    if _matches(name, _INVESTING_KEYWORDS):
        return PLCategory.INVESTING
    if _matches(name, _FINANCING_KEYWORDS):
        return PLCategory.FINANCING
    return PLCategory.OPERATING


def calculate_niif18_subtotals(
    accounts: List[ExtractedAccount],
    depreciation_amortization: float = 0.0,
    leaf_ids: set[str] | None = None,
) -> NIIF18Subtotals:
    """
    Compute NIIF 18 mandatory P&L subtotals from the accounts list.

    Only P&L *line items* are processed (revenue/expense, leaf rows, non-subtotals):
      - "other" rows are statement-of-changes / cash-flow lines (opening NAV, contributions,
        redemptions), NOT P&L — including them inflated resultado_operativo to a 1067% margin.
      - is_total rows and running-total subtotals ("Utilidad operacional", "Utilidad del
        período") are excluded so they are not summed with their own components.
      - `leaf_ids` (from hierarchy_engine) drops a statement line whose breakdown lives on a
        detail sheet, so the line and its per-item rows are not both counted.

    Revenue values are signed (a valuation loss reduces the result); expense magnitudes
    always reduce the result regardless of source sign convention.
    EBITDA uses resultado_operativo as base per NIIF 18 MPM rules.
    """
    s = NIIF18Subtotals()
    cat_net: dict[PLCategory, float] = {c: 0.0 for c in PLCategory}

    for acc in accounts:
        cat = acc.category.lower()
        if cat not in ("revenue", "expense"):
            continue
        if acc.is_total:
            continue
        if leaf_ids is not None and acc.account_id not in leaf_ids:
            continue
        if _norm_name(acc.normalized_account_name) in _PL_SUBTOTAL_NAMES:
            continue
        pl_cat = classify_pl_category(acc.normalized_account_name, cat)
        val = float(acc.current_value)
        if cat == "expense":
            cat_net[pl_cat] -= abs(val)
        else:
            cat_net[pl_cat] += val

    s.resultado_operativo = round(cat_net[PLCategory.OPERATING], 3)
    s.resultado_inversion = round(cat_net[PLCategory.INVESTING], 3)
    s.resultado_financiamiento = round(cat_net[PLCategory.FINANCING], 3)
    # Tax is stored as a positive expense amount for readability
    s.impuestos = round(-cat_net[PLCategory.TAXES], 3)
    s.resultado_operaciones_disc = round(cat_net[PLCategory.DISCONTINUED], 3)

    s.resultado_antes_fin_imp = round(
        s.resultado_operativo + s.resultado_inversion, 3
    )
    s.resultado_antes_impuestos = round(
        s.resultado_antes_fin_imp + s.resultado_financiamiento, 3
    )
    s.resultado_neto = round(
        s.resultado_antes_impuestos - s.impuestos + s.resultado_operaciones_disc, 3
    )
    # NIIF 18 EBITDA: resultado_operativo + D&A (NOT net_income + D&A)
    s.ebitda_niif18 = round(s.resultado_operativo + depreciation_amortization, 3)

    return s


def classify_oci(account_name: str) -> OCIType:
    """
    Classify an account as recyclable OCI, non-recyclable OCI, or not OCI.
    Based on NIIF 18 / NIC 1 OCI presentation requirements.
    """
    name = account_name.lower()
    if _matches(name, _OCI_RECYCLABLE_KEYWORDS):
        return OCIType.RECYCLABLE
    if _matches(name, _OCI_NON_RECYCLABLE_KEYWORDS):
        return OCIType.NON_RECYCLABLE
    return OCIType.NOT_OCI


def calculate_total_comprehensive_income(
    resultado_neto: float,
    accounts: List[ExtractedAccount],
) -> dict:
    """
    Compute Total Comprehensive Income (TRI) = resultado_neto + OCI.

    OCI items are detected by keyword-matching all accounts regardless of category,
    since some extractors place OCI accounts under 'equity' or 'other'.

    Args:
        resultado_neto: NIIF 18 resultado_neto (from NIIF18Subtotals).
    """
    oci_recyclable = 0.0
    oci_non_recyclable = 0.0

    for acc in accounts:
        oci_type = classify_oci(acc.normalized_account_name)
        if oci_type == OCIType.NOT_OCI:
            continue
        val = float(acc.current_value)
        if oci_type == OCIType.RECYCLABLE:
            oci_recyclable += val
        else:
            oci_non_recyclable += val

    total_oci = round(oci_recyclable + oci_non_recyclable, 3)
    return {
        "resultado_neto": round(resultado_neto, 3),
        "oci_recyclable": round(oci_recyclable, 3),
        "oci_non_recyclable": round(oci_non_recyclable, 3),
        "total_oci": total_oci,
        "total_comprehensive_income": round(resultado_neto + total_oci, 3),
    }
