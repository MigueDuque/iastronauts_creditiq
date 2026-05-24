# Template: Reporte Final de Estados Financieros — CreditIQ

Este archivo es el **template oficial** que deben seguir los agentes para generar el reporte final de análisis de estados financieros. El output debe replicar **exactamente** esta estructura: primero un bloque de metadatos JSON en comentario HTML, seguido del reporte en Markdown legible.

---

## INSTRUCCIONES PARA EL AGENTE

1. **El output es un único archivo `.md`** que contiene dos partes:
   - **Parte 1**: Un comentario HTML con el JSON estructurado completo (metadatos de máquina).
   - **Parte 2**: El reporte en Markdown legible por humanos (para PDF/visualización).
2. Ambas partes deben ser **100% consistentes** entre sí: los mismos valores, las mismas cuentas, las mismas notas.
3. Usa los **valores reales** extraídos de los estados financieros procesados. No inventes ni estimes cifras.
4. Los textos narrativos (`executive_summary`, `board_summary`, `executive_insight`, notas NIIF) deben ser redactados en **español formal financiero**.
5. Las unidades monetarias van en **COP MM** (millones de pesos colombianos) salvo que se indique lo contrario.

---

## PARTE 1: BLOQUE JSON (Comentario HTML)

El archivo debe comenzar con este bloque. El agente debe completar todos los campos.

```
<!-- CREDITIQ_REPORT
{JSON_COMPLETO}
-->
```

### Estructura JSON detallada

```json
{
  "job_id": "string — identificador único del job, formato: job-{empresa-slug}-{año}",
  "tenant_id": "string — identificador del cliente/tenant en CreditIQ",
  "company_name": "string — nombre legal completo de la empresa o fondo analizado",
  "periods": ["string — período actual en formato YYYY-MM", "string — período anterior en formato YYYY-MM"],
  "generated_at": "string — timestamp ISO 8601 de generación del reporte, ej: 2025-01-15T10:30:00Z",
  "validation_score": "integer — score de validación del análisis, rango 0-100",
  "overall_risk_score": "string — nivel de riesgo global: 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL'",
  "overall_financial_health": "string — salud financiera general: 'GROWING' | 'STABLE' | 'DECLINING' | 'CRITICAL'",
  "executive_summary": "string — párrafo único de resumen ejecutivo, máximo 3 oraciones, en español. Describe lo más relevante del período: crecimiento/contracción, principales drivers y conclusión de riesgo.",
  "board_summary": "string — párrafo de resumen para Junta Directiva, más detallado que executive_summary. Incluye: cifras clave, perfil de riesgo, cumplimiento NIIF y recomendaciones. En español.",
  "analysis_results": [
    {
      "account_id": "string — identificador único de la cuenta, formato: act-{NNN}",
      "account_name": "string — nombre de la cuenta contable tal como aparece en los estados financieros",
      "current_value": "number — valor del período actual en COP MM (float, dos decimales)",
      "previous_value": "number — valor del período anterior en COP MM (float, dos decimales)",
      "absolute_variation": "number — variación absoluta: current_value - previous_value (puede ser negativo)",
      "variation_pct": "number — variación porcentual redondeada a 1 decimal (puede ser negativo)",
      "materiality": "string — nivel de materialidad: 'HIGH' | 'MEDIUM' | 'LOW'",
      "requires_niif_note": "boolean — true si la variación o saldo requiere revelación NIIF",
      "niif_note_references": ["string — lista de IDs de notas NIIF relacionadas, ej: ['note-001', 'note-003']. Lista vacía [] si no aplica."],
      "risk_level": "string — nivel de riesgo específico de esta cuenta: 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL'",
      "possible_causes": ["string — lista de 2 a 4 causas posibles de la variación, en español, redactadas como frases cortas"],
      "executive_insight": "string — párrafo corto (1-2 oraciones) con el insight ejecutivo de la variación de esta cuenta, en español",
      "anomaly_detected": "boolean — true si se detectó una anomalía que requiere atención inmediata"
    }
  ],
  "niif_note_drafts": [
    {
      "note_id": "string — identificador único de la nota, formato: note-{NNN}",
      "niif_reference": "string — norma NIIF aplicable, ej: 'NIIF 9' | 'NIIF 13' | 'NIIF 7' | 'NIIF 15' | 'NIIF 16'",
      "title": "string — título descriptivo de la nota, en español",
      "content": "string — texto completo de la nota revelación, en español formal contable. 2-4 oraciones. Debe explicar: la política contable aplicada, la medición/valoración usada, y cualquier impacto relevante en los estados financieros.",
      "affected_account_ids": ["string — lista de IDs de cuentas (account_id) afectadas por esta nota"],
      "requires_disclosure": "boolean — true si la nota es de revelación obligatoria en los estados financieros"
    }
  ],
  "markdown_report_url": "string — ruta relativa del archivo .md generado, formato: reports/{tenant_id}/{company-slug}/{año}/{mes}/report_{job_id}.md",
  "pdf_report_url": "string | null — URL del PDF generado, null si aún no se ha generado"
}
```

### Reglas de validación del JSON

- `analysis_results` debe tener **al menos una entrada** por cada cuenta material analizada.
- `niif_note_drafts` solo debe incluir notas para cuentas con `requires_niif_note: true`.
- Los `niif_note_references` en `analysis_results` deben corresponder a `note_id` existentes en `niif_note_drafts`.
- Los `affected_account_ids` en `niif_note_drafts` deben corresponder a `account_id` existentes en `analysis_results`.
- `variation_pct` = `(absolute_variation / previous_value) * 100`, redondeado a 1 decimal.
- `absolute_variation` = `current_value - previous_value`.
- Si `previous_value` es 0, `variation_pct` debe ser `null` (no dividir por cero).

---

## PARTE 2: REPORTE MARKDOWN

Inmediatamente después del bloque HTML de comentario, el archivo continúa con el reporte en Markdown. Debe seguir **exactamente** esta estructura de secciones:

---

### Encabezado principal

```markdown
# {company_name} — Reporte Financiero CreditIQ

*Período: {mes_actual} {año_actual} — {mes_anterior} {año_anterior} | Generado: {generated_at formateado} UTC | Score de Validación: {validation_score}/100 | Riesgo Global: {overall_risk_score en español}*

---
```

- `overall_risk_score` se traduce: `LOW` → `BAJO`, `MEDIUM` → `MEDIO`, `HIGH` → `ALTO`, `CRITICAL` → `CRÍTICO`.
- `overall_financial_health` se traduce: `GROWING` → `CRECIENTE`, `STABLE` → `ESTABLE`, `DECLINING` → `DECRECIENTE`, `CRITICAL` → `CRÍTICO`.

---

### Sección 1: Resumen Ejecutivo

```markdown
## Resumen Ejecutivo

{executive_summary — texto completo}

**Principales drivers del período:**

- {driver 1 extraído del análisis}
- {driver 2}
- {driver 3}
- {driver N — tantos como sean relevantes}

{Conclusión de riesgos y anomalías: ej. "No se identificaron anomalías críticas, deterioros materiales ni incumplimientos regulatorios." o descripción de los hallazgos si los hay}

---
```

---

### Sección 2: Resumen para Junta Directiva

```markdown
## Resumen para Junta Directiva

{board_summary — primera oración introductoria}

| Indicador | Resultado |
|-----------|-----------|
| {cuenta 1 material} | COP ${valor_actual} MM ({signo}{variation_pct}%) |
| {cuenta 2 material} | COP ${valor_actual} MM ({signo}{variation_pct}%) |
| {cuenta 3 material} | COP ${valor_actual} MM ({signo}{variation_pct}%) |
| Riesgo Global | {overall_risk_score en español} |
| Score de Validación | {validation_score}/100 |
| Requiere Revisión Humana | {Sí / No} |

**Factores de atención:**

- {factor 1 — riesgos o concentraciones identificadas}
- {factor 2}
- {factor N}

**Recomendaciones:**

- {recomendación 1}
- {recomendación 2}
- {recomendación N}

---
```

> **Nota para el agente**: La tabla de indicadores debe incluir las 3 cuentas con `materiality: "HIGH"` y mayor `variation_pct`. El campo "Requiere Revisión Humana" es "No" si `validation_score >= 80` y no hay `anomaly_detected: true` en ninguna cuenta; de lo contrario es "Sí".

---

### Sección 3: Análisis de Variaciones por Cuenta

```markdown
## Análisis de Variaciones por Cuenta

| Cuenta | Valor {año_actual} (COP MM) | Valor {año_anterior} (COP MM) | Variación % | Materialidad | Riesgo |
|--------|--------------------:|--------------------:|------------:|:------------:|:------:|
| {account_name 1} | {current_value formateado con comas} | {previous_value formateado con comas} | {signo}{variation_pct}% | {materiality en español} | {risk_level en español} |
| {account_name 2} | ... | ... | ... | ... | ... |
| {account_name N} | ... | ... | ... | ... | ... |

**Anomalías detectadas:** {Resumen de anomalías: "Ninguna crítica." si no hay, o descripción si las hay. Agregar contexto de riesgos identificados.}

---
```

- `materiality` se traduce: `HIGH` → `ALTA`, `MEDIUM` → `MEDIA`, `LOW` → `BAJA`.
- `risk_level` se traduce: `LOW` → `BAJO`, `MEDIUM` → `MEDIO`, `HIGH` → `ALTO`, `CRITICAL` → `CRÍTICO`.
- Las filas se ordenan por `materiality` (HIGH primero) y luego por `variation_pct` descendente en valor absoluto.
- Los valores numéricos se formatean con separadores de miles (coma).

---

### Sección 4: Riesgos Identificados

```markdown
## Riesgos Identificados

| Riesgo | Nivel | Descripción |
|--------|-------|-------------|
| Riesgo de Mercado | {nivel} | {descripción breve} |
| Riesgo de Liquidez | {nivel} | {descripción breve} |
| Riesgo Operacional | {nivel} | {descripción breve} |
| Riesgo Regulatorio | {nivel} | {descripción breve} |
| Riesgo ASG | {nivel} | {descripción breve} |
| Riesgo de Concentración | {nivel} | {descripción breve} |

---
```

> **Nota para el agente**: Los niveles de riesgo se derivan del análisis de las cuentas y del contexto del negocio. Siempre incluir las 6 categorías de riesgo listadas. Si no hay información suficiente para una categoría, su nivel es `BAJO` con descripción "Sin hallazgos materiales en el período."

---

### Sección 5: Notas NIIF Requeridas

Por cada entrada en `niif_note_drafts`, generar una subsección:

```markdown
## Notas NIIF Requeridas

### Nota {N}: {title} ({niif_reference})

{content — texto completo de la nota}

*Cuentas afectadas: {lista de account_name de los affected_account_ids, separados por comas}*
*Requiere revelación obligatoria.*

---
```

> Si `requires_disclosure` es `false`, omitir la línea `*Requiere revelación obligatoria.*` y reemplazar por `*Nota informativa interna.*`

---

### Sección 6: Indicadores de Cumplimiento

```markdown
## Indicadores de Cumplimiento

- **Score de validación:** {validation_score}/100
- **Salud financiera:** {overall_financial_health en español}
- **Flags de cumplimiento:** {lista de normas NIIF validadas} · {normativas regulatorias aplicadas}
- **Anomalías críticas:** {Sí / No}
- **Requiere revisión humana:** {Sí / No}
- **Confianza del análisis:** {Alta / Media / Baja según validation_score: >=80 → Alta, 60-79 → Media, <60 → Baja}
```

---

## EJEMPLO DE REFERENCIA COMPLETO

Ver archivo: `test_files/reporte_final_eeff_diciembre_2024.md`

Este archivo es el ejemplo canónico de output esperado. El agente debe generar un archivo con **exactamente la misma estructura**, adaptando todos los valores al caso específico analizado.

---

## CHECKLIST DE VALIDACIÓN ANTES DE ENTREGAR EL OUTPUT

El agente debe verificar que el archivo generado cumple:

- [ ] El archivo comienza con `<!-- CREDITIQ_REPORT` y termina el bloque JSON con `-->`
- [ ] El JSON es válido (sin errores de sintaxis)
- [ ] Todos los `niif_note_references` en `analysis_results` existen en `niif_note_drafts`
- [ ] Todos los `affected_account_ids` en `niif_note_drafts` existen en `analysis_results`
- [ ] Los cálculos de `variation_pct` y `absolute_variation` son correctos
- [ ] El Markdown tiene las 6 secciones en el orden correcto
- [ ] Los valores del JSON y del Markdown son consistentes (mismos números, mismas cuentas)
- [ ] Los textos están en español formal financiero
- [ ] No hay campos vacíos (excepto `pdf_report_url` que puede ser `null`)
- [ ] La tabla de "Análisis de Variaciones" contiene TODAS las cuentas de `analysis_results`
- [ ] Las notas NIIF del Markdown corresponden exactamente a las de `niif_note_drafts`
