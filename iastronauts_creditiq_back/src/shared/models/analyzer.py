from pydantic import BaseModel

from .base import MaterialityLevel, RiskLevel, FinancialHealth, OutputFormat
from .orchestrator import BusinessContext


class AccountAnalysis(BaseModel):
    account_id: str
    account_name: str
    current_value: float
    previous_value: float
    # False when no prior period existed for this account (brand-new position).
    # Consumers must branch on this rather than treating previous_value==0.0 as a
    # real zero baseline — the two are semantically distinct.
    has_previous_value: bool = True
    absolute_variation: float
    # None when the percentage is not economically meaningful (no baseline,
    # near-zero baseline, or extreme reclassification). 0.0 means a literal zero
    # change. The true computed number, if any, is preserved in computation_trace.
    variation_pct: float | None = None
    materiality: MaterialityLevel
    requires_niif_note: bool
    niif_note_references: list[str]
    risk_level: RiskLevel
    possible_causes: list[str]
    executive_insight: str
    anomaly_detected: bool

    # ── Improvement #1: Variation Reliability ────────────────────────────────
    variation_reliability: str = "RELIABLE"   # RELIABLE | NEW_ACCOUNT | INSUFFICIENT_BASELINE | EXTREME_VARIATION
    reliability_label: str = ""               # human-readable display label

    # ── Improvement #5: Economic Relevance Ranking ───────────────────────────
    impact_score: float = 0.0                 # 0–100; used for ordering accounts by relevance

    # ── Improvement #6: Trend Label ──────────────────────────────────────────
    trend_label: str = ""                     # human-readable trend description

    # ── Improvement #8: Confidence Engine ───────────────────────────────────
    confidence: float = 1.0                   # 0.0–1.0; confidence in this account's insight
    evidence_count: int = 0                   # number of deterministic signals supporting the insight
    evidence_sources: list[str] = []          # list of evidence descriptions

    # ── Improvement #2: Causality Chain ─────────────────────────────────────
    # Mejora 10: optional — emit None (omitted) instead of a noisy empty [] when
    # no causal chain was detected for this account.
    causality_chain: list[str] | None = None  # causal effects this account participates in

    # ── Related-party detection (NIC 24) ────────────────────────────────────
    is_related_party: bool = False
    related_party_counterpart: str | None = None

    # ── Mejora 6: Anti-hallucination flagging (set by Risk Scorer) ───────────
    # True when an anti-hallucination check failed for this account; the detail
    # explains which claim could not be verified against the source data.
    hallucination_flag: bool = False
    hallucination_detail: str | None = None

    # ── Dashboard investment signal (asset accounts only) ────────────────────
    investment_signal: str | None = None

    # ── Evidence First (Sprint 1 Item 2) ────────────────────────────────────
    # Machine-checkable causal claims produced by CausalityAgent.
    # Format: [{"claim": "...", "evidence_type": "account|variation|news|policy|note", "ref": "..."}]
    evidence: list[dict] = []

    # ── Account hierarchy (set by hierarchy_engine) ──────────────────────────
    # Role of this row within the statement structure, so downstream consumers
    # never mix levels of the same money (e.g. a portfolio summary line and its
    # per-instrument breakdown on a detail sheet):
    #   "primary_line"     — a line on a primary statement (balance/income/…)
    #   "breakdown_detail" — a detail row that rolls up into a primary line
    #   "subtotal"         — an intra-statement sum/subtotal row
    #   "grand_total"      — a top-level reported total (Total Activos, …)
    statement_role: str = "primary_line"
    # account_id of the primary line this row breaks down (only for breakdown_detail).
    parent_account_id: str | None = None
    # True when this account is NOT itself broken down elsewhere — i.e. a leaf
    # position safe to use for concentration/position-level aggregation.
    is_leaf: bool = True

    # ── Statement provenance (propagated from the extractor) ──────────────────
    # Carried through so downstream consumers (RiskScorer, ReportGenerator) can
    # apply the movement-account predicate (is_cash_flow_account) reliably on the
    # explicit statement_type instead of falling back to fragile name matching.
    # "cash_flow" and "equity_changes" rows are period movements, not closing
    # positions — they must NOT enter material-variation / anomaly / top-variation
    # rankings (their period-over-period % is economically meaningless).
    statement_type: str | None = None  # balance_sheet | income_statement | cash_flow | equity_changes
    source_sheet: str | None = None    # Excel sheet name (None for PDF/CSV)

    # ── Computation audit trail ───────────────────────────────────────────────
    # Deterministic trace of every formula used to produce this account's values.
    # Keys: absolute_variation, variation_pct, materiality_threshold,
    #       materiality_classification, impact_score.
    # Each entry: {formula, inputs, result, [note], [components]}.
    computation_trace: dict | None = None


class AnalyzerOutput(BaseModel):
    """
    Output del Agente 2 (Financial Analyzer) → input del Risk Scorer.
    Propaga el contexto global recibido de ExtractorOutput.
    """
    # Contexto global propagado desde ExtractorOutput
    job_id: str
    tenant_id: str
    business_context: BusinessContext
    niif_standards: list[str]
    report_language: str
    output_formats: list[OutputFormat]

    # Output del analyzer
    company_name: str
    currency: str
    periods: list[str]
    financial_ratios: dict
    analysis_results: list[AccountAnalysis]
    high_materiality_accounts: list[str]
    niif_notes_required: list[str]
    overall_financial_health: FinancialHealth
    executive_narrative: str
    niif18_compliance: dict = {}

    # ── Improvement #3: Earnings Quality ─────────────────────────────────────
    earnings_quality: dict = {}               # EarningsQualityResult as dict

    # ── Improvement #4: Portfolio Concentration ──────────────────────────────
    portfolio_concentration: dict = {}        # ConcentrationResult as dict

    # ── Improvement #2: Causality Chains ────────────────────────────────────
    causality_chains: list[dict] = []         # CausalChain list serialized

    # NIIF structural compliance result from DocumentExtractor (passed through)
    niif_validation: dict = {}

    # ── Fund analysis (populated when EEFF is an investment fund / CIV) ─────────
    fund_analysis: dict = {}   # FundAnalysis serialized; empty dict for non-fund entities

    # ── Macro Context Engine output ──────────────────────────────────────────
    macro_context: dict = {}   # MacroContextOutput from shared.macro_context.engine

    # ── Executive Intelligence Layer ─────────────────────────────────────────
    executive_kpis: dict = {}          # ROE, ROA, margins, liquidity, concentration, fund-specific
    portfolio_thesis: str = ""         # LLM-inferred strategic portfolio thesis (1 paragraph)
    insight_tiers: dict = {}           # {tier1_critical: [...], tier2_material: [...]}
    narrative_layers: dict = {}        # {executive: str, tactical: str, technical: str}

    # ── Phase 1 Roadmap: Executive Synthesis Engine ──────────────────────────
    executive_synthesis: dict = {}     # Deterministic portfolio story from synthesis_engine

    # ── Financial Intelligence Upgrade ───────────────────────────────────────
    # 6-section institutional analysis (revenue, balance, cashflow, equity, risks, outlook)
    structured_analysis: dict = {}
    # LLM-detected cross-statement correlation patterns (earnings-cashflow, etc.)
    cross_statement_signals: list[dict] = []
    # Sustainability classification of earnings: STRONG | MODERATE | WEAK
    earnings_sustainability: str = ""
    # Deterministic cross-statement diagnostic signals from financial_diagnostics_engine
    financial_diagnostics: dict = {}

    # ── Sheet-based Concentration (Activos / Instrumentos / Bancos) ──────────
    # Populated from source_sheet metadata; empty dict when source lacks sheet data
    sheet_concentration: dict = {}

    # ── Comparative period basis (propagated from ExtractorOutput) ───────────
    # Keyed by statement type; each entry: {current, comparative, rule, [mismatch_warning]}
    comparative_basis: dict = {}

    # ── Fund Policy Assessment (Sprint 2 Item 1) ─────────────────────────────
    # Per-dimension status vs. regulatory/internal limits for investment funds.
    # Empty dict for non-fund entities or when no concentration data is available.
    # Keys: fund_type, policy_source, limits, assessments, breach_count, near_count,
    #       within_count, summary.
    fund_policy_assessment: dict = {}

    # ── Top Variations with period labels (Sprint 2 Item 5) ──────────────────
    # Top 15 non-cash-flow accounts by |absolute_variation|, each carrying
    # current_period and comparative_period from comparative_basis.
    # Empty list when no accounts have a previous period baseline.
    top_variations: list[dict] = []

    # ── Global computation audit trail ────────────────────────────────────────
    # Covers the materiality threshold derivation and aggregation counts.
    # Per-account traces live in AccountAnalysis.computation_trace.
    # Ratio/totals traces live in financial_ratios._computation_trace.
    computation_trace: dict = {}
