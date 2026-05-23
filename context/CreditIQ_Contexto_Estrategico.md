# CreditIQ — Contexto Estratégico, Arquitectura y Diseño Multiagente
**AI Innovation Challenge 2026 · BTG Pactual Colombia**

---

## Visión General

**CreditIQ** es una plataforma enterprise basada en IA multiagente orientada al análisis financiero corporativo automatizado. Transforma documentos financieros complejos en análisis inteligentes, notas NIIF automáticas, insights ejecutivos, reportes corporativos, entregables para juntas directivas, análisis de riesgos y narrativa financiera profesional.

---

## Problema de Negocio

Los procesos financieros relacionados con estados financieros, notas a los EEFF, rendiciones de cuentas, reportes ejecutivos e informes regulatorios son actualmente:

- Altamente manuales y repetitivos
- Costosos y lentos
- Dependientes del conocimiento humano
- Propensos a errores

## Insight Estratégico

Las notas financieras y los informes corporativos tienen patrones repetitivos, reglas NIIF claras, narrativa homogénea, estructuras comparativas, modelos históricos reutilizables y lógica financiera consistente. Esto los convierte en un caso de uso ideal para IA.

---

## Objetivo: "Dora la Contadora"

Un sistema inteligente capaz de interpretar estados financieros, generar análisis automáticos, aplicar reglas NIIF, generar narrativa financiera, producir entregables corporativos y asistir contadores y analistas financieros.

---

## Arquitectura

### Stack Tecnológico

**Backend:** Python · AWS Lambda · AWS SAM · Step Functions · API Gateway · DynamoDB · S3 · Cognito

**IA:** Anthropic API / Amazon Bedrock mediante wrapper desacoplado (evita vendor lock-in)

### Arquitectura Serverless (AWS)

Escalado a cero, event-driven, escalabilidad instantánea, orquestación compleja con Step Functions.

### Cuatro Agentes Principales

```
UPLOAD DOCUMENTS
      ↓
ORCHESTRATOR AGENT
      ↓
DOCUMENT EXTRACTOR
      ↓
FINANCIAL ANALYZER
      ↓
RISK SCORER
      ↓
REPORT GENERATOR
      ↓
ENTREGABLES CORPORATIVOS
```

**Filosofía:** Los agentes NO se comunican en lenguaje natural. Se comunican mediante contratos JSON estructurados validados con Pydantic.

---

## Agentes

### Orchestrator Agent
Punto de entrada único. Recibe todos los inputs, valida, normaliza, convierte el contexto de negocio (texto libre) a JSON estructurado mediante LLM, genera el `job_id` único y coordina el pipeline vía Step Functions. No procesa documentos ni genera análisis.

### Agente 1 — Document Extractor
Transforma documentos financieros desordenados en información estructurada. Integra: validación documental, clasificación, OCR/Textract, extracción financiera y normalización de cuentas.

**Input:** `OrchestratorOutput` (archivos en S3 + contexto global)
**Output:** `ExtractorOutput` con `FinancialAccount[]`, periodos, moneda, confidence scores

### Agente 2 — Financial Analyzer
Interpreta financieramente la información estructurada. Calcula variaciones, detecta materialidad, aplica reglas NIIF y genera insights ejecutivos.

**Input:** `ExtractorOutput`
**Output:** `AnalyzerOutput` con `VarianceAnalysis[]`, flags NIIF, executive narrative, overall financial health

### Agente 3 — Risk Scorer
Valida calidad, riesgos y confiabilidad del análisis. Validación matemática, detección de alucinaciones, compliance regulatorio y scoring de confiabilidad.

**Input:** `AnalyzerOutput`
**Output:** `ScorerOutput` con validation score (0–100), compliance flags, `requires_human_review`

### Agente 4 — Report Generator
Transforma análisis técnicos en entregables ejecutivos corporativos: notas NIIF, executive summary, board summary, PDF, Markdown, PPT.

**Input:** `ScorerOutput`
**Output:** `FinalReportOutput` con contenido del reporte y URLs S3

---

## Contratos JSON (resumen)

### OrchestratorOutput → ExtractorInput
```json
{
  "job_id": "uuid",
  "tenant_id": "string",
  "created_at": "ISO8601",
  "business_context": {
    "company_name": "string | null",
    "industry": "string | null",
    "fiscal_year": "string | null",
    "reporting_period": "string | null",
    "key_events": ["string"],
    "strategic_context": "string | null",
    "regulatory_context": "string | null",
    "analyst_instructions": ["string"],
    "raw_context": "string"
  },
  "files_to_process": [
    {
      "file_name": "eeff_2025.pdf",
      "s3_location": "s3://bucket/eeff_2025.pdf",
      "file_type": "pdf"
    }
  ],
  "historical_eeff_json": {},
  "niif_standards": ["NIIF_1", "NIIF_7", "NIIF_9"],
  "report_language": "es",
  "output_formats": ["markdown", "pdf"]
}
```

### ExtractorOutput → AnalyzerInput
```json
{
  "job_id": "uuid",
  "tenant_id": "string",
  "business_context": {},
  "niif_standards": [],
  "report_language": "es",
  "output_formats": [],
  "company_name": "BTG Pactual",
  "statement_type": "balance_sheet",
  "currency": "COP",
  "periods": ["2025", "2024"],
  "accounts": [
    {
      "account_id": "ACC_001",
      "raw_account_name": "Activos financieros a valor razonable",
      "normalized_account_name": "financial_assets_fair_value",
      "category": "assets",
      "subcategory": "investment_assets",
      "current_value": 1200000000,
      "previous_value": 980000000,
      "currency": "COP",
      "confidence_score": 0.97,
      "source_file": "eeff_2025.pdf"
    }
  ],
  "extraction_confidence": 0.95,
  "extraction_warnings": []
}
```

### AnalyzerOutput → ScorerInput
```json
{
  "job_id": "uuid",
  "company_name": "BTG Pactual",
  "analysis_results": [
    {
      "account_id": "ACC_001",
      "account_name": "financial_assets_fair_value",
      "current_value": 1200000000,
      "previous_value": 980000000,
      "absolute_variation": 220000000,
      "variation_pct": 22.45,
      "materiality": "HIGH",
      "requires_niif_note": true,
      "niif_note_references": ["NIIF_9"],
      "risk_level": "MEDIUM",
      "possible_causes": ["Incremento en valorización de activos de inversión"],
      "executive_insight": "La variación refleja un incremento significativo...",
      "anomaly_detected": false
    }
  ],
  "high_materiality_accounts": ["ACC_001"],
  "niif_notes_required": ["NIIF_9"],
  "overall_financial_health": "STABLE",
  "executive_narrative": "Durante el periodo 2025..."
}
```

### ScorerOutput → ReportGeneratorInput
```json
{
  "job_id": "uuid",
  "validation_score": 94,
  "overall_risk_score": "MEDIUM",
  "issues_found": [],
  "compliance_flags": [],
  "requires_human_review": false,
  "analysis_confidence": 0.94,
  "anti_hallucination_passed": true
}
```

### FinalReportOutput
```json
{
  "job_id": "uuid",
  "company_name": "BTG Pactual",
  "generated_at": "ISO8601",
  "validation_score": 94,
  "overall_risk_score": "MEDIUM",
  "executive_summary": "Durante el periodo 2025...",
  "board_summary": "Resumen ejecutivo para junta directiva...",
  "niif_note_drafts": [
    {
      "note_id": "NOTE_001",
      "niif_reference": "NIIF_9",
      "title": "Activos financieros a valor razonable",
      "content": "De conformidad con la NIIF 9...",
      "affected_account_ids": ["ACC_001"],
      "requires_disclosure": true
    }
  ],
  "markdown_report_url": "s3://creditiq/reports/job_id/report.md",
  "pdf_report_url": "s3://creditiq/reports/job_id/report.pdf",
  "ppt_report_url": "s3://creditiq/reports/job_id/report.pptx"
}
```

---

## Principios de Propagación de Contexto

El contexto global (`job_id`, `tenant_id`, `business_context`, `niif_standards`, `report_language`, `output_formats`) se propaga en cada contrato desde el orquestador hasta el reporte final. Ningún agente reconstruye contexto; el output de cada agente es el input del siguiente.

---

## Tipos de Responsabilidad por Agente

| Agente | Tipo de lógica |
|---|---|
| Orchestrator | Coordinación / determinística |
| Document Extractor | Extracción / determinística |
| Financial Analyzer | Análisis / híbrida (reglas + LLM) |
| Risk Scorer | Validación / híbrida |
| Report Generator | Narrativa / generativa |

---

## Roadmap

1. Definir inputs, outputs, contratos, schemas
2. Construir Base Models Pydantic y estructura financiera
3. Construir Document Extractor (cuello de botella, primer WOW)
4. Construir Financial Analyzer (variaciones, insights, materialidad)
5. Construir Risk Scorer (validación, compliance, scoring)
6. Construir Report Generator (narrativa, PDF, Markdown, PPT)

---

## Narrativa del Pitch

> "Sistema Inteligente Multiagente para Automatización y Análisis Financiero Corporativo"

El proyecto demuestra dos innovaciones: IA como producto (la plataforma financiera) e IA como acelerador de desarrollo (uso de Claude, prompting engineering, generación de código, productividad).

El concurso se gana con wow factor, claridad, demo funcional, impacto real y storytelling — no con dashboards complejos ni auth avanzada.
