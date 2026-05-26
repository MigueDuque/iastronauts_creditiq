"""
service.py

Orchestrates the complete FinancialAnalyzer pipeline.

Flow (Math First — LLM Second):
  1. Enrich previous_value from S3 historical reports
  2. Per-account variation calculation          ← ratio_engine
  3. Global financial totals + ratios           ← ratio_engine
  4. Materiality threshold                      ← materiality_engine
  5. Per-account materiality classification     ← materiality_engine
  6. Per-account trend detection                ← trend_engine
  7. Per-account anomaly detection              ← anomaly_detector
  8. Structural balance-sheet anomaly check     ← anomaly_detector
  9. LLM qualitative reasoning                  ← llm_reasoning
 10. Merge deterministic + LLM → AccountAnalysis list
 11. Build + persist AnalyzerOutput
"""

import json
import logging
import os
from datetime import datetime

import boto3

from shared.llm_provider import LLMProvider
from shared.models import ExtractorOutput, AnalyzerOutput, FinancialHealth, AccountAnalysis
from shared.models.base import MaterialityLevel, RiskLevel
from shared.s3_report_store import fetch_historical_reports, slugify
from shared.s3_instructions import load_text as load_instruction

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
)
from .trend_engine import Trend, detect_trend
from .anomaly_detector import (
    AnomalyResult,
    detect_account_anomaly,
    detect_structural_anomalies,
)
from .llm_reasoning import LLMAnalysisResult, AccountLLMInsight, run_llm_analysis
from .niif18_engine import (
    NIIF18ComplianceResult,
    check_niif18_compliance,
    niif18_to_dict,
)

logger = logging.getLogger("financial_analyzer.service")

BUCKET = os.environ.get("MAIN_BUCKET", "")

_VALID_HEALTH: set[str] = {h.value for h in FinancialHealth}
_VALID_RISK: set[str] = {r.value for r in RiskLevel}

# S3 key for the NIIF reference document used in LLM reasoning
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

        Args:
            payload: Validated ExtractorOutput from the DocumentExtractor agent.
            lambda_context: AWS Lambda context (used for timeout checks if needed).

        Returns:
            AnalyzerOutput ready to be passed to the RiskScorer agent.
        """
        # ── Step 1: Enrich previous_value from S3 history ──────────────────
        enriched_accounts = self._enrich_with_history(payload)

        # ── Step 2: Per-account variation ──────────────────────────────────
        variations: list[AccountVariation] = [
            calculate_account_variation(acc) for acc in enriched_accounts
        ]

        # ── Step 3: Global financial totals + ratios ────────────────────────
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

        # ── Step 4: Materiality threshold ──────────────────────────────────
        threshold: float = determine_threshold(totals)

        # ── Step 5: Per-account materiality ────────────────────────────────
        materialities: dict[str, MaterialityLevel] = {
            v.account_id: classify_materiality(v, threshold) for v in variations
        }

        # ── Step 6: Per-account trend ───────────────────────────────────────
        trends: dict[str, Trend] = {
            v.account_id: detect_trend(v) for v in variations
        }

        # ── Step 7: Per-account anomaly detection ───────────────────────────
        anomalies: dict[str, AnomalyResult] = {
            v.account_id: detect_account_anomaly(v, threshold, trends[v.account_id])
            for v in variations
        }
        anomaly_count = sum(1 for a in anomalies.values() if a.anomaly_detected)

        # ── Step 8: Structural balance-sheet anomalies ──────────────────────
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

        # ── Step 9a: NIIF 18 subtotals (deterministic) ─────────────────────
        niif18_subtotals: NIIF18Subtotals = calculate_niif18_subtotals(
            enriched_accounts,
            depreciation_amortization=totals.depreciation_amortization,
        )

        # ── Step 9b: Total Comprehensive Income ────────────────────────────
        tci_dict: dict = calculate_total_comprehensive_income(
            resultado_neto=niif18_subtotals.resultado_neto,
            accounts=enriched_accounts,
        )

        # ── Step 9c: NIIF 18 compliance flags ──────────────────────────────
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

        # ── Step 9d: Load NIIF reference from S3 ───────────────────────────
        niif_reference_text = load_instruction(
            _NIIF_REFERENCE_KEY,
            fallback="",
        )

        # Inject NIIF 18 data into ratios_dict so the LLM has full context
        ratios_dict["niif18"] = niif18_section

        logger.info(
            "pre_llm | job=%s threshold=%.2f high_mat=%d anomalies=%d structural=%d niif_ref_chars=%d",
            payload.job_id, threshold, len(high_materiality_accounts),
            anomaly_count, len(structural_issues), len(niif_reference_text),
        )

        # ── Step 9e: LLM qualitative reasoning ─────────────────────────────
        llm_result: LLMAnalysisResult = run_llm_analysis(
            company_name=payload.company_name,
            periods=payload.periods,
            business_context_snippet=payload.business_context.raw_context,
            ratios_dict=ratios_dict,
            threshold=threshold,
            variations=variations,
            materialities=materialities,
            llm=self._llm,
            tenant_id=payload.tenant_id,
            job_id=payload.job_id,
            niif_reference_text=niif_reference_text,
        )

        # ── Step 10: Merge math + LLM → AccountAnalysis ─────────────────────
        analysis_results = self._merge_results(
            variations=variations,
            materialities=materialities,
            trends=trends,
            anomalies=anomalies,
            llm_result=llm_result,
        )

        # Collect NIIF notes: LLM-detected + per-account inferred + structural
        niif_notes_required: set[str] = set(llm_result.niif_notes_required)
        for ar in analysis_results:
            if ar.requires_niif_note:
                niif_notes_required.update(ar.niif_note_references)

        overall_health = self._parse_health(llm_result.overall_financial_health)

        # ── Step 11: Build output + save to S3 ─────────────────────────────
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
        )

        self._save_to_s3(result)

        logger.info(
            "analysis_complete | job=%s health=%s accounts=%d high_mat=%d "
            "niif_notes=%d anomalies=%d",
            result.job_id, result.overall_financial_health.value,
            len(result.analysis_results), len(result.high_materiality_accounts),
            len(result.niif_notes_required), anomaly_count,
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
        trends: dict[str, Trend],
        anomalies: dict[str, AnomalyResult],
        llm_result: LLMAnalysisResult,
    ) -> list[AccountAnalysis]:
        """
        Merge deterministic math outputs with LLM qualitative insights
        into the final AccountAnalysis list.

        Priority rules:
        - risk_level:        LLM (if valid enum) → anomaly severity → materiality fallback
        - anomaly_detected:  detector flag OR LLM anomaly_override
        - possible_causes:   LLM → anomaly description fallback → trend-based default
        - executive_insight: LLM → computed fallback
        - niif_note_references: union(inferred by keyword, LLM-suggested)
        - requires_niif_note:  HIGH materiality always → True; else LLM decision
        """
        results: list[AccountAnalysis] = []

        for v in variations:
            mat = materialities[v.account_id]
            anomaly = anomalies[v.account_id]
            trend = trends[v.account_id]
            insight: AccountLLMInsight | None = llm_result.account_insights.get(v.account_id)

            # NIIF references: keyword inference + LLM suggestions
            inferred_niif = infer_niif_references(v.account_name, v.category)
            llm_niif = insight.niif_note_references if insight else []
            niif_refs = sorted(set(inferred_niif) | set(llm_niif))

            # requires_niif_note
            requires_niif = (mat == MaterialityLevel.HIGH) or (
                insight.requires_niif_note if insight else False
            )

            # risk_level
            risk_lvl = self._resolve_risk_level(insight, anomaly, mat)

            # anomaly_detected
            anomaly_detected = anomaly.anomaly_detected or (
                insight.anomaly_override if insight else False
            )

            # possible_causes
            if insight and insight.possible_causes:
                possible_causes = insight.possible_causes
            elif anomaly.anomaly_detected:
                possible_causes = [anomaly.description]
            else:
                possible_causes = [
                    f"Tendencia {trend.value} con variación de "
                    f"{v.variation_pct:+.1f}% ({v.absolute_variation:+,.1f} COP MM)."
                ]

            # executive_insight
            if insight and insight.executive_insight:
                executive_insight = insight.executive_insight
            else:
                executive_insight = (
                    f"Cuenta {v.account_name}: variación {trend.value} de "
                    f"{v.variation_pct:+.1f}% ({v.absolute_variation:+,.1f} COP MM). "
                    f"Materialidad: {mat.value}."
                )

            results.append(AccountAnalysis(
                account_id=v.account_id,
                account_name=v.account_name,
                current_value=v.current_value,
                previous_value=v.previous_value,
                absolute_variation=v.absolute_variation,
                variation_pct=v.variation_pct,
                materiality=mat,
                requires_niif_note=requires_niif,
                niif_note_references=niif_refs,
                risk_level=risk_lvl,
                possible_causes=possible_causes,
                executive_insight=executive_insight,
                anomaly_detected=anomaly_detected,
            ))

        return results

    @staticmethod
    def _resolve_risk_level(
        insight: AccountLLMInsight | None,
        anomaly: AnomalyResult,
        materiality: MaterialityLevel,
    ) -> RiskLevel:
        """
        Determine risk level using a priority chain:
        1. LLM suggestion (if it's a valid RiskLevel value)
        2. Anomaly severity (HIGH anomaly → HIGH risk; MEDIUM → MEDIUM)
        3. Materiality-based fallback
        """
        if insight and insight.risk_level in _VALID_RISK:
            return RiskLevel(insight.risk_level)
        if anomaly.anomaly_detected:
            return RiskLevel.HIGH if anomaly.severity == "HIGH" else RiskLevel.MEDIUM
        if materiality == MaterialityLevel.HIGH:
            return RiskLevel.MEDIUM
        return RiskLevel.LOW

    @staticmethod
    def _parse_health(health_str: str) -> FinancialHealth:
        return (
            FinancialHealth(health_str)
            if health_str in _VALID_HEALTH
            else FinancialHealth.STABLE
        )

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
            self._s3.put_object(
                Bucket=BUCKET,
                Key=f"jobs/{result.job_id}/analyzer_output.json",
                Body=json.dumps(result.model_dump(mode="json"), ensure_ascii=False),
                ContentType="application/json",
            )
            logger.info("s3_saved | job=%s", result.job_id)
        except Exception as exc:
            logger.warning("s3_save_failed | job=%s error=%s", result.job_id, exc)
