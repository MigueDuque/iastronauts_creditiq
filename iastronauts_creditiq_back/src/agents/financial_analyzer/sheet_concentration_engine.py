"""
sheet_concentration_engine.py  —  Sheet-based Concentration Analysis

Produces three concentration views using the source_sheet metadata on ExtractedAccount
(populated by Agent 1 from Excel sheet name markers):

  1. Asset concentration   — balance-sheet accounts grouped into asset categories
                             (Efectivo, Instrumentos financieros, Cuentas por cobrar, Otros)
  2. Instrument concentration — Inversiones/INSTRUMENTOS sheet accounts grouped by
                             instrument type (Deuda / Patrimonio / Derechos fiduciarios /
                             Compromisos futuros) and, within each type, by emisor
  3. Bank concentration    — Efectivo/EFECTIVO sheet accounts, one row per bank

All logic is purely deterministic. No LLM calls.
"""

from dataclasses import dataclass, field

from shared.models import ExtractedAccount


# ── Sheet name matchers ───────────────────────────────────────────────────────

_BALANCE_KW: tuple[str, ...] = (
    "balance",
    "estado situacion",
    "estado de situacion",
    "situacion financiera",
    "estado financiero",
)
_INVESTMENT_KW: tuple[str, ...] = (
    "inversion",
    "inversiones",
    "instrumentos",
    "portafolio",
)
_CASH_KW: tuple[str, ...] = (
    "efectivo",
    "caja",
)


# ── Asset-type keywords (applied to account names in the balance sheet) ───────

_ASSET_TYPE_MAP: list[tuple[str, tuple[str, ...]]] = [
    ("Efectivo y equivalentes", (
        "efectivo", "caja", "banco ", "bancos", "equivalente de efectivo",
        "deposito", "depósito",
    )),
    ("Instrumentos financieros de inversión", (
        "instrumento financiero", "inversion", "inversión", "portafolio",
        "titulo", "título", "acciones", "renta fija", "renta variable",
        "inversiones",
    )),
    ("Cuentas por cobrar", (
        "cuenta por cobrar", "cuentas por cobrar", "cartera", "deudor",
        "deudores", "por cobrar", "cobrar",
    )),
]
_ASSET_OTHER = "Otros activos"

# ── Instrument-type keywords (applied to accounts in the Inversiones sheet) ───

_INSTRUMENT_TYPE_MAP: list[tuple[str, tuple[str, ...]]] = [
    ("Instrumentos de Deuda", (
        "bono", "tes", "cdt", "deuda", "renta fija", "titulo de deuda",
        "título de deuda", "obligacion", "obligación", "deuda publica",
        "deuda pública", "credit", "tasa fija",
    )),
    ("Instrumentos de Patrimonio", (
        "accion", "acción", "acciones", "renta variable", "equity",
        "bolsa", "patrimon",
    )),
    ("Derechos Fiduciarios", (
        "fideicomiso", "fiduciario", "fiduciaria", "derecho fiduciario",
        "derechos fiduciarios",
    )),
    ("Compromisos Futuros", (
        "futuro", "forward", "derivado", "swap", "opcion", "opción",
        "compromiso",
    )),
]
_INSTRUMENT_OTHER = "Otros instrumentos"

# investment_type from Agent 1 prompt → canonical concentration bucket
# Takes priority over keyword matching when the field is present and non-null.
_INVESTMENT_TYPE_TO_BUCKET: dict[str, str] = {
    "bond":          "Instrumentos de Deuda",
    "sovereign_debt":"Instrumentos de Deuda",
    "equity":        "Instrumentos de Patrimonio",
    "private_equity":"Instrumentos de Patrimonio",
    "trust_rights":  "Derechos Fiduciarios",
    "futures":       "Compromisos Futuros",
    # "fund" uses keyword fallback (see loop below); FIC participaciones in equity
    # funds are typically classified as Instrumentos de Patrimonio in Colombian NIIF.
    "cash":          _INSTRUMENT_OTHER,
}


# ── Normalisation helper ──────────────────────────────────────────────────────

_ACCENT_MAP = str.maketrans("áéíóúÁÉÍÓÚñÑ", "aeiouAEIOUnN")


def _norm(s: str) -> str:
    return s.lower().translate(_ACCENT_MAP)


def _sheet_matches(sheet: str | None, keywords: tuple[str, ...]) -> bool:
    if not sheet:
        return False
    s = _norm(sheet)
    return any(k in s for k in keywords)


def _classify_name(name: str, type_map: list[tuple[str, tuple[str, ...]]], default: str) -> str:
    n = _norm(name)
    for label, keywords in type_map:
        if any(k in n for k in keywords):
            return label
    return default


def _try_classify_name(name: str, type_map: list[tuple[str, tuple[str, ...]]]) -> str | None:
    """Like _classify_name but returns None instead of a default when no keyword matches."""
    n = _norm(name)
    for label, keywords in type_map:
        if any(k in n for k in keywords):
            return label
    return None


# ── Output structures ─────────────────────────────────────────────────────────

@dataclass
class ConcentrationRow:
    name: str
    value: float      # COP MM
    pct: float        # % of the view total


@dataclass
class InstrumentTypeRow:
    instrument_type: str
    total: float
    pct: float
    emisores: list[ConcentrationRow] = field(default_factory=list)


@dataclass
class SheetConcentrationResult:
    # Balance-sheet asset view
    asset_total: float
    asset_breakdown: list[ConcentrationRow]
    asset_available: bool

    # Inversiones/INSTRUMENTOS view
    instrument_total: float
    instrument_breakdown: list[InstrumentTypeRow]
    instrument_available: bool

    # Efectivo view
    bank_total: float
    bank_breakdown: list[ConcentrationRow]
    bank_available: bool


# ── Main public function ──────────────────────────────────────────────────────

def analyze_sheet_concentration(accounts: list[ExtractedAccount]) -> SheetConcentrationResult:
    """
    Partition ExtractedAccount list into three concentration views based on source_sheet.
    Accounts with no source_sheet (PDF/CSV) are silently ignored — the views will be
    marked as unavailable if no matching sheet accounts are found.
    """
    # Partition into three pools — mutual-exclusive priority:
    #   cash sheet > investment sheet > balance sheet for ambiguous names
    balance: list[ExtractedAccount] = []
    investment: list[ExtractedAccount] = []
    cash: list[ExtractedAccount] = []

    for acc in accounts:
        if acc.current_value <= 0 or acc.is_total:
            continue
        if _sheet_matches(acc.source_sheet, _CASH_KW):
            cash.append(acc)
        elif _sheet_matches(acc.source_sheet, _INVESTMENT_KW):
            investment.append(acc)
        elif _sheet_matches(acc.source_sheet, _BALANCE_KW) and acc.category == "assets":
            balance.append(acc)

    # ── 1. Asset concentration ────────────────────────────────────────────────
    asset_buckets: dict[str, float] = {}
    for acc in balance:
        label = _classify_name(acc.normalized_account_name, _ASSET_TYPE_MAP, _ASSET_OTHER)
        asset_buckets[label] = asset_buckets.get(label, 0.0) + acc.current_value

    asset_total = sum(asset_buckets.values())
    denom = max(asset_total, 0.001)
    asset_breakdown = sorted(
        [
            ConcentrationRow(
                name=k,
                value=round(v, 2),
                pct=round(v / denom * 100, 1),
            )
            for k, v in asset_buckets.items()
        ],
        key=lambda r: -r.value,
    )

    # ── 2. Instrument concentration ───────────────────────────────────────────
    # type → {emisor_name → total_value}
    inst_map: dict[str, dict[str, float]] = {}
    for acc in investment:
        # Prefer investment_type from Agent 1 (more reliable than name keywords).
        if acc.investment_type and acc.investment_type in _INVESTMENT_TYPE_TO_BUCKET:
            inst_type = _INVESTMENT_TYPE_TO_BUCKET[acc.investment_type]
        elif acc.investment_type == "fund":
            # FIC participaciones: try keyword matching on account name first.
            # Colombian equity funds are often classified as Instrumentos de Patrimonio
            # in NIIF statements; default to that when no specific keyword matches.
            keyword_match = _try_classify_name(acc.normalized_account_name, _INSTRUMENT_TYPE_MAP)
            inst_type = keyword_match if keyword_match else "Instrumentos de Patrimonio"
        else:
            inst_type = _classify_name(
                acc.normalized_account_name, _INSTRUMENT_TYPE_MAP, _INSTRUMENT_OTHER
            )
        # Prefer issuer_name from Agent 1 (cleaner than normalized_account_name).
        emisor = acc.issuer_name if acc.issuer_name else acc.normalized_account_name
        type_bucket = inst_map.setdefault(inst_type, {})
        type_bucket[emisor] = type_bucket.get(emisor, 0.0) + acc.current_value

    instrument_total = sum(
        sum(emisores.values()) for emisores in inst_map.values()
    )
    inst_denom = max(instrument_total, 0.001)

    instrument_breakdown = sorted(
        [
            InstrumentTypeRow(
                instrument_type=inst_type,
                total=round(sum(emisores.values()), 2),
                pct=round(sum(emisores.values()) / inst_denom * 100, 1),
                emisores=sorted(
                    [
                        ConcentrationRow(
                            name=e,
                            value=round(v, 2),
                            pct=round(v / max(sum(emisores.values()), 0.001) * 100, 1),
                        )
                        for e, v in emisores.items()
                    ],
                    key=lambda r: -r.value,
                ),
            )
            for inst_type, emisores in inst_map.items()
        ],
        key=lambda r: -r.total,
    )

    # ── 3. Bank concentration ─────────────────────────────────────────────────
    bank_buckets: dict[str, float] = {}
    for acc in cash:
        bank_buckets[acc.normalized_account_name] = (
            bank_buckets.get(acc.normalized_account_name, 0.0) + acc.current_value
        )

    bank_total = sum(bank_buckets.values())
    bank_denom = max(bank_total, 0.001)
    bank_breakdown = sorted(
        [
            ConcentrationRow(
                name=k,
                value=round(v, 2),
                pct=round(v / bank_denom * 100, 1),
            )
            for k, v in bank_buckets.items()
        ],
        key=lambda r: -r.value,
    )

    return SheetConcentrationResult(
        asset_total=round(asset_total, 2),
        asset_breakdown=asset_breakdown,
        asset_available=bool(balance),
        instrument_total=round(instrument_total, 2),
        instrument_breakdown=instrument_breakdown,
        instrument_available=bool(investment),
        bank_total=round(bank_total, 2),
        bank_breakdown=bank_breakdown,
        bank_available=bool(cash),
    )


def sheet_concentration_to_dict(result: SheetConcentrationResult) -> dict:
    """Serialize SheetConcentrationResult to a JSON-safe dict for AnalyzerOutput."""
    return {
        "asset_total": result.asset_total,
        "asset_breakdown": [
            {"name": r.name, "value": r.value, "pct": r.pct}
            for r in result.asset_breakdown
        ],
        "asset_available": result.asset_available,
        "instrument_total": result.instrument_total,
        "instrument_breakdown": [
            {
                "instrument_type": ib.instrument_type,
                "total": ib.total,
                "pct": ib.pct,
                "emisores": [
                    {"name": e.name, "value": e.value, "pct": e.pct}
                    for e in ib.emisores
                ],
            }
            for ib in result.instrument_breakdown
        ],
        "instrument_available": result.instrument_available,
        "bank_total": result.bank_total,
        "bank_breakdown": [
            {"name": r.name, "value": r.value, "pct": r.pct}
            for r in result.bank_breakdown
        ],
        "bank_available": result.bank_available,
    }
