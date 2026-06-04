"""
Regression tests for the 2026-06-02 remediation (improving_creditiq.md).

Covers, without any AWS/LLM calls:
  §1/§6.2  every NIIF standard the pipeline can emit has an uploaded note file
  §5       new-account absolute_variation == current_value; suppressed pct → None in trace
  §2       Revisor cross-reference matches by standard, not note_id
  §4       Revisor math checks are null/reliability-aware
  §4.5     equity > assets is flagged
  §6.4     a single check_id cannot floor the score (penalty cap per check_id)
"""
import os

import pytest

from shared.models.base import MaterialityLevel, RiskLevel, FinancialHealth
from shared.models.analyzer import AccountAnalysis
from shared.models.report import FinalReportOutput, NiifNoteDraft
from shared.models.revisor import ValidationFlag, ValidationCategory, ValidationSeverity

from agents.financial_analyzer.materiality_engine import _NIIF_MAP, build_account_trace
from agents.financial_analyzer.ratio_engine import (
    AccountVariation, FinancialTotals, calculate_account_variation,
)
from agents.financial_analyzer.variation_reliability import VariationReliability
from agents.revisor_inteligente import handler as revisor

from shared import niif_notes


# ── §1 / §6.2 — note-file coverage ──────────────────────────────────────────────

def _all_standards() -> set[str]:
    refs: set[str] = {"NIIF 18"}
    for cat in _NIIF_MAP.values():
        for standards in cat.values():
            refs.update(standards)
    # Cross-category rules add these explicitly.
    refs.update({"NIC 24", "NIC 12", "NIC 7", "NIC 1"})
    return refs


def test_every_niif_standard_has_a_local_note_file():
    local_dir = niif_notes._LOCAL_DIR
    missing = []
    for standard in sorted(_all_standards()):
        path = os.path.join(local_dir, f"{niif_notes.slug_for_standard(standard)}.md")
        if not os.path.isfile(path):
            missing.append((standard, path))
    assert not missing, f"NIIF note files missing for: {missing}"


def test_slug_rule():
    assert niif_notes.slug_for_standard("NIIF 9") == "niif_9"
    assert niif_notes.slug_for_standard("NIC 12") == "nic_12"
    assert niif_notes.slug_for_standard("  NIIF 18 ") == "niif_18"


# ── §5 — engine no-baseline behaviour ───────────────────────────────────────────

class _FakeAccount:
    """Minimal stand-in for ExtractedAccount fields read by calculate_account_variation."""
    def __init__(self, current, previous):
        self.account_id = "a1"
        self.normalized_account_name = "Inversión nueva"
        self.category = "assets"
        self.source_file = "f.xlsx"
        self.confidence_score = 1.0
        self.current_value = current
        self.previous_value = previous
        self.source_sheet = None
        self.is_total = False


def test_new_account_absolute_variation_is_current_value():
    v = calculate_account_variation(_FakeAccount(15617.0, None))
    assert v.has_previous_value is False
    assert v.absolute_variation == pytest.approx(15617.0)  # not 0.0


def test_zero_baseline_absolute_variation_is_current_value():
    v = calculate_account_variation(_FakeAccount(100.0, 0.0))
    assert v.has_previous_value is True
    assert v.absolute_variation == pytest.approx(100.0)


def test_trace_pct_is_none_when_suppressed():
    v = AccountVariation(
        account_id="a1", account_name="x", category="assets", source_file="f",
        confidence_score=1.0, current_value=100.0, previous_value=0.0,
        has_previous_value=False, absolute_variation=100.0, variation_pct=0.0,
    )
    trace = build_account_trace(
        variation=v, threshold=10.0, totals=FinancialTotals(total_assets=1000.0),
        materiality=MaterialityLevel.HIGH, impact_score=50.0,
        reliability=VariationReliability.NEW_ACCOUNT, anomaly_detected=False,
    )
    assert trace["variation_pct"]["result"] is None


# ── Revisor test helpers ────────────────────────────────────────────────────────

def _account(**kw) -> AccountAnalysis:
    base = dict(
        account_id="a1", account_name="Cuenta", current_value=100.0, previous_value=80.0,
        has_previous_value=True, absolute_variation=20.0, variation_pct=25.0,
        materiality=MaterialityLevel.LOW, requires_niif_note=False, niif_note_references=[],
        risk_level=RiskLevel.LOW, possible_causes=[], executive_insight="", anomaly_detected=False,
    )
    base.update(kw)
    return AccountAnalysis(**base)


def _report(accounts, notes=None, **kw) -> FinalReportOutput:
    base = dict(
        job_id="j", tenant_id="t", company_name="Acme", periods=["2025-06", "2024-06"],
        validation_score=90, overall_risk_score=RiskLevel.LOW,
        overall_financial_health=FinancialHealth.STABLE, analysis_results=accounts,
        niif_note_drafts=notes or [],
    )
    base.update(kw)
    return FinalReportOutput(**base)


# ── §2 — cross-reference matches by standard ────────────────────────────────────

def test_cross_reference_matches_by_standard_no_false_positive():
    acc = _account(requires_niif_note=True, niif_note_references=["NIIF 9"])
    note = NiifNoteDraft(
        note_id="note-001", niif_reference="NIIF 9", title="t",
        content="c", affected_account_ids=["a1"], requires_disclosure=True,
    )
    flags = revisor._check_cross_references(_report([acc], [note]))
    assert not [f for f in flags if f.check_id == "3.1"]
    assert not [f for f in flags if f.check_id == "3.4"]


def test_cross_reference_flags_missing_standard():
    acc = _account(requires_niif_note=True, niif_note_references=["NIIF 9"])
    flags = revisor._check_cross_references(_report([acc], []))
    assert [f for f in flags if f.check_id == "3.1"]


# ── §4 — math checks null/reliability-aware ─────────────────────────────────────

def test_new_account_no_math_error():
    acc = _account(
        has_previous_value=False, previous_value=0.0, current_value=15617.0,
        absolute_variation=15617.0, variation_pct=None,
    )
    flags = revisor._check_mathematical(_report([acc]))
    assert not [f for f in flags if f.severity == ValidationSeverity.ERROR]


def test_suppressed_pct_none_no_2_2_error():
    acc = _account(variation_pct=None, previous_value=80.0, absolute_variation=20.0)
    flags = revisor._check_mathematical(_report([acc]))
    assert not [f for f in flags if f.check_id == "2.2"]


# ── §4.5 — equity must not exceed assets ────────────────────────────────────────

def test_equity_exceeds_assets_flagged():
    report = _report(
        [_account()],
        financial_ratios={"totals": {"total_assets": 39813.0, "total_equity": 120522.0}},
    )
    flags = revisor._check_mathematical(report)
    assert [f for f in flags if f.check_id == "2.5" and f.severity == ValidationSeverity.ERROR]


def test_equity_below_assets_ok():
    report = _report(
        [_account()],
        financial_ratios={"totals": {"total_assets": 39813.0, "total_equity": 28034.0}},
    )
    flags = revisor._check_mathematical(report)
    assert not [f for f in flags if f.check_id == "2.5"]


# ── §6.4 — penalty cap per check_id ─────────────────────────────────────────────

def test_single_check_id_cannot_floor_score():
    flags = [
        ValidationFlag(
            check_id="3.1", category=ValidationCategory.CROSS_REFERENCE,
            severity=ValidationSeverity.ERROR, message=f"err {i}",
        )
        for i in range(50)
    ]
    score = revisor._compute_adjusted_score(100, flags)
    # 50 ERRORs of one check_id would be -500 uncapped; capped at -30.
    assert score == 70


def test_info_findings_do_not_affect_score():
    flags = [
        ValidationFlag(
            check_id="4.3", category=ValidationCategory.BUSINESS_LOGIC,
            severity=ValidationSeverity.INFO, message="info",
        )
        for _ in range(10)
    ]
    assert revisor._compute_adjusted_score(88, flags) == 88
