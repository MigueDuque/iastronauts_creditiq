"""
llm_reasoning.py

Generates a qualitative risk narrative using the LLM.
Receives pre-computed risk dimension results (no arithmetic delegated to LLM).

Output: 3-paragraph risk summary in Spanish + structured risk recommendations.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from shared.llm_provider import LLMProvider
from shared.s3_instructions import load_text

logger = logging.getLogger("risk_scorer.llm_reasoning")

_PROMPT_S3_KEY = "instructions/prompts/03_prompt_agent_risk-analyzer.md"
_LOCAL_PROMPT_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "system_pompts",
                 "03_prompt_agent_risk-analyzer.md")
)
_INLINE_FALLBACK = (
    "Eres un analista de riesgo financiero senior especializado en fondos de inversión colectiva "
    "y empresas corporativas latinoamericanas bajo NIIF. Redacta evaluaciones de riesgo ejecutivas "
    "basadas exclusivamente en los datos pre-calculados recibidos. "
    "Responde ÚNICAMENTE con JSON válido con los campos: "
    "risk_narrative_paragraph1, risk_narrative_paragraph2, risk_narrative_paragraph3, "
    "risk_recommendations (lista), risk_headline."
)

_prompt_cache: str | None = None


def _get_system_prompt() -> str:
    global _prompt_cache
    if _prompt_cache is not None:
        return _prompt_cache
    # 1. Try S3
    s3_text = load_text(_PROMPT_S3_KEY, fallback="")
    if s3_text:
        _prompt_cache = s3_text
        logger.info("risk_prompt | source=s3 chars=%d", len(s3_text))
        return _prompt_cache
    # 2. Try local file (works in local dev / unit tests)
    try:
        with open(_LOCAL_PROMPT_PATH, encoding="utf-8") as f:
            local_text = f.read()
        if len(local_text) > 100:
            _prompt_cache = local_text
            logger.info("risk_prompt | source=local chars=%d", len(local_text))
            return _prompt_cache
    except OSError:
        pass
    # 3. Inline fallback — never blocks the pipeline
    logger.warning("risk_prompt | source=inline_fallback")
    _prompt_cache = _INLINE_FALLBACK
    return _prompt_cache

_RISK_USER_TEMPLATE = """
Analiza el perfil de riesgo financiero de {company_name} para el período {period}.

TIPO DE ENTIDAD: {entity_type}
PUNTUACIÓN COMPUESTA DE RIESGO: {composite_score}/100 (mayor = menor riesgo)
NIVEL GENERAL DE RIESGO: {overall_risk}

DIMENSIONES DE RIESGO:
{dimensions_json}

PRINCIPALES FACTORES DE RIESGO IDENTIFICADOS:
{risk_drivers_text}

HALLAZGOS CLAVE POR DIMENSIÓN:
{key_findings_text}

Responde con un JSON con exactamente esta estructura:
{{
  "risk_narrative_paragraph1": "Párrafo sobre el perfil general de riesgo y la dimensión de mayor riesgo.",
  "risk_narrative_paragraph2": "Párrafo sobre riesgos secundarios y su interacción.",
  "risk_narrative_paragraph3": "Párrafo sobre fortalezas y contexto mitigante.",
  "risk_recommendations": ["Acción concreta 1", "Acción concreta 2", "Acción concreta 3"],
  "risk_headline": "Una sola oración que resume el perfil de riesgo para el encabezado del informe."
}}
"""


def _format_dimensions(dimension_scores: dict, dimension_levels: dict) -> str:
    rows = []
    labels = {
        "liquidity": "Riesgo de Liquidez",
        "credit": "Riesgo de Crédito/Contraparte",
        "solvency": "Riesgo de Solvencia/Apalancamiento",
        "market": "Riesgo de Mercado",
        "operational": "Riesgo Operacional/Rentabilidad",
    }
    for key, label in labels.items():
        score = dimension_scores.get(key, 0)
        level = dimension_levels.get(key, "N/A")
        rows.append(f"  {label}: {level} (score {score}/100)")
    return "\n".join(rows)


def _format_drivers(all_drivers: list[str]) -> str:
    if not all_drivers:
        return "  (No se identificaron factores de riesgo críticos)"
    return "\n".join(f"  • {d}" for d in all_drivers[:8])


def _format_findings(dimension_findings: dict) -> str:
    labels = {
        "liquidity": "Liquidez",
        "credit": "Crédito",
        "solvency": "Solvencia",
        "market": "Mercado",
        "operational": "Operacional",
    }
    lines = []
    for key, label in labels.items():
        findings = dimension_findings.get(key, [])
        for f in findings[:2]:  # top 2 findings per dimension
            lines.append(f"  [{label}] {f}")
    return "\n".join(lines) if lines else "  (Sin hallazgos significativos)"


def generate_risk_narrative(
    company_name: str,
    period: str,
    is_investment_fund: bool,
    composite_score: int,
    overall_risk: str,
    dimension_scores: dict,
    dimension_levels: dict,
    dimension_findings: dict,
    all_risk_drivers: list[str],
    tenant_id: Optional[str],
    job_id: Optional[str],
    llm: LLMProvider,
) -> dict:
    """
    Returns dict with keys:
      risk_narrative_paragraph1, risk_narrative_paragraph2, risk_narrative_paragraph3,
      risk_recommendations, risk_headline
    Returns fallback dict on LLM failure.
    """
    entity_type = "Fondo de inversión colectiva (CIV)" if is_investment_fund else "Empresa corporativa"

    user_prompt = _RISK_USER_TEMPLATE.format(
        company_name=company_name,
        period=period,
        entity_type=entity_type,
        composite_score=composite_score,
        overall_risk=overall_risk,
        dimensions_json=_format_dimensions(dimension_scores, dimension_levels),
        risk_drivers_text=_format_drivers(all_risk_drivers),
        key_findings_text=_format_findings(dimension_findings),
    )

    try:
        result = llm.generate_json(
            system_prompt=_get_system_prompt(),
            user_prompt=user_prompt,
            temperature=0.2,
            tenant_id=tenant_id,
            job_id=job_id,
            max_tokens=2000,
        )
        required = {
            "risk_narrative_paragraph1",
            "risk_narrative_paragraph2",
            "risk_narrative_paragraph3",
            "risk_recommendations",
            "risk_headline",
        }
        if required.issubset(result.keys()):
            return result
        logger.warning("LLM response missing required keys; using fallback.")
    except Exception as exc:
        logger.error("LLM risk narrative failed: %s", exc)

    # Fallback: deterministic narrative from top findings
    high_dims = [k for k, v in dimension_levels.items() if v == "HIGH"]
    medium_dims = [k for k, v in dimension_levels.items() if v == "MEDIUM"]

    dim_es = {
        "liquidity": "liquidez",
        "credit": "crédito",
        "solvency": "solvencia",
        "market": "mercado",
        "operational": "operacional",
    }

    p1 = (
        f"{company_name} presenta un perfil de riesgo {overall_risk.lower()} con una puntuación compuesta de {composite_score}/100. "
        + (f"Las dimensiones de mayor riesgo son: {', '.join(dim_es.get(d, d) for d in high_dims)}. " if high_dims else "")
        + "Se requiere seguimiento activo por parte de la administración."
    )

    p2 = (
        (f"Dimensiones con riesgo medio: {', '.join(dim_es.get(d, d) for d in medium_dims)}. " if medium_dims else "")
        + "Los factores identificados pueden interactuar y amplificar el perfil de riesgo global en escenarios adversos."
    )

    low_dims = [k for k, v in dimension_levels.items() if v == "LOW"]
    p3 = (
        (f"Fortalezas: bajo riesgo en {', '.join(dim_es.get(d, d) for d in low_dims)}. " if low_dims else "")
        + "La entidad mantiene capacidad para gestionar los riesgos identificados en condiciones normales de mercado."
    )

    return {
        "risk_narrative_paragraph1": p1,
        "risk_narrative_paragraph2": p2,
        "risk_narrative_paragraph3": p3,
        "risk_recommendations": all_risk_drivers[:3] if all_risk_drivers else ["Monitorear indicadores de riesgo trimestralmente."],
        "risk_headline": f"Perfil de riesgo {overall_risk.lower()} — score compuesto {composite_score}/100.",
    }
