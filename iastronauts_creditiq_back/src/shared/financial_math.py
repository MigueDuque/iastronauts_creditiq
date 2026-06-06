import logging
from typing import List, Dict, Any, Tuple
from shared.models import ExtractedAccount
from shared.models.base import MaterialityLevel

logger = logging.getLogger("financial_math")
logger.setLevel(logging.INFO)

# ── Cash-flow / equity-changes exclusion ─────────────────────────────────────
# These accounts express movements over a period, not closing positions.
# They must not enter top-variations, materiality rankings, or concentration
# metrics. Do NOT use this predicate in liquidity / cash-flow analysis paths.

# Per-account statement_type values that are always movements, never positions.
_MOVEMENT_STATEMENT_TYPES: frozenset[str] = frozenset({"cash_flow", "equity_changes"})

_CASH_FLOW_SHEET_KEYWORDS: tuple[str, ...] = (
    "flujo de efectivo",
    "flujo de caja",
    "cash flow",
    "estado de flujos",
    "flujos de efectivo",
    "flujos de caja",
    # common abbreviations used by Colombian fund administrators
    "efe",   # Estado de Flujos de Efectivo
    "efnc",  # Estado de Flujos No Corrientes
    "effe",
    "efc",   # Estado de Flujos de Caja
)
_CASH_FLOW_NAME_KEYWORDS: tuple[str, ...] = (
    "flujo de efectivo",
    "flujo neto",
    "flujo de caja",
    "cash flow",
    "net cash",
    "efectivo inicial",
    "efectivo final",
    "aumento neto de efectivo",
    "variación de efectivo",
    "flujo neto de",
    # typical cash-flow line items in Colombian fund EEFF
    "actividades de operación",
    "actividades de inversión",
    "actividades de financiación",
    "actividades operativas",
    "actividades de financiamiento",
    "incorporaciones en inversiones",
    "enajenaciones de inversiones",
    "distribución de rendimientos",
    "rendimientos distribuidos",
    # equity-changes statement items (also movements, not positions)
    "aportes de inversionistas",
    "retiros de inversionistas",
    "patrimonio neto inicial",
    "patrimonio neto final",
    "saldo inicial del período",
    "saldo final del período",
)


def is_cash_flow_account(account) -> bool:
    """
    Return True when the account is a movement row (cash-flow or equity-changes
    statement) — not a balance-sheet closing position.

    Accepts ExtractedAccount, AccountVariation, or any object with the relevant
    attributes. Check order (strongest signal first):
      1. statement_type field explicitly set by the extractor LLM
      2. source_sheet name keyword match (Excel only)
      3. account name keyword fallback (PDF / CSV)
    """
    # 1. Explicit per-account type set by extractor LLM — most reliable
    stmt = getattr(account, "statement_type", None) or ""
    if stmt in _MOVEMENT_STATEMENT_TYPES:
        return True

    # 2. Excel sheet name (strong signal when source_sheet is present)
    sheet = getattr(account, "source_sheet", None) or ""
    if any(kw in sheet.lower() for kw in _CASH_FLOW_SHEET_KEYWORDS):
        return True

    # 3. Account name keywords (fallback for PDF / when statement_type is absent)
    name = (
        getattr(account, "normalized_account_name", None)
        or getattr(account, "account_name", None)
        or ""
    )
    return any(kw in name.lower() for kw in _CASH_FLOW_NAME_KEYWORDS)


# ── Equity-flow exclusion ─────────────────────────────────────────────────────
# These equity-category accounts come from the statement of changes in equity
# (aportes, retiros, opening/closing balances, period result).  Including them
# in total_equity double-counts values already reflected in the balance-sheet
# equity and produces equity_ratio > 1.0, which is mathematically impossible.
_EQUITY_FLOW_KEYWORDS: tuple[str, ...] = (
    "aportes de inversionistas",
    "aporte inversionistas",
    "retiros de inversionistas",
    "retiro inversionistas",
    "patrimonio neto inicial",
    "patrimonio neto final",
    "saldo inicial",
    "suscripciones",
    "redenciones",
    "utilidad del período",
    "ganancia del período",
    "pérdida del período",
    "resultado del período",
)


def calculate_variations(current_val: float, previous_val: float | None) -> Tuple[float, float]:
    """
    Calcula la variación absoluta y porcentual de una cuenta.
    Retorna (absolute_variation, variation_pct).
    """
    if previous_val is None:
        return 0.0, 0.0
    
    abs_var = current_val - previous_val
    if previous_val == 0.0:
        # Si la base anterior es cero, la variación porcentual se define como 0.0 para evitar infinito
        pct_var = 0.0
    else:
        pct_var = (abs_var / previous_val) * 100.0
        
    return round(abs_var, 3), round(pct_var, 2)


def get_inventories_total(accounts: List[ExtractedAccount]) -> float:
    """Suma todas las cuentas que pertenezcan a inventarios."""
    total = 0.0
    for acc in accounts:
        name = acc.normalized_account_name.lower()
        cat = acc.category.lower()
        if cat == "assets" and ("inventar" in name or "existencia" in name or "mercanc" in name):
            total += acc.current_value
    return total


def get_cost_of_sales_total(accounts: List[ExtractedAccount]) -> float:
    """Suma los costos de ventas / costo directo de las cuentas de gasto."""
    total = 0.0
    for acc in accounts:
        name = acc.normalized_account_name.lower()
        cat = acc.category.lower()
        if cat == "expense" and ("costo de venta" in name or "costo de operac" in name or "costo directo" in name):
            total += acc.current_value
    return total


def classify_current_noncurrent(accounts: List[ExtractedAccount]) -> Tuple[float, float, float, float]:
    """
    Clasifica de forma determinista las cuentas en Corrientes y No Corrientes
    utilizando palabras clave estándar bajo NIIF en español.
    Retorna (current_assets, non_current_assets, current_liabilities, non_current_liabilities).
    """
    current_assets = 0.0
    non_current_assets = 0.0
    current_liabilities = 0.0
    non_current_liabilities = 0.0

    for acc in accounts:
        if acc.is_total:
            continue
        cat = acc.category.lower()
        name = acc.normalized_account_name.lower()
        val = acc.current_value

        if cat == "assets":
            if ("no corriente" in name or "no_corriente" in name or
                    "propiedad" in name or "planta" in name or "equipo" in name or "intangible" in name or
                    "largo plazo" in name or "diferido" in name or "valorizac" in name):
                non_current_assets += val
            else:
                current_assets += val

        elif cat == "liabilities":
            if ("no corriente" in name or "no_corriente" in name or
                    "largo plazo" in name or "largo_plazo" in name or "pasivo diferido" in name):
                non_current_liabilities += val
            else:
                current_liabilities += val
                
    return (
        round(current_assets, 3),
        round(non_current_assets, 3),
        round(current_liabilities, 3),
        round(non_current_liabilities, 3)
    )


def calculate_financial_ratios(accounts: List[ExtractedAccount]) -> Dict[str, Any]:
    """
    Calcula determinísticamente los principales ratios financieros.
    Retorna un diccionario estructurado con los ratios.
    """
    # 1. Agrupar totales generales por categoría
    # Skip is_total rows to avoid double-counting detail rows + their sum row.
    total_assets = 0.0
    total_liabilities = 0.0
    total_equity = 0.0
    total_revenue = 0.0
    total_expense = 0.0

    for acc in accounts:
        if acc.is_total:
            continue
        cat = acc.category.lower()
        val = acc.current_value
        if cat == "assets":
            total_assets += val
        elif cat == "liabilities":
            total_liabilities += val
        elif cat == "equity":
            name_lower = acc.normalized_account_name.lower()
            if not any(kw in name_lower for kw in _EQUITY_FLOW_KEYWORDS):
                total_equity += val
        elif cat == "revenue":
            total_revenue += val
        elif cat == "expense":
            total_expense += val

    # 2. Corrientes vs No Corrientes
    curr_assets, non_curr_assets, curr_liab, non_curr_liab = classify_current_noncurrent(accounts)
    inventories = get_inventories_total(accounts)
    cost_of_sales = get_cost_of_sales_total(accounts)
    
    # 3. Ratios de Liquidez
    razon_corriente = round(curr_assets / curr_liab, 2) if curr_liab > 0 else 0.0
    prueba_acida = round((curr_assets - inventories) / curr_liab, 2) if curr_liab > 0 else 0.0
    capital_trabajo = round(curr_assets - curr_liab, 3)
    
    # 4. Ratios de Solvencia / Endeudamiento
    deuda_patrimonio = round(total_liabilities / total_equity, 2) if total_equity > 0 else 0.0
    endeudamiento_global = round(total_liabilities / total_assets, 2) if total_assets > 0 else 0.0
    
    # 5. Ratios de Rentabilidad
    # Margen Bruto: (Ventas - Costo de Ventas) / Ventas
    margen_bruto = round(((total_revenue - cost_of_sales) / total_revenue) * 100.0, 2) if total_revenue > 0 else 0.0
    
    # Utilidad neta o margen neto
    # Bajo NIIF: Utilidad = Ingresos - Gastos
    net_income = total_revenue - total_expense
    margen_neto = round((net_income / total_revenue) * 100.0, 2) if total_revenue > 0 else 0.0
    
    # EBITDA aproximado (Utilidad Operativa + Depreciación/Amortización si viene en gastos)
    # Buscamos dep/amort en los gastos para sumarla
    deprec = 0.0
    for acc in accounts:
        name = acc.normalized_account_name.lower()
        if acc.category.lower() == "expense" and ("depreciac" in name or "amortizac" in name):
            deprec += acc.current_value
            
    ebitda = round(net_income + deprec, 3)
    margen_ebitda = round((ebitda / total_revenue) * 100.0, 2) if total_revenue > 0 else 0.0

    return {
        "totals": {
            "total_assets": round(total_assets, 3),
            "total_liabilities": round(total_liabilities, 3),
            "total_equity": round(total_equity, 3),
            "total_revenue": round(total_revenue, 3),
            "total_expense": round(total_expense, 3),
            "current_assets": curr_assets,
            "non_current_assets": non_curr_assets,
            "current_liabilities": curr_liab,
            "non_current_liabilities": non_curr_liab,
            "inventories": round(inventories, 3),
            "cost_of_sales": round(cost_of_sales, 3),
            "net_income": round(net_income, 3),
            "ebitda": ebitda
        },
        "ratios": {
            "razon_corriente": razon_corriente,
            "prueba_acida": prueba_acida,
            "capital_trabajo": capital_trabajo,
            "deuda_patrimonio": deuda_patrimonio,
            "endeudamiento_global": endeudamiento_global,
            "margen_bruto_pct": margen_bruto,
            "margen_neto_pct": margen_neto,
            "margen_ebitda_pct": margen_ebitda
        }
    }


def determine_materiality_threshold(accounts: List[ExtractedAccount]) -> float:
    """
    Calcula el umbral de materialidad global para la auditoría contable.
    Estándar: 1% del mayor entre Activos Totales e Ingresos Totales.
    """
    total_assets = 0.0
    total_revenue = 0.0
    for acc in accounts:
        if acc.is_total:
            continue
        cat = acc.category.lower()
        val = acc.current_value
        if cat == "assets":
            total_assets += val
        elif cat == "revenue":
            total_revenue += val
            
    base = max(total_assets, total_revenue)
    threshold = base * 0.01  # 1% de materialidad contable
    
    # Umbral mínimo de salvaguarda: si los EEFF están en ceros o valores mínimos
    return max(threshold, 1.0)  # Equivalente a 1.0 COP MM


def get_materiality_level(abs_var: float, threshold: float) -> MaterialityLevel:
    """Asigna el nivel de materialidad según la variación absoluta de la cuenta."""
    abs_var_value = abs(abs_var)
    if abs_var_value >= threshold:
        return MaterialityLevel.HIGH
    elif abs_var_value >= threshold * 0.5:
        return MaterialityLevel.MEDIUM
    return MaterialityLevel.LOW
