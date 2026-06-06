# CreditIQ Engine Eval Harness

Turns "run one analysis and eyeball the output" into a **scored, repeatable
regression check**. Every golden case runs through the real production
deterministic code path; the resulting metrics ("probes") are diffed against a
recorded snapshot. When you tune an engine, this tells you immediately whether
you also moved a *different* case you didn't mean to touch.

No LLM calls and no AWS access — runs in <1s, deterministic, safe for CI.

## Run it

```bash
# from iastronauts_creditiq_back/
python -m tests.eval.runner            # run all cases, scorecard, exit 1 on regression
python -m tests.eval.runner -v         # show every probe, not just failures
python -m tests.eval.runner --case btg_inmobiliario_fund_2025q2
pytest tests/eval/                     # same checks, as pytest (CI)
```

## The workflow you'll actually use

1. Change an engine.
2. `python -m tests.eval.runner` — see exactly which probes moved, in which cases.
3. If the change is **correct**, re-snapshot and review the diff before committing:
   ```bash
   python -m tests.eval.runner --update
   git diff tests/eval/cases/*/expected.json   # this diff IS the review
   ```
4. If a probe moved that you did **not** intend → that's the regression the harness exists to catch.

The golden `expected.json` is "current accepted behavior." Correctness lives in
the review of the snapshot diff, not in the harness asserting absolute truth.

## Adding a case

```
tests/eval/cases/<name>/
  meta.json     {"stage": "risk_scorer", "input": "input.json", "description": "..."}
  input.json    the stage's input model (AnalyzerOutput for the risk_scorer stage)
```

Then `python -m tests.eval.runner --case <name> --update` and commit after reviewing
`expected.json`.

Good cases to add next (one archetype each, so a fix that helps one can't silently
break another): leveraged corporate, cash-stressed entity, multi-sheet portfolio,
a clean low-risk baseline.

To capture a real input, save a stage's input from a live run — e.g. the
`jobs/{job_id}/analyzer_output.json` artifact in S3 is exactly the `risk_scorer`
input.

## Stages

| stage | input model | code path exercised |
|-------|-------------|---------------------|
| `risk_scorer` | `AnalyzerOutput` | `agents.risk_scorer.scoring.compute_risk` — 5 deterministic engines + composite + 3 report-facing risk categories |

Adding a new stage = add a runner fn to `STAGE_RUNNERS` in `runner.py` and a probe
extractor in `probes.py`. Next planned stage: `financial_analyzer` (Agent 2
engines), which needs the LLM sub-agents mocked since its deterministic engines run
before the LLM call.

## Files

- `runner.py` — case discovery, stage execution, diff, scorecard, `--update`.
- `probes.py` — flattens a stage's rich output into named scalar probes.
- `test_eval_cases.py` — pytest wrapper (one parametrized test per case).
- `cases/<name>/` — `meta.json`, `input.json`, `expected.json` (the snapshot).
