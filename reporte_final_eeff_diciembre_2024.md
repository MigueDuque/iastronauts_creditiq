<!-- CREDITIQ_REPORT
{
  "job_id": "job-btg-acciones-colombia-2024",
  "tenant_id": "creditiq-demo",
  "company_name": "Fondo de Inversión Colectiva Abierto BTG Pactual Acciones Colombia",
  "periods": ["2024-12", "2023-12"],
  "generated_at": "2025-01-15T10:30:00Z",
  "validation_score": 87,
  "overall_risk_score": "LOW",
  "overall_financial_health": "GROWING",
  "executive_summary": "El Fondo de Inversión Colectiva Abierto BTG Pactual Acciones Colombia presentó un crecimiento excepcional durante 2024, con expansión de activos del 377% y utilidades del 466%, impulsado por el rally accionario colombiano, incremento acelerado de aportes de inversionistas y eventos corporativos estratégicos del GEA. No se identificaron anomalías críticas, deterioros materiales ni incumplimientos regulatorios.",
  "board_summary": "El fondo cerró 2024 con resultados sobresalientes: activos totales de COP $52,704 MM (+377%), utilidad del ejercicio de COP $5,388 MM (+466%) y aportes de inversionistas de COP $43,061 MM (+713%). El perfil de riesgo general es BAJO, con exposición moderada a volatilidad accionaria y concentración en emisores del GEA. Cumplimiento adecuado NIIF 9, NIIF 13 y NIIF 7. Se recomienda monitorear concentración sectorial y evaluar escenarios de estrés de mercado.",
  "analysis_results": [
    {
      "account_id": "act-001",
      "account_name": "Activos Totales",
      "current_value": 52704.0,
      "previous_value": 11053.0,
      "absolute_variation": 41651.0,
      "variation_pct": 377.4,
      "materiality": "HIGH",
      "requires_niif_note": true,
      "niif_note_references": ["note-001", "note-003"],
      "risk_level": "MEDIUM",
      "possible_causes": [
        "Incremento acelerado de aportes de inversionistas",
        "Valorización del portafolio accionario colombiano",
        "Rebalanceos de índices internacionales"
      ],
      "executive_insight": "El crecimiento de activos totales en 377% refleja la expansión acelerada del fondo, impulsada principalmente por flujos de nuevos inversionistas y valorización del portafolio en renta variable.",
      "anomaly_detected": false
    },
    {
      "account_id": "act-002",
      "account_name": "Instrumentos Financieros de Inversión",
      "current_value": 52560.0,
      "previous_value": 10806.0,
      "absolute_variation": 41754.0,
      "variation_pct": 386.4,
      "materiality": "HIGH",
      "requires_niif_note": true,
      "niif_note_references": ["note-001", "note-002"],
      "risk_level": "MEDIUM",
      "possible_causes": [
        "Rally accionario colombiano 2024",
        "Incremento de aportes de inversionistas",
        "Rebalanceos de índices internacionales y eventos corporativos GEA"
      ],
      "executive_insight": "Principal activo del fondo. El crecimiento de 386% refleja tanto la entrada de nuevos recursos como la valorización del portafolio en renta variable colombiana.",
      "anomaly_detected": false
    },
    {
      "account_id": "act-003",
      "account_name": "Utilidad del Ejercicio",
      "current_value": 5388.0,
      "previous_value": 952.0,
      "absolute_variation": 4436.0,
      "variation_pct": 466.0,
      "materiality": "HIGH",
      "requires_niif_note": false,
      "niif_note_references": [],
      "risk_level": "LOW",
      "possible_causes": [
        "Valorización del portafolio accionario",
        "Dividendos recibidos de emisores estratégicos",
        "Ciclo de reducción de tasas del Banco de la República"
      ],
      "executive_insight": "Crecimiento excepcional de utilidades consistente con el desempeño del mercado accionario colombiano en 2024.",
      "anomaly_detected": false
    },
    {
      "account_id": "act-004",
      "account_name": "Aportes de Inversionistas",
      "current_value": 43061.0,
      "previous_value": 5293.0,
      "absolute_variation": 37768.0,
      "variation_pct": 713.4,
      "materiality": "HIGH",
      "requires_niif_note": true,
      "niif_note_references": ["note-003"],
      "risk_level": "LOW",
      "possible_causes": [
        "Mayor apetito por renta variable colombiana",
        "Rebalanceos de índices internacionales",
        "Eventos corporativos estratégicos GEA"
      ],
      "executive_insight": "El crecimiento de 713% en aportes es el principal driver de la expansión patrimonial del fondo durante 2024.",
      "anomaly_detected": false
    },
    {
      "account_id": "act-005",
      "account_name": "Retiros de Inversionistas",
      "current_value": 6700.0,
      "previous_value": 6354.0,
      "absolute_variation": 346.0,
      "variation_pct": 5.4,
      "materiality": "MEDIUM",
      "requires_niif_note": false,
      "niif_note_references": [],
      "risk_level": "LOW",
      "possible_causes": [
        "Rotación normal de inversionistas",
        "Toma de utilidades parciales"
      ],
      "executive_insight": "Los retiros crecieron apenas 5% frente a aportes que crecieron 713%, evidenciando alta retención de inversionistas.",
      "anomaly_detected": false
    },
    {
      "account_id": "act-006",
      "account_name": "Gastos Administrativos",
      "current_value": 313.0,
      "previous_value": 239.0,
      "absolute_variation": 74.0,
      "variation_pct": 31.0,
      "materiality": "MEDIUM",
      "requires_niif_note": false,
      "niif_note_references": [],
      "risk_level": "LOW",
      "possible_causes": [
        "Crecimiento proporcional al tamaño del fondo",
        "Incremento en comisiones de administración"
      ],
      "executive_insight": "El crecimiento de gastos (31%) es significativamente menor al de activos (377%), evidenciando alta eficiencia operativa.",
      "anomaly_detected": false
    },
    {
      "account_id": "act-007",
      "account_name": "Efectivo",
      "current_value": 137.0,
      "previous_value": 144.0,
      "absolute_variation": -7.0,
      "variation_pct": -4.9,
      "materiality": "LOW",
      "requires_niif_note": false,
      "niif_note_references": [],
      "risk_level": "LOW",
      "possible_causes": [
        "Estrategia de inversión agresiva en renta variable",
        "Alta rotación de caja hacia instrumentos financieros"
      ],
      "executive_insight": "La baja caja es coherente con la estrategia del fondo de maximizar exposición a renta variable colombiana.",
      "anomaly_detected": false
    }
  ],
  "niif_note_drafts": [
    {
      "note_id": "note-001",
      "niif_reference": "NIIF 9",
      "title": "Instrumentos Financieros",
      "content": "Las inversiones del fondo son clasificadas como activos financieros a valor razonable con cambios en resultados (FVTPL). La valoración se realiza utilizando precios suministrados por el proveedor autorizado PIP conforme a los lineamientos de la Superintendencia Financiera de Colombia. La medición diaria a valor razonable incrementa la sensibilidad del estado de resultados frente a fluctuaciones del mercado accionario colombiano.",
      "affected_account_ids": ["act-001", "act-002"],
      "requires_disclosure": true
    },
    {
      "note_id": "note-002",
      "niif_reference": "NIIF 13",
      "title": "Valor Razonable",
      "content": "La mayoría de los instrumentos del portafolio corresponden a activos Nivel 1 (precios cotizados en mercados activos). La predominancia de instrumentos Nivel 1 reduce los riesgos asociados a modelos de valoración complejos y favorece la transparencia en medición. No se identificaron reclasificaciones entre niveles durante el período.",
      "affected_account_ids": ["act-002"],
      "requires_disclosure": true
    },
    {
      "note_id": "note-003",
      "niif_reference": "NIIF 7",
      "title": "Gestión de Riesgo Financiero",
      "content": "El fondo mantiene exposición principalmente a riesgo de mercado (alta concentración en renta variable colombiana), riesgo de liquidez (fondo abierto con dinámica adecuada de aportes y retiros) y riesgo de concentración (participación relevante en emisores del GEA). La estructura del portafolio es consistente con el mandato de inversión del vehículo.",
      "affected_account_ids": ["act-001", "act-004"],
      "requires_disclosure": true
    }
  ],
  "markdown_report_url": "reports/creditiq-demo/fondo-de-inversion-colectiva-abierto-btg-pactual-acciones-colombia/2025/01/report_job-btg-acciones-colombia-2024.md",
  "pdf_report_url": null
}
-->

# Fondo de Inversión Colectiva Abierto BTG Pactual Acciones Colombia — Reporte Financiero CreditIQ

*Período: dic 2024 — dic 2023 | Generado: 2025-01-15 10:30 UTC | Score de Validación: 87/100 | Riesgo Global: BAJO*

---

## Resumen Ejecutivo

El Fondo de Inversión Colectiva Abierto BTG Pactual Acciones Colombia presentó un crecimiento excepcional durante 2024, con expansión de activos del 377% y utilidades del 466%, impulsado por el rally accionario colombiano, el incremento acelerado de aportes de inversionistas y eventos corporativos estratégicos del GEA.

**Principales drivers del período:**

- Valorización del mercado accionario colombiano.
- Incremento acelerado de aportes de nuevos inversionistas.
- Rebalanceos de índices internacionales.
- Eventos corporativos estratégicos relacionados con emisores del GEA.
- Ciclo de reducción de tasas del Banco de la República (~350 pb acumulados).

No se identificaron anomalías críticas, deterioros materiales ni incumplimientos regulatorios.

---

## Resumen para Junta Directiva

El fondo cerró 2024 con resultados sobresalientes en rentabilidad y expansión patrimonial.

| Indicador | Resultado |
|-----------|-----------|
| Activos Totales | COP $52,704 MM (+377%) |
| Utilidad del Ejercicio | COP $5,388 MM (+466%) |
| Aportes de Inversionistas | COP $43,061 MM (+713%) |
| Riesgo Global | BAJO |
| Score de Validación | 87/100 |
| Requiere Revisión Humana | No |

**Factores de atención:**

- Alta sensibilidad al mercado accionario colombiano.
- Concentración relevante en emisores del GEA (Bancolombia, Grupo Sura, Grupo Argos, ISA).
- Dependencia del ciclo económico local.

**Recomendaciones:**

- Monitorear exposición sectorial y concentración en emisores GEA.
- Evaluar escenarios de estrés de mercado accionario.
- Mantener seguimiento de dinámica de liquidez (aportes vs. retiros).

---

## Análisis de Variaciones por Cuenta

| Cuenta | Valor 2024 (COP MM) | Valor 2023 (COP MM) | Variación % | Materialidad | Riesgo |
|--------|--------------------:|--------------------:|------------:|:------------:|:------:|
| Activos Totales | 52,704 | 11,053 | +377.4% | ALTA | MEDIO |
| Instrumentos Financieros de Inversión | 52,560 | 10,806 | +386.4% | ALTA | MEDIO |
| Utilidad del Ejercicio | 5,388 | 952 | +466.0% | ALTA | BAJO |
| Aportes de Inversionistas | 43,061 | 5,293 | +713.4% | ALTA | BAJO |
| Retiros de Inversionistas | 6,700 | 6,354 | +5.4% | MEDIA | BAJO |
| Gastos Administrativos | 313 | 239 | +31.0% | MEDIA | BAJO |
| Efectivo | 137 | 144 | -4.9% | BAJA | BAJO |

**Anomalías detectadas:** Ninguna crítica. Alta concentración en emisores de renta variable colombiana con sensibilidad moderada a volatilidad de mercado.

---

## Riesgos Identificados

| Riesgo | Nivel | Descripción |
|--------|-------|-------------|
| Riesgo de Mercado | MEDIO | Alta exposición a renta variable colombiana |
| Riesgo de Liquidez | BAJO | Fondo abierto con adecuada dinámica de aportes |
| Riesgo Operacional | BAJO | No se identificaron incidentes materiales |
| Riesgo Regulatorio | BAJO | Cumplimiento adecuado NIIF y SFC |
| Riesgo ASG | BAJO | Alineación progresiva con Circular SFC 005/2024 |
| Riesgo de Concentración | MEDIO | Participación relevante en emisores GEA |

---

## Notas NIIF Requeridas

### Nota 1: Instrumentos Financieros (NIIF 9)

Las inversiones del fondo son clasificadas como activos financieros a valor razonable con cambios en resultados (FVTPL). La valoración se realiza utilizando precios suministrados por el proveedor autorizado PIP conforme a los lineamientos de la Superintendencia Financiera de Colombia.

La medición diaria a valor razonable incrementa la sensibilidad del estado de resultados frente a fluctuaciones del mercado accionario colombiano.

*Cuentas afectadas: Activos Totales, Instrumentos Financieros de Inversión*
*Requiere revelación obligatoria.*

---

### Nota 2: Valor Razonable (NIIF 13)

La mayoría de los instrumentos del portafolio corresponden a activos Nivel 1 (precios cotizados en mercados activos). La predominancia de instrumentos Nivel 1 reduce los riesgos asociados a modelos de valoración complejos y favorece la transparencia en medición. No se identificaron reclasificaciones entre niveles durante el período.

*Cuentas afectadas: Instrumentos Financieros de Inversión*
*Requiere revelación obligatoria.*

---

### Nota 3: Gestión de Riesgo Financiero (NIIF 7)

El fondo mantiene exposición principalmente a riesgo de mercado (alta concentración en renta variable colombiana), riesgo de liquidez (fondo abierto con dinámica adecuada de aportes y retiros) y riesgo de concentración (participación relevante en emisores del GEA). La estructura del portafolio es consistente con el mandato de inversión del vehículo.

*Cuentas afectadas: Activos Totales, Aportes de Inversionistas*
*Requiere revelación obligatoria.*

---

## Indicadores de Cumplimiento

- **Score de validación:** 87/100
- **Salud financiera:** CRECIENTE
- **Flags de cumplimiento:** NIIF 9 validado · NIIF 13 validado · NIIF 7 validado · Circular SFC 005/2024 aplicada
- **Anomalías críticas:** No
- **Requiere revisión humana:** No
- **Confianza del análisis:** Alta
