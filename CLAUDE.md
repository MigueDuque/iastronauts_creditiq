# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**CreditIQ** is a multi-agent AI platform for automated financial analysis, developed for the **AI Innovation Challenge 2026 — BTG Pactual Colombia**. It ingests financial statements (PDF/Excel/CSV) and produces standardized reports: NIIF notes drafts, variance analysis, risk scoring, and executive summaries.

The repo contains two sub-projects:
- `iastronauts_creditiq_back/` — AWS SAM serverless backend (Python 3.12)
- `iastronauts_creditiq_front/` — React 19 + Vite + TypeScript frontend

---

## Backend (AWS SAM)

### Commands

```bash
# From iastronauts_creditiq_back/

# Build SAM application (reads src/requirements.txt automatically)
sam build --use-container   # always use --use-container (local Python may differ from Lambda runtime)

# Deploy to AWS
sam deploy --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM \
  --parameter-overrides Stage=dev LlmProvider=anthropic_api AnthropicApiKey=YOUR_KEY

# ── Local dev (preferred — no Docker, no SAM, no deploy cycle) ──────────────
# Uses real AWS (S3, Textract) with credentials from .env
pip install fastapi uvicorn python-dotenv
uvicorn local_server:app --reload --port 8000
```

**`local_server.py`** — FastAPI wrapper at the repo root of `iastronauts_creditiq_back/`. Translates HTTP → Lambda event format (HTTP API Gateway v2) and calls the same handlers as production. Extra route:

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/analyses/{id}/continue` | Runs Agents 2–5 sequentially in a background thread after Agent 1 completes |

Set `LOCAL_DEV_BYPASS_SFN=true` in `.env` to skip Step Functions and run Agent 1 directly in a background thread. Status is written to `jobs/{job_id}/status.json` in S3; the status handler reads it as fallback when SFN execution doesn't exist.

### Dependencies

**`src/requirements.txt` is the authoritative dependency file.** SAM resolves it from the `CodeUri: src/` directory. The root-level `requirements.txt` is a dev reference only — changes must be kept in sync with `src/requirements.txt`, which is the one actually bundled into Lambdas.

Current packages: `anthropic>=0.49.0`, `boto3>=1.35.81`, `pydantic>=2.10.3`, `tenacity>=9.0.0`, `pandas>=2.2.3`, `openpyxl>=3.1.5`.

`OutputFormat` has two values: `markdown` and `pdf`. PPT was removed — do not re-add it.

### Architecture

The analysis pipeline is a **sequential Step Functions state machine** defined in `step_functions/analysis_workflow.json`. Each state calls a dedicated Lambda (agent) and passes its output as input to the next:

```
DocumentExtractor → FinancialAnalyzer → RiskScorer → ReportGenerator → RevisorInteligente
```

Each agent is an independent Lambda function under `src/agents/<agent_name>/handler.py` with a `lambda_handler(event, context)` entrypoint. Agents receive the previous agent's full output dict (via Step Functions state passing) and must include all context fields in their own output model.

**Agent output pattern** — every handler follows the same structure:
```python
payload = PreviousAgentOutput.model_validate(event)   # validate input
result  = ThisAgentOutput(                             # build output
    job_id=payload.job_id,
    tenant_id=payload.tenant_id,                      # NEVER drop this
    ...
)
return result.model_dump(mode="json")                  # return as dict
```

### API Layer (`src/api/`)

Four HTTP endpoints, all under `CreditIQApi` (AWS HTTP API Gateway):

| Method | Path | Handler | Purpose |
|--------|------|---------|---------|
| `POST` | `/upload-url` | `api.presigned_url.handler` | Returns an S3 presigned PUT URL + `s3_key` so the frontend uploads directly to S3 |
| `POST` | `/analyses` | `api.orchestrator.handler` | Validates input, builds `OrchestratorOutput`, starts Step Functions execution using `job_id` as execution name |
| `GET` | `/analyses/{analysis_id}` | `api.analysis_status.handler` | Calls `states:DescribeExecution` and maps execution status to `pending/processing/extraction_complete/completed/failed`. Falls back to `jobs/{id}/status.json` in S3 when SFN execution doesn't exist (local dev). |
| `GET` | `/analyses/{analysis_id}/report` | `api.report_url.handler` | Reads execution output, extracts `markdown_report_url`, returns a presigned GET URL (1h TTL) |

`tenant_id` is accepted both as a request body field and as the `x-tenant-id` header.

### LLM Provider (`src/shared/llm_provider.py`)

Single integration point for all AI calls. Reads `LLM_PROVIDER` from env:
- `anthropic_api` → Anthropic SDK, requires `ANTHROPIC_API_KEY`. Default model: `claude-sonnet-4-6`
- `bedrock` → `boto3` Converse API. Default model: `us.anthropic.claude-sonnet-4-5-20251001`

Override models via `LLM_MODEL` (Anthropic) or `BEDROCK_MODEL` (Bedrock) env vars.

Always route LLM calls through this wrapper — never import the SDKs directly in agent handlers.

Methods:
- `generate_text(system_prompt, user_prompt, temperature, tenant_id, job_id, max_tokens)` → `str` — for narrative/markdown output
- `generate_json(system_prompt, user_prompt, temperature, tenant_id, job_id, max_tokens)` → `dict` — for structured extraction; enforces JSON-only output and strips accidental markdown fences

**Tenant isolation:** Both methods accept `tenant_id` and `job_id`. When provided, a tenant boundary block is injected into the system prompt to prevent cross-tenant AI context contamination in warm Lambda containers.

### Financial Math Engine (`src/shared/financial_math.py`)

Deterministic financial calculations — **never relies on LLM for arithmetic**. Used by `FinancialAnalyzer` before the qualitative LLM call.

| Function | Purpose |
|----------|---------|
| `calculate_variations(current, previous)` | Returns `(absolute_variation, variation_pct)` |
| `calculate_financial_ratios(accounts)` | Full ratio suite: liquidity (razón corriente, prueba ácida, capital de trabajo), solvency (deuda/patrimonio, endeudamiento global), profitability (margen bruto, neto, EBITDA) |
| `classify_current_noncurrent(accounts)` | Keyword-based classification into corrientes/no corrientes using NIIF Spanish terminology |
| `determine_materiality_threshold(accounts)` | 1% of max(total_assets, total_revenue), min floor 1.0 COP MM |
| `get_materiality_level(abs_var, threshold)` | Returns `MaterialityLevel.HIGH/MEDIUM/LOW` |
| `get_inventories_total(accounts)` | Sums inventory-related asset accounts by keyword matching |
| `get_cost_of_sales_total(accounts)` | Sums cost-of-sales expense accounts by keyword matching |

### Multi-Tenant Security (`src/shared/`)

Three modules handle tenant isolation:

**`tenant_context.py`** — `TenantContext` Pydantic model: immutable security context built once at the API boundary. Contains `tenant_id`, `tenant_name`, `tier` (free/professional/enterprise), `permissions`, `authenticated_via` (jwt/header/body). Key methods:
- `can(permission)` — permission check with wildcard support
- `assert_s3_key(key)` — raises `TenantBoundaryViolation` if S3 key falls outside `uploads/{tid}/`, `reports/{tid}/`, `rag/{tid}/`
- `assert_analysis_ownership(analysis_tenant_id)` — prevents horizontal privilege escalation

**`tenant_middleware.py`** — `extract_tenant_context(event)` extracts tenant identity from Lambda events. Priority: (1) Cognito JWT claims → (2) `x-tenant-id` header → (3) body `tenant_id`. Production must use JWT; header/body are dev-only fallbacks. Also provides `validate_requested_tenant()` for resource-level authorization.

**`audit_logger.py`** — Structured audit trail via `log_audit_event()`. Emits JSON events to CloudWatch with `{"audit": true}` index key. Actions include `analysis.started/completed/failed`, `report.accessed`, `file.uploaded`, `ai.llm_call`, `security.tenant_boundary_violation`. Never raises — audit logging never blocks the happy path.

### Historical Report Storage (`src/shared/s3_report_store.py`)

Reports are stored as `.md` files under the `reports/` prefix of `MainBucket`. Each file is self-describing: all `FinalReportOutput` fields are embedded as JSON inside an HTML comment at the top, followed by a human-readable markdown body.

**S3 key structure:**
```
reports/{tenant_id}/{company_slug}/{YYYY}/{MM:02d}/report_{job_id}.md
```

`company_slug` is produced by `slugify(company_name)` — lowercase, hyphens, no special characters.

**Key functions:**

| Function | Purpose |
|---|---|
| `save_report(report, bucket)` | Serializes and uploads a `FinalReportOutput`; returns the S3 key |
| `fetch_historical_reports(tenant_id, company_slug, reference_date, bucket)` | Returns reports from the same calendar trimester + December of `reference_date.year - 1` |
| `serialize_to_markdown(report)` | `FinalReportOutput` → `.md` string (generates account table from `analysis_results`) |
| `deserialize_from_markdown(content)` | `.md` string → `FinalReportOutput` (reads from the `<!-- CREDITIQ_REPORT ... -->` JSON block) |
| `slugify(text)` | Company name → filesystem-safe slug |

**`.md` file structure** — two layers in one file:

1. `<!-- CREDITIQ_REPORT { ...full model_dump... } -->` — machine-readable; `deserialize_from_markdown` reads only this block. Must be a valid `FinalReportOutput.model_dump(mode="json")`.
2. Human-readable markdown body: Resumen Ejecutivo → Resumen Junta Directiva → Análisis de Variaciones (table generated from `analysis_results`) → Riesgos → Notas NIIF → Indicadores de Cumplimiento.

`test_files/reporte_final_eeff_diciembre_2024.md` is the canonical example of a correctly-structured report file.

**Historical fetch logic:** given `reference_date`, lists only the S3 prefixes for the three months of that quarter one year back, plus `/12/` (December) if Q4 is not the current quarter. Malformed files are skipped silently so they never halt the pipeline.

`ReportGenerator` calls `fetch_historical_reports` at startup. The resulting `list[FinalReportOutput]` gives the `FinancialAnalyzer` per-account historical values via `report.analysis_results` for `previous_value` calculation and trend context.

### S3 Bucket Structure (`MAIN_BUCKET`)

The bucket `iastronauts-creditiq-us-east-1-dev` has three top-level prefixes:

```
iastronauts-creditiq-us-east-1-dev/
├── instructions/
│   └── template_reporte_final_eeff.md     ← RAG input for ReportGenerator
├── uploads/
│   └── junio-2025/
│       ├── EEFF_BTGPactual_COMPLETO.xlsx  (19.6 KB, subido 2026-05-24)
│       └── Rendición Cuentas Acciones Colombia Junio 2025.pdf  (748.0 KB, subido 2026-05-24)
├── jobs/
│   └── {job_id}/
│       ├── status.json                    ← pipeline status for local dev fallback
│       ├── extractor_output.json          ← Agent 1 output (persisted for /continue)
│       ├── analyzer_output.json           ← Agent 2 output (persisted)
│       └── final_report.json              ← final pipeline output
└── reports/
    └── {tenant_id}/{company_slug}/{YYYY}/{MM}/report_{job_id}.md
```

**`instructions/`** — archivos de instrucciones estáticos para los agentes. `template_reporte_final_eeff.md` es el template oficial del output: documenta campo a campo la estructura JSON del bloque `<!-- CREDITIQ_REPORT -->` y el esquema de las 6 secciones Markdown del reporte. El `ReportGenerator` (y cualquier agente que genere el reporte final) debe leer este archivo como parte de su contexto RAG antes de llamar al LLM, para que el modelo sepa exactamente qué estructura producir.

**`uploads/`** — documentos financieros subidos por los clientes, organizados por carpeta de período (ej. `junio-2025/`). El `DocumentExtractor` lee desde este prefijo. El texto OCR de rendición de cuentas (PDF) se guarda como `{tenant_id}/{job_id}_rendicion.txt` para uso cualitativo posterior.

**`jobs/`** — artefactos intermedios del pipeline. Cada job crea su carpeta con los outputs parciales de cada agente. Usado por `local_server.py` para la ruta `/continue` y como fallback de estado.

**`reports/`** — reportes `.md` generados por el pipeline, escritos por `s3_report_store.save_report()`. Ver sección *Historical Report Storage* para la lógica de fetch.

---

### `tenant_id` Propagation

`tenant_id` must flow through every agent output so the report is stored under the correct S3 prefix. The full chain:

```
OrchestratorOutput.tenant_id
  → ExtractorOutput.tenant_id
  → AnalyzerOutput.tenant_id
  → ScorerOutput.tenant_id
  → FinalReportOutput.tenant_id
  → RevisorOutput.tenant_id
```

**Never drop `tenant_id` when building a new agent output model.** This has caused silent pipeline crashes before (it was missing in `FinancialAnalyzer` and `RiskScorer` — now fixed). Pydantic raises `ValidationError` at the *receiving* agent, which makes the error appear one step later than the actual omission.

### Step Functions Workflow

`step_functions/analysis_workflow.json` defines the sequential pipeline. Each Task state has:

- **`TimeoutSeconds`**: Lambda timeout + 30s buffer (Extractor/Analyzer/ReportGenerator: 330s, RiskScorer: 210s)
- **Two-tier `Retry`**: Lambda transient errors (`Lambda.ServiceException`, `Lambda.AWSLambdaException`, `Lambda.SdkClientException`, `Lambda.TooManyRequestsException`) — 3 attempts, 2s/2× backoff; business errors (`States.TaskFailed`) — 2 attempts, 3s/1.5× backoff
- **`Catch`**: all errors route to `WorkflowFailed` (a `Fail` terminal state), preserving error details in `$.error`

Centralized logs go to `/aws/states/creditiq-analysis-workflow-{stage}` (ERROR level, 30-day retention).

**Execution ARN pattern** (used by `analysis_status` and `report_url` to call `DescribeExecution`):
```
arn:aws:states:{AWS_REGION}:{AWS_ACCOUNT_ID}:execution:creditiq-analysis-workflow-{STAGE}:{job_id}
```
The Orchestrator uses `job_id` as the Step Functions execution name, making the ARN fully reconstructable from env vars alone.

### Infrastructure as Code

All AWS resources are declared in `template.yaml` (AWS SAM / CloudFormation). Resource names use `!Sub` with `${AWS::StackName}` or `${AWS::AccountId}` to avoid collisions across environments. New Lambda agents must be added both here and wired into `step_functions/analysis_workflow.json`.

**IAM split:** `ApiLambdaRole` (API handlers) and `AgentLambdaRole` (pipeline agents) are separate roles. Agent role has Textract and Bedrock permissions; API role has Step Functions permissions.

**`states:StartExecution` targets the state machine ARN; `states:DescribeExecution` targets the execution ARN pattern** (`execution:...:*`). These are different resource types in IAM — do not consolidate them into one statement.

**No DynamoDB.** All persistence is S3-based via `src/shared/s3_report_store.py`. There is no database in this stack.

---

### Output Models — Key Fields

**Enums** (in `shared/models/base.py`):
- `MaterialityLevel`: `LOW | MEDIUM | HIGH`
- `RiskLevel`: `LOW | MEDIUM | HIGH`
- `FinancialHealth`: `STABLE | DECLINING | GROWING | CRITICAL`
- `OutputFormat`: `markdown | pdf`

**`BusinessContext`** (in `shared/models/orchestrator.py`) — business metadata attached to every analysis:
```python
class BusinessContext(BaseModel):
    company_name: str | None = None
    industry: str | None = None
    fiscal_year: str | None = None
    reporting_period: str | None = None   # YYYY-MM format from GUI quarter selector
    key_events: list[str]
    strategic_context: str | None = None
    regulatory_context: str | None = None
    analyst_instructions: list[str]
    raw_context: str
```

**`ExtractedAccount`** (in `shared/models/extractor.py`) — one row per financial account extracted from source documents:

```python
class ExtractedAccount(BaseModel):
    account_id: str                  # re-indexed globally as "act-001", "act-002", …
    raw_account_name: str            # exact text from the document
    normalized_account_name: str     # NIIF-standard name in Spanish
    category: str                    # "assets" | "liabilities" | "equity" | "revenue" | "expense" | "other"
    current_value: float             # COP MM
    previous_value: float | None     # COP MM, null if not in document
    currency: str                    # always "COP"
    confidence_score: float          # 0.0–1.0
    source_file: str                 # original file name
```

Note: `subcategory` was intentionally removed — it added noise without downstream value.

**`ExtractorOutput`** (in `shared/models/extractor.py`) — full Agent 1 output:

```python
class ExtractorOutput(BaseModel):
    # Pipeline context (propagated from OrchestratorOutput)
    job_id: str
    tenant_id: str
    business_context: BusinessContext
    niif_standards: list[str]
    report_language: str
    output_formats: list[OutputFormat]
    # Extraction results
    company_name: str
    statement_type: str              # "balance_sheet" | "income_statement" | "cash_flow"
    currency: str
    periods: list[str]               # up to 2 most-recent, format YYYY-MM
    accounts: list[ExtractedAccount]
    extraction_confidence: float     # average confidence across all accounts
    extraction_warnings: list[str]
    rendicion_text_s3_key: str | None  # S3 key of extracted PDF accountability text
```

**`AccountAnalysis`** (in `shared/models/analyzer.py`) — per-account analysis produced by the FinancialAnalyzer:

```python
class AccountAnalysis(BaseModel):
    account_id: str
    account_name: str
    current_value: float
    previous_value: float
    absolute_variation: float        # deterministic: current - previous
    variation_pct: float             # deterministic: (abs_var / previous) * 100
    materiality: MaterialityLevel    # from financial_math.get_materiality_level
    requires_niif_note: bool
    niif_note_references: list[str]  # e.g. ["NIC 16", "NIIF 9"]
    risk_level: RiskLevel
    possible_causes: list[str]       # from LLM or default fallback
    executive_insight: str           # LLM-generated one-liner for board
    anomaly_detected: bool           # true if large variation has no documented cause
```

**`AnalyzerOutput`** (in `shared/models/analyzer.py`) — full Agent 2 output:

```python
class AnalyzerOutput(BaseModel):
    # Pipeline context propagated
    job_id: str
    tenant_id: str
    business_context: BusinessContext
    niif_standards: list[str]
    report_language: str
    output_formats: list[OutputFormat]
    # Analysis results
    company_name: str
    currency: str
    periods: list[str]
    analysis_results: list[AccountAnalysis]
    high_materiality_accounts: list[str]
    niif_notes_required: list[str]           # sorted unique NIIF standards
    overall_financial_health: FinancialHealth
    executive_narrative: str                 # LLM-generated 3-paragraph summary
```

**`ScorerOutput`** (in `shared/models/scorer.py`) — Agent 3 output (stub):

```python
class ScorerOutput(BaseModel):
    # Propagated context + analyzer results
    ...
    # Scorer-specific fields
    validation_score: int            # 0–100
    overall_risk_score: RiskLevel
    issues_found: list[str]
    compliance_flags: list[str]
    requires_human_review: bool
    analysis_confidence: float       # 0.0–1.0
    anti_hallucination_passed: bool
```

**`FinalReportOutput`** (in `shared/models/report.py`) is the pipeline's final deliverable and the format used for all historical records in S3:

```python
class FinalReportOutput(BaseModel):
    job_id: str
    tenant_id: str
    company_name: str
    periods: list[str]                        # e.g. ["2024-12", "2023-12"]
    generated_at: datetime
    validation_score: int                     # 0–100
    overall_risk_score: RiskLevel             # LOW | MEDIUM | HIGH
    overall_financial_health: FinancialHealth # STABLE | DECLINING | GROWING | CRITICAL
    executive_summary: str
    board_summary: str
    analysis_results: list[AccountAnalysis]   # per-account data — core of historical records
    niif_note_drafts: list[NiifNoteDraft]
    markdown_report_url: str                  # S3 key of the saved .md file
    pdf_report_url: str | None = None
```

**`RevisorOutput`** (in `shared/models/revisor.py`) — Agent 5 output, extends `FinalReportOutput`:

```python
class RevisorOutput(FinalReportOutput):
    validation_flags: list[ValidationFlag]   # detailed check results
    errors_count: int
    warnings_count: int
    validation_passed: bool                  # True if errors_count == 0
```

Supporting types: `ValidationFlag(check_id, category, severity, message, affected_field, expected_value, actual_value)`, `ValidationCategory` (STRUCTURAL/MATHEMATICAL/CROSS_REFERENCE/BUSINESS_LOGIC/CONSISTENCY/NARRATIVE), `ValidationSeverity` (ERROR/WARNING/INFO).

`analysis_results` is the key field for historical comparison: when `FinancialAnalyzer` loads prior reports via `fetch_historical_reports`, it reads each `AccountAnalysis.current_value` keyed by `account_id` to derive `previous_value` for the current period.

---

### Current Agent Implementation Status

| # | Agent | File | Status |
|---|-------|------|--------|
| 1 | DocumentExtractor | `src/agents/document_extractor/handler.py` | **Implemented** — Textract (PDF async polling), pandas/openpyxl (Excel/CSV), LLM normalization via `_EXTRACTION_SYSTEM_PROMPT`. PDF text saved to S3 as `_rendicion.txt` for qualitative use. |
| 2 | FinancialAnalyzer | `src/agents/financial_analyzer/handler.py` | **Implemented** — Deterministic math engine (`financial_math.py`) calculates ratios, variations, materiality thresholds. LLM qualitative analysis via `_ANALYZER_SYSTEM_PROMPT` for causes, insights, NIIF compliance. Historical enrichment from S3 reports. Output saved to `jobs/{id}/analyzer_output.json`. |
| 3 | RiskScorer | `src/agents/risk_scorer/handler.py` | **Stub** — passes through analyzer results with zero-value scoring fields. TODO: validation, hallucination detection, compliance. |
| 4 | ReportGenerator | `src/agents/report_generator/handler.py` | **Stub** — saves to S3 via `s3_report_store`, fetches historical reports; TODO: LLM narrative generation for executive/board summaries and NIIF note drafts. |
| 5 | RevisorInteligente | `src/agents/revisor_inteligente/handler.py` | **Implemented** — 6-category validation: (1) Structural checks, (2) Mathematical verification (tolerance-based), (3) Cross-reference integrity (accounts ↔ NIIF notes bidirectionality), (4) Business logic coherence, (5) Consistency (URL patterns, coverage), (6) Narrative quality (LLM-assessed). Score adjusted with penalties: ERROR=−10, WARNING=−3. |

### FinancialAnalyzer Architecture (Agent 2)

The analyzer follows a **"Math First, LLM Second"** pattern:

1. **Historical enrichment** — loads previous reports from S3 via `fetch_historical_reports()`, maps `previous_value` by account name for accounts where it's null
2. **Deterministic math** — `calculate_financial_ratios()` produces ratios (razón corriente, prueba ácida, capital de trabajo, deuda/patrimonio, endeudamiento global, márgenes bruto/neto/EBITDA). `calculate_variations()` and `get_materiality_level()` compute per-account variations and materiality classification
3. **LLM qualitative** — sends the ratios summary, materiality threshold, per-account variations, and rendición de cuentas text to the LLM for `overall_financial_health`, `executive_narrative`, and per-account `possible_causes`, `executive_insight`, `risk_level`, `anomaly_detected`, and NIIF note requirements
4. **Consolidation** — merges math results with LLM qualitative output, applies fallback defaults for missing LLM data, builds `AnalyzerOutput`

### RevisorInteligente Architecture (Agent 5)

Six validation categories run in sequence; Category 6 (LLM) only runs if no structural ERRORs exist:

| Cat | Name | Checks |
|-----|------|--------|
| 1 | Structural | Required fields non-empty, periods count = 2, period ordering, generated_at sanity, analysis_results non-empty |
| 2 | Mathematical | `absolute_variation = current - previous` (±0.15 tolerance), `variation_pct` recalculation (±0.2pp), scale outlier detection (1000× median) |
| 3 | Cross-reference | NIIF note references exist in `niif_note_drafts`, bidirectional referencing (account ↔ note), `requires_niif_note` consistency |
| 4 | Business logic | Anomaly count vs validation_score coherence, `overall_risk_score` vs individual risks, `anomaly_detected` + `risk_level=LOW` conflict, large variation → HIGH materiality, HIGH materiality → ≥2 causes, financial health vs profit trend |
| 5 | Consistency | Non-empty analysis_results for table, `markdown_report_url` follows S3 key pattern |
| 6 | Narrative (LLM) | Cifra accuracy in summaries, executive_summary ≤3 sentences, board_summary more detailed than executive, insight coherence with variation_pct, NIIF note quality, cause plausibility |

---

## Frontend (React + Vite + TypeScript)

### Commands

```bash
# From iastronauts_creditiq_front/

npm install          # Install dependencies
npm run dev          # Start dev server (localhost:5173)
npm run build        # Type-check + production build
npm run lint         # ESLint
npm run preview      # Preview production build locally
```

### Current State

The frontend is fully scaffolded with MUI components. Key pages and components:

| File | Purpose |
|------|---------|
| `src/pages/AnalysisPage.tsx` | Main analysis page — upload trigger, polling, accounts table, two-phase pipeline UX |
| `src/pages/DashboardPage.tsx` | Dashboard overview page |
| `src/pages/JobResultPage.tsx` | Detailed view of a completed job's results |
| `src/components/AiReasoningPipeline.tsx` | Left sidebar — 4-step pipeline progress driven by job status |
| `src/components/UploadDialog.tsx` | File upload dialog → presigned URL → S3 PUT → POST /analyses. Includes **quarter and year selectors** |
| `src/components/AppLayout.tsx` | Shell with sidebar nav |
| `src/components/Header.tsx` | Top bar |
| `src/components/Sidebar.tsx` | Navigation sidebar |
| `src/components/Footer.tsx` | Footer |

**Quarter & Year Selectors in UploadDialog** — The upload dialog includes selectable fields for `reporting_period`:
- **Year:** auto-detected from filenames, editable
- **Quarter (Trimestre):** dropdown with `03` (T1 Marzo), `06` (T2 Junio), `09` (T3 Septiembre), `12` (T4 Diciembre)
- Period is composed as `YYYY-MM` and sent as `business_context.reporting_period` to the orchestrator
- Auto-inferred from filenames following `{ORG}_{FUND}_{TYPE}_{YYYY-MM-DD}.ext` convention, but user can override

**Two-phase pipeline UX** — Agent 1 runs and shows results, then the user reviews the extracted accounts and clicks "Run Agents 2–4" to continue:
1. Upload → `POST /analyses` → status = `processing`
2. Poll until `extraction_complete` → fetch extractor output from `/analyses/{id}/report` → show accounts table
3. User reviews → clicks "Run Agents 2–4" → `POST /analyses/{id}/continue` → status = `processing` again
4. Poll until `completed` → show final report

**Status values**: `pending | processing | extraction_complete | completed | failed`

**State persistence**: `jobId`, `jobStatus`, and `report` are cached in `localStorage` under keys `creditiq_analysis_id`, `creditiq_status`, `creditiq_report`. Navigation away and back restores data immediately without waiting for a poll. State is cleared only when the user explicitly clicks "Clear".

**Processing animation**: `AnalysisPage` drives a 3-step progress indicator using the `elapsed` timer (seconds since job start). Thresholds: S3 download active 0–3s, document parsing active 4–54s, LLM normalization active 55s+. Steps advance independently of actual backend progress — honest enough for UX without requiring backend instrumentation.

**API base URL**: `VITE_API_URL` env var (`.env.local`). Set to `http://localhost:8000` for local dev, CloudFront URL for production.

---

## Agent Roadmap

The full pipeline has 5 agents. Current implementation status:

| # | Agent | Responsibility | Status |
|---|-------|---------------|--------|
| 1 | DocumentExtractor | OCR (Textract for PDF), pandas/openpyxl for Excel, LLM normalization | ✅ Implemented |
| 2 | FinancialAnalyzer | Deterministic math engine + LLM qualitative analysis, NIIF compliance, variance analysis | ✅ Implemented |
| 3 | RiskScorer | NIIF rules, materiality detection, risk score, anti-hallucination | ⬜ Stub |
| 4 | ReportGenerator | LLM narrative, NIIF note drafts, saves `.md` to S3 | ⬜ Stub |
| 5 | RevisorInteligente | Anti-hallucination, 6-category validation, figure verification | ✅ Implemented |

New agents follow the same pattern: create `src/agents/<name>/handler.py`, add `AWS::Serverless::Function` resource in `template.yaml`, add a `Task` state in `analysis_workflow.json`, add the Lambda ARN to `WorkflowRole`.

---

## Environment Variables

Global Lambda env vars are set in `template.yaml` under `Globals.Function.Environment.Variables`. Local dev uses `.env`:

```
LLM_PROVIDER=anthropic_api           # or: bedrock
ANTHROPIC_API_KEY=sk-ant-...
LLM_MODEL=claude-haiku-4-5-20251001  # optional override; haiku is cheapest for extraction
BEDROCK_MODEL=...                     # optional override (bedrock only)
AWS_REGION=us-east-1
STAGE=dev
MAIN_BUCKET=iastronauts-creditiq-us-east-1-dev
AWS_ACCOUNT_ID=123456789012
WORKFLOW_ARN=arn:aws:states:...
LOCAL_DEV_BYPASS_SFN=true            # skip Step Functions; run extractor in-process
```

`MAIN_BUCKET`, `WORKFLOW_ARN`, `AWS_ACCOUNT_ID`, and `STAGE` are injected automatically at deploy time. `LLM_PROVIDER` is the primary feature flag. `LOCAL_DEV_BYPASS_SFN` is only for `local_server.py` — never set it in production.
