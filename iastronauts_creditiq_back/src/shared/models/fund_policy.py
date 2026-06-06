"""
fund_policy.py — FundPolicy model and Colombian FIC/CIV regulatory defaults.

Limits are expressed as percentages (e.g. 10.0 = 10 %).
Default values follow SFC Colombia guidelines (Decreto 2555 de 2010 and
Circular Básica Jurídica, Título IV — Fondos de Inversión Colectiva).
When a policy document is uploaded, the loader overrides these defaults and
sets source = "policy_document".
"""

from dataclasses import dataclass

# Colombian SFC default limits by fund_type.
# Keys mirror fund_engine.py's detect_fund_type() return values.
_FIC_DEFAULTS: dict[str, dict[str, float]] = {
    "equity_fund": {
        "per_issuer_pct": 10.0,
        "per_sector_pct": 30.0,
        "per_asset_class_pct": 100.0,
        "per_counterparty_pct": 25.0,
    },
    "fixed_income_fund": {
        "per_issuer_pct": 30.0,
        "per_sector_pct": 50.0,
        "per_asset_class_pct": 100.0,
        "per_counterparty_pct": 25.0,
    },
    "money_market_fund": {
        "per_issuer_pct": 40.0,
        "per_sector_pct": 60.0,
        "per_asset_class_pct": 100.0,
        "per_counterparty_pct": 35.0,
    },
    "balanced_fund": {
        "per_issuer_pct": 15.0,
        "per_sector_pct": 40.0,
        "per_asset_class_pct": 100.0,
        "per_counterparty_pct": 25.0,
    },
    "alternatives_fund": {
        "per_issuer_pct": 25.0,
        "per_sector_pct": 50.0,
        "per_asset_class_pct": 100.0,
        "per_counterparty_pct": 25.0,
    },
    "general": {
        "per_issuer_pct": 20.0,
        "per_sector_pct": 40.0,
        "per_asset_class_pct": 100.0,
        "per_counterparty_pct": 25.0,
    },
}


@dataclass
class FundPolicy:
    fund_type: str
    per_issuer_pct: float           # max single-issuer concentration
    per_sector_pct: float           # max single-sector concentration
    per_asset_class_pct: float      # max single-asset-class concentration
    per_counterparty_pct: float     # max single-counterparty (custodian) concentration
    source: str = "default"         # "default" | "policy_document"
    notes: str = ""


def get_default_policy(fund_type: str) -> FundPolicy:
    """Return SFC default limits for the given fund_type."""
    limits = _FIC_DEFAULTS.get(fund_type, _FIC_DEFAULTS["general"])
    return FundPolicy(
        fund_type=fund_type,
        per_issuer_pct=limits["per_issuer_pct"],
        per_sector_pct=limits["per_sector_pct"],
        per_asset_class_pct=limits["per_asset_class_pct"],
        per_counterparty_pct=limits["per_counterparty_pct"],
        source="default",
        notes=f"SFC Colombia defaults for {fund_type} (Decreto 2555 de 2010)",
    )
