"""
role_context.py — Role Context Engine (AI Analysis Perspectives).

Lets the user pick WHO is performing the evaluation (fund manager, auditor,
board member, accountant, ...) so every downstream agent adapts its
priorities, materiality lens, risk emphasis and narrative tone to that
professional perspective — without ever opening the system to free-form
user prompts. The selection is a closed catalog defined in role_profiles.json.

Profile resolution follows the same three-tier fallback as system prompts:
  1. S3   — instructions/prompts/role_profiles.json  (live production copy)
  2. Local — src/agents/system_pompts/role_profiles.json (packaged copy)
  3. Inline — minimal hardcoded defaults (labels only, no specialization)

Consumers:
  • API orchestrator     — normalize_role() validates the requested role.
  • Agents 2/3/4         — build_role_prompt_block() returns the Spanish
    perspective block injected into LLM system prompts (via
    LLMProvider.set_role_context, so every LLM call in the agent inherits it).
  • Report Generator     — get_role_profile()["report_emphasis"] drives which
    report sections get more/less visual weight.

The "general" role is the default and produces NO injection — the pipeline
behaves exactly as before this feature existed.
"""

import json
import logging
import os

from .s3_instructions import load_text

logger = logging.getLogger("shared.role_context")

DEFAULT_ROLE = "general"

_S3_PROFILES_KEY = "instructions/prompts/role_profiles.json"
_LOCAL_PROFILES_PATH = os.path.join(
    os.path.dirname(__file__), "..", "agents", "system_pompts", "role_profiles.json"
)

# Tier 3 — inline minimal catalog. Keeps role validation working even if both
# S3 and the packaged file are unavailable; specialization degrades gracefully.
_INLINE_PROFILES: dict = {
    "default_role": DEFAULT_ROLE,
    "profiles": {
        "general": {"label": "Análisis General CreditIQ"},
        "fund_manager": {"label": "Administrador de Fondos"},
        "financial_analyst": {"label": "Analista Financiero"},
        "financial_manager": {"label": "Gerente Financiero"},
        "fiscal_reviewer": {"label": "Revisor Fiscal"},
        "external_auditor": {"label": "Auditor Externo"},
        "board_member": {"label": "Miembro de Junta Directiva"},
        "risk_investments": {"label": "Riesgos e Inversiones"},
        "accountant": {"label": "Contador"},
    },
}

# Per-container cache (same lifecycle as the s3_instructions prompt cache).
_profiles_cache: dict | None = None


def load_role_profiles() -> dict:
    """Return the full profiles document {default_role, profiles: {...}}."""
    global _profiles_cache
    if _profiles_cache is not None:
        return _profiles_cache

    raw = load_text(_S3_PROFILES_KEY, fallback="")
    if not raw:
        try:
            with open(_LOCAL_PROFILES_PATH, encoding="utf-8") as fh:
                raw = fh.read()
        except OSError as exc:
            logger.warning("role_profiles_local_load_failed | %s — using inline defaults", exc)

    if raw:
        try:
            doc = json.loads(raw)
            if isinstance(doc.get("profiles"), dict) and doc["profiles"]:
                _profiles_cache = doc
                return doc
        except json.JSONDecodeError as exc:
            logger.warning("role_profiles_invalid_json | %s — using inline defaults", exc)

    _profiles_cache = _INLINE_PROFILES
    return _INLINE_PROFILES


def valid_roles() -> list[str]:
    return list(load_role_profiles()["profiles"].keys())


def normalize_role(value) -> str:
    """Validate a requested role id; unknown/empty values fall back to general."""
    role = str(value or "").strip().lower()
    if role in load_role_profiles()["profiles"]:
        return role
    if role:
        logger.warning("unknown_analysis_role | role=%s — falling back to %s", role, DEFAULT_ROLE)
    return DEFAULT_ROLE


def get_role_profile(role_id: str) -> dict:
    """Full profile dict for a role (label, priorities, key_questions, report_emphasis, ...)."""
    profiles = load_role_profiles()["profiles"]
    return profiles.get(normalize_role(role_id), profiles[DEFAULT_ROLE])


def get_role_label(role_id: str) -> str:
    return get_role_profile(role_id).get("label", "Análisis General CreditIQ")


_TONE_DIRECTIVES = {
    "executive_plain_language": (
        "Minimiza el lenguaje técnico contable y financiero. Redacta conclusiones "
        "ejecutivas, directas y accionables, como si presentaras a una junta directiva "
        "sin formación financiera profunda. Prefiere implicaciones y decisiones sobre cifras crudas."
    ),
    "formal": (
        "Usa un tono formal y normativo, con referencias precisas a las partidas y, "
        "cuando aplique, a las normas NIIF/NIC involucradas."
    ),
    "technical": (
        "Usa un tono técnico-contable preciso: refiérete a cuentas, clasificaciones, "
        "conciliaciones y ajustes con terminología contable exacta."
    ),
}


def build_role_prompt_block(role_id: str) -> str:
    """
    Build the perspective block injected into agent system prompts.

    Returns "" for the default/general role so the baseline pipeline (and its
    prompt-cache prefix) is completely untouched when no perspective is chosen.
    The block adapts emphasis, ranking and narrative — it must NEVER override
    deterministic figures, scores or risk levels (LLM ceiling rule).
    """
    role = normalize_role(role_id)
    if role == DEFAULT_ROLE:
        return ""

    p = get_role_profile(role)
    priorities = "\n".join(f"- {item}" for item in p.get("priorities", []))
    questions = "\n".join(f"- {item}" for item in p.get("key_questions", []))
    tone = _TONE_DIRECTIVES.get(p.get("tone", ""), "")
    risk_focus = ", ".join(p.get("risk_focus", []))

    lines = [
        "\n\n=== PERSPECTIVA DE ANÁLISIS (ROLE CONTEXT) ===",
        f"Este análisis será leído por un(a): {p.get('label', role)}.",
        p.get("description", ""),
        "",
        "Adapta prioridades, ranking de hallazgos, énfasis de materialidad y narrativa a esta perspectiva.",
    ]
    if priorities:
        lines += ["", "Prioridades de este perfil:", priorities]
    if questions:
        lines += ["", "Preguntas que el análisis DEBE responder explícitamente:", questions]
    if risk_focus:
        lines += ["", f"Focos de riesgo a destacar: {risk_focus}."]
    if tone:
        lines += ["", f"Tono: {tone}"]
    lines += [
        "",
        "REGLAS INVIOLABLES:",
        "- NO alteres cifras, scores, niveles de riesgo ni clasificaciones determinísticas; "
        "solo ajusta el énfasis, el orden y la redacción.",
        "- NO omitas hallazgos críticos aunque estén fuera de las prioridades del perfil; "
        "menciónalos con menor desarrollo.",
        "- Mantén intacto el contrato JSON (claves, enums, IDs).",
        "=== FIN PERSPECTIVA ===\n",
    ]
    return "\n".join(line for line in lines if line is not None)


def clear_cache() -> None:
    """Evict the cached profiles document. Intended for tests only."""
    global _profiles_cache
    _profiles_cache = None
