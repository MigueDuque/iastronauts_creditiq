# CreditIQ — Plataforma Multi-Agente de Análisis Financiero

**BTG Pactual AI Challenge 2026 · Equipo iastronauts**

> CreditIQ ingiere estados financieros (PDF / Excel / CSV), los normaliza bajo NIIF,
> y ejecuta un pipeline de agentes de IA que producen análisis de variaciones,
> scoring de riesgo, notas NIIF y reportes ejecutivos auditables — con un humano
> en el bucle entre cada etapa.

---

## 1. Resumen ejecutivo

CreditIQ resuelve un problema concreto del análisis de crédito y de fondos de inversión:
convertir estados financieros heterogéneos y semiestructurados en un **dictamen
financiero estructurado, trazable y revisable** en minutos en lugar de horas.

La plataforma combina:

- **Motores determinísticos** (la "matemática" — ratios, materialidad, anomalías,
  concentración, NIIF 18, análisis de fondos) que producen cifras exactas y reproducibles.
- **Agentes LLM (Claude)** que **narran e interpretan** esas cifras, pero **nunca las
  sobrescriben** (regla del "techo del LLM").
- **Compuertas de revisión humana** (human-in-the-loop) entre cada agente, de modo que
  un analista valida cada etapa antes de continuar.

Principio rector de todo el sistema: **"Math First, Synthesis Second, LLM Third"** —
primero la matemática determinística, luego la síntesis estructurada, y solo al final
el LLM para redactar.

---

## 2. El problema

El análisis financiero tradicional para crédito y gestión de fondos enfrenta:

1. **Heterogeneidad de fuentes** — los EEFF llegan en PDF escaneado, Excel multi-hoja o CSV,
   cada uno con estructuras y nomenclaturas distintas.
2. **Trabajo manual y propenso a error** — extracción, normalización a plan de cuentas,
   cálculo de variaciones y ratios, todo a mano.
3. **Falta de trazabilidad** — los reportes ejecutivos rara vez muestran *cómo* se llegó
   a cada cifra, lo que dificulta la auditoría y el cumplimiento NIIF.
4. **Riesgo de alucinación** — usar un LLM "a secas" sobre cifras financieras produce
   números inventados, inaceptables en un contexto regulado.

CreditIQ ataca los cuatro: extracción multiformato, normalización NIIF automática,
cálculo determinístico con traza de fórmulas, y un LLM acotado que solo interpreta.

---

## 3. Arquitectura general

CreditIQ es una aplicación **serverless** sobre AWS, orquestada por **Step Functions**,
con un frontend SPA en React.

```
┌─────────────────────────────────────────────────────────────────────┐
│  Frontend (React 19 + Vite + TS)                                      │
│  Upload → Pipeline UX en 3 fases con compuertas de revisión humana    │
└───────────────┬───────────────────────────────────────────────────────┘
                │  REST (API Gateway)
┌───────────────▼───────────────────────────────────────────────────────┐
│  API Layer (AWS Lambda, Python 3.12)                                   │
│  /upload-url · /analyses · /analyses/{id} · /continue · /report · …     │
└───────────────┬───────────────────────────────────────────────────────┘
                │  inicia ejecución
┌───────────────▼───────────────────────────────────────────────────────┐
│  Step Functions — pipeline secuencial con compuertas de pausa          │
│                                                                         │
│  DocumentExtractor → [pausa] → FinancialAnalyzer → [pausa] →            │
│  RiskScorer → [pausa] → ReportGenerator → [pausa] → RevisorInteligente  │
│                                                                         │
│  Cada [pausa] = waitForTaskToken: el analista revisa y reanuda.         │
└───────────────┬───────────────────────────────────────────────────────┘
                │
┌───────────────▼───────────────────────────────────────────────────────┐
│  Persistencia: 100% S3 (sin DynamoDB)                                   │
│  uploads/ · jobs/{job_id}/ · reports/{tenant}/{empresa}/{año}/{mes}/    │
└─────────────────────────────────────────────────────────────────────────┘
```

### Decisiones de diseño clave

- **Pipeline secuencial con state-passing**: cada agente recibe el output completo del
  anterior como dict validado por Pydantic. El `tenant_id` fluye por **todos** los outputs
  (romper esa cadena causa un `ValidationError` en el agente *siguiente*).
- **Claim-check pattern**: Step Functions tiene un límite de payload de 256 KB. Los agentes
  devuelven un *envelope* delgado (puntero a S3) y rehidratan su input completo desde S3
  vía `shared/agent_handoff.py`. Nunca se devuelve un dict pesado desde un Lambda.
- **Persistencia 100% S3**: sin DynamoDB. Toda la persistencia pasa por
  `shared/s3_report_store.py` y `shared/job_store.py`.
- **Three-tier fallback** para prompts y plantillas: **S3 → archivo local → string inline**,
  de modo que el sistema funciona aunque S3 no tenga la última versión.

---

## 4. El pipeline de 5 agentes

| # | Agente | Rol | Naturaleza |
|---|--------|-----|------------|
| 1 | **DocumentExtractor** | Extrae cuentas de PDF/Excel/CSV y las normaliza | Textract + pandas/openpyxl + LLM |
| 2 | **FinancialAnalyzer** | Análisis financiero: ratios, variaciones, tesis | 14 motores determinísticos + 4 sub-agentes LLM |
| 3 | **RiskScorer** | Scoring de riesgo multidimensional | 5 motores determinísticos + narrativa LLM |
| 4 | **ReportGenerator** | Genera el reporte ejecutivo (.docx/.md) | Plantilla RAG + LLM |
| 5 | **RevisorInteligente** | Control de calidad / validación cruzada | 6 categorías de validación + LLM |

### Agente 1 — DocumentExtractor

- **PDF** vía AWS Textract; **Excel/CSV** vía pandas/openpyxl.
- Un LLM (por defecto Haiku, el más económico) normaliza nombres de cuenta a un plan
  estándar y clasifica cada cuenta.
- Enriquece cada cuenta con campos no triviales:
  - `source_sheet` — hoja Excel de origen (clave para análisis de concentración).
  - `is_total` — marca filas de subtotal/total, que se excluyen para no doble-contar.
  - `investment_type` — `equity | bond | sovereign_debt | trust_rights | futures | fund | cash`.
  - `issuer_name` — emisor, preferido sobre el nombre de cuenta en vistas de concentración.

### Agente 2 — FinancialAnalyzer

El corazón analítico. Filosofía **"Math First, Synthesis Second, LLM Third"**:

**14 motores determinísticos** (todas las cifras salen de aquí, no del LLM):

| Motor | Propósito |
|-------|-----------|
| `ratio_engine` | Variaciones, totales, ratios financieros, subtotales NIIF 18 |
| `materiality_engine` | Umbral de materialidad (1% del máximo de activos/ingresos) |
| `trend_engine` | Etiquetas de tendencia por cuenta |
| `anomaly_detector` | Anomalías a nivel de cuenta y estructurales |
| `variation_reliability` | Marca `NEW_ACCOUNT`, `INSUFFICIENT_BASELINE`, `EXTREME_VARIATION` |
| `causality_engine` | Cadenas causales entre cuentas |
| `earnings_quality` | Calidad de utilidades (fair value vs. operativo) |
| `concentration_engine` | HHI de concentración de cartera |
| `sheet_concentration_engine` | Tres vistas: activos, instrumentos+emisores, bancos |
| `niif18_engine` | Flags de cumplimiento NIIF 18 |
| `fund_engine` | Detección de fondo, reconciliación de NAV, posiciones |
| `kpi_engine` | Tarjetas KPI del dashboard |
| `synthesis_engine` | Historia determinística del portafolio antes del LLM |
| `financial_diagnostics_engine` | Señales heurísticas cruzadas entre estados |

**4 sub-agentes LLM** (refactorizados desde una sola llamada de 32k tokens que producía
salidas vacías con 60+ cuentas — ahora cada uno recibe un digest compacto ≤6k tokens):

| Sub-agente | Función |
|------------|---------|
| `movement_intelligence` | Interpreta los movimientos materiales |
| `causality_agent` | Explica relaciones causa-efecto |
| `thesis_agent` | Construye la tesis del portafolio |
| `narrative_agent` | Redacta la narrativa ejecutiva |

**Regla del techo del LLM**: el LLM no puede sobrescribir los niveles de riesgo ni las
cifras determinísticas.

### Agente 3 — RiskScorer

5 motores determinísticos → score compuesto → narrativa LLM. Es **fund-aware**
(pesos ajustados según sea fondo o empresa operativa):

- **Liquidez**, **Solvencia** → agrupadas en *Riesgo Financiero*.
- **Crédito** → concentración de **contraparte/custodio** (`bank_breakdown`).
- **Mercado** → riesgo de **tasa de interés** (renta fija vía `instrument_breakdown`)
  + exposición **cambiaria (FX)**.
- **Operacional**.

La salida se reagrupa en 3 categorías orientadas al reporte:
**Riesgo de Crédito / Riesgo de Mercado / Riesgo Financiero**.

### Agente 4 — ReportGenerator

- Lee una **plantilla .docx** (patrón RAG) desde S3 y la rellena vía LLM.
- Escribe a dos prefijos S3: `jobs/` (operativo) y `reports/` (habilita comparación
  histórica y detección de duplicados).
- El `.md` lleva un bloque JSON legible por máquina
  (`<!-- CREDITIQ_REPORT {...} -->`) seguido del markdown legible por humanos.

### Agente 5 — RevisorInteligente

Control de calidad automático en **6 categorías**: estructural, matemática, referencias
cruzadas, lógica de negocio, consistencia, y narrativa (LLM). Penaliza ERROR = −10,
WARNING = −3 y produce un *score de validación* sobre 100.

---

## 5. Capacidades diferenciadoras

### 5.1 Análisis especializado de fondos de inversión

A diferencia de un analizador genérico de EEFF, CreditIQ **detecta automáticamente fondos**
y adapta el análisis:

- **Reconciliación de NAV**: apertura + aportes − redenciones + retorno = cierre.
- **Concentración de cartera** por activo, por instrumento y por emisor (HHI).
- **AUM** como KPI principal (se eliminó "Razón Corriente" por no ser significativa en fondos).
- Pesos de riesgo específicos para fondos.

### 5.2 Perspectivas de análisis por rol

El usuario elige **quién** realiza la evaluación, y los agentes 2/3/4 adaptan énfasis,
orden y tono — **nunca las cifras**. Catálogo de **9 roles cerrados**:
`general` (default), `fund_manager`, `financial_analyst`, `financial_manager`,
`fiscal_reviewer`, `external_auditor`, `board_member`, `risk_investments`, `accountant`.

La inyección del contexto de rol viaja en el sufijo dinámico del prompt, de modo que llega
a cada llamada LLM **sin invalidar el prompt-cache** y sin tocar la matemática determinística.

### 5.3 Comparación histórica automática

`fetch_historical_reports()` recupera reportes del mismo trimestre del año anterior
+ diciembre del año previo, para análisis de variación interanual.
Clave S3: `reports/{tenant_id}/{empresa}/{YYYY}/{MM}/report_{job_id}.md`.

### 5.4 Trazabilidad y anti-alucinación

- Cada KPI lleva un `_computation_trace` (fórmula + inputs + resultado) que el frontend
  muestra al pasar el cursor sobre la tarjeta.
- El RiskScorer reporta un resultado anti-alucinación con los checks ejecutados/fallidos.
- El Agente 5 valida matemáticamente el reporte antes de darlo por bueno.

### 5.5 Contexto de mercado (módulo complementario)

Agentes adicionales fuera del pipeline principal:
- **market_ingestion** — ingiere FX/índices (yfinance), macro de Colombia (Trading
  Economics) y titulares financieros (GNews).
- **market_interpreter** — clasifica movimientos y genera señales (misma regla "Math First").
- **management_report** — genera un reporte de gobierno/comité bajo demanda (opt-in).

---

## 6. Human-in-the-loop: el pipeline en 3 fases

La UX del frontend refleja las compuertas de Step Functions:

1. **Subir documentos** → corre Agente 1 → estado `extraction_complete` → se muestra la
   tabla de cuentas extraídas para revisión.
2. **"Continuar"** → Agente 2 → `analysis_complete` → se muestra el análisis financiero.
3. **"Continuar"** → Agentes 3 + 4 + 5 → `completed` → se muestra el reporte y el QA.

Estados posibles del job:
`pending | processing | extraction_complete | analysis_complete | scoring_complete | report_complete | completed | failed | cancelled`.

Cada compuerta usa un **task token de un solo uso** (`waitForTaskToken`); el endpoint
`/continue` reanuda la ejecución con `SendTaskSuccess`. Las pausas tienen *heartbeat*
para expirar revisiones abandonadas (`ReviewExpired`).

---

## 7. Seguridad multi-tenant

- **`tenant_context.py`** — `TenantContext` inmutable construido en la frontera de la API;
  `assert_s3_key()` fuerza los límites `uploads/{tid}/`, `reports/{tid}/`, `rag/{tid}/`.
- **`tenant_middleware.py`** — `extract_tenant_context()` con prioridad
  **JWT → header `x-tenant-id` → body**. JWT obligatorio en producción (AWS Cognito).
- **`audit_logger.py`** — `log_audit_event()` nunca lanza excepción ni bloquea el happy path.
- **Aislamiento de IA**: el prompt inyecta un bloque de frontera de tenant cuando hay
  `tenant_id`, de modo que un LLM no mezcle datos entre clientes.

---

## 8. Stack tecnológico

**Backend**
- AWS SAM (serverless), Python 3.12
- AWS Lambda · Step Functions · API Gateway · S3 · Textract · Cognito
- Anthropic Claude (vía SDK propio o Bedrock — conmutable por `LLM_PROVIDER`)
- Pydantic 2 (validación de modelos), pandas/openpyxl (Excel), tenacity (reintentos)

**Frontend**
- React 19 + Vite + TypeScript
- Tailwind CSS v4 (`@theme` en `index.css` como fuente de verdad del design system)
- KaTeX (fórmulas), MUI (componentes)

**Proveedor LLM** (`shared/llm_provider.py`) — **toda** llamada LLM pasa por este wrapper:
- `anthropic_api` → SDK Anthropic, modelo por defecto `claude-sonnet-4-6`
- `bedrock` → boto3 Converse API
- Métodos: `generate_text() → str`, `generate_json() → dict`; ambos inyectan la frontera
  de tenant y el contexto de rol en el system prompt.

---

## 9. Calidad e ingeniería: el harness de evaluación

CreditIQ reemplaza el "corre un análisis y míralo a ojo" por un **scorecard de regresión**:

- Cada caso golden corre por el **camino determinístico real** (sin LLM, sin AWS) y
  compara `probes` (scores/niveles por dimensión, compuesto, categorías de riesgo)
  contra un snapshot `expected.json`.
- Tras un cambio intencional del motor: `--update` y **el `git diff` de `expected.json`
  es la revisión**. Un movimiento no intencional de un probe es exactamente la regresión
  que el harness existe para atrapar (p. ej. doble conteo de hojas).

```bash
pytest tests/                          # smoke + plantilla + remediación + casos eval
python -m tests.eval.runner            # scorecard del motor (exit 1 en regresión)
python -m tests.eval.runner --update   # re-snapshot tras un cambio intencional
```

El núcleo determinístico de cada agente es una función pura, sin LLM ni S3, que el eval
puede invocar directamente (patrón `risk_scorer/scoring.py::compute_risk`).

---

## 10. Modelos y estructura de datos clave

**Enums** (`shared/models/base.py`):
- `MaterialityLevel` / `RiskLevel`: `LOW | MEDIUM | HIGH`
- `FinancialHealth`: `STABLE | DECLINING | GROWING | CRITICAL` + estados de fondo
  (`LIQUID | LEVERAGED | SPECULATIVE | CASH_STRESSED | VALUATION_DRIVEN | CONCENTRATED`)
- `OutputFormat`: `markdown | pdf`

**Cadena de outputs** (cada uno valida al siguiente vía Pydantic):
`OrchestratorOutput → ExtractorOutput → AnalyzerOutput → ScorerOutput → FinalReportOutput → RevisorOutput`

**Estructura S3**:
```
iastronauts-creditiq-us-east-1-dev/
├── instructions/        ← plantillas RAG, referencia NIIF 18, prompts de producción
├── uploads/{period}/    ← documentos del cliente
├── jobs/{job_id}/       ← status.json, outputs intermedios, reporte final
└── reports/{tenant}/{empresa}/{año}/{mes}/report_{job_id}.md
```

---

## 11. Despliegue

**Backend**
```bash
cd iastronauts_creditiq_back
sam build --use-container
sam deploy --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM \
  --parameter-overrides Stage=dev LlmProvider=anthropic_api AnthropicApiKey=YOUR_KEY
```

**Desarrollo local** (sin Docker, sin SAM): `local_server.py` envuelve los Lambdas en
FastAPI traduciendo HTTP → evento Lambda.
```bash
uvicorn local_server:app --reload --port 8000
```

**Frontend**
```bash
cd iastronauts_creditiq_front
npm install && npm run dev    # localhost:5173
npm run build                 # type-check + build de producción
```

---

## 12. Endpoints principales de la API

| Método | Ruta | Propósito |
|--------|------|-----------|
| `POST` | `/upload-url` | URL presignada S3 (PUT) para subida directa desde el frontend |
| `POST` | `/analyses` | Valida input e inicia la ejecución de Step Functions |
| `GET` | `/analyses/{id}` | Mapea el estado de SFN al estado del job |
| `POST` | `/analyses/{id}/continue` | Reanuda el pipeline desde la compuerta de pausa |
| `GET` | `/analyses/{id}/report` | URL presignada (GET) para descargar el reporte |
| `DELETE` | `/analyses/{id}` | Cancela el análisis |

---

## 13. Resumen de valor para el challenge

CreditIQ demuestra una arquitectura de IA **responsable y auditable** para un dominio
regulado:

1. **Multi-agente especializado** — 5 agentes en pipeline, cada uno con una
   responsabilidad clara y compuertas de revisión humana.
2. **IA acotada, no autónoma** — la matemática es determinística y reproducible; el LLM
   solo interpreta y redacta, sin poder sobrescribir cifras (anti-alucinación by design).
3. **Trazabilidad total** — cada KPI muestra su fórmula; el QA valida el reporte;
   todo queda auditado.
4. **Especialización financiera real** — NIIF 18, análisis de fondos con reconciliación
   de NAV, concentración por emisor, riesgo de tasa y FX.
5. **Producción-ready** — serverless, multi-tenant con JWT, seguro por defecto, con un
   harness de evaluación que atrapa regresiones automáticamente.

---

*Documento generado para el BTG Pactual AI Challenge 2026 — Equipo iastronauts.*
