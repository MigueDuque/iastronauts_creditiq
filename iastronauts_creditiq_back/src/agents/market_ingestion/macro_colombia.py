"""
Colombian macro indicators — BanRep policy rate, DANE annual inflation, TES 10Y.

tradingeconomics was the original provider but requires a paid key (the guest
tier was discontinued, HTTP 410). No other free/reliable JSON endpoint exists
for these three series, so they are served from maintained constants.

Each constant has an as_of date so the dashboard can show "as of Apr 2026 · BanRep"
instead of a misleading live-change row. Update the values here when the official
figure changes (BanRep meets ~8x/year; DANE publishes CPI monthly).

fetch_macro_metrics() returns a list of raw-metric dicts in the same shape that
handler.py builds for yfinance metrics, so ingest() can extend(metrics) directly.
"""

from typing import Any

# ── Maintained constants ──────────────────────────────────────────────────────────
# Sources:
#   BANREP — Banco de la República junta directiva decisions
#   INFL   — DANE IPC variación anual (boletín mensual)
#   TES10Y — Reference yield from market data
MACRO_CONSTANTS: list[dict[str, Any]] = [
    {
        "key": "TES10Y",
        "label": "TES 10Y",
        "value": 11.50,
        "as_of": "2026-05-01",
        "source": "manual",
        "unit": "%",
    },
    {
        "key": "INFL",
        "label": "Inflación (COL)",
        "value": 5.35,
        "as_of": "2026-01-31",
        "source": "DANE",
        "unit": "%",
    },
    {
        "key": "BANREP",
        "label": "Tasa BanRep",
        "value": 9.25,
        "as_of": "2026-04-30",
        "source": "BanRep",
        "unit": "%",
    },
]


def fetch_macro_metrics() -> list[dict[str, Any]]:
    """Return the three macro cards as raw-metric dicts ready for ingest()."""
    return [
        {
            "key": c["key"],
            "label": c["label"],
            "value": c["value"],
            "prev_close": c["value"],
            "change_pct": 0.0,
            "unit": c["unit"],
            "history": [c["value"], c["value"]],
            "as_of": c["as_of"],
            "source": c["source"],
            "ok": True,
        }
        for c in MACRO_CONSTANTS
    ]
