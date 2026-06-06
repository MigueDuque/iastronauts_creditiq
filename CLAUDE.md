# CLAUDE.md

**CreditIQ** — Multi-agent AI platform for automated financial analysis (BTG Pactual AI Challenge 2026). Ingests PDF/Excel/CSV financial statements → NIIF notes, variance analysis, risk scoring, executive reports.

Sub-projects:
- `iastronauts_creditiq_back/` — AWS SAM serverless backend (Python 3.12)
- `iastronauts_creditiq_front/` — React 19 + Vite + TypeScript frontend

---

## Backend Commands

```bash
# From iastronauts_creditiq_back/
sam build --use-container   # always --use-container
sam deploy --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM \
  --parameter-overrides Stage=dev LlmProvider=anthropic_api AnthropicApiKey=YOUR_KEY

# Local dev (preferred — no Docker, no SAM)
uvicorn local_server:app --reload --port 8000
```

**`local_server.py`** — FastAPI wrapper that translates HTTP → Lambda event format. Extra routes:
- `POST /analyses/{id}/continue` — routes to Agent 2 if `extraction_complete`, Agents 3+4 if `analysis_complete`
- `DELETE /analyses/{id}` — cancels via `_cancelled_jobs` in-memory list

Set `LOCAL_DEV_BYPASS_SFN=true` in `.env` to run Agent 1 in a background thread, skipping Step Functions.

**`src/requirements.txt` is the authoritative dependency file** (not root-level). Packages: `anthropic>=0.49.0`, `boto3>=1.35.81`, `pydantic>=2.10.3`, `tenacity>=9.0.0`, `pandas>=2.2.3`, `openpyxl>=3.1.5`.

`OutputFormat` = `markdown | pdf` only. PPT was removed — do not re-add it.

---

## Testing

```bash
# From iastronauts_creditiq_back/
pytest tests/                          # smoke + template + remediation + eval cases
python -m tests.eval.runner            # engine eval scorecard (exit 1 on regression)
python -m tests.eval.runner --update   # re-snapshot golden after an INTENTIONAL engine change
```

**Engine eval harness (`tests/eval/`)** — scored regression check that replaces "run one
analysis and eyeball it". Each golden case runs through the real deterministic code path
(no LLM, no AWS) and diffs `probes` (per-dimension scores/levels, composite, risk
categories) against a recorded `expected.json` snapshot. After an intended engine change,
`--update` and **review the `git diff` of `expected.json` — that diff is the review**. An
unintended probe move is the regression it exists to catch (guards the multi-engine
fan-out, e.g. sheet double-count). Add cases under `cases/<name>/` (`meta.json` + stage
input `input.json`). See `tests/eval/README.md`.

The deterministic core of an agent should be a pure, LLM/S3-free function the eval can call
(pattern: `risk_scorer/scoring.py::compute_risk`, shared with `handler.py`).

---

## Architecture

Sequential Step Functions pipeline (`step_functions/analysis_workflow.json`):
```
DocumentExtractor → FinancialAnalyzer → RiskScorer → ReportGenerator → RevisorInteligente
```

Each agent: `src/agents/<name>/handler.py` with `lambda_handler(event, context)`. Receives previous agent's full output dict via Step Functions state passing.

**Agent output pattern:**
```python
payload = PreviousAgentOutput.model_validate(event)
result  = ThisAgentOutput(job_id=payload.job_id, tenant_id=payload.tenant_id, ...)
return result.model_dump(mode="json")
```

**CRITICAL — `tenant_id` must flow through every agent output.** Dropping it causes a `ValidationError` at the *next* agent, not the one that dropped it. Chain: `OrchestratorOutput → ExtractorOutput → AnalyzerOutput → ScorerOutput → FinalReportOutput → RevisorOutput`.

**No DynamoDB.** All persistence is S3-based (`src/shared/s3_report_store.py`).

---

## API Endpoints (`src/api/`)

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/upload-url` | S3 presigned PUT URL + `s3_key` for direct frontend upload |
| `POST` | `/analyses` | Validates input, starts Step Functions execution (uses `job_id` as execution name) |
| `GET` | `/analyses/{id}` | Maps SFN status → `pending/processing/extraction_complete/analysis_complete/completed/failed`. Falls back to `jobs/{id}/status.json` in S3 (local dev). |
| `GET` | `/analyses/{id}/report` | Returns presigned GET URL (1h TTL) for the `.md` report |

`tenant_id` accepted as request body field or `x-tenant-id` header.

---

## LLM Provider (`src/shared/llm_provider.py`)

Always route LLM calls through this wrapper — never import SDKs directly in agent handlers.

- `LLM_PROVIDER=anthropic_api` → Anthropic SDK, default model `claude-sonnet-4-6`
- `LLM_PROVIDER=bedrock` → boto3 Converse API, default model `us.anthropic.claude-sonnet-4-5-20251001`
- Override: `LLM_MODEL` (Anthropic) or `BEDROCK_MODEL` (Bedrock)

Methods: `generate_text(...)` → `str`, `generate_json(...)` → `dict`. Both inject a tenant boundary block into the system prompt when `tenant_id` is provided.

---

## Key Models

**Enums** (`shared/models/base.py`):
- `MaterialityLevel`: `LOW | MEDIUM | HIGH`
- `RiskLevel`: `LOW | MEDIUM | HIGH`
- `FinancialHealth`: `STABLE | DECLINING | GROWING | CRITICAL` (legacy) + `LIQUID | LEVERAGED | SPECULATIVE | CASH_STRESSED | VALUATION_DRIVEN | CONCENTRATED`
- `OutputFormat`: `markdown | pdf`

**`ExtractedAccount`** — non-obvious fields:
- `source_sheet: str | None` — Excel sheet name; used by `sheet_concentration_engine`. None for PDF/CSV.
- `is_total: bool` — True for sum/subtotal rows; `calculate_financial_totals()` skips these to avoid double-counting.
- `investment_type: str | None` — `equity | bond | sovereign_debt | trust_rights | futures | fund | cash`; extracted by Agent 1 LLM.
- `issuer_name: str | None` — preferred over account name in concentration views.
- `subcategory` was intentionally removed.

**`AnalyzerOutput`** — notable fields beyond the obvious:
- `sheet_concentration: dict` — three views (`asset_breakdown`, `instrument_breakdown`, `bank_breakdown`); `available=True` only when matching `source_sheet` accounts exist.
- `executive_kpis: dict` — KPI card #5 is **AUM** (closing NAV for funds, total assets otherwise). "Razón Corriente" was removed — not meaningful for funds.
- `executive_synthesis: dict` — deterministic portfolio story from `synthesis_engine` (runs before LLM).

**Historical reports** (`src/shared/s3_report_store.py`):
- S3 key: `reports/{tenant_id}/{company_slug}/{YYYY}/{MM:02d}/report_{job_id}.md`
- `.md` file has a machine-readable JSON block `<!-- CREDITIQ_REPORT {...} -->` at top, followed by human-readable markdown.
- `fetch_historical_reports()` returns reports from same quarter one year back + December of prior year.
- `test_files/reporte_final_eeff_diciembre_2024.md` is the canonical example.

---

## Agent Status

| # | Agent | File | Status |
|---|-------|------|--------|
| 1 | DocumentExtractor | `src/agents/document_extractor/handler.py` | ✅ Textract (PDF), pandas/openpyxl (Excel/CSV), LLM normalization. Populates `source_sheet`, `is_total`, `investment_type`, `issuer_name`. |
| 2 | FinancialAnalyzer | `src/agents/financial_analyzer/handler.py` | ✅ 14-engine deterministic + 4-sub-agent LLM ("Math First, Synthesis Second, LLM Third") |
| 3 | RiskScorer | `src/agents/risk_scorer/handler.py` | ✅ 5 deterministic engines (liquidity, credit, solvency, market, operational) + composite + LLM narrative. Fund-aware weights. Credit engine scores **counterparty/custodian** concentration (`bank_breakdown`); market engine scores **interest-rate** (fixed-income via `instrument_breakdown`) + **FX** exposure. Output regrouped into 3 report-facing categories: `risk_categories` = **Riesgo de Crédito / Mercado / Financiero** (financiero = liquidity+solvency). |
| 4 | ReportGenerator | `src/agents/report_generator/handler.py` | ✅ Fills .docx template via LLM; writes to `jobs/` + `reports/` prefix (enables historical/duplicate detection). TODO: richer LLM executive narrative. |
| 5 | RevisorInteligente | `src/agents/revisor_inteligente/handler.py` | ✅ 6-category validation (structural, math, cross-ref, business logic, consistency, narrative LLM). ERROR=−10, WARNING=−3. Now wired into Step Functions workflow (`RevisorFunctionArn`). |

**Adding a new agent**: create `handler.py`, add `AWS::Serverless::Function` in `template.yaml`, add `Task` state in `analysis_workflow.json`, add Lambda ARN to `WorkflowRole`.

---

## FinancialAnalyzer Engines (`src/agents/financial_analyzer/`)

| Engine | File | Purpose |
|--------|------|---------|
| ratio_engine | `ratio_engine.py` | Variations, totals, financial ratios, NIIF 18 subtotals |
| materiality_engine | `materiality_engine.py` | Threshold (1% of max assets/revenue), materiality level, impact score |
| trend_engine | `trend_engine.py` | Per-account trend labels |
| anomaly_detector | `anomaly_detector.py` | Account-level and structural anomaly flags |
| variation_reliability | `variation_reliability.py` | Flags `NEW_ACCOUNT | INSUFFICIENT_BASELINE | EXTREME_VARIATION` |
| causality_engine | `causality_engine.py` | Causal chains between accounts |
| earnings_quality | `earnings_quality.py` | Fair value vs operating income ratio |
| concentration_engine | `concentration_engine.py` | Portfolio-wide HHI |
| sheet_concentration_engine | `sheet_concentration_engine.py` | Three sheet views: balance assets, Inversiones instruments+emisores, Efectivo banks |
| niif18_engine | `niif18_engine.py` | NIIF 18 compliance flags |
| fund_engine | `fund_engine.py` | Fund detection, NAV reconciliation, position tracking |
| kpi_engine | `kpi_engine.py` | Dashboard KPI cards |
| synthesis_engine | `synthesis_engine.py` | Deterministic portfolio story before LLM |
| financial_diagnostics_engine | `financial_diagnostics_engine.py` | Cross-statement heuristic signals |
| llm_reasoning | `llm_reasoning.py` | Orchestrates 4 sub-agents → `LLMAnalysisResult` |
| service | `service.py` | Orchestrates all engines + merges results |

**4 LLM sub-agents** (refactored 2026-05-27 — was a single 32k-token call, caused empty outputs with 60+ accounts):

| Sub-agent | Max tokens | S3 prompt |
|-----------|-----------|-----------|
| movement_intelligence | 5 000 | `02a_prompt_subagent_movement_intelligence.md` |
| causality_agent | 6 000 | `02b_prompt_subagent_causality.md` |
| thesis_agent | 5 000 | `02c_prompt_subagent_thesis.md` |
| narrative_agent | 4 000 | `02d_prompt_subagent_narrative.md` |

Each receives a compact text digest (≤6 000 tokens). LLM ceiling rule: cannot override deterministic risk levels.

---

## System Prompts

Stored in `src/agents/system_pompts/` (**typo in folder name — do not rename, referenced in code**).

Three-tier fallback: **S3** → **local file** → **inline string**.

| File | S3 key | Used by |
|------|--------|---------|
| `01_prompt_agent_extractor.md` | `instructions/prompts/01_…` | Agent 1 |
| `02a–02d_prompt_subagent_*.md` | `instructions/prompts/02a–02d_…` | Agent 2 sub-agents |
| `03_prompt_agent_risk-analyzer.md` | `instructions/prompts/03_…` | Agent 3 |

Update in production:
```bash
aws s3 cp src/agents/system_pompts/<file>.md \
  s3://iastronauts-creditiq-us-east-1-dev/instructions/prompts/<file>.md
```
Cache (`_prompt_cache`) resets on cold start.

---

## S3 Bucket Structure

```
iastronauts-creditiq-us-east-1-dev/
├── instructions/
│   ├── template_reporte_final_eeff.md   ← RAG template for ReportGenerator (read before LLM call)
│   ├── niff_18_explicacion.md           ← NIIF 18 reference for Agent 2
│   └── prompts/                         ← live production system prompts
├── uploads/{period}/                    ← client financial documents
├── jobs/{job_id}/
│   ├── status.json                      ← local dev status fallback
│   ├── extractor_output.json            ← Agent 1 output (for /continue)
│   ├── analyzer_output.json             ← Agent 2 output
│   └── final_report.json
└── reports/{tenant_id}/{company_slug}/{YYYY}/{MM}/report_{job_id}.md
```

---

## Multi-tenant Security (`src/shared/`)

- **`tenant_context.py`** — `TenantContext`: immutable security context built at API boundary. `assert_s3_key(key)` enforces `uploads/{tid}/`, `reports/{tid}/`, `rag/{tid}/` boundaries.
- **`tenant_middleware.py`** — `extract_tenant_context(event)`: priority JWT → `x-tenant-id` header → body. JWT required in production.
- **`audit_logger.py`** — `log_audit_event()`: never raises, never blocks the happy path.

---

## Frontend Commands

```bash
# From iastronauts_creditiq_front/
npm install && npm run dev    # localhost:5173
npm run build                 # type-check + production build
```

**Key files**: `AnalysisPage.tsx`, `DashboardPage.tsx`, `JobResultPage.tsx`, `AiReasoningPipeline.tsx`, `UploadDialog.tsx`.

**3-phase pipeline UX**:
1. Upload → Agent 1 runs → status `extraction_complete` → show accounts table
2. "Continue" → Agent 2 → `analysis_complete`
3. "Continue" → Agents 3+4 → `completed` → show report

**Status values**: `pending | processing | extraction_complete | analysis_complete | completed | failed | cancelled`

**API base URL**: `VITE_API_URL` in `.env.local` (`http://localhost:8000` for local dev).

State cached in `localStorage` (keys: `creditiq_analysis_id`, `creditiq_status`, `creditiq_report`). Cleared only on explicit "Clear" click.

---

## Environment Variables

```
LLM_PROVIDER=anthropic_api           # or: bedrock
ANTHROPIC_API_KEY=sk-ant-...
LLM_MODEL=claude-haiku-4-5-20251001  # optional; haiku is cheapest for extraction
BEDROCK_MODEL=...                     # optional (bedrock only)
AWS_REGION=us-east-1
STAGE=dev
MAIN_BUCKET=iastronauts-creditiq-us-east-1-dev
AWS_ACCOUNT_ID=123456789012
WORKFLOW_ARN=arn:aws:states:...
LOCAL_DEV_BYPASS_SFN=true            # local_server.py only — never in production
```

`MAIN_BUCKET`, `WORKFLOW_ARN`, `AWS_ACCOUNT_ID`, `STAGE` are injected at deploy time via `template.yaml`.