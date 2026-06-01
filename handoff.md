# CreditIQ — Session Handoff Checkpoint
**Date:** 2026-05-31  
**Branch:** master

---

## Objective

CreditIQ is a multi-agent AI platform for automated financial analysis (BTG Pactual AI Challenge 2026). It ingests PDF/Excel/CSV financial statements and produces NIIF notes, variance analysis, risk scoring, and executive Word reports.

Pipeline: `DocumentExtractor → FinancialAnalyzer → RiskScorer → ReportGenerator → RevisorInteligente`

---

## Project Status

5-agent pipeline is fully wired in Step Functions. All agents are functional. The session focused entirely on **Agent 4 (ReportGenerator)** and **Agent 5 (RevisorInteligente)** improvements, plus a frontend bug fix.

---

## Files Changed This Session

### Backend — `iastronauts_creditiq_back/src/agents/report_generator/`

| File | What changed |
|------|-------------|
| `handler.py` | Major changes — see detail below |
| `template_filler.py` | `build_deterministic_fields` now accepts `row_needs`; `_balance`, `_income`, `_portfolio` accept dynamic `n_rows` |

### Backend — `iastronauts_creditiq_back/src/agents/revisor_inteligente/`

| File | What changed |
|------|-------------|
| `handler.py` | Extracted inline `_NARRATIVE_SYSTEM_PROMPT` to S3/local/inline three-tier loader; added `logging`, `os`, `load_text` imports |

### New files

| File | Purpose |
|------|---------|
| `src/agents/system_pompts/05_prompt_agent_revisor-inteligente.md` | System prompt for Agent 5 narrative quality checks (6 rules: figure consistency, sentence count, note completeness, etc.) |

### Frontend — `iastronauts_creditiq_front/src/components/`

| File | What changed |
|------|-------------|
| `EarthIntelligenceCard.tsx` | Fixed Earth globe not rendering: replaced `<div style={{backgroundImage}}>` (collapsed to 0px height) with `<img src={imageSrc}>` with explicit equal width+height clamp; `ref` type updated to `HTMLImageElement` |

---

## Detailed Changes — `report_generator/handler.py`

### 1. Instruction stripping (`~~text~~`)
- Added `_INSTRUCTION_RE = re.compile(r"~~.+?~~", re.DOTALL)`
- `_replace_paragraph` now strips `~~...~~` markers deterministically alongside `{{PLACEHOLDER}}` substitution
- Covers body paragraphs, all table cells (nested too), headers and footers

### 2. Header/footer + nested table scanning (previous session fix, already in code)
- `_extract_placeholders` and `_fill_docx_template` now iterate `doc.sections` for all 6 header/footer variants
- `_scan_table` / `_replace_table` are recursive for nested tables

### 3. Empty page fix (previous session fix, already in code)
- When a placeholder maps to `""`, runs are cleared instead of leaving a styled empty paragraph

### 4. Agent 1 extractor output integration
**New imports:** `EXTRACTOR`, `load as job_load` from `shared.job_store`

**New functions in handler.py:**
- `_load_extractor_accounts(job_id)` — loads `extractor_response.json` from S3; returns `[]` on failure (non-fatal)
- `_classify_extractor_account(acc: dict)` — classifies raw account dict as `balance_sheet` or `income_statement`
- `_compute_row_needs(accounts)` — returns `{"BS": N, "IS_ACC": N, "IS_Q2": N, "PORTFOLIO": N}` from extractor account counts
- `_detect_table_row_info(table)` — finds `(prefix, max_row)` for `PREFIX_Rn_Cm` tables
- `_expand_docx_table(table, current_n, target_n, prefix)` — clones anchor row, renames placeholders (`R6_` → `R7_`, etc.) using lxml `w:t` element text replacement
- `_expand_all_tables(docx_bytes, row_needs)` — applies expansion to all tables; capped at `_MAX_TABLE_ROWS = 30`

**`lambda_handler` flow (new steps):**
1. After historical reports: call `_load_extractor_accounts` + `_compute_row_needs`
2. **Before** `_extract_placeholders`: call `_expand_all_tables` so new rows exist when template is scanned
3. Pass `row_needs` to `build_deterministic_fields`
4. Pass `extractor_accounts` to `_build_llm_digest` — adds `## CUENTAS POR HOJA (EXTRACTOR)` section grouped by `source_sheet`

**`template_filler.py` changes:**
- `build_deterministic_fields(... row_needs: dict[str, int] | None = None)` — new kwarg
- `_balance(fields, payload, n_rows=6)`, `_income(fields, payload, n_rows=8)`, `_portfolio(fields, payload, n_rows=10)` — dynamic row count from `row_needs`

---

## What Was Tried / Outcome

| Item | Outcome |
|------|---------|
| Extract Agent 5 system prompt to .md file + S3 loader | ✅ Done |
| Fix header/footer fields not being modified in .docx | ✅ Done |
| Fix nested tables not being edited | ✅ Done |
| Fix empty pages from blank placeholder paragraphs | ✅ Done |
| Strip `~~instruction~~` markers deterministically | ✅ Done |
| Fix Earth globe not showing in Dashboard | ✅ Fixed — was a `height: auto` collapse on position:absolute div |
| Automated browser screenshot to verify Earth fix | ❌ Could not screenshot — sandbox prevents headless Chrome from binding to localhost; TypeScript compiled clean with 0 errors |
| Load extractor output for dynamic table row expansion | ✅ Done |

---

## Architecture Notes to Keep in Mind

- **System prompt folder has a typo:** `system_pompts/` — do NOT rename it, it's referenced in code
- **`tenant_id` must flow through every agent output** — dropping it causes `ValidationError` at the *next* agent
- **S3 job artifact path:** `jobs/{YYYY-MM-DD}/{job_id}/{artifact}.json` — use `job_store.load/save`, never raw boto3
- **Extractor artifact name:** `extractor_response` (not `extractor_output`) — confirmed in `job_store.py` line 35
- **LLM provider:** always use `shared/llm_provider.py` wrapper, never import SDK directly in agent handlers
- **OutputFormat:** `markdown | pdf` only — PPT was removed, do not re-add

---

## Next Steps

- [ ] **Deploy** to AWS: `sam build --use-container && sam deploy` — required to activate Agent 5 in Step Functions and any Lambda env var changes
- [ ] **Upload Agent 5 prompt to S3** so production uses the file instead of inline fallback:
  ```bash
  aws s3 cp src/agents/system_pompts/05_prompt_agent_revisor-inteligente.md \
    s3://iastronauts-creditiq-us-east-1-dev/instructions/prompts/05_prompt_agent_revisor-inteligente.md
  ```
- [ ] **Verify Earth globe** in browser after `npm run dev` — TypeScript is clean, visual confirmation pending
- [ ] **Test `~~instruction~~` stripping** end-to-end with a real template that has instruction markers
- [ ] **Test dynamic table expansion** end-to-end — run a job with more accounts than template rows and verify the Word doc has the right number of rows filled
- [ ] **Agent 4 LLM narrative quality** — the system prompt (`04_prompt_agent_report-generator.md`) is solid but the executive narrative output is still basic; richer prompting or a dedicated narrative sub-agent could improve it
- [ ] **`IS_Q2` table** — quarterly split still shows `N/D` for all values because upstream agents don't produce quarterly breakdowns; consider removing or labeling this as "not applicable"
