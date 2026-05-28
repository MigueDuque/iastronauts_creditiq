# CreditIQ — Session Handoff Checkpoint

## Objective

Improve the multi-agent financial analysis pipeline (CreditIQ) for the AI Innovation Challenge 2026 — BTG Pactual Colombia. The platform ingests Excel/PDF financial statements and produces NIIF-compliant reports using 5 sequential AWS Lambda agents.

---

## Project Status

**Backend:** AWS SAM serverless (Python 3.12) — `iastronauts_creditiq_back/`  
**Frontend:** React 19 + Vite + TypeScript — `iastronauts_creditiq_front/`  
**Local dev:** `uvicorn local_server:app --reload --port 8000` (set `LOCAL_DEV_BYPASS_SFN=true` in `.env`)

| Agent | Status |
|-------|--------|
| 1 — DocumentExtractor | Implemented |
| 2 — FinancialAnalyzer | Implemented |
| 3 — RiskScorer | Stub |
| 4 — ReportGenerator | Stub |
| 5 — RevisorInteligente | Implemented |

---

## What We Worked On This Session

### Problem Identified

Agent 1 extracts accounts from multi-sheet Excel files but was losing two critical pieces of information:

1. Which sheet each account came from — so Agent 2 had no way to distinguish a balance sheet summary line ("Instrumentos financieros = 39,521,260") from individual investment positions in the "Inversiones" detail sheet (Grupo Cibest, ISA, Ecopetrol, etc.) that sum to the same number.

2. Whether a row is a total/subtotal — so `calculate_financial_totals()` was summing both the individual line items AND their "Total" row, causing double-counting in every ratio calculation (total_assets, total_liabilities, total_revenue, etc.).

The test file `test_files/BTG_P.ACCIONES_EEFF_2025-06-30.xlsx` has 18 sheets with this exact pattern — e.g., "Efectivo" detail sheet (3 banks + Total=262131) AND "Estado Situacion Financiera" also has Efectivo=262131. Without is_total, both the 3 bank lines AND the total get summed = 2x the real value.

---

## Files Changed

### 1. `src/shared/models/extractor.py`
Added two fields to `ExtractedAccount`:
- `source_sheet: str | None = None` — Excel sheet name where this row was found (None for PDF/CSV)
- `is_total: bool = False` — True when this row is a sum/subtotal row; skip when re-summing categories

### 2. `src/agents/document_extractor/handler.py`
- Inline fallback prompt (`_EXTRACTION_SYSTEM_PROMPT_FALLBACK`): Added two new instruction blocks teaching the LLM to read `=== Hoja: <name> ===` markers (already emitted by `extract_excel()`) and copy the sheet name into `source_sheet`; and to detect total rows by keywords and set `is_total: true`
- `_build_accounts()`: Reads `source_sheet` and `is_total` from the LLM JSON response

### 3. `src/agents/financial_analyzer/ratio_engine.py`
- `AccountVariation` dataclass gains `source_sheet` and `is_total` fields (propagated through all 13 engines)
- `calculate_account_variation()` copies them from `ExtractedAccount`
- `calculate_financial_totals()` now skips accounts where `is_total=True` at the top of the loop

### 4. `src/shared/financial_math.py`
Same `is_total` skip guard added to:
- `calculate_financial_ratios()`
- `classify_current_noncurrent()`
- `determine_materiality_threshold()`

### 5. `src/agents/system_pompts/01_prompt_agent_extractor.md`
- `source_sheet` and `is_total` added to the accounts JSON schema
- Full instruction blocks for both fields with concrete examples from BTG sheet names
- Already uploaded to S3: `iastronauts-creditiq-us-east-1-dev/instructions/prompts/01_prompt_agent_extractor.md`

---

## What Was Successful

- Identified the root cause of wrong ratio calculations (double-counting totals)
- Added source_sheet and is_total cleanly at the model level — backward compatible (both default to None/False so existing data is unaffected)
- The extract_excel() function already emitted sheet name markers, so the LLM has all the context it needs without any change to Excel parsing logic
- AccountVariation now carries sheet/total metadata through the entire Agent 2 engine chain
- S3 prompt uploaded; next run will use the updated prompt automatically

---

## What Was Not Tried / Next Steps

1. End-to-end test — Run a full analysis with the BTG Excel file and verify that extracted accounts now have source_sheet populated and is_total=true on Total rows. Check that ratios no longer double-count.

2. Agent 2 LLM awareness of totals — The is_total flag stops double-counting in math, but the LLM sub-agents (movement, causality, thesis, narrative) receive a text digest. Consider flagging is_total accounts in the digest so the LLM treats them as section summaries, not independent accounts.

3. Agent 2 cross-sheet deduplication — The same account can appear in both a detail sheet and the balance sheet summary. Both are currently kept in analysis_results (intentional), but this may confuse the LLM. Future improvement: mark the summary version as is_total=true so only detail versions drive narrative analysis.

4. Implement Agent 3 (RiskScorer) — Currently a stub. Needs NIIF validation rules, materiality detection, hallucination checks.

5. Implement Agent 4 (ReportGenerator) — Currently a stub. Needs LLM narrative generation for executive/board summaries and NIIF note drafts.

---

## Key Files Reference

| Path | Purpose |
|------|---------|
| src/shared/models/extractor.py | ExtractedAccount model — source_sheet, is_total added here |
| src/agents/document_extractor/handler.py | Agent 1 — extraction logic + inline fallback prompt |
| src/agents/system_pompts/01_prompt_agent_extractor.md | Agent 1 S3 prompt (source of truth at runtime) |
| src/agents/financial_analyzer/ratio_engine.py | Agent 2 math — calculate_financial_totals() is the key function |
| src/agents/financial_analyzer/service.py | Agent 2 orchestration — calls all 13 engines in sequence |
| src/shared/financial_math.py | Legacy math module — also patched with is_total guard |
| local_server.py | FastAPI local dev wrapper |
| test_files/BTG_P.ACCIONES_EEFF_2025-06-30.xlsx | Reference test file — 18 sheets, investment fund EEFF |
| agents_outputs_test/financial_analyzer_response.json | Latest Agent 2 output for inspection |

---

## Environment

```
LLM_PROVIDER=anthropic_api
ANTHROPIC_API_KEY=sk-ant-...
MAIN_BUCKET=iastronauts-creditiq-us-east-1-dev
LOCAL_DEV_BYPASS_SFN=true
AWS_REGION=us-east-1
STAGE=dev
```

Local server: uvicorn local_server:app --reload --port 8000 (run from iastronauts_creditiq_back/)
