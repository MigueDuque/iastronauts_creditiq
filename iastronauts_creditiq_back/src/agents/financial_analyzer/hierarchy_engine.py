"""
hierarchy_engine.py

Deterministic account-hierarchy classification. No LLM.

A financial-statements workbook represents the SAME money at several levels:
a primary statement (e.g. the balance sheet) carries summary lines, and separate
*detail sheets* re-expand individual summary lines into their components
(``Activos financieros a valor razonable`` on BALANCE  ==  the 19 instrument rows
on INSTRUMENTOS). Any consumer that flattens all accounts into one list and sums
or ranks them will double-count.

This engine tags every account with a ``statement_role`` and, for breakdown rows,
the ``parent_account_id`` they roll up into, so downstream engines can pick a single,
non-overlapping level:

    grand_total       top-level reported total (Total Activos / Pasivos / Patrimonio)
    subtotal          any other is_total row (intra-statement sum)
    breakdown_detail  a detail row whose sheet reconciles to a parent primary line
    primary_line      everything else — the canonical statement line level

Detection is purely reconciliation-based: a sheet is a *breakdown* of a parent line
when the sheet's own net total (its non-total rows) matches the value of a single
line on another sheet, within tolerance. This needs no sheet-name whitelist, so it
generalises across workbook layouts. PDF/CSV inputs (no ``source_sheet``) collapse to
a single group and every row stays ``primary_line`` — behaviour is unchanged.
"""

from __future__ import annotations

import logging
import unicodedata
from dataclasses import dataclass

logger = logging.getLogger("financial_analyzer.hierarchy_engine")

# Canonical names of top-level reported totals (accent-free, exact match).
_GRAND_TOTAL_NAMES = {
    "total activos", "total de activos", "total del activo", "activos totales",
    "total activo", "total pasivos", "total de pasivos", "total del pasivo",
    "pasivos totales", "total pasivo", "total patrimonio", "patrimonio total",
    "total del patrimonio", "total patrimonio neto", "total activos netos",
    "total activos netos de los inversionistas",
}


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return " ".join(s.lower().split())


@dataclass
class AccountHierarchy:
    statement_role: str          # grand_total | subtotal | breakdown_detail | primary_line
    parent_account_id: str | None = None
    is_leaf: bool = True         # False when this account is broken down elsewhere


# Minimum rows a sheet needs before we treat it as a breakdown of a parent line —
# avoids matching a one-row sheet to any same-valued line by coincidence.
_MIN_BREAKDOWN_ROWS = 2


def classify_accounts(variations: list) -> dict[str, AccountHierarchy]:
    """Classify each account. `variations` items expose: account_id, account_name,
    category, current_value, source_sheet, is_total.

    Returns: { account_id -> AccountHierarchy }.
    """
    result: dict[str, AccountHierarchy] = {}

    # ── 1. Base roles from is_total / name ───────────────────────────────────
    for v in variations:
        if getattr(v, "is_total", False):
            role = "grand_total" if _norm(v.account_name) in _GRAND_TOTAL_NAMES else "subtotal"
        else:
            role = "primary_line"
        result[v.account_id] = AccountHierarchy(statement_role=role)

    # ── 2. Group non-total rows by sheet and compute each sheet's net total ──
    sheets: dict[str, list] = {}
    for v in variations:
        if getattr(v, "is_total", False):
            continue
        sheet = getattr(v, "source_sheet", None)
        if sheet is None:
            continue  # PDF/CSV — no sheet structure to reconcile
        sheets.setdefault(sheet, []).append(v)

    if len(sheets) < 2:
        return result  # nothing to reconcile across

    sheet_totals = {s: sum(float(v.current_value) for v in rows) for s, rows in sheets.items()}

    # All non-total rows are candidate parents, indexed for value lookup.
    candidates = [v for v in variations if not getattr(v, "is_total", False)]

    # ── 3. Detect breakdown sheets by reconciling their total to a parent line ──
    # First pass: record every (sheet → parent line) match. We resolve conflicts in a
    # second pass because two DIFFERENT sheets can reconcile to the SAME parent — they
    # are alternative *views* of the same money (e.g. holdings by issuer on INSTRUMENTOS
    # vs. by fair-value-hierarchy level on VALOR_RAZONABLE_JERARQUIA, both summing to the
    # single "Instrumentos financieros de inversión" balance line). Counting both as
    # leaves double-counts the portfolio (the bug that produced top-3 > 100% and HHI = 1).
    matches: list[tuple[str, list, object]] = []
    for sheet, rows in sheets.items():
        if len(rows) < _MIN_BREAKDOWN_ROWS:
            continue
        net = sheet_totals[sheet]
        if abs(net) < 1e-6:
            continue
        tol = max(0.5, abs(net) * 0.01)  # 1% relative, 0.5 COP-MM floor

        # A valid parent is a single line on a DIFFERENT sheet whose value ≈ net.
        best = None
        best_gap = tol
        for c in candidates:
            if getattr(c, "source_sheet", None) == sheet:
                continue
            gap = abs(float(c.current_value) - net)
            if gap <= best_gap:
                best, best_gap = c, gap

        if best is None:
            continue  # self-contained primary sheet — leave its rows as primary_line
        matches.append((sheet, rows, best))

    # Second pass: group matched sheets by the parent they reconcile to. When several
    # sheets share a parent, only the finest-grained view (most rows; tie-break: smaller
    # net per row) stays as breakdown_detail leaves. The coarser alternative views roll
    # up to the same parent too, so they are marked non-leaf and must not be ranked/summed.
    by_parent: dict[str, list[tuple[str, list]]] = {}
    for sheet, rows, best in matches:
        by_parent.setdefault(best.account_id, []).append((sheet, rows))

    for parent_id, sheet_rows in by_parent.items():
        sheet_rows.sort(
            key=lambda sr: (len(sr[1]), -abs(sheet_totals[sr[0]]) / max(len(sr[1]), 1)),
            reverse=True,
        )
        result[parent_id].is_leaf = False
        primary_sheet, primary_rows = sheet_rows[0]
        for v in primary_rows:
            result[v.account_id].statement_role = "breakdown_detail"
            result[v.account_id].parent_account_id = parent_id
        logger.info(
            "hierarchy | sheet=%s (%d rows, total=%.2f) is breakdown of parent_id=%s",
            primary_sheet, len(primary_rows), sheet_totals[primary_sheet], parent_id,
        )
        # Demote alternative breakdowns of the same parent: same money, finer view kept.
        for sheet, rows in sheet_rows[1:]:
            for v in rows:
                result[v.account_id].statement_role = "subtotal"
                result[v.account_id].parent_account_id = parent_id
                result[v.account_id].is_leaf = False
            logger.info(
                "hierarchy | sheet=%s (%d rows) is an alternative view of parent_id=%s "
                "— demoted to non-leaf to avoid double-count",
                sheet, len(rows), parent_id,
            )

    return result


def primary_account_ids(hierarchy: dict[str, AccountHierarchy]) -> set[str]:
    """The canonical non-overlapping aggregation set: primary lines only
    (totals/subtotals are aggregates; breakdown detail is represented by its parent)."""
    return {aid for aid, h in hierarchy.items() if h.statement_role == "primary_line"}


def leaf_account_ids(hierarchy: dict[str, AccountHierarchy]) -> set[str]:
    """Leaf positions for position-level concentration: every account that is not a
    total/subtotal and is not itself broken down elsewhere. This drops a parent
    summary line in favour of its detail rows, so the same money is counted once."""
    return {
        aid for aid, h in hierarchy.items()
        if h.statement_role in ("primary_line", "breakdown_detail") and h.is_leaf
    }
