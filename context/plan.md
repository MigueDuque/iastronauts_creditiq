# CreditIQ — Plan de Acción Técnico y Funcional
## AI Innovation Challenge 2026 — BTG Pactual Colombia

> Proyecto: Ecosistema Multiagente de IA para análisis financiero, generación automática de notas NIIF, rendición de cuentas y reporting ejecutivo.

---

# Objetivo Estratégico

Construir una plataforma serverless basada en IA multiagente capaz de:

- Analizar estados financieros
- Interpretar notas NIIF
- Detectar variaciones materiales
- Generar análisis financieros automáticos
- Crear borradores de notas contables
- Generar resúmenes ejecutivos
- Facilitar procesos de revisión contable
- Escalar a múltiples áreas corporativas

---

# Objetivo del Concurso

Cumplir y sobresalir en los criterios del AI Innovation Challenge:

- [ ] Innovación y Creatividad
- [ ] Escalabilidad e Impacto
- [ ] Viabilidad Técnica
- [ ] Presentación y Calidad
- [ ] Demo funcional WOW factor

---

# Estado Actual del Proyecto

## Infraestructura y Arquitectura

- [x] Definición de arquitectura serverless
- [x] Decisión AWS SAM
- [x] Diseño base backend
- [x] Diseño de estructura multiagente
- [x] Definición de uso de Bedrock/Anthropic
- [x] Servicio de conexión a LLMs
- [x] Wrapper para múltiples proveedores LLM
- [x] Soporte Bedrock
- [x] Soporte Anthropic API
- [x] Step Functions workflow completo (retries, catch, timeouts, logs)
- [x] Almacenamiento histórico en S3 (sin DynamoDB)

---

# Roadmap General del Proyecto

---

# FASE 1 — Diseño del Core del Sistema

## Objetivo

Diseñar el flujo completo funcional y técnico del sistema multiagente.

---

## 1.1 Definición de Inputs

### Documentos soportados

- [ ] PDF estados financieros
- [ ] Excel balances
- [ ] CSV financieros
- [ ] Notas a EEFF
- [ ] Informes de rendición de cuentas
- [ ] PPTs ejecutivos
- [ ] Trial Balance
- [ ] Auxiliares contables

---

## 1.2 Definición de Outputs

### Entregables generados por IA

- [ ] Resumen ejecutivo
- [ ] Notas NIIF draft
- [ ] Análisis financiero
- [ ] Variaciones materiales
- [ ] Hallazgos automáticos
- [ ] Alertas financieras
- [ ] Markdown report
- [ ] JSON estructurado
- [ ] PPT-ready summaries
- [ ] Draft para juntas directivas

---

## 1.3 Definición de Agentes

### Agente 1 — Extractor Documental

Responsabilidades:
- OCR
- Parsing PDF
- Lectura Excel
- Extracción tablas
- Normalización estructura

Estado:
- [ ] Diseño
- [ ] Desarrollo
- [ ] Testing

---

### Agente 2 — Estructurador Financiero

Responsabilidades:
- Clasificación cuentas
- Homologación financiera
- Estandarización datos

Estado:
- [ ] Diseño
- [ ] Desarrollo
- [ ] Testing

---

### Agente 3 — Motor NIIF

Responsabilidades:
- Aplicación reglas NIIF
- Validaciones contables
- Identificación materialidad
- Detección cuentas relevantes

Estado:
- [ ] Diseño
- [ ] Desarrollo
- [ ] Testing

---

### Agente 4 — Analista Financiero

Responsabilidades:
- Análisis variaciones
- Insights financieros
- Riesgos
- Hallazgos
- Explicaciones financieras

Estado:
- [ ] Diseño
- [ ] Desarrollo
- [ ] Testing

---

### Agente 5 — Narrador Ejecutivo

Responsabilidades:
- Redacción corporativa
- Notas financieras
- Lenguaje ejecutivo
- Resúmenes

Estado:
- [ ] Diseño
- [ ] Desarrollo
- [ ] Testing

---

### Agente 6 — Revisor Inteligente

Responsabilidades:
- Validación coherencia
- Anti-hallucination
- Verificación cifras
- Revisión narrativa

Estado:
- [ ] Diseño
- [ ] Desarrollo
- [ ] Testing

---

# FASE 2 — Arquitectura Técnica

## 2.1 Infraestructura AWS

### Servicios

- [ ] API Gateway
- [ ] AWS Lambda
- [x] Step Functions
- [x] S3
- [x] CloudFront
- [x] IAM

---

## 2.2 Orquestación

### Step Functions

- [x] Flujo principal
- [x] Manejo de errores (Catch → WorkflowFailed Fail state)
- [x] Retries automáticos (Lambda transient + TaskFailed, dos capas)
- [x] Timeout management (TimeoutSeconds por Task = Lambda timeout + 30s)
- [x] Logs centralizados (CloudWatch, nivel ERROR, 30 días retención)

---

## 2.3 Multi-tenancy

- [x] tenant_id propagado a través de todos los agentes
- [x] Separación lógica en S3 por prefijo `reports/{tenant_id}/`
- [ ] Seguridad contextual (autenticación / autorización por tenant)

---

# FASE 3 — Data Contracts y Schemas

## Objetivo

Estandarizar comunicación entre agentes.

---

## 3.1 Schemas principales

- [ ] FinancialStatement
- [ ] FinancialNote
- [ ] VarianceAnalysis
- [ ] ExecutiveSumm