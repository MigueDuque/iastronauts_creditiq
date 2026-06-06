"""
CreditIQ engine eval harness.

Runs each golden case through the real production deterministic code path and
diffs the resulting probes against a recorded snapshot. This replaces the
"run one analysis and eyeball the output" loop with a scored, repeatable
regression check that catches when an engine change silently moves another case.

Usage (from iastronauts_creditiq_back/):

    python -m tests.eval.runner                 # run all cases, print scorecard
    python -m tests.eval.runner --case NAME     # run one case
    python -m tests.eval.runner --update        # re-snapshot expected.json (review the diff!)
    python -m tests.eval.runner -v              # show every probe, not just failures

Exit code is non-zero if any probe regresses, so it can gate CI.

Adding a case:
    tests/eval/cases/<name>/
        meta.json      {"stage": "risk_scorer", "input": "input.json", "description": "..."}
        input.json     the stage's input model (AnalyzerOutput for risk_scorer)
    Then run with --update to record expected.json, and commit after reviewing it.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

# ── Make src/ importable whether run as module or script ────────────────────────
_BACK = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_BACK / "src"))

# Test-safe env so handler modules' module-level boto3 clients initialize.
for _k, _v in {
    "MAIN_BUCKET": "eval-bucket",
    "STAGE": "test",
    "AWS_REGION": "us-east-1",
    "AWS_ACCOUNT_ID": "000000000000",
    "LLM_PROVIDER": "anthropic_api",
    "ANTHROPIC_API_KEY": "sk-ant-eval",
}.items():
    os.environ.setdefault(_k, _v)

from . import probes  # noqa: E402

CASES_DIR = Path(__file__).resolve().parent / "cases"
FLOAT_EPS = 1e-6


# ── Stage runners ───────────────────────────────────────────────────────────────

def _run_risk_scorer(input_data: dict) -> dict[str, Any]:
    """Run the deterministic risk pipeline (no LLM, no S3) and return probes."""
    from shared.models import AnalyzerOutput
    from agents.risk_scorer.scoring import compute_risk

    payload = AnalyzerOutput.model_validate(input_data)
    comp = compute_risk(payload)
    return probes.risk_probes(comp)


def _run_scale_normalization(input_data: dict) -> dict[str, Any]:
    """Run normalize_scale (pure function, no LLM, no S3) and return canonical totals."""
    from shared.models import ExtractedAccount
    from agents.document_extractor.handler import normalize_scale

    raw_accounts = [ExtractedAccount.model_validate(a) for a in input_data.get("accounts", [])]
    detected_scale = input_data.get("detected_scale", "millions")
    normalized = normalize_scale(raw_accounts, detected_scale)

    asset_total = round(
        sum(a.current_value for a in normalized if a.category == "assets" and not a.is_total), 4
    )
    equity_total = round(
        sum(a.current_value for a in normalized if a.category == "equity" and not a.is_total), 4
    )
    return {
        "asset_total_cop_mm": asset_total,
        "equity_total_cop_mm": equity_total,
        "account_count": len(normalized),
        "detected_scale": detected_scale,
    }


def _run_comparative_basis(input_data: dict) -> dict[str, Any]:
    """Run resolve_comparative_basis (pure function) and return the resolved rules."""
    from agents.document_extractor.handler import resolve_comparative_basis

    periods = input_data.get("periods", [])
    basis = resolve_comparative_basis(periods)
    return {
        "balance_sheet_rule": (basis.get("balance_sheet") or {}).get("rule", ""),
        "balance_sheet_comparative": (basis.get("balance_sheet") or {}).get("comparative", ""),
        "income_statement_rule": (basis.get("income_statement") or {}).get("rule", ""),
        "income_statement_comparative": (basis.get("income_statement") or {}).get("comparative", ""),
        "cash_flow_rule": (basis.get("cash_flow") or {}).get("rule", ""),
        "current": (basis.get("balance_sheet") or {}).get("current", ""),
        "has_mismatch_warning": any(
            "mismatch_warning" in (v or {}) for v in basis.values()
        ),
    }


STAGE_RUNNERS = {
    "risk_scorer": _run_risk_scorer,
    "scale_normalization": _run_scale_normalization,
    "comparative_basis": _run_comparative_basis,
}


# ── Comparison ──────────────────────────────────────────────────────────────────

def _compare(name: str, expected: Any, actual: Any, tol: float | None) -> bool:
    if isinstance(expected, bool) or isinstance(actual, bool):
        return expected == actual
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        eps = tol if tol is not None else FLOAT_EPS
        return abs(float(expected) - float(actual)) <= eps
    return expected == actual


class CaseResult:
    def __init__(self, name: str):
        self.name = name
        self.rows: list[tuple[str, Any, Any, bool]] = []  # metric, expected, actual, ok
        self.error: str | None = None

    @property
    def failures(self) -> int:
        return sum(1 for _, _, _, ok in self.rows if not ok)

    @property
    def passed(self) -> bool:
        return self.error is None and self.failures == 0


def _run_case(case_dir: Path, update: bool) -> CaseResult:
    res = CaseResult(case_dir.name)
    try:
        meta = json.loads((case_dir / "meta.json").read_text(encoding="utf-8"))
        stage = meta["stage"]
        runner = STAGE_RUNNERS.get(stage)
        if runner is None:
            res.error = f"unknown stage '{stage}' (known: {', '.join(STAGE_RUNNERS)})"
            return res

        input_data = json.loads(
            (case_dir / meta.get("input", "input.json")).read_text(encoding="utf-8")
        )
        actual = runner(input_data)
    except Exception as exc:  # noqa: BLE001 — surface any failure as a case error
        res.error = f"{type(exc).__name__}: {exc}"
        return res

    expected_path = case_dir / "expected.json"

    if update:
        existing = {}
        if expected_path.exists():
            existing = json.loads(expected_path.read_text(encoding="utf-8"))
        snapshot = {
            "description": meta.get("description", ""),
            "metrics": actual,
            # Preserve any hand-tuned tolerances across re-snapshots.
            "tolerances": existing.get("tolerances", {}),
        }
        expected_path.write_text(
            json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        for k in sorted(actual):
            res.rows.append((k, actual[k], actual[k], True))
        return res

    if not expected_path.exists():
        res.error = "no expected.json — run with --update to snapshot a baseline"
        return res

    golden = json.loads(expected_path.read_text(encoding="utf-8"))
    expected_metrics = golden.get("metrics", {})
    tolerances = golden.get("tolerances", {})

    keys = sorted(set(expected_metrics) | set(actual))
    for k in keys:
        if k not in actual:
            res.rows.append((k, expected_metrics[k], "<MISSING>", False))
        elif k not in expected_metrics:
            res.rows.append((k, "<NEW>", actual[k], False))
        else:
            ok = _compare(k, expected_metrics[k], actual[k], tolerances.get(k))
            res.rows.append((k, expected_metrics[k], actual[k], ok))
    return res


# ── CLI / reporting ─────────────────────────────────────────────────────────────

def _print_case(res: CaseResult, verbose: bool, update: bool) -> None:
    status = "ERROR" if res.error else ("PASS" if res.passed else "FAIL")
    print(f"\n{'='*72}\n[{status}]  {res.name}")
    if res.error:
        print(f"  !! {res.error}")
        return
    for metric, expected, actual, ok in res.rows:
        if ok and not verbose:
            continue
        if ok:
            print(f"  ok  {metric:42} = {actual}")
        else:
            print(f"  XX  {metric:42} expected {expected!r}  got {actual!r}")
    if update:
        print(f"  ~ snapshotted {len(res.rows)} probes")
    elif res.passed:
        print(f"  {len(res.rows)} probes OK")
    else:
        print(f"  {res.failures}/{len(res.rows)} probes FAILED")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="CreditIQ engine eval harness")
    ap.add_argument("--case", help="run only this case (directory name)")
    ap.add_argument("--update", action="store_true", help="re-snapshot expected.json")
    ap.add_argument("-v", "--verbose", action="store_true", help="show all probes")
    args = ap.parse_args(argv)

    if not CASES_DIR.exists():
        print(f"No cases dir at {CASES_DIR}", file=sys.stderr)
        return 2

    case_dirs = sorted(d for d in CASES_DIR.iterdir() if d.is_dir() and (d / "meta.json").exists())
    if args.case:
        case_dirs = [d for d in case_dirs if d.name == args.case]
        if not case_dirs:
            print(f"Case '{args.case}' not found", file=sys.stderr)
            return 2
    if not case_dirs:
        print("No cases found. Add one under tests/eval/cases/.", file=sys.stderr)
        return 2

    results = [_run_case(d, args.update) for d in case_dirs]
    for res in results:
        _print_case(res, args.verbose, args.update)

    total = len(results)
    errored = sum(1 for r in results if r.error)
    failed = sum(1 for r in results if not r.passed and not r.error)
    passed = total - errored - failed

    print(f"\n{'='*72}")
    if args.update:
        print(f"Snapshotted {total} case(s).")
        return 0
    print(f"SUMMARY  {passed} passed, {failed} failed, {errored} errored  ({total} total)")
    return 0 if (failed == 0 and errored == 0) else 1


if __name__ == "__main__":
    raise SystemExit(main())
