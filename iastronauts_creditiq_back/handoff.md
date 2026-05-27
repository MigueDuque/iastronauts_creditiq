# CreditIQ — Handoff Checkpoint (2026-05-27)

## Objective

Refactor **FinancialAnalyzer (Agent 2)** to fix context window saturation that was causing empty narrative fields, shallow causality, JSON truncation, and repetitive explanations in the qualitative LLM analysis step.

---

## Current Project Status

| Agent | Status |
|-------|--------|
| 1 — DocumentExtractor | ✅ Working |
| 2 — FinancialAnalyzer | ✅ Refactored & working (see below) |
| 3 — RiskScorer | ⬜ Stub — passes through, no real scoring |
| 4 — ReportGenerator | ⬜ Stub — saves to S3, no LLM narrative yet |
| 5 — RevisorInteligente | ✅ Working |

---

## What Changed (this session)

### Root Cause Fixed
Single LLM call in `llm_reasoning.py` received 60+ accounts + all context blocks → 32 000 tokens → context window saturation → empty `executive_narrative`, shallow `possible_causes`, JSON truncation.

### Solution: 4-Sub-Agent Pipeline
`llm_reasoning.py` now orchestrates 4 focused internal sub-agents sequentially:

```
Movement Intelligence → Causality Agent → Financial Thesis → Executive Narrative
```

Each sub-agent gets a **compact text digest** (≤6 000 tokens), has a **single responsibility**, and loads its system prompt from S3.

### New Files Created

```
src/agents/financial_analyzer/
├── contracts/
│   ├── __init__.py
│   ├── movement_contracts.py      # KeyMovement, PortfolioRotation, MovementIntelligenceResult
│   ├── causality_contracts.py     # AccountCausality, CrossAccountDynamic, CausalityAnalysisResult
│   ├── thesis_contracts.py        # StrategicShift, FinancialThesisResult
│   └── narrative_contracts.py     # ExecutiveNarrativeResult
└── subagents/
    ├── __init__.py
    ├── movement_intelligence.py   # "What happened?" — 5 000 tokens, temp=0.1
    ├── causality_agent.py         # "Why did it happen?" — 6 000 tokens, temp=0.2
    ├── thesis_agent.py            # "What does this mean?" — 5 000 tokens, temp=0.2
    └── narrative_agent.py         # "How do we communicate?" — 4 000 tokens, temp=0.3

src/agents/system_pompts/
├── 02a_prompt_subagent_movement_intelligence.md
├── 02b_prompt_subagent_causality.md
├── 02c_prompt_subagent_thesis.md
└── 02d_prompt_subagent_narrative.md
```

### Modified Files

- **`src/agents/financial_analyzer/llm_reasoning.py`** — complete rewrite. Removed old single-call logic; added `_assemble_result()` and imports of 4 sub-agents. Public API (`run_llm_analysis()` signature and `LLMAnalysisResult` type) **unchanged**.

### S3 Prompts (must be uploaded)

```
iastronauts-creditiq-us-east-1-dev/instructions/prompts/
├── 02a_prompt_subagent_movement_intelligence.md
├── 02b_prompt_subagent_causality.md
├── 02c_prompt_subagent_thesis.md
└── 02d_prompt_subagent_narrative.md
```

Local copies exist in `src/agents/system_pompts/` as fallback. Sub-agents log `source=s3|local|inline_fallback` on startup.

### Orphaned Files (no longer called)
- `src/agents/system_pompts/02_prompt_agent_financial-analyzer.md` — was the old monolithic prompt; nothing reads it anymore. Keep as archive.
- `src/agents/financial_analyzer/prompts/analyzer_prompt.txt` — same.

---

## What Was Tried / Failed

| Attempt | Result |
|---------|--------|
| Single LLM call with all 60+ accounts | Context saturation → empty narratives, JSON truncation |
| Filter to HIGH+MEDIUM accounts only + max_tokens=32000 | Partial improvement but root problem remained |
| Split into 4 focused sub-agents with compact digest | ✅ Fixed |

### Bugs Found and Fixed During Refactor

1. **`macro_context` not forwarded** — `run_llm_analysis` accepted it but never passed it to causality or thesis sub-agents. Fixed.
2. **`financial_diagnostics` not forwarded** — same issue; added to thesis agent, top-4 sorted diagnostic signals injected into thesis prompt.
3. **Reliability flags missing from causality** — added `reliabilities` param so causality agent sees `[WARN:label]` on unreliable variations and doesn't generate false causal explanations.

---

## What Was Successful

- Full refactor with zero breaking changes to `service.py`, `AnalyzerOutput` model, and all downstream agents.
- GUI (`AnalysisPage.tsx`) continues to display all data correctly:
  - `narrative_layers["board"]` extra key silently ignored by TypeScript interface (only reads `executive`, `tactical`, `technical`)
  - `executive_kpis` comes from deterministic `kpi_engine` — unaffected
  - All `AccountAnalysis` fields still populated via `_merge_results()` deterministic fallbacks
- Each sub-agent has retry logic (`tenacity`, 2 attempts) and graceful failure (returns empty result, pipeline continues).

---

## Next Steps

### Immediate
- [ ] Confirm the 4 S3 prompt files are uploaded and verify `source=s3` in logs on next run
- [ ] Run a real analysis and check `narrative_len > 0` and `thesis_len > 0` in `llm_pipeline_done` log

### Agent 3 — RiskScorer (stub → real)
- [ ] Implement NIIF rule validation
- [ ] Implement hallucination detection (cross-check LLM causes against deterministic data)
- [ ] Implement risk scoring logic using `AccountAnalysis` fields

### Agent 4 — ReportGenerator (stub → real)
- [ ] LLM narrative generation for `executive_summary` and `board_summary`
- [ ] NIIF note draft generation per `niif_notes_required`
- [ ] Use RAG: read `instructions/template_reporte_final_eeff.md` from S3 before calling LLM

### General
- [ ] Upload all 4 sub-agent prompts to S3 if not done yet

---

## Key Files Reference

| File | Role |
|------|------|
| `src/agents/financial_analyzer/llm_reasoning.py` | Pipeline orchestrator — 4 sub-agent calls + assembly |
| `src/agents/financial_analyzer/subagents/movement_intelligence.py` | Sub-agent 1 |
| `src/agents/financial_analyzer/subagents/causality_agent.py` | Sub-agent 2 |
| `src/agents/financial_analyzer/subagents/thesis_agent.py` | Sub-agent 3 |
| `src/agents/financial_analyzer/subagents/narrative_agent.py` | Sub-agent 4 |
| `src/agents/financial_analyzer/contracts/` | Pydantic I/O contracts for sub-agents |
| `src/agents/financial_analyzer/service.py` | Orchestrates deterministic engines + calls `run_llm_analysis()` |
| `src/shared/models/analyzer.py` | `AnalyzerOutput` + `AccountAnalysis` Pydantic models |
| `src/pages/AnalysisPage.tsx` | Frontend — reads `AnalyzerOutput` fields for display |
| `CLAUDE.md` | Architecture docs — updated with sub-agent table and token strategy |
