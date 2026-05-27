"""
service.py

Orchestrates the complete FinancialAnalyzer pipeline.

Flow (Math First — LLM Second):
  1.  Enrich previous_value from S3 historical reports
  2.  Per-account variation calculation                  ← ratio_engine
  3.  Variation reliability assessment                   ← variation_reliability  [#1]
  4.  Global financial totals + ratios                   ← ratio_engine
  5.  Materiality threshold                              ← materiality_engine
  6.  Per-account materiality + impact score             ← materiality_engine      [#5]
  7.  Per-account trend analysis (with labels)           ← trend_engine            [#6]
  8.  Per-account anomaly detection                      ← anomaly_detector
  9.  Structural balance-sheet anomaly check             ← anomaly_detector
  10. Causality chain detection                          ← causality_engine        [#2]
  11. Earnings quality analysis                          ← earnings_quality        [#3]
  12. Portfolio concentration analysis                   ← concentration_engine    [#4]
  13. NIIF 18 subtotals + Total Comprehensive Income     ← ratio_engine
  14. NIIF 18 compliance flags                           ← niif18_engine
  15. LLM constrained qualitative reasoning              ← llm_reasoning           [#7+#8]
  16. Merge deterministic + LLM → AccountAnalysis list
  17. Build + persist AnalyzerOutput
"""

import concurrent.futures
import json
import logging
import os
from dataclasses import asdict
from datetime import datetime

import boto3

from shared.llm_provider import LLMProvider
from shared.models import ExtractorOutput, AnalyzerOutput, FinancialHealth, AccountAnalysis
from shared.models.base import MaterialityLevel, RiskLevel
from shared.s3_report_store import fetch_historical_reports, slugify
from shared.s3_instructions import load_text as load_instruction
from shared.job_store import save as job_save, FINANCIAL_ANALYZER

from .ratio_engine import (
    AccountVariation,
    FinancialTotals,
    FinancialRatios,
    NIIF18Subtotals,
    calculate_account_variation,
    calculate_financial_totals,
    calculate_ratios,
    ratios_to_dict,
    calculate_niif18_subtotals,
    calculate_total_comprehensive_income,
)
from .materiality_engine import (
    determine_threshold,
    classify as classify_materiality,
    infer_niif_references,
    calculate_impact_score,
)
from .trend_engine import Trend, detect_trend, detect_trend_full, TrendAnalysis
from .anomaly_detector import (
    AnomalyResult,
    detect_account_anomaly,
    detect_structural_anomalies,
)
from .variation_reliability import ReliabilityResult, VariationReliability, assess_reliability
from .causality_engine import CausalChain, detect_causality_chains
from .earnings_quality import EarningsQualityResult, analyze_earnings_quality
from .concentration_engine import ConcentrationResult, analyze_concentration
from .llm_reasoning import (
    LLMAnalysisResult,
    AccountLLMInsight,
    run_llm_analysis,
    compute_confidence,
)
from .niif18_engine import (
    NIIF18ComplianceResult,
    check_niif18_compliance,
    niif18_to_dict,
)
from .fund_engine import FundAnalysis, analyze_fund, fund_analysis_to_dict
from .kpi_engine import calculate_executive_kpis
from .synthesis_engine import ExecutiveSynthesis, synthesize as synthesize_portfolio, synthesis_to_dict
from shared.progress_store import emit as _emit_step
from shared.macro_context import generate_macro_context

logger = logging.getLogger("financial_analyzer.service")

BUCKET = os.environ.get("MAIN_BUCKET", "")

_VALID_HEALTH: set[str] = {h.value for h in FinancialHealth}
_VALID_RISK: set[str] = {r.value for r in RiskLevel}

_NIIF_REFERENCE_KEY = "instructions/niff_18_explicacion.md"


class FinancialAnalyzerService:
    """
    Stateless orchestrator for the FinancialAnalyzer agent.
    Instantiate once per Lambda invocation and call analyze().
    """

    def __init__(self) -> None:
        self._s3 = boto3.client("s3")
        self._llm = LLMProvider()

    # ── Public API ────────────────────────────────────────────────────────────

    def analyze(self, payload: ExtractorOutput, lambda_context=None) -> AnalyzerOutput:
        """
        Execute the full analysis pipeline for one job.
        Returns AnalyzerOutput ready to be passed to the RiskScorer agent.
        """
        # ── Step 1: Enrich previous_value from S3 history ──────────────────
        _emit_step(payload.job_id, "Loading historical data")
        enriched_accounts = self._enrich_with_history(payload)

        # ── Step 2: Per-account variation ───────────────────────────────────
        variations: list[AccountVariation] = [
            calculate_account_variation(acc) for acc in enriched_accounts
        ]

        # ── Step 3: Variation reliability [Improvement #1] ──────────────────
        reliabilities: dict[str, ReliabilityResult] = {
            v.account_id: assess_reliability(v) for v in variations
        }
        unreliable_count = sum(
            1 for r in reliabilities.values()
            if r.reliability != VariationReliability.RELIABLE
        )
        if unreliable_count:
            logger.info(
                "reliability_assessed | job=%s unreliable=%d",
                payload.job_id, unreliable_count,
            )

        # ── Step 4: Global financial totals + ratios ─────────────────────────
        _emit_step(payload.job_id, "Computing ratios & materiality")
        totals: FinancialTotals = calculate_financial_totals(enriched_accounts)
        ratios: FinancialRatios = calculate_ratios(totals)
        ratios_dict: dict = ratios_to_dict(totals, ratios)

        logger.info(
            "ratios_computed | job=%s accounts=%d "
            "assets=%.0f liab=%.0f equity=%.0f revenue=%.0f net_income=%.0f",
            payload.job_id, len(variations),
            totals.total_assets, totals.total_liabilities,
            totals.total_equity, totals.total_revenue, totals.net_income,
        )

        # ── Step 5: Materiality threshold ────────────────────────────────────
        threshold: float = determine_threshold(totals)

        # ── Step 6: Per-account materiality + impact score [Improvement #5] ──
        materialities: dict[str, MaterialityLevel] = {
            v.account_id: classify_materiality(v, threshold) for v in variations
        }
        # Detect anomalies minimally for impact_score (anomaly_detected needed pre-llm)
        # We'll do full detection in step 8 — use a placeholder flag here
        # (impact_score will be recalculated in merge if needed, but we compute
        # a first-pass here so we can sort accounts before LLM call)
        _pre_anomaly: dict[str, bool] = {}
        for v in variations:
            t = detect_trend(v)
            a = detect_account_anomaly(v, threshold, t)
            _pre_anomaly[v.account_id] = a.anomaly_detected

        impact_scores: dict[str, float] = {
            v.account_id: calculate_impact_score(
                v, threshold, totals,
                reliabilities[v.account_id].reliability,
                _pre_anomaly[v.account_id],
            )
            for v in variations
        }

        # ── Step 7: Per-account trend analysis [Improvement #6] ─────────────
        trend_analyses: dict[str, TrendAnalysis] = {
            v.account_id: detect_trend_full(v) for v in variations
        }
        trends: dict[str, Trend] = {aid: ta.trend for aid, ta in trend_analyses.items()}

        # ── Step 8: Per-account anomaly detection ────────────────────────────
        _emit_step(payload.job_id, "Detecting anomalies & causality")
        # Skip % -based anomaly rules for unreliable variations: the detector uses
        # thresholds like >200% which are meaningless on near-zero baselines or
        # reclassifications.  NEW_ACCOUNT is kept — a large new account IS a real signal.
        _SKIP_ANOMALY = {
            VariationReliability.INSUFFICIENT_BASELINE,
            VariationReliability.EXTREME_VARIATION,
        }
        anomalies: dict[str, AnomalyResult] = {
            v.account_id: (
                AnomalyResult(anomaly_detected=False)
                if reliabilities[v.account_id].reliability in _SKIP_ANOMALY
                else detect_account_anomaly(v, threshold, trends[v.account_id])
            )
            for v in variations
        }
        anomaly_count = sum(1 for a in anomalies.values() if a.anomaly_detected)

        # ── Step 9: Structural balance-sheet anomalies ───────────────────────
        structural_issues = detect_structural_anomalies(totals, ratios)
        if structural_issues:
            logger.warning(
                "structural_anomalies | job=%s count=%d issues=%s",
                payload.job_id, len(structural_issues), structural_issues,
            )

        high_materiality_accounts = [
            v.account_name
            for v in variations
            if materialities[v.account_id] == MaterialityLevel.HIGH
        ]

        # ── Step 10: Causality chain detection [Improvement #2] ──────────────
        causal_chains: list[CausalChain] = detect_causality_chains(variations, totals)
        chains_dicts = [
            {
                "chain_id": c.chain_id,
                "driver_account": c.driver_account,
                "driver_direction": c.driver_direction,
                "effects": c.effects,
                "narrative": c.narrative,
                "confidence": c.confidence,
                "evidence": c.evidence,
            }
            for c in causal_chains
        ]
        if causal_chains:
            logger.info(
                "causality_chains | job=%s chains=%d",
                payload.job_id, len(causal_chains),
            )

        # ── Step 11: Earnings quality [Improvement #3] ───────────────────────
        earnings_quality: EarningsQualityResult = analyze_earnings_quality(variations, totals)
        eq_dict = {
            "quality_score": earnings_quality.quality_score,
            "quality_label": earnings_quality.quality_label,
            "fair_value_income_ratio": earnings_quality.fair_value_income_ratio,
            "operating_income_ratio": earnings_quality.operating_income_ratio,
            "non_recurring_income_ratio": earnings_quality.non_recurring_income_ratio,
            "key_insight": earnings_quality.key_insight,
            "recommendations": earnings_quality.recommendations,
        }
        logger.info(
            "earnings_quality | job=%s score=%.1f label=%s fair_value_pct=%.1f",
            payload.job_id, earnings_quality.quality_score,
            earnings_quality.quality_label, earnings_quality.fair_value_income_ratio,
        )

        # ── Step 12: Portfolio concentration [Improvement #4] ────────────────
        concentration: ConcentrationResult = analyze_concentration(variations, totals)
        conc_dict = {
            "top_account_name": concentration.top_account_name,
            "top_account_pct": concentration.top_account_pct,
            "top3_concentration_pct": concentration.top3_concentration_pct,
            "category_concentration": concentration.category_concentration,
            "concentration_label": concentration.concentration_label,
            "insight": concentration.insight,
            "top_accounts": concentration.top_accounts,
            "hhi": concentration.hhi,
            "effective_positions": concentration.effective_positions,
        }
        logger.info(
            "concentration | job=%s label=%s top1_pct=%.1f top3_pct=%.1f",
            payload.job_id, concentration.concentration_label,
            concentration.top_account_pct, concentration.top3_concentration_pct,
        )

        # ── Step 12b: Fund-specific analysis ─────────────────────────────────
        fund_analysis: FundAnalysis
        fund_analysis, fund_chains = analyze_fund(
            accounts=enriched_accounts,
            variations=variations,
            totals=totals,
        )
        fund_dict = fund_analysis_to_dict(fund_analysis)
        # Carry Agent 1's extracted fund metadata into the fund dict so it flows to the LLM
        if payload.fund_metadata:
            fund_dict["fund_metadata_from_document"] = payload.fund_metadata

        # Merge fund-specific chains into the main chain list
        if fund_chains:
            causal_chains = fund_chains + causal_chains
            chains_dicts = [
                {
                    "chain_id": c.chain_id,
                    "driver_account": c.driver_account,
                    "driver_direction": c.driver_direction,
                    "effects": c.effects,
                    "narrative": c.narrative,
                    "confidence": c.confidence,
                    "evidence": c.evidence,
                }
                for c in causal_chains
            ]
            logger.info(
                "fund_chains_merged | job=%s fund_chains=%d total_chains=%d",
                payload.job_id, len(fund_chains), len(causal_chains),
            )

        # ── Step 12c: Executive Synthesis ────────────────────────────────────
        _emit_step(payload.job_id, "Building executive synthesis")
        portfolio_synthesis: ExecutiveSynthesis = synthesize_portfolio(
            fund_analysis=fund_dict,
            concentration=conc_dict,
            earnings_quality=eq_dict,
        )
        synthesis_dict = synthesis_to_dict(portfolio_synthesis)
        logger.info(
            "synthesis | theme=%r signals=%d conclusions=%d alerts=%d",
            portfolio_synthesis.main_portfolio_theme[:60],
            len(portfolio_synthesis.signals),
            len(portfolio_synthesis.executive_conclusions),
            len(portfolio_synthesis.board_alerts),
        )

        # ── Step 13: NIIF 18 subtotals ───────────────────────────────────────
        niif18_subtotals: NIIF18Subtotals = calculate_niif18_subtotals(
            enriched_accounts,
            depreciation_amortization=totals.depreciation_amortization,
        )
        tci_dict: dict = calculate_total_comprehensive_income(
            resultado_neto=niif18_subtotals.resultado_neto,
            accounts=enriched_accounts,
        )

        # ── Step 14: NIIF 18 compliance flags ────────────────────────────────
        niif18_compliance: NIIF18ComplianceResult = check_niif18_compliance(
            enriched_accounts, niif18_subtotals
        )
        niif18_section = niif18_to_dict(niif18_subtotals, tci_dict, niif18_compliance)

        logger.info(
            "niif18_computed | job=%s score=%d flags=%d "
            "ebitda_niif18=%.2f resultado_operativo=%.2f resultado_neto=%.2f",
            payload.job_id, niif18_compliance.compliance_score,
            len(niif18_compliance.flags),
            niif18_subtotals.ebitda_niif18,
            niif18_subtotals.resultado_operativo,
            niif18_subtotals.resultado_neto,
        )

        niif_reference_text = load_instruction(_NIIF_REFERENCE_KEY, fallback="")
        ratios_dict["niif18"] = niif18_section

        # Build a compact NIIF validation summary for the LLM prompt
        niif_validation_context: str | None = None
        if payload.niif_validation:
            v = payload.niif_validation
            niif_validation_context = (
                f"score={v.compliance_score}/100  "
                f"compliant={v.is_niif_compliant}  "
                f"equation_balanced={v.accounting_equation_balanced}\n"
            )
            for flag in v.flags:
                niif_validation_context += (
                    f"  [{flag.severity}] {flag.rule_id} "
                    f"({flag.standard}): {flag.message}\n"
                )

        logger.info(
            "pre_llm | job=%s threshold=%.2f high_mat=%d anomalies=%d "
            "structural=%d unreliable=%d causal_chains=%d",
            payload.job_id, threshold, len(high_materiality_accounts),
            anomaly_count, len(structural_issues), unreliable_count, len(causal_chains),
        )

        # ── Step 14b: Macro context enrichment ───────────────────────────────
        # Hard timeout: yfinance and TradingEconomics have no built-in timeout;
        # without this they can hang 60+ seconds and block the entire analysis.
        _MACRO_TIMEOUT = 25
        _emit_step(payload.job_id, "Fetching macro context")
        macro_ctx: dict | None = None
        try:
            reporting_period = getattr(payload.business_context, "reporting_period", None) or ""
            analysis_period = reporting_period or None
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as _pool:
                _fut = _pool.submit(
                    generate_macro_context,
                    analysis_period=analysis_period,
                    llm_provider=self._llm,
                )
                try:
                    macro_ctx = _fut.result(timeout=_MACRO_TIMEOUT)
                    logger.info(
                        "macro_context_ok | job=%s signals=%d assets=%d news=%d",
                        payload.job_id,
                        len(macro_ctx.get("macro_signals", [])),
                        len(macro_ctx.get("market_assets_context", [])),
                        len(macro_ctx.get("news_context", [])),
                    )
                except concurrent.futures.TimeoutError:
                    logger.warning(
                        "macro_context_timeout | job=%s exceeded=%ds — continuing without macro",
                        payload.job_id, _MACRO_TIMEOUT,
                    )
        except Exception as exc:
            logger.warning("macro_context_failed | job=%s error=%s", payload.job_id, exc)
            macro_ctx = None

        # ── Step 15: LLM constrained qualitative reasoning [#7 + #8] ─────────
        _emit_step(payload.job_id, "LLM qualitative analysis")
        llm_result: LLMAnalysisResult = run_llm_analysis(
            company_name=payload.company_name,
            periods=payload.periods,
            business_context_snippet=payload.business_context.raw_context,
            ratios_dict=ratios_dict,
            threshold=threshold,
            variations=variations,
            materialities=materialities,
            reliabilities=reliabilities,
            llm=self._llm,
            tenant_id=payload.tenant_id,
            job_id=payload.job_id,
            niif_reference_text=niif_reference_text,
            causality_chains=chains_dicts if chains_dicts else None,
            earnings_quality=eq_dict,
            concentration=conc_dict,
            niif_validation_context=niif_validation_context,
            fund_analysis=fund_dict if fund_analysis.is_investment_fund else None,
            macro_context=macro_ctx,
            executive_synthesis=synthesis_dict,
        )

        # ── Step 15b: Executive KPI consolidation ────────────────────────────
        executive_kpis = calculate_executive_kpis(
            ratios_dict=ratios_dict,
            earnings_quality=eq_dict,
            concentration=conc_dict,
            fund_analysis=fund_dict if fund_analysis.is_investment_fund else None,
        )
        logger.info(
            "executive_kpis | job=%s roe=%.1f net_margin=%.1f eq_score=%.0f top1=%.1f",
            payload.job_id,
            executive_kpis.get("profitability", {}).get("roe_pct", 0),
            executive_kpis.get("profitability", {}).get("net_margin_pct", 0),
            executive_kpis.get("earnings_quality", {}).get("quality_score", 0),
            executive_kpis.get("concentration", {}).get("top1_concentration_pct", 0),
        )

        # ── Step 16: Merge math + LLM → AccountAnalysis ──────────────────────
        analysis_results = self._merge_results(
            variations=variations,
            materialities=materialities,
            reliabilities=reliabilities,
            impact_scores=impact_scores,
            trend_analyses=trend_analyses,
            trends=trends,
            anomalies=anomalies,
            causal_chains=causal_chains,
            llm_result=llm_result,
        )

        # Sort by impact_score descending [Improvement #5]
        analysis_results.sort(key=lambda a: a.impact_score, reverse=True)

        # Collect NIIF notes
        niif_notes_required: set[str] = set(llm_result.niif_notes_required)
        for ar in analysis_results:
            if ar.requires_niif_note:
                niif_notes_required.update(ar.niif_note_references)

        overall_health = self._parse_health(llm_result.overall_financial_health)
        # Improvement #9: override generic LLM health with deterministic taxonomy hint
        _GENERIC_HEALTH = {FinancialHealth.STABLE, FinancialHealth.DECLINING, FinancialHealth.GROWING}
        if overall_health in _GENERIC_HEALTH:
            det_hint = self._suggest_health_taxonomy(ratios_dict, eq_dict, conc_dict)
            if det_hint is not None:
                logger.info(
                    "health_taxonomy_override | job=%s llm=%s det=%s",
                    payload.job_id, overall_health.value, det_hint.value,
                )
                overall_health = det_hint

        # ── Step 17: Build output + save to S3 ───────────────────────────────
        result = AnalyzerOutput(
            job_id=payload.job_id,
            tenant_id=payload.tenant_id,
            business_context=payload.business_context,
            niif_standards=payload.niif_standards,
            report_language=payload.report_language,
            output_formats=payload.output_formats,
            company_name=payload.company_name,
            currency=payload.currency,
            periods=payload.periods,
            financial_ratios=ratios_dict,
            analysis_results=analysis_results,
            high_materiality_accounts=high_materiality_accounts,
            niif_notes_required=sorted(niif_notes_required),
            overall_financial_health=overall_health,
            executive_narrative=llm_result.executive_narrative,
            niif18_compliance=niif18_section,
            earnings_quality=eq_dict,
            portfolio_concentration=conc_dict,
            causality_chains=chains_dicts,
            niif_validation=payload.niif_validation.model_dump(mode="json")
            if payload.niif_validation else {},
            fund_analysis=fund_dict,
            macro_context=macro_ctx or {},
            executive_kpis=executive_kpis,
            portfolio_thesis=llm_result.portfolio_thesis,
            insight_tiers=llm_result.insight_tiers,
            narrative_layers=llm_result.narrative_layers,
            executive_synthesis=synthesis_dict,
        )

        self._save_to_s3(result)

        logger.info(
            "analysis_complete | job=%s health=%s accounts=%d high_mat=%d "
            "niif_notes=%d anomalies=%d eq_score=%.1f conc=%s chains=%d",
            result.job_id, result.overall_financial_health.value,
            len(result.analysis_results), len(result.high_materiality_accounts),
            len(result.niif_notes_required), anomaly_count,
            earnings_quality.quality_score, concentration.concentration_label,
            len(causal_chains),
        )

        return result

    # ── Private helpers ───────────────────────────────────────────────────────

    def _enrich_with_history(self, payload: ExtractorOutput) -> list:
        """
        Fill previous_value from S3 historical reports for accounts whose
        document contained only a single period column.
        """
        reference_date = self._parse_reference_date(payload.periods)
        history_map: dict[str, float] = {}

        try:
            slug = slugify(payload.company_name)
            historical = fetch_historical_reports(
                tenant_id=payload.tenant_id,
                company_slug=slug,
                reference_date=reference_date,
                bucket=BUCKET,
            )
            if historical:
                for entry in historical[0].analysis_results:
                    history_map[entry.account_name.lower().strip()] = entry.current_value
            logger.info(
                "history_loaded | job=%s reports=%d history_keys=%d",
                payload.job_id, len(historical), len(history_map),
            )
        except Exception as exc:
            logger.warning("history_load_failed | job=%s error=%s", payload.job_id, exc)

        enriched = []
        enriched_count = 0
        for acc in payload.accounts:
            prev = acc.previous_value
            if prev is None:
                key = acc.normalized_account_name.lower().strip()
                prev = history_map.get(key)
                if prev is not None:
                    enriched_count += 1
            enriched.append(acc.model_copy(update={"previous_value": prev}))

        if enriched_count:
            logger.info(
                "accounts_enriched | job=%s count=%d", payload.job_id, enriched_count
            )

        return enriched

    def _merge_results(
        self,
        variations: list[AccountVariation],
        materialities: dict[str, MaterialityLevel],
        reliabilities: dict[str, ReliabilityResult],
        impact_scores: dict[str, float],
        trend_analyses: dict[str, TrendAnalysis],
        trends: dict[str, Trend],
        anomalies: dict[str, AnomalyResult],
        causal_chains: list[CausalChain],
        llm_result: LLMAnalysisResult,
    ) -> list[AccountAnalysis]:
        """
        Merge deterministic math outputs with LLM qualitative insights.

        Priority rules:
        - risk_level:           LLM (if valid) → anomaly severity → materiality fallback
        - anomaly_detected:     detector flag OR LLM anomaly_override
        - possible_causes:      LLM → anomaly description → trend-based default
        - executive_insight:    LLM → computed fallback
        - confidence:           deterministic (anchored to reliability + evidence)
        - variation shown:      only if RELIABLE; else display reliability_label
        """
        # Map which causal chains each account participates in
        chain_map: dict[str, list[str]] = {}
        for chain in causal_chains:
            aid_lower = chain.driver_account.lower()
            for v in variations:
                if v.account_name.lower() == aid_lower:
                    chain_map.setdefault(v.account_id, []).append(chain.narrative)

        results: list[AccountAnalysis] = []

        for v in variations:
            mat = materialities[v.account_id]
            reliability = reliabilities[v.account_id]
            anomaly = anomalies[v.account_id]
            trend_analysis = trend_analyses[v.account_id]
            trend = trends[v.account_id]
            insight: AccountLLMInsight | None = llm_result.account_insights.get(v.account_id)
            impact = impact_scores[v.account_id]

            # NIIF references: keyword inference + LLM
            inferred_niif = infer_niif_references(v.account_name, v.category, mat)
            llm_niif = insight.niif_note_references if insight else []
            niif_refs = sorted(set(inferred_niif) | set(llm_niif))

            requires_niif = (mat == MaterialityLevel.HIGH) or (
                insight.requires_niif_note if insight else False
            )

            risk_lvl = self._resolve_risk_level(insight, anomaly, mat)

            # anomaly_override is only trusted when materiality ≥ MEDIUM:
            # prevents the LLM from flagging stable LOW-materiality accounts as anomalous.
            llm_anomaly_override = (
                insight.anomaly_override
                if insight and mat != MaterialityLevel.LOW
                else False
            )
            anomaly_detected = anomaly.anomaly_detected or llm_anomaly_override

            # Possible causes — reliability explanation takes priority over anomaly
            # description when the variation itself is flagged as untrustworthy.
            if insight and insight.possible_causes:
                possible_causes = insight.possible_causes
            elif reliability.suppress_pct:
                # Unreliable variation: the reliability explanation is more truthful
                # than any anomaly description that referenced the bad percentage.
                possible_causes = [reliability.explanation or reliability.display_label]
            elif anomaly.anomaly_detected:
                possible_causes = [anomaly.description]
            else:
                possible_causes = [
                    f"Tendencia {trend.value}: {trend_analysis.trend_label}."
                ]

            # Executive insight
            if insight and insight.executive_insight:
                executive_insight = insight.executive_insight
            elif reliability.reliability != VariationReliability.RELIABLE:
                executive_insight = (
                    f"Cuenta {v.account_name}: {reliability.display_label}. "
                    f"Valor actual: {v.current_value:,.1f} COP MM. "
                    f"Materialidad: {mat.value}."
                )
            else:
                executive_insight = (
                    f"Cuenta {v.account_name}: {trend_analysis.trend_label}. "
                    f"Materialidad: {mat.value}. Impact score: {impact:.0f}/100."
                )

            # Confidence [Improvement #8]
            llm_hint = insight.llm_confidence_hint if insight else 0.8
            confidence, evidence_count, evidence_sources = compute_confidence(
                variation=v,
                reliability=reliability,
                materiality=mat,
                anomaly_detected=anomaly_detected,
                llm_hint=llm_hint,
            )
            if insight and insight.llm_evidence_sources:
                evidence_sources = evidence_sources + insight.llm_evidence_sources

            # Causality chain participation
            chain_narratives = chain_map.get(v.account_id, [])

            # BUG FIX: never propagate a suppressed variation_pct downstream.
            # Consumers (RevisorInteligente, ReportGenerator, frontend) would treat
            # it as a real number. Zeroed here; reliability_label carries the explanation.
            safe_variation_pct = 0.0 if reliability.suppress_pct else v.variation_pct

            results.append(AccountAnalysis(
                account_id=v.account_id,
                account_name=v.account_name,
                current_value=v.current_value,
                previous_value=v.previous_value,
                absolute_variation=v.absolute_variation,
                variation_pct=safe_variation_pct,
                materiality=mat,
                requires_niif_note=requires_niif,
                niif_note_references=niif_refs,
                risk_level=risk_lvl,
                possible_causes=possible_causes,
                executive_insight=executive_insight,
                anomaly_detected=anomaly_detected,
                # New fields
                variation_reliability=reliability.reliability.value,
                reliability_label=reliability.display_label,
                impact_score=impact,
                trend_label=trend_analysis.trend_label,
                confidence=confidence,
                evidence_count=evidence_count,
                evidence_sources=evidence_sources,
                causality_chain=chain_narratives,
                is_related_party=insight.is_related_party if insight else False,
                related_party_counterpart=insight.related_party_counterpart if insight else None,
                investment_signal=insight.investment_signal if insight else None,
            ))

        return results

    @staticmethod
    def _resolve_risk_level(
        insight: AccountLLMInsight | None,
        anomaly: AnomalyResult,
        materiality: MaterialityLevel,
    ) -> RiskLevel:
        # Deterministic baseline from hard evidence
        det_level = RiskLevel.LOW
        if anomaly.anomaly_detected:
            det_level = RiskLevel.HIGH if anomaly.severity == "HIGH" else RiskLevel.MEDIUM
        elif materiality == MaterialityLevel.HIGH:
            det_level = RiskLevel.MEDIUM

        if not (insight and insight.risk_level in _VALID_RISK):
            return det_level

        llm_level = RiskLevel(insight.risk_level)

        # Ceiling rule: LLM cannot assert HIGH without a deterministic anomaly,
        # and cannot assert MEDIUM without at least MEDIUM materiality.
        # This prevents the LLM from hallucinating risk on stable accounts.
        _RANK = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2}
        if _RANK[llm_level] > _RANK[det_level] + 1:
            # LLM jumped two levels above evidence — cap at det+1
            return RiskLevel.MEDIUM if det_level == RiskLevel.LOW else RiskLevel.HIGH
        return llm_level

    @staticmethod
    def _parse_health(health_str: str) -> FinancialHealth:
        return (
            FinancialHealth(health_str)
            if health_str in _VALID_HEALTH
            else FinancialHealth.STABLE
        )

    @staticmethod
    def _suggest_health_taxonomy(
        ratios_dict: dict,
        earnings_quality: dict,
        concentration: dict,
    ) -> FinancialHealth | None:
        """
        Improvement #9: Deterministic health taxonomy hint.
        Returns a finance-oriented FinancialHealth value if signals are strong
        enough to override a generic LLM response (STABLE/DECLINING/GROWING).
        Returns None when no dominant pattern is detected.
        """
        eq_score = earnings_quality.get("quality_score", 100)
        fair_value_pct = earnings_quality.get("fair_value_income_ratio", 0)
        conc_label = concentration.get("concentration_label", "LOW")

        # ratios_dict structure: {"totals": {...}, "ratios": {...}, "niif18": {...}}
        ratios = ratios_dict.get("ratios", {})
        deuda_patrimonio = ratios.get("deuda_patrimonio", 0) or 0
        razon_corriente = ratios.get("razon_corriente", 1) or 1

        # niif18.subtotals keys: resultado_operativo, resultado_financiamiento, resultado_neto
        niif18 = ratios_dict.get("niif18", {})
        subtotals = niif18.get("subtotals", {})
        resultado_financiamiento = subtotals.get("resultado_financiamiento", None)

        if fair_value_pct > 50 and eq_score < 50:
            return FinancialHealth.VALUATION_DRIVEN

        # Negative financing result signals cash stress from debt servicing
        if resultado_financiamiento is not None and resultado_financiamiento < -1.0:
            return FinancialHealth.CASH_STRESSED

        if deuda_patrimonio > 3.0:
            return FinancialHealth.LEVERAGED

        if conc_label in ("HIGH", "CRITICAL"):
            return FinancialHealth.CONCENTRATED

        if deuda_patrimonio > 5.0 or razon_corriente < 0.5:
            return FinancialHealth.SPECULATIVE

        if razon_corriente > 2.5 and deuda_patrimonio < 0.5:
            return FinancialHealth.LIQUID

        return None

    @staticmethod
    def _parse_reference_date(periods: list[str]) -> datetime:
        if not periods:
            return datetime.utcnow()
        try:
            parts = periods[0].split("-")
            year = int(parts[0])
            month = int(parts[1]) if len(parts) > 1 else 12
            return datetime(year, month, 1)
        except Exception:
            return datetime.utcnow()

    def _save_to_s3(self, result: AnalyzerOutput) -> None:
        try:
            job_save(result.job_id, FINANCIAL_ANALYZER, result.model_dump(mode="json"), s3_client=self._s3)
        except Exception as exc:
            logger.warning("s3_save_failed | job=%s error=%s", result.job_id, exc)
