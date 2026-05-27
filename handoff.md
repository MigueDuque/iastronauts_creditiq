# CreditIQ — Session Handoff Checkpoint
**Date:** 2026-05-26  
**Branch:** master  
**Last job tested:** `018592e3-ea10-4908-b2f8-ec7e0fbc2baf`

---

## Project Objective

**CreditIQ** is a multi-agent AI platform for automated financial analysis, built for the **AI Innovation Challenge 2026 — BTG Pactual Colombia**.

It ingests financial statements (PDF/Excel/CSV) and produces:
- NIIF note drafts
- Variance analysis
- Risk scoring
- Executive summaries

**Stack:** AWS SAM backend (Python 3.12, Lambda + Step Functions) + React 19 + Vite frontend.

**Pipeline (5 agents):**
```
DocumentExtractor → FinancialAnalyzer → RiskScorer → ReportGenerator → RevisorInteligente
```

---

## Current Project Status

| Agent | Status |
|-------|--------|
| 1 — DocumentExtractor | ✅ Implemented & working |
| 2 — FinancialAnalyzer | ✅ Implemented — **BUG (see below)** |
| 3 — RiskScorer | ⬜ Stub |
| 4 — ReportGenerator | ⬜ Stub |
| 5 — RevisorInteligente | ✅ Implemented |

Local dev runs via `uvicorn local_server:app --reload --port 8000` with `LOCAL_DEV_BYPASS_SFN=true`.

---

## What Was Done This Session

### 1. Built the Macro Context Engine (new module)

**Location:** `iastronauts_creditiq_back/src/shared/macro_context/`

**Files created:**
| File | Purpose |
|------|---------|
| `__init__.py` | Public API: `from shared.macro_context import generate_macro_context` |
| `engine.py` | Main orchestrator — fetches all 3 sources, classifies, builds output dict |
| `tradingeconomics_client.py` | Colombian macro indicators (interest rate, inflation, GDP, unemployment). Requires `TRADINGECONOMICS_API_KEY` env var |
| `gnews_client.py` | Top-headlines + targeted searches (ES + EN) for Colombian financial news. Requires `GNEWS_API_KEY` |
| `yfinance_client.py` | 90-day market data for EC, CIB, GRUPOSURA.CL, GRUPOARGOS.CL, EEB.CL, GXG, COP=X, ^GSPC |
| `macro_classifier.py` | Pure functions converting raw numbers → qualitative states (declining/stable/increasing, etc.) |
| `signal_builder.py` | Builds 3–6 executive macro signals + market_assets_context entries |
| `prompts/macro_context_prompt.txt` | LLM prompt for news relevance scoring |

**Output schema** (returned as dict, stored in `AnalyzerOutput.macro_context`):
```json
{
  "country": "Colombia",
  "analysis_period": "2025-H1",
  "macro_context": { "interest_rate_environment", "inflation_trend", "market_liquidity", "currency_environment", "economic_cycle" },
  "market_context": { "equity_market_sentiment", "fixed_income_environment", "market_volatility", "investor_risk_appetite" },
  "sector_context": [{ "sector", "trend" }],
  "news_context": [{ "headline", "summary", "relevance" }],
  "market_assets_context": [{ "ticker", "company", "trend", "market_signal" }],
  "macro_signals": ["...executive signal strings..."],
  "_data_availability": { "tradingeconomics", "gnews", "yfinance" }
}
```

**Key design rules:**
- Each data source fails silently (returns safe defaults) if API key is missing or call errors
- No trading recommendations, no buy/sell signals, no fabricated values
- `_data_availability` field shows which sources were live

### 2. Integrated Macro Context into FinancialAnalyzer

**Files modified:**
- `src/agents/financial_analyzer/service.py` — added Step 14b: fetches macro context before LLM call (non-fatal if fails)
- `src/agents/financial_analyzer/llm_reasoning.py` — added `macro_context` param to `run_llm_analysis` and `_build_user_prompt`; injects compact macro summary + top 3 news headlines into the LLM prompt; added constraint rule #7 (LLM must use macro as backdrop only, not invent values)
- `src/shared/models/analyzer.py` — added `macro_context: dict = {}` field to `AnalyzerOutput` so it flows to downstream agents

### 3. Fixed Analysis Bugs in Macro Context Clients

After analysis of all 3 clients, these bugs were found and fixed:

**GNews:**
- Removed `country=co` from `/search` endpoint (was blocking Reuters/Bloomberg/FT international coverage of Colombia)
- Added 4 English-language targeted queries (`_COLOMBIA_QUERIES_EN`) alongside the 6 Spanish ones

**yfinance:**
- Removed `ISA.CL` ticker (ISA was delisted after Ecopetrol acquisition in 2022)
- Added `EEB.CL` (Empresa de Energía de Bogotá) as live infrastructure replacement

**TradingEconomics:**
- Removed unused `_COLOMBIA_INDICATORS` constant (dead code)
- Fixed wrong fallback: `row.get("LatestValue") or row.get("LastUpdate")` — `LastUpdate` is a date string, not a number; removed it

**engine.py:**
- Removed unused `import os`

### 4. Requirements & Documentation Sync

- `requirements.txt` (root dev reference) — added `yfinance>=0.2.40` and `tradingeconomics==4.5.10` (was out of sync with `src/requirements.txt`)
- `.env.example` — added `GNEWS_API_KEY` and `TRADINGECONOMICS_API_KEY` with explanatory comments

---

## Active Bug — FinancialAnalyzer LLM Failure

### Symptom
```
llm_failed | job=018592e3-ea10-4908-b2f8-ec7e0fbc2baf error=RetryError[<Future at 0x2bfd89f3fd0 state=finished raised Exception>]
```

The FinancialAnalyzer agent fails at the LLM qualitative reasoning step (Step 15). Tenacity retries 3 times, all fail.

**Important behavior observed:** When the user does something in the GUI and reloads the job, it works — and the extraction JSON (`extractor_output.json`) IS in S3 correctly. So:
- Agent 1 (DocumentExtractor) completed successfully
- The extracted data is persisted in S3
- The failure is specifically in Agent 2's LLM call

### Root Cause (diagnosed but not yet fixed)

The partial LLM output in the error log shows the JSON was cut off mid-string:
```
"evidence_sources": [
  "Total   ← truncated here
```

This is a **`max_tokens` output limit exhaustion**. The `_invoke` function in `llm_reasoning.py` calls `llm.generate_json()` with **no `max_tokens` argument**, defaulting to `4096` in `LLMProvider`:

```python
# llm_reasoning.py line 346-354
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=8))
def _invoke(system_prompt, user_prompt, llm, tenant_id, job_id) -> dict:
    result = llm.generate_json(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        temperature=0.2,
        tenant_id=tenant_id,
        job_id=job_id,
        # ← max_tokens NOT passed → defaults to 4096
    )
```

A large investment fund analysis with many accounts + fund_analysis + causal chains + earnings quality + concentration + macro context JSON easily exceeds 4096 output tokens → JSON truncated → parse fails → all 3 retries fail with same truncation → `RetryError`.

### Fix Required

In `src/agents/financial_analyzer/llm_reasoning.py`, change `_invoke` to pass a higher `max_tokens`. The `LLMProvider` docstring says up to 16384 is supported for large JSON arrays:

```python
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=8))
def _invoke(system_prompt: str, user_prompt: str, llm: LLMProvider, tenant_id: str, job_id: str) -> dict:
    result = llm.generate_json(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        temperature=0.2,
        tenant_id=tenant_id,
        job_id=job_id,
        max_tokens=16384,   # ← ADD THIS
    )
    return result if isinstance(result, dict) else {}
```

**Why 16384:** Investment fund analyses with 30-50 accounts, each with `possible_causes`, `executive_insight`, `evidence_sources`, plus the global `executive_narrative` block, routinely exceed 4096 tokens. 16384 is the max supported by the provider wrapper and matches what DocumentExtractor already uses for large extractions.

---

## Key Files Reference

### Backend
```
iastronauts_creditiq_back/
├── src/
│   ├── agents/
│   │   ├── financial_analyzer/
│   │   │   ├── service.py          ← orchestrator (17 steps); Step 14b = macro fetch
│   │   │   ├── llm_reasoning.py    ← _invoke (BUG: max_tokens=4096 default)
│   │   │   ├── handler.py
│   │   │   ├── ratio_engine.py
│   │   │   ├── materiality_engine.py
│   │   │   ├── trend_engine.py
│   │   │   ├── anomaly_detector.py
│   │   │   ├── variation_reliability.py
│   │   │   ├── causality_engine.py
│   │   │   ├── earnings_quality.py
│   │   │   ├── concentration_engine.py
│   │   │   ├── niif18_engine.py
│   │   │   └── fund_engine.py
│   │   └── document_extractor/handler.py
│   └── shared/
│       ├── macro_context/          ← NEW module (this session)
│       │   ├── engine.py
│       │   ├── gnews_client.py
│       │   ├── yfinance_client.py
│       │   ├── tradingeconomics_client.py
│       │   ├── macro_classifier.py
│       │   ├── signal_builder.py
│       │   └── prompts/macro_context_prompt.txt
│       ├── models/
│       │   └── analyzer.py         ← added macro_context: dict = {} field
│       ├── llm_provider.py
│       ├── financial_math.py
│       └── s3_report_store.py
├── requirements.txt                ← synced (yfinance + tradingeconomics added)
├── src/requirements.txt            ← canonical Lambda deps (source of truth)
├── .env.example                    ← updated with GNEWS_API_KEY, TRADINGECONOMICS_API_KEY
└── local_server.py
```

### Frontend
```
iastronauts_creditiq_front/src/
├── pages/AnalysisPage.tsx
├── pages/DashboardPage.tsx
├── pages/JobResultPage.tsx
└── components/
    ├── UploadDialog.tsx
    ├── AiReasoningPipeline.tsx
    └── AppLayout.tsx
```

---

## Environment Variables

```
# Core
LLM_PROVIDER=anthropic_api
ANTHROPIC_API_KEY=sk-ant-...
AWS_REGION=us-east-1
STAGE=dev
MAIN_BUCKET=iastronauts-creditiq-us-east-1-dev
LOCAL_DEV_BYPASS_SFN=true

# Macro Context Engine (new — all optional, missing key = source silently skipped)
GNEWS_API_KEY=...          ← user already has this key
TRADINGECONOMICS_API_KEY=... ← needs to be set if TE is desired
# yfinance needs no API key
```

---

## Next Steps (Priority Order)

1. **[CRITICAL] Fix FinancialAnalyzer `max_tokens` bug**
   - File: `src/agents/financial_analyzer/llm_reasoning.py`, line ~347
   - Change: add `max_tokens=16384` to the `_invoke` → `llm.generate_json()` call
   - Test: rerun the failing job `018592e3-ea10-4908-b2f8-ec7e0fbc2baf` (or new job with same files)

2. **Implement RiskScorer (Agent 3)**
   - Currently a stub — passes through analyzer results with zero-value scoring fields
   - Needs: NIIF rule validation, materiality detection, hallucination check, compliance flags

3. **Implement ReportGenerator (Agent 4)**
   - Currently a stub — saves to S3 but no LLM narrative
   - Needs: LLM executive/board summary generation, NIIF note drafts, RAG from `instructions/template_reporte_final_eeff.md`

4. **Validate Macro Context Engine end-to-end**
   - After fixing the LLM bug, confirm `macro_context` field appears in S3 `analyzer_output.json`
   - Confirm news headlines appear in the output (they should be in `macro_context.news_context`)
   - Check `_data_availability` flags to see which sources returned data

5. **Add `TRADINGECONOMICS_API_KEY` to `.env` if account available**
   - Without it, TE client is silently disabled and all macro classifier functions fall back to `"unknown"` or heuristics

---

## Notes for Next Session

- The pipeline's 3-phase local flow: Upload → `/analyses` (Agent 1) → `/analyses/{id}/continue` (Agent 2) → `/analyses/{id}/continue` (Agents 3+4)
- Status values: `pending | processing | extraction_complete | analysis_complete | completed | failed | cancelled`
- Job state is cached in `localStorage` under `creditiq_analysis_id`, `creditiq_status`, `creditiq_report`
- S3 bucket: `iastronauts-creditiq-us-east-1-dev`
- The macro context engine is fully isolated — if any source fails, the analysis continues without it (non-fatal by design)
