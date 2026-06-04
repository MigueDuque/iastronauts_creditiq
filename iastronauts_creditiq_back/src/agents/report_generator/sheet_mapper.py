"""
sheet_mapper.py — Maps actual Excel sheet names (from Agent 1) to CreditIQ template table prefixes.

Strategy:
  1. Keyword scoring — normalized lowercase substring matching against canonical keyword sets.
  2. LLM disambiguation — single JSON call, only when keyword scoring leaves canonical
     sheets unmatched AND there are still free (unassigned) actual sheet names.

Call match_sheets() with the list of actual sheet names found in the extractor output.
It returns {canonical_key: actual_sheet_name | None} for every canonical sheet.
"""
from __future__ import annotations

import logging
import unicodedata
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from shared.llm_provider import LLMProvider

logger = logging.getLogger("report_generator.sheet_mapper")

# Defines the six table-driven sections the template populates from raw Excel sheets.
# Other template sections (risk, materiality, market risk, validation) come from
# downstream agent outputs and are NOT driven by sheet mapping.
CANONICAL_SHEETS: dict[str, dict] = {
    "BALANCE": {
        "table_prefix": "BS",
        "table_name": "Balance General / Estado de Situación Financiera",
        "keywords": ["balance", "situacion", "estado situacion", "posicion financiera"],
        "n_cols": 6,
    },
    "ESTADO_RESULTADOS": {
        "table_prefix": "IS_ACC",
        "table_name": "Resultados Acumulados / Estado de Resultados",
        "keywords": ["resultado", "pyg", "p&g", "p.y.g", "p y g", "estado resultado"],
        "n_cols": 5,
    },
    "INSTRUMENTOS": {
        "table_prefix": "PORTFOLIO",
        "table_name": "Composición del Portafolio / Instrumentos de Inversión",
        "keywords": [
            "instrumento", "invers", "portafolio", "portfolio",
            "p.dinamico", "p.inmobiliario", "dinamico", "inmobiliario",
        ],
        "n_cols": 6,
    },
    "VALOR_RAZONABLE_ACTIVOS": {
        "table_prefix": "FV_HIER",
        "table_name": "Valor Razonable de Activos (NIIF 13)",
        "keywords": ["valor razonable", "fair value", "vr activo", "jerarquia valor"],
        "n_cols": 5,
    },
    "ACTIVOS_NETOS": {
        "table_prefix": "NAV_CLASS",
        "table_name": "Composición por Clase / Activos Netos",
        "keywords": ["activo neto", "activos netos", "por clase", "composicion clase"],
        "n_cols": 6,
    },
    "FLUJO_EFECTIVO": {
        "table_prefix": "NAV_FLOW",
        "table_name": "Movimientos del Periodo / Flujo de Efectivo",
        "keywords": ["flujo", "efectivo", "movimiento", "suscripcion", "redencion", "cash flow"],
        "n_cols": 4,
    },
}


def _normalize(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text.lower())
    stripped = "".join(c for c in nfkd if not unicodedata.combining(c))
    # Treat separators (_, -, ., /) as spaces so multi-word keywords like
    # "valor razonable" match sheet names like "VALOR_RAZONABLE_JERARQUIA".
    for sep in ("_", "-", ".", "/", "\\"):
        stripped = stripped.replace(sep, " ")
    return " ".join(stripped.split())


def _keyword_score(sheet_name: str, keywords: list[str]) -> int:
    n = _normalize(sheet_name)
    score = sum(1 for k in keywords if k in n)
    # Extra weight when the canonical key itself (underscores → spaces) appears verbatim
    return score


def match_sheets(
    actual_sheet_names: list[str],
    llm: "LLMProvider | None" = None,
    tenant_id: str | None = None,
    job_id: str | None = None,
) -> dict[str, str | None]:
    """Return {canonical_key: matched_actual_sheet_name | None} for every canonical sheet.

    Each actual sheet name is assigned to at most one canonical key (greedy highest-score).
    LLM is called only for canonical keys that keyword scoring could not resolve when
    there are still unassigned actual sheets available.
    """
    result: dict[str, str | None] = {k: None for k in CANONICAL_SHEETS}

    if not actual_sheet_names:
        return result

    # --- Tier 1: keyword scoring ---
    # scores[canonical_key][actual_name] = score
    scores: dict[str, dict[str, int]] = {}
    for canonical_key, meta in CANONICAL_SHEETS.items():
        row: dict[str, int] = {}
        for actual in actual_sheet_names:
            s = _keyword_score(actual, meta["keywords"])
            # Bonus: canonical key (e.g. "BALANCE") is a verbatim substring of the sheet name
            canon_normalized = _normalize(canonical_key.replace("_", " "))
            if canon_normalized in _normalize(actual):
                s += 2
            if s > 0:
                row[actual] = s
        scores[canonical_key] = row

    # Greedy assignment — highest-scoring canonical gets its best match first
    assigned: set[str] = set()
    for canonical_key in sorted(
        CANONICAL_SHEETS,
        key=lambda k: max(scores[k].values(), default=0),
        reverse=True,
    ):
        best_actual, best_score = None, 0
        for actual, s in scores[canonical_key].items():
            if actual not in assigned and s > best_score:
                best_actual, best_score = actual, s
        if best_actual:
            result[canonical_key] = best_actual
            assigned.add(best_actual)

    # --- Tier 2: LLM for remaining unmatched canonical sheets ---
    unmatched_keys = [k for k, v in result.items() if v is None]
    free_actuals = [s for s in actual_sheet_names if s not in assigned]

    if unmatched_keys and free_actuals and llm is not None:
        llm_matches = _llm_match(
            unmatched_keys, free_actuals, llm, tenant_id=tenant_id, job_id=job_id
        )
        for k, actual in llm_matches.items():
            if k in result and actual in free_actuals:
                result[k] = actual
                free_actuals = [s for s in free_actuals if s != actual]

    for k, v in result.items():
        prefix = CANONICAL_SHEETS[k]["table_prefix"]
        logger.info(
            "sheet_mapper | canonical=%s prefix=%s → actual=%s",
            k, prefix, v or "UNMATCHED",
        )
    return result


def _llm_match(
    unmatched_keys: list[str],
    free_actuals: list[str],
    llm: "LLMProvider",
    *,
    tenant_id: str | None,
    job_id: str | None,
) -> dict[str, str]:
    """Single small LLM call to resolve ambiguous sheet name matches. Returns {} on error."""
    canonical_desc = "\n".join(
        f"- {k}: {CANONICAL_SHEETS[k]['table_name']}" for k in unmatched_keys
    )
    actual_list = "\n".join(f"- {s}" for s in free_actuals)
    prompt = (
        "You are matching Excel sheet names to financial statement types.\n\n"
        f"CANONICAL STATEMENTS TO MATCH:\n{canonical_desc}\n\n"
        f"AVAILABLE SHEET NAMES:\n{actual_list}\n\n"
        "Rules: each sheet may be used at most once; only include matches you are confident about.\n"
        'Respond ONLY with JSON: {"CANONICAL_KEY": "actual_sheet_name", ...}'
    )
    try:
        raw = llm.generate_json(
            system_prompt=(
                "You are a financial document expert. "
                "Match Excel sheet names to canonical statement types. "
                "Respond ONLY with a JSON object."
            ),
            user_prompt=prompt,
            temperature=0.0,
            max_tokens=300,
            tenant_id=tenant_id,
            job_id=job_id,
        )
        valid = {
            k: v for k, v in raw.items()
            if k in set(unmatched_keys) and isinstance(v, str) and v in free_actuals
        }
        logger.info("sheet_mapper_llm | resolved=%d keys=%s", len(valid), list(valid.keys()))
        return valid
    except Exception as exc:
        logger.warning("sheet_mapper_llm | error=%s", exc)
        return {}


def get_accounts_for_canonical(
    extractor_accounts: list[dict],
    canonical_key: str,
    sheet_mapping: dict[str, str | None],
    *,
    include_totals: bool = False,
) -> list[dict]:
    """Return extractor accounts belonging to the given canonical sheet.

    Order is preserved from the extractor output, which mirrors the row order of
    the source Excel sheet — so the rendered table reproduces the statement as it
    appears in the EEFF input. ``is_total`` rows (e.g. "Total activos") are kept
    only when *include_totals* is True, which financial statements (balance,
    income) want and holdings lists (portfolio) do not.
    """
    actual_sheet = (sheet_mapping or {}).get(canonical_key)
    if not actual_sheet:
        return []
    actual_lower = actual_sheet.lower()
    return [
        acc for acc in extractor_accounts
        if (acc.get("source_sheet") or "").lower() == actual_lower
        and (include_totals or not acc.get("is_total"))
    ]
