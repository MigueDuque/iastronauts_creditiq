"""
Probes — flatten an agent's structured output into a flat dict of scalar metrics
that can be diffed against a golden snapshot.

A "probe" is one named scalar (int / float / str / bool) extracted from the rich
output object. The eval runner compares each probe against the recorded golden
value with a per-metric tolerance.

Keep probes *stable and semantically meaningful*: each one should correspond to a
decision a credit analyst would care about (a risk level, a composite score, a
human-review flag), not an incidental implementation detail. When you change what
an engine computes on purpose, re-snapshot (`runner.py --update`) and review the
diff in the PR.
"""
from __future__ import annotations

from typing import Any


def _enum_str(value: Any) -> str:
    """Normalize a RiskLevel enum (or plain string) to its string value."""
    return getattr(value, "value", value)


def risk_probes(comp: Any) -> dict[str, Any]:
    """Extract probes from a risk_scorer.scoring.RiskComputation."""
    probes: dict[str, Any] = {"is_fund": bool(comp.is_fund)}

    # ── Per-dimension score + level ─────────────────────────────────────────────
    for dim in ("liquidity", "credit", "solvency", "market", "operational"):
        engine = getattr(comp, dim)
        probes[f"{dim}.score"] = int(engine.score)
        probes[f"{dim}.level"] = _enum_str(engine.level)

    # ── Composite ───────────────────────────────────────────────────────────────
    c = comp.composite
    probes["composite.score"] = int(c.composite_score)
    probes["composite.overall_risk"] = _enum_str(c.overall_risk_score)
    probes["composite.validation_score"] = int(c.validation_score)
    probes["composite.requires_human_review"] = bool(c.requires_human_review)
    probes["composite.analysis_confidence"] = round(float(c.analysis_confidence), 4)
    probes["composite.anti_hallucination_passed"] = bool(c.anti_hallucination_passed)
    probes["composite.anti_hallucination_failures"] = len(
        (c.anti_hallucination_result or {}).get("failures", [])
    )

    # ── Report-facing risk categories (Crédito / Mercado / Financiero) ──────────
    for key in sorted((comp.risk_categories or {}).keys()):
        cat = comp.risk_categories[key]
        level = cat.get("level") if isinstance(cat, dict) else getattr(cat, "level", None)
        probes[f"category.{key}.level"] = _enum_str(level)

    return probes
