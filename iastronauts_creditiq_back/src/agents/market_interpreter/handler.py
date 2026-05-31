"""
Agent 2 — Market Interpretation.

Reads the raw snapshot (Agent 1) and produces the card-ready pulse.json:

    overview[]  → Market Overview card  (formatted value/change/sign/color/points)
    signals[]   → AI Signals card       (deterministic + LLM)
    news[]      → Relevant News card    (impact-tagged headlines)

House rule — "Math First, Synthesis Second, LLM Third":
  1. Deterministic pass classifies every metric move (sign + significance) and
     tags each headline by impact category. This always runs, even with no LLM.
  2. The LLM only *narrates* — it turns the already-computed signals into the
     3 AI Signal cards. It cannot invent a metric move the math didn't flag, and
     if it fails we fall back to deterministic signals so the card is never empty.

The field names mirror the frontend's hardcoded MARKET_METRICS / AI_SIGNALS /
NewsItem shapes so wiring the dashboard is a state swap, not a refactor.
"""

import json
import logging
from typing import Any, Optional

from shared import market_store

logger = logging.getLogger("agents.market_interpreter")
logger.setLevel(logging.INFO)

# Sparkline / change colors (match DashboardPage palette).
_GREEN = "#4ade80"
_RED = "#f87171"
_GREY = "#94a3b8"

# A move smaller than this (abs %) is "flat" → stable/grey.
_FLAT_THRESHOLD = 0.05

# Per-metric % move that counts as *significant* (drives volatility signals).
# Rates barely move, so their bar is far lower than FX.
_SIGNIFICANCE: dict[str, float] = {
    "USDCOP": 0.5, "COLCAP": 0.7, "TES10Y": 0.3, "INFL": 0.0, "BANREP": 0.0,
}

# News impact categories — keyword → bucket. First match wins.
_IMPACT_RULES: list[tuple[str, list[str]]] = [
    ("Monetary Policy", ["banrep", "tasa", "política monetaria", "banco de la república", "interés"]),
    ("Fixed Income",    ["tes", "bono", "renta fija", "deuda", "rendimiento", "yield"]),
    ("Global Outlook",  ["global", "fed", "eeuu", "emergentes", "petróleo", "dólar", "china"]),
]

_SYSTEM_PROMPT = (
    "You are a markets strategist for a Colombian asset manager. You are given a "
    "compact digest of live market metrics (already classified by significance) "
    "and recent headlines. Produce exactly 3 concise AI signals that a portfolio "
    "manager would act on. Each signal: a short title (2-4 words, e.g. 'Fixed "
    "Income Pressure'), a one-sentence rationale grounded ONLY in the digest, a "
    "category from [Monetary Policy, Fixed Income, Global Outlook], and a severity "
    "from [info, warning, critical]. Do NOT claim a move the digest did not list. "
    'Output JSON: {"signals":[{"title","desc","category","severity"}]}'
)


def _impact_category(text: str) -> str:
    low = (text or "").lower()
    for label, keywords in _IMPACT_RULES:
        if any(k in low for k in keywords):
            return label
    return "Global Outlook"


def _fmt_value(value: float, unit: str) -> str:
    """Human card value: thousands separators for prices, % suffix for rates."""
    if unit == "%":
        return f"{value:.2f}%"
    return f"{value:,.2f}"


def _classify(change_pct: float) -> tuple[str, str]:
    """(sign, color) from a signed % change."""
    if abs(change_pct) < _FLAT_THRESHOLD:
        return "stable", _GREY
    if change_pct > 0:
        return "up", _GREEN
    return "down", _RED


# ── Deterministic pass ───────────────────────────────────────────────────────────

def _build_overview(
    snapshot: dict, prev_pulse: Optional[dict]
) -> tuple[list[dict], list[dict]]:
    """Return (overview_cards, significant_moves).

    Metrics missing from this snapshot (failed source) are carried forward from
    the previous pulse so the card never blanks — flagged stale=True.
    """
    fresh = {m["key"]: m for m in snapshot.get("metrics", [])}
    prev_by_key = {o["key"]: o for o in (prev_pulse or {}).get("overview", [])}

    overview: list[dict] = []
    significant: list[dict] = []

    for spec_key in ["USDCOP", "TES10Y", "COLCAP", "INFL", "BANREP"]:
        m = fresh.get(spec_key)
        if m:
            change_pct = m["change_pct"]
            sign, color = _classify(change_pct)
            card = {
                "key": m["key"],
                "label": m["label"],
                "value": _fmt_value(m["value"], m["unit"]),
                "raw": m["value"],
                "change": f"{change_pct:+.2f}%",
                "change_pct": change_pct,
                "sign": sign,
                "color": color,
                "points": m.get("history", []),
                "stale": False,
            }
            threshold = _SIGNIFICANCE.get(spec_key, 0.0)
            if abs(change_pct) >= threshold and sign != "stable":
                significant.append({"label": m["label"], "change": card["change"], "sign": sign})
        elif spec_key in prev_by_key:
            card = dict(prev_by_key[spec_key])  # carry forward last good
            card["stale"] = True
        else:
            continue
        overview.append(card)

    return overview, significant


def _tag_news(snapshot: dict) -> list[dict]:
    out: list[dict] = []
    for a in snapshot.get("news", []):
        category = _impact_category(f"{a.get('title', '')} {a.get('description', '')}")
        out.append({
            "title": a.get("title"),
            "sub": f"Impacto: {category}",
            "category": category,
            "published_at": a.get("published_at"),
            "time": "",  # frontend derives a relative label from published_at
            "thumb": a.get("image_url"),
            "url": a.get("url"),
        })
    return out


def _deterministic_signals(significant: list[dict], news: list[dict]) -> list[dict]:
    """Fallback signals used when the LLM is unavailable. Never empty."""
    signals: list[dict] = []
    for mv in significant[:3]:
        direction = "increasing" if mv["sign"] == "up" else "decreasing"
        signals.append({
            "title": f"{mv['label']} Move",
            "desc": f"{mv['label']} is {direction} ({mv['change']}), watch exposed positions.",
            "category": "Fixed Income" if "TES" in mv["label"] else "Global Outlook",
            "severity": "warning",
        })
    if not signals and news:
        top = news[0]
        signals.append({
            "title": "Market Watch",
            "desc": top["title"][:120],
            "category": top["category"],
            "severity": "info",
        })
    return signals or [{
        "title": "Markets Stable",
        "desc": "No significant moves detected in the latest refresh.",
        "category": "Global Outlook",
        "severity": "info",
    }]


# ── LLM pass ─────────────────────────────────────────────────────────────────────

def _digest(overview: list[dict], significant: list[dict], news: list[dict]) -> str:
    lines = ["METRICS:"]
    for o in overview:
        flag = " [SIGNIFICANT]" if any(s["label"] == o["label"] for s in significant) else ""
        lines.append(f"- {o['label']}: {o['value']} ({o['change']}){flag}")
    lines.append("\nHEADLINES:")
    for n in news[:6]:
        lines.append(f"- [{n['category']}] {n['title']}")
    return "\n".join(lines)


def _llm_signals(digest: str) -> Optional[list[dict]]:
    """Ask the LLM for 3 signals. Returns None on any failure (caller falls back).

    tenant_id is None — market data is public, so no tenant boundary is injected.
    """
    try:
        from shared.llm_provider import LLMProvider

        result = LLMProvider().generate_json(
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=digest,
            temperature=0.3,
            max_tokens=1200,
        )
        signals = result.get("signals") if isinstance(result, dict) else None
        if isinstance(signals, list) and signals:
            return [
                {
                    "title": str(s.get("title", ""))[:40],
                    "desc": str(s.get("desc", ""))[:200],
                    "category": s.get("category", "Global Outlook"),
                    "severity": s.get("severity", "info"),
                }
                for s in signals[:3]
                if s.get("title")
            ]
    except Exception as exc:
        logger.warning("LLM signal generation failed, using deterministic: %s", exc)
    return None


# ── Orchestration ────────────────────────────────────────────────────────────────

def interpret(snapshot: Optional[dict] = None) -> dict[str, Any]:
    """Build + persist pulse.json from a raw snapshot (loads latest if not given)."""
    snapshot = snapshot or market_store.read_raw_snapshot()
    if not snapshot:
        raise ValueError("No raw snapshot available to interpret")

    prev_pulse = market_store.read_pulse()
    overview, significant = _build_overview(snapshot, prev_pulse)
    news = _tag_news(snapshot)

    signals = _llm_signals(_digest(overview, significant, news)) \
        or _deterministic_signals(significant, news)

    pulse = {
        "as_of": snapshot.get("as_of", market_store.now_iso()),
        "stale": False,
        "overview": overview,
        "signals": signals,
        "news": news,
        "source_errors": snapshot.get("errors", []),
    }
    market_store.write_pulse(pulse)
    logger.info(
        "interpret complete | overview=%d signals=%d news=%d",
        len(overview), len(signals), len(news),
    )
    return pulse


def lambda_handler(event, context) -> dict[str, Any]:
    """EventBridge / manual entrypoint. Accepts an inline snapshot via event['snapshot']."""
    snapshot = event.get("snapshot") if isinstance(event, dict) else None
    return interpret(snapshot)
