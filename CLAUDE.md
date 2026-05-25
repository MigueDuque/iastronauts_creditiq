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
sam build

# Deploy to AWS (guided first time)
sam deploy --guided

# Run locally (requires Docker)
sam local invoke ExtractorFunction --event tests/events/sample_document.json
sam local start-api

# Run a specific agent lambda locally
sam local invoke AnalyzerFunction --event tests/events/sample_analysis.json
```

### Dependencies

**`src/requirements.txt` is the authoritative dependency file.** SAM resolves it from the `CodeUri: src/` directory. The root-level `requirements.txt` is a dev reference only — changes must be kept in sync with `src/requirements.txt`, which is the one actually bundled into Lambdas.

Current packages: `anthropic>=0.49.0`, `boto3>=1.35.81`, `pydantic>=2.10.3`, `tenacity>=9.0.0`, `pandas>=2.2.3`, `openpyxl>=3.1.5`.

`OutputFormat` has two values: `markdown` and `pdf`. PPT was removed — do not re-add it.

### Architecture

The analysis pipeline is a **sequential Step Functions state machine** defined in `step_functions/analysis_workflow.json`. Each state calls a dedicated Lambda (agent) and passes its output as input to the next:

```
DocumentExtractor → FinancialAnalyzer → RiskScorer → ReportGenerator
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
| `GET` | `/analyses/{analysis_id}` | `api.analysis_status.handler` | Calls `states:DescribeExecution` and maps execution status to `pending/processing/completed/failed` |
| `GET` | `/analyses/{analysis_id}/report` | `api.report_url.handler` | Reads execution output, extracts `markdown_report_url`, returns a presigned GET URL (1h TTL) |

`tenant_id` is accepted both as a request body field and as the `x-tenant-id` header.

### LLM Provider (`src/shared/llm_provider.py`)

Single integration point for all AI calls. Reads `LLM_PROVIDER` from env:
- `anthropic_api` → Anthropic SDK, requires `ANTHROPIC_API_KEY`. Default model: `claude-sonnet-4-6`
- `bedrock` → `boto3` Converse API. Default model: `us.anthropic.claude-sonnet-4-5-20251001`

Override models via `LLM_MODEL` (Anthropic) or `BEDROCK_MODEL` (Bedrock) env vars.

Always route LLM calls through this wrapper — never import the SDKs directly in agent handlers.

Methods:
- `generate_text(system_prompt, user_prompt, temperature)` → `str` — for narrative/markdown output
- `generate_json(system_prompt, user_prompt, temperature)` → `dict` — for structured extraction; enforces JSON-only output and strips accidental markdown fences

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
└── reports/
    └── {tenant_id}/{company_slug}/{YYYY}/{MM}/report_{job_id}.md
```

**`instructions/`** — archivos de instrucciones estáticos para los agentes. `template_reporte_final_eeff.md` es el template oficial del output: documenta campo a campo la estructura JSON del bloque `<!-- CREDITIQ_REPORT -->` y el esquema de las 6 secciones Markdown del reporte. El `ReportGenerator` (y cualquier agente que genere el reporte final) debe leer este archivo como parte de su contexto RAG antes de llamar al LLM, para que el modelo sepa exactamente qué estructura producir.

**`uploads/`** — documentos financieros subidos por los clientes, organizados por carpeta de período (ej. `junio-2025/`). El `DocumentExtractor` lee desde este prefijo.

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

### Output Models — Key Fields

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

`analysis_results` is the key field for historical comparison: when `FinancialAnalyzer` loads prior reports via `fetch_historical_reports`, it reads each `AccountAnalysis.current_value` keyed by `account_id` to derive `previous_value` for the current period.

### Current Agent Implementation Status

All 4 agent handlers exist and are structurally correct (input validation, output models, `tenant_id` propagation). They are currently **stubs** — they pass data through but do not call the LLM or perform real analysis. The actual logic is marked `# TODO` in each handler.

| Agent | File | Status |
|-------|------|--------|
| DocumentExtractor | `src/agents/document_extractor/handler.py` | Stub — no Textract/pandas yet |
| FinancialAnalyzer | `src/agents/financial_analyzer/handler.py` | Stub — no LLM call yet |
| RiskScorer | `src/agents/risk_scorer/handler.py` | Stub — no scoring logic yet |
| ReportGenerator | `src/agents/report_generator/handler.py` | Partial — saves to S3, fetches historical reports; LLM narrative TODO |

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

`src/App.tsx` is still the default Vite scaffold. The frontend needs to be built for:
1. Auth via AWS Cognito
2. File upload (PDF/Excel) → `POST /upload-url` then PUT to presigned URL, then `POST /analyses`
3. Polling `GET /analyses/{id}` until `status === "completed"`
4. Fetching and displaying the generated Markdown report via `GET /analyses/{id}/report`

---

## Planned Agent Roadmap

The full pipeline will expand to 6 agents. Current implementation has 4 stubs. Agents 5 and 6 are not yet scaffolded:

| # | Agent | Responsibility |
|---|-------|---------------|
| 1 | DocumentExtractor | OCR (Textract for PDF), pandas/openpyxl for Excel |
| 2 | FinancialAnalyzer | Account classification, NIIF homologation, variance analysis |
| 3 | RiskScorer | NIIF rules, materiality detection, risk score, anti-hallucination |
| 4 | ReportGenerator | LLM narrative, NIIF note drafts, saves `.md` to S3 |
| 5 | NarradorEjecutivo | Corporate writing, executive summaries _(not yet created)_ |
| 6 | RevisorInteligente | Anti-hallucination, figure verification _(not yet created)_ |

New agents follow the same pattern: create `src/agents/<name>/handler.py`, add `AWS::Serverless::Function` resource in `template.yaml`, add a `Task` state in `analysis_workflow.json`, add the Lambda ARN to `WorkflowRole`.

---

## Environment Variables

Global Lambda env vars are set in `template.yaml` under `Globals.Function.Environment.Variables`. Local dev uses `.env`:

```
LLM_PROVIDER=anthropic_api       # or: bedrock
ANTHROPIC_API_KEY=sk-ant-...
LLM_MODEL=claude-sonnet-4-6      # optional override
BEDROCK_MODEL=...                 # optional override (bedrock only)
AWS_REGION=us-east-1
STAGE=dev
MAIN_BUCKET=iastronauts-creditiq-us-east-1-dev
AWS_ACCOUNT_ID=123456789012
WORKFLOW_ARN=arn:aws:states:...
```

`MAIN_BUCKET`, `WORKFLOW_ARN`, `AWS_ACCOUNT_ID`, and `STAGE` are injected automatically at deploy time. `LLM_PROVIDER` is the primary feature flag — no other flags exist.
