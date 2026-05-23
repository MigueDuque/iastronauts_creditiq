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

# Build SAM application
sam build

# Deploy to AWS (guided first time)
sam deploy --guided

# Run locally (requires Docker)
sam local invoke ExtractorFunction --event tests/events/sample_document.json
sam local start-api

# Run a specific agent lambda locally
sam local invoke AnalyzerFunction --event tests/events/sample_analysis.json
```

### Architecture

The analysis pipeline is a **sequential Step Functions state machine** defined in `step_functions/analysis_workflow.json`. Each state calls a dedicated Lambda (agent) and passes its output as input to the next:

```
DocumentExtractor → FinancialAnalyzer → RiskScorer → ReportGenerator
```

Each agent is an independent Lambda function under `src/agents/<agent_name>/handler.py` with a `lambda_handler(event, context)` entrypoint. Agents receive the accumulated state dict and must spread it forward (`**event`) alongside their own output keys.

### LLM Provider Abstraction

`src/shared/llm_provider.py` (to be built) is the single integration point for AI calls. It reads `LLM_PROVIDER` from env:
- `anthropic_api` → uses `ANTHROPIC_API_KEY` via the Anthropic SDK directly
- `bedrock` → uses `boto3` with Amazon Bedrock

Always route LLM calls through this wrapper, never import the SDKs directly in agent handlers.

### DynamoDB Schema

Table `creditiq-analyses-{stack}` uses a **composite key**:
- Partition key: `tenant_id` (String) — multi-tenancy from day 1
- Sort key: `analysis_id` (String)

All new attributes must preserve this key structure. Never use a single-key design.

### Infrastructure as Code

All AWS resources are declared in `template.yaml` (AWS SAM / CloudFormation). Resource names use `!Sub` with `${AWS::StackName}` or `${AWS::AccountId}` to avoid collisions across environments. New Lambda agents must be added both here and wired into `step_functions/analysis_workflow.json`.

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
2. File upload (PDF/Excel) → triggers `analysis-start` Lambda
3. Display of the generated Markdown report

---

## Planned Agent Roadmap

Per `plan.md`, the full pipeline will expand to 6 agents. Current implementation has 4 stubs. Agents 5 and 6 are not yet scaffolded:

| # | Agent | Responsibility |
|---|-------|---------------|
| 1 | DocumentExtractor | OCR (Textract for PDF), pandas/openpyxl for Excel |
| 2 | FinancialAnalyzer | Account classification, NIIF homologation |
| 3 | RiskScorer | NIIF rules, materiality detection, risk score |
| 4 | ReportGenerator | Generates `.md` report to S3 |
| 5 | NarradorEjecutivo | Corporate writing, executive summaries _(not yet created)_ |
| 6 | RevisorInteligente | Anti-hallucination, figure verification _(not yet created)_ |

New agents follow the same pattern: create `src/agents/<name>/handler.py`, add `AWS::Serverless::Function` resource in `template.yaml`, add a `Task` state in `analysis_workflow.json`.

---

## Environment Variables

Copy `.env.example` to `.env` before developing:

```
LLM_PROVIDER=anthropic_api   # or: bedrock
ANTHROPIC_API_KEY=...
AWS_REGION=us-east-1
ENVIRONMENT=dev
```

The `LLM_PROVIDER` switch is the primary feature flag; no other flags exist.
