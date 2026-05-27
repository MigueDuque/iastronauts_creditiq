# CreditIQ — Handoff Document
**Date:** 2026-05-26  
**Branch:** master  
**Project:** CreditIQ — AI Innovation Challenge 2026, BTG Pactual Colombia

---

## Objective

Build a multi-agent AI pipeline that ingests financial statements (PDF/Excel/CSV) and produces:
- Structured NIIF-compliant account extractions
- Variance analysis with deterministic math
- Risk scoring and anti-hallucination validation
- Executive/board-ready reports in Markdown (PDF planned)

The pipeline runs on AWS SAM (Step Functions + Lambda). Local development uses `uvicorn local_server:app --reload --port 8000` which bypasses Step Functions and runs agents sequentially in background threads.

---

## Current Pipeline Status

```
Agent 1 — DocumentExtractor     ✅ Implemented & tested
Agent 2 — FinancialAnalyzer     ✅ Implemented (not yet tested end-to-end)
Agent 3 — RiskScorer            ⬜ Stub (passes through, no real logic)
Agent 4 — ReportGenerator       ⬜ Stub (saves to S3, no LLM narrative yet)
Agent 5 — RevisorInteligente    ✅ Implemented (not yet tested end-to-end)
```

### Local dev flow (3-phase)
```
POST /analyses            → runs Agent 1 in background thread → status: extraction_complete
POST /analyses/{id}/continue  → runs Agent 2 → status: analysis_complete
POST /analyses/{id}/continue  → runs Agents 3+4 → status: completed
```

Status is written to S3 at `jobs/{job_id}/status.json`. **This file only ever holds the current status string** — it has no history, no timestamps, and is not useful for debugging or auditing. It is purely a polling target for the frontend.

---

## What Has Been Built (and What Changed Since Last Sprint)

### Agent 1 — DocumentExtractor (`src/agents/document_extractor/handler.py`)
- Extracts Excel/CSV via pandas; PDF via Textract async (TABLE feature only)
- PDF files are treated as **rendición de cuentas** (qualitative text), saved to S3 as `{folder}/{job_id}_rendicion.txt`, not parsed for accounts — this is intentional
- LLM normalization uses a lazy-loaded prompt from S3 (`instructions/prompts/01_prompt_agent_extractor.md`); falls back to an inline prompt if S3 key doesn't exist
- The inline fallback prompt includes detailed column-selection rules for multi-period tables (e.g., ignore quarterly columns, use only "Jun YYYY" comparables, handle Nominal vs Valor columns for investment tables)
- Added `NiifValidationResult` via new `niif_validator.py` — runs deterministic checks (NIC 1 accounting equation balance, category misclassification, count/confidence gates) and attaches the result to `ExtractorOutput.niif_validation`
- Output persisted to S3 via `job_store.save()` as `extractor_response.json`

### Agent 2 — FinancialAnalyzer (`src/agents/financial_analyzer/`)
Fully refactored into modular engines — **"Math First, LLM Second"** pattern:

| Engine | File | Purpose |
|--------|------|---------|
| ratio_engine | `ratio_engine.py` | Variations, totals, financial ratios, NIIF 18 subtotals |
| materiality_engine | `materiality_engine.py` | Threshold, materiality level, impact score |
| trend_engine | `trend_engine.py` | Per-account trend detection with labels |
| anomaly_detector | `anomaly_detector.py` | Account-level and structural anomaly flags |
| variation_reliability | `variation_reliability.py` | Flags unreliable variations (new accounts, extreme %, near-zero baselines) |
| causality_engine | `causality_engine.py` | Detects causal chains between accounts |
| earnings_quality | `earnings_quality.py` | Fair value vs operating income ratio, earnings quality score |
| concentration_engine | `concentration_engine.py` | Portfolio concentration analysis |
| niif18_engine | `niif18_engine.py` | NIIF 18 compliance flags and subtotals |
| llm_reasoning | `llm_reasoning.py` | Constrained LLM call for qualitative insights |
| service | `service.py` | Orchestrates all 17 steps; merges deterministic + LLM results |

Key design decisions:
- LLM **cannot override** deterministic risk/anomaly by more than one level (ceiling rule)
- `variation_pct` is zeroed out for unreliable variations (instead of propagating a misleading number downstream)
- `FinancialHealth` has extended taxonomy: `VALUATION_DRIVEN`, `CASH_STRESSED`, `LEVERAGED`, `CONCENTRATED`, `SPECULATIVE`, `LIQUID` (added alongside STABLE/GROWING/DECLINING/CRITICAL)
- NIIF 18 subtotals computed: `ebitda_niif18`, `resultado_operativo`, `resultado_financiamiento`, `resultado_neto`

### Local Server (`local_server.py`)
- Added smart-continue logic: `POST /analyses/{id}/continue` reads current status from S3 and routes to the correct next stage (Agent 2 if `extraction_complete`, Agents 3+4 if `analysis_complete`)
- Added `DELETE /analyses/{id}` cancel endpoint with in-memory `_cancelled_jobs` set; background threads check before doing work
- Flow is now 3-phase (Agent 1 → pause → Agent 2 → pause → Agents 3+4) instead of single-run

### Models (`src/shared/models/`)
- `AccountAnalysis` gained new fields: `variation_reliability`, `reliability_label`, `impact_score`, `trend_label`, `confidence`, `evidence_count`, `evidence_sources`, `causality_chain`
- `AnalyzerOutput` gained: `financial_ratios`, `niif18_compliance`, `earnings_quality`, `portfolio_concentration`, `causality_chains`, `niif_validation`
- `ExtractorOutput` gained: `niif_validation` (NiifValidationResult)
- `FinancialHealth` extended with 6 new taxonomy values

---

## What Was Tested

### Agent 1 — Extraction
- Ran against real BTG Pactual data: `EEFF_BTGPactual_COMPLETO.xlsx` + `Rendición Cuentas Acciones Colombia Junio 2025.pdf`
- The extractor successfully processed the Excel and produced `extractor_response.json` in S3
- PDF was saved as rendición text (not parsed for accounts) — expected behavior
- **Awaiting**: the user will provide the `extractor_response.json` to review extraction quality

### Agent 2 — FinancialAnalyzer
- Code is complete but **has not been run end-to-end yet**
- Will run immediately after Agent 1 output is validated

---

## What Failed / Known Issues

1. **`status.json` is not useful for debugging** — it's a single-state write-only file. There is no event log or state history. If a job fails mid-pipeline, only the final error string is available. Debugging requires reading CloudWatch logs or the intermediate JSON files in `jobs/{job_id}/`.

2. **`previous_value` for new accounts** — when a fund or position didn't exist in the prior period, the LLM must set `previous_value: null`. The inline prompt has explicit rules for this, but the LLM may still emit `0` instead of `null` in edge cases. The downstream `calculate_account_variation` treats `previous_value=None` as a new account (reliability=`NEW_ACCOUNT`), so variation_pct is suppressed — this is the safe fallback.

3. **Multi-column table selection** — if the Excel has 4+ value columns (e.g., Jun 2025 | Jun 2024 | Trim 2025 | Trim 2024), the LLM must pick only the two acumulado comparable columns and ignore the trimestrales. The inline prompt has explicit rules, but this is the most fragile part of extraction.

4. **S3 prompt not yet uploaded** — `instructions/prompts/01_prompt_agent_extractor.md` may not exist in S3 yet. The handler falls back to the inline prompt automatically, so extraction still works.

5. **Agents 3 and 4 are stubs** — RiskScorer passes all zeros; ReportGenerator saves an empty-narrative `.md` to S3. The pipeline completes but the final report has no real content beyond the account analysis table from Agent 2.

---

## What Was Successful

- Agent 1 runs end-to-end in local dev (real AWS S3, real Textract, real Anthropic API)
- NIIF structural validator catches missing categories, equation imbalance, low confidence
- Lazy-loaded S3 prompts with inline fallback works transparently
- Local server 3-phase flow confirmed working with cancel support
- Agent 2 architecture is sound — all 17 pipeline steps are wired; the deterministic math engines are independent and individually testable

---

## Files Currently Being Worked On

| File | Status |
|------|--------|
| `src/agents/document_extractor/handler.py` | Stable — Agent 1 complete |
| `src/agents/document_extractor/niif_validator.py` | New — deterministic NIIF checks |
| `src/agents/financial_analyzer/service.py` | New — full 17-step pipeline orchestrator |
| `src/agents/financial_analyzer/ratio_engine.py` | New — math engine |
| `src/agents/financial_analyzer/materiality_engine.py` | New |
| `src/agents/financial_analyzer/trend_engine.py` | New |
| `src/agents/financial_analyzer/anomaly_detector.py` | New |
| `src/agents/financial_analyzer/variation_reliability.py` | New |
| `src/agents/financial_analyzer/causality_engine.py` | New |
| `src/agents/financial_analyzer/earnings_quality.py` | New |
| `src/agents/financial_analyzer/concentration_engine.py` | New |
| `src/agents/financial_analyzer/niif18_engine.py` | New |
| `src/agents/financial_analyzer/llm_reasoning.py` | New |
| `src/agents/financial_analyzer/handler.py` | New — Lambda entry point |
| `src/agents/risk_scorer/handler.py` | Stub — TODO |
| `src/agents/report_generator/handler.py` | Stub — TODO |
| `local_server.py` | Updated — 3-phase flow, cancel endpoint |
| `src/shared/models/analyzer.py` | Updated — new AccountAnalysis fields |
| `src/shared/models/extractor.py` | Updated — added niif_validation |
| `src/shared/models/base.py` | Updated — extended FinancialHealth enum |

---

## Next Steps (in order)

### Immediate — Validate Agent 1 Output
1. User provides `extractor_response.json` content
2. Review: correct period detection (should be `["2025-06", "2024-06"]`), correct `current_value`/`previous_value` column mapping, no spurious `0` where `null` is expected, reasonable `confidence_score` values
3. If extraction quality is poor, update the S3 prompt at `instructions/prompts/01_prompt_agent_extractor.md` and re-run without touching code

### Next — Test Agent 2 End-to-End
4. After Agent 1 output is validated, call `POST /analyses/{id}/continue` to trigger Agent 2
5. Check `financial_analyzer_response.json` in S3:
   - Verify `financial_ratios` (razón corriente, prueba ácida, capital de trabajo, deuda/patrimonio)
   - Verify NIIF 18 subtotals make sense for a fund (BTG may have no COGS → ebitda_niif18 calculation needs review)
   - Check `overall_financial_health` value — expect `VALUATION_DRIVEN` or `CONCENTRATED` for a securities fund
   - Spot-check `analysis_results` for top 5 accounts by `impact_score`: causes, insights, anomaly flags

### After Agent 2 Passes
6. **Implement Agent 3 — RiskScorer**: replace stub with real validation logic using `RevisorInteligente` patterns (math verification, hallucination detection, compliance flags, `validation_score` 0–100)
7. **Implement Agent 4 — ReportGenerator**: LLM call to generate `executive_summary`, `board_summary`, NIIF note drafts. Read template from `instructions/template_reporte_final_eeff.md`. Use `executive_narrative` from Agent 2 as context seed.
8. Run full pipeline end-to-end and validate final `.md` report in S3

### Frontend
- The two-phase UI (Agent 1 → review → Agents 2–4) is already wired in `AnalysisPage.tsx`
- A third "Continue (Agent 2 → Agent 3+4)" button may be needed now that there are 3 phases locally
- The accounts table display from Agent 1 output needs to be verified against the actual `extractor_response.json` field names

---

## Key S3 Artifacts for Debugging

```
iastronauts-creditiq-us-east-1-dev/
├── jobs/{job_id}/
│   ├── status.json                  ← current status only (not useful for history)
│   ├── extractor_response.json      ← Agent 1 full output ← REVIEW THIS FIRST
│   ├── financial_analyzer_response.json  ← Agent 2 full output
│   └── report_generator_response.json    ← Agent 4 output (stub currently)
├── uploads/junio-2025/
│   ├── EEFF_BTGPactual_COMPLETO.xlsx
│   ├── Rendición Cuentas Acciones Colombia Junio 2025.pdf
│   └── {job_id}_rendicion.txt       ← Textract text from PDF
└── instructions/
    ├── template_reporte_final_eeff.md
    └── prompts/01_prompt_agent_extractor.md  ← may not exist yet (falls back to inline)
```

---

## Environment Setup (Local Dev)

```bash
# From iastronauts_creditiq_back/
uvicorn local_server:app --reload --reload-dir src --port 8000

# .env must have:
LLM_PROVIDER=anthropic_api
ANTHROPIC_API_KEY=sk-ant-...
AWS_REGION=us-east-1
STAGE=dev
MAIN_BUCKET=iastronauts-creditiq-us-east-1-dev
LOCAL_DEV_BYPASS_SFN=true
```
