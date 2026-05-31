from pydantic import BaseModel

from .base import MaterialityLevel, RiskLevel, FinancialHealth, OutputFormat
from .orchestrator import BusinessContext


class AccountAnalysis(BaseModel):
    account_id: str
    account_name: str
    current_value: float
    previous_value: float
    absolute_variation: float
    variation_pct: float
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
