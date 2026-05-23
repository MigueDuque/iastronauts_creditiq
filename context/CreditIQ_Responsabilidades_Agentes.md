# CreditIQ — Responsabilidades Detalladas de los Agentes
## Basado en Contracts JSON + Propagación de Contexto

Documento alineado con:
- arquitectura multiagente final
- contracts JSON oficiales
- propagación de contexto
- Step Functions orchestration
- comunicación estructurada entre agentes

Fuente de contexto oficial:
:contentReference[oaicite:0]{index=0}

---

# Filosofía General del Sistema

La arquitectura NO está basada en agentes conversacionales.

Los agentes:
- NO se hablan entre sí en lenguaje natural
- NO generan contexto libre
- NO reconstruyen información

La arquitectura está basada en:
# contratos JSON tipados y propagación de contexto estructurado.

---

# Flujo General del Sistema

text
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


---

# Principio Arquitectónico Clave

## El output de un agente es el input del siguiente.

Ningún agente:
- recalcula contexto
- vuelve a extraer información
- vuelve a interpretar metadata global

Todo se propaga desde:
# Orchestrator Agent

:contentReference[oaicite:1]{index=1}

---

# ORCHESTRATOR AGENT — Responsabilidades Detalladas

# Objetivo General

Ser el:
# punto de entrada central del sistema.

Este agente:
- NO analiza documentos
- NO hace reasoning financiero
- NO genera reportes

Su responsabilidad es:
# coordinar y estructurar el pipeline completo.

---

# Responsabilidades Principales

---

# 1. Recepción de Inputs

## Responsabilidad

Recibir todos los inputs iniciales del sistema.

---

## Inputs recibidos

- documentos financieros
- instrucciones del usuario
- contexto de negocio
- metadata financiera
- configuraciones de salida

---

## Ejemplo

json
{
  "uploaded_files": [],
  "raw_context": "Empresa del sector financiero..."
}


---

# 2. Validación Inicial

## Responsabilidad

Validar:
- estructura inicial
- payloads
- formatos
- archivos mínimos requeridos

---

# 3. Generación de job_id

## Responsabilidad

Generar identificador único global.

---

## Objetivo

Permitir:
- trazabilidad
- auditoría
- debugging
- seguimiento pipeline

---

# 4. Conversión de Contexto Libre → JSON Estructurado

# RESPONSABILIDAD MÁS IMPORTANTE

---

## Qué hace

Convierte:

text
"Empresa financiera con exposición..."


en:

json
{
  "industry": "financial_services",
  "strategic_context": "...",
  "reporting_period": "2025"
}


---

# 5. Normalización Global de Contexto

## Responsabilidad

Crear:
# el contexto maestro propagado a TODOS los agentes.

---

# Contexto global propagado

json
{
  "job_id": "",
  "tenant_id": "",
  "business_context": {},
  "niif_standards": [],
  "report_language": "",
  "output_formats": []
}


---

# 6. Coordinación del Pipeline

## Responsabilidad

Disparar:
- Step Functions
- secuencia agentes
- manejo workflow

---

# 7. Persistencia Inicial

## Responsabilidad

Guardar:
- metadata
- estado inicial
- contexto global

en:
- DynamoDB
- S3 JSON state

---

# Output Oficial

## Produce

json
OrchestratorOutput


---

# Responsabilidad Final

# “Coordinar, estructurar y propagar el contexto global del sistema.”

---

---

# DOCUMENT EXTRACTOR — Responsabilidades Detalladas

# Objetivo General

Transformar documentos financieros no estructurados en:
# información financiera estructurada y normalizada.

---

# Input Oficial

## Recibe

json
OrchestratorOutput


---

# Información que recibe

- archivos S3
- business_context
- niif_standards
- idioma reporte
- formatos salida
- contexto financiero global

---

# Responsabilidades Principales

---

# 1. Validación Documental

## Responsabilidad

Validar:
- formato
- integridad
- tamaño
- accesibilidad S3

---

# 2. Clasificación Documental

## Responsabilidad

Detectar:
- tipo documento
- idioma
- periodo
- estructura financiera

---

## Ejemplos

- EEFF
- notas NIIF
- rendición cuentas
- trial balance

---

# 3. OCR y Parsing

## Responsabilidad

Extraer:
- texto
- tablas
- encabezados
- subtotales

---

## Tecnologías

- Textract
- OCR
- pandas
- openpyxl

---

# 4. Extracción Financiera

## Responsabilidad

Identificar:
- cuentas financieras
- valores
- periodos
- monedas
- referencias notas

---

# 5. Normalización Financiera

## Responsabilidad

Homologar:
- nombres cuentas
- categorías
- estructura financiera

---

## Ejemplo

text
"Activos financieros a valor razonable"


↓

text
financial_assets_fair_value


---

# 6. Confidence Scoring

## Responsabilidad

Calcular:
- confidence OCR
- confidence parsing
- warnings extracción

---

# 7. Construcción del Financial Schema

## Responsabilidad

Generar:
# ExtractorOutput

---

# Output Oficial

## Produce

json
ExtractorOutput


---

# Output contiene

- accounts[]
- confidence_score
- periods[]
- company_name
- currency
- extraction_warnings

---

# Responsabilidad Final

# “Convertir documentos financieros complejos en datos financieros estructurados y normalizados.”

---

---

# FINANCIAL ANALYZER — Responsabilidades Detalladas

# Objetivo General

Interpretar financieramente la información estructurada.

Este agente:
# PIENSA como analista financiero.

---

# Input Oficial

## Recibe

json
ExtractorOutput


---

# Información que recibe

- cuentas normalizadas
- valores financieros
- periodos
- confidence scores
- contexto negocio
- estándares NIIF

---

# Responsabilidades Principales

---

# 1. Variance Analysis

## Responsabilidad

Calcular:
- variaciones absolutas
- variaciones porcentuales
- tendencias
- comparativos

---

# 2. Materiality Analysis

## Responsabilidad

Clasificar:
- LOW
- MEDIUM
- HIGH

---

## Aplicar thresholds financieros

Ejemplo:

python
if variation_pct > 20:


---

# 3. NIIF Rules Engine

## Responsabilidad

Aplicar:
- reglas NIIF
- compliance financiero
- obligatoriedad notas
- disclosure requirements

---

# 4. Financial Reasoning

## Responsabilidad

Interpretar:
- comportamiento financiero
- riesgos
- tendencias
- causas posibles

---

# 5. Hypothesis Generation

## Responsabilidad

Generar:
- hipótesis financieras
- posibles causas
- interpretaciones económicas

---

# 6. Executive Insights

## Responsabilidad

Generar:
- executive_insight
- overall financial health
- narrativa financiera base

---

# 7. Identificación de Riesgos Financieros

## Responsabilidad

Detectar:
- anomalías
- riesgos financieros
- comportamientos sospechosos

---

# Output Oficial

## Produce

json
AnalyzerOutput


---

# Output contiene

- analysis_results[]
- materiality
- risk_level
- niif flags
- executive insights
- overall financial health

---

# Responsabilidad Final

# “Entender financieramente lo que está ocurriendo.”

---

---

# RISK SCORER — Responsabilidades Detalladas

# Objetivo General

Validar:
- confiabilidad
- consistencia
- compliance
- calidad análisis
- riesgo general

---

# Input Oficial

## Recibe

json
AnalyzerOutput


---

# Información que recibe

- insights financieros
- cálculos variaciones
- reglas NIIF
- análisis ejecutivos
- anomalías detectadas

---

# Responsabilidades Principales

---

# 1. Validation Engine

## Responsabilidad

Validar:
- cálculos
- coherencia matemática
- consistencia narrativa
- integridad análisis

---

# 2. Anti-Hallucination Engine

## Responsabilidad

Detectar:
- insights inventados
- explicaciones inconsistentes
- contradicciones
- reasoning sin soporte

---

# 3. Compliance Validation

## Responsabilidad

Validar:
- reglas regulatorias
- compliance financiero
- disclosures requeridos
- validaciones NIIF

---

# 4. Risk Scoring

## Responsabilidad

Calcular:
- overall_risk_score
- validation_score
- analysis_confidence

---

# 5. Human Review Decision

## Responsabilidad

Decidir:
- requires_human_review
- auto-approval
- escalation_required

---

# 6. Quality Assurance

## Responsabilidad

Garantizar:
- calidad reporte
- consistencia outputs
- seguridad narrativa

---

# Output Oficial

## Produce

json
ScorerOutput


---

# Output contiene

- validation_score
- compliance_flags
- issues_found
- anti_hallucination_passed
- requires_human_review

---

# Responsabilidad Final

# “Validar que el análisis sea consistente, confiable y seguro.”

---

---

# REPORT GENERATOR — Responsabilidades Detalladas

# Objetivo General

Transformar análisis financieros complejos en:
# entregables corporativos ejecutivos.

---

# Input Oficial

## Recibe

json
ScorerOutput


+

contexto propagado previamente:
- AnalyzerOutput
- business_context
- niif_standards
- report_language

---

# Responsabilidades Principales

---

# 1. Executive Narrative Generation

## Responsabilidad

Generar:
- executive_summary
- board_summary
- narrativa financiera

---

# 2. NIIF Draft Generation

## Responsabilidad

Construir:
- notas NIIF
- disclosures
- explicaciones contables

---

# 3. Corporate Formatting

## Responsabilidad

Aplicar:
- templates
- branding
- estructura reportes
- consistencia narrativa

---

# 4. Board Presentation Preparation

## Responsabilidad

Generar:
- PPT summaries
- bullet points ejecutivos
- highlights corporativos

---

# 5. Multi-format Exporting

## Responsabilidad

Generar:
- Markdown
- PDF
- PPT
- JSON export

---

# 6. Final Packaging

## Responsabilidad

Subir reportes finales a:
- S3
- storage final

---

# Output Oficial

## Produce

json
FinalReportOutput


---

# Output contiene

- executive_summary
- niif_note_drafts[]
- board_summary
- markdown_report_url
- pdf_report_url
- ppt_report_url

---

# Responsabilidad Final

# “Transformar análisis financieros en entregables ejecutivos listos para negocio.”

---

# Resumen Arquitectónico Final

| Agente | Responsabilidad Principal |
|---|---|
| Orchestrator | Coordinar y propagar contexto |
| Document Extractor | Extraer y estructurar información |
| Financial Analyzer | Interpretar y analizar financieramente |
| Risk Scorer | Validar, controlar y evaluar riesgos |
| Report Generator | Generar entregables corporativos |

---

# Filosofía Final del Sistema

Cada agente:
- tiene responsabilidades claras
- recibe contracts tipados
- devuelve contracts tipados
- NO reconstruye contexto
- NO hace trabajo de otros agentes
- NO genera outputs ambiguos

Esto convierte a CreditIQ en:
# una plataforma multiagente enterprise real.