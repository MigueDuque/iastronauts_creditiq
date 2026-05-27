"""
llm_reasoning.py

Generates a qualitative risk narrative using the LLM.
Receives pre-computed risk dimension results (no arithmetic delegated to LLM).

Output: 3-paragraph risk summary in Spanish + structured risk recommendations.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from shared.llm_provider import LLMProvider

logger = logging.getLogger("risk_scorer.llm_reasoning")

_RISK_SYSTEM_PROMPT = """Eres un analista de riesgo financiero senior especializado en entidades de inversión colectiva
y empresas corporativas latinoamericanas. Tu función es redactar evaluaciones de riesgo ejecutivas,
concisas y basadas en cifras concretas.

INSTRUCCIONES:
1. Redacta exclusivamente en español.
2. Usa los datos pre-calculados que se te proporcionan — NO realices aritmética ni inventes cifras.
3. Estructura tu respuesta como JSON válido con exactamente los campos indicados.
4. El tono debe ser profesional, directo y orientado a la junta directiva o inversionistas institucionales.
5. Cada párrafo debe tener entre 3 y 5 oraciones. No uses viñetas dentro de los párrafos.
6. Las recomendaciones deben ser acciones concretas, no declaraciones vagas.
7. No repitas las mismas cifras en más de un párrafo.
"""

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
            system_prompt=_RISK_SYSTEM_PROMPT,
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
