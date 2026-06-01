# CreditIQ — Improvement Roadmap

_Derived from the end-to-end system flow analysis (2026-05-31). Read-only assessment; no code changed._

## Recommended Interventions (by priority)

| Priority | Area | Intervention |
|---|---|---|
| **P0** | Auth | Attach a Cognito (or Lambda) JWT authorizer to the `HttpApi`; make `extract_tenant_context` reject `authenticated_via != "jwt"` when `STAGE != dev`. Call `validate_requested_tenant()` in **every** GET/DELETE handler before any S3 read. |
| **P0** | Tenant keys | Move job artifacts under a tenant-scoped prefix (`jobs/{tenant_id}/...`) and add `s3:prefix`/resource conditions to the IAM policies so isolation isn't code-only. |
| **P0** | Durability | Make `report_generator` write the final deliverable to the lifecycle-exempt `reports/{tenant}/{company}/{YYYY}/{MM}/` prefix (both the `.docx` and the machine-readable `.md` that historical/duplicate logic expects). |
| **P0** | CORS | Replace `AllowOrigins: '*'` with the explicit CloudFront domain. |
| **P1** | Deploy parity | Add `RevisorInteligenteFunction` + a `RevisorInteligente` task state to the workflow, **or** explicitly descope Agent 5 and update CLAUDE.md. Decide one source of truth for the pipeline shape. |
| **P1** | Market subsystem | Either deploy the `market_*` Lambdas + routes + an EventBridge-scheduled refresher, or feature-flag the frontend Market/Dashboard pages off in production so they don't 404. |
| **P1** | UI deliverable | Wire `docx_url` into a download action in `JobResultPage`/`AnalysisPage` so the generated report is reachable. |
| **P1** | Drift control | Pick one execution model. Recommended: make `local_server.py` a thin translator over the *same* SFN definition (e.g., Step Functions Local) instead of a parallel 5-phase reimplementation. |
| **P2** | CI/CD | Add `master` to the workflow triggers (or rename branch); add a real `tests/` suite (even smoke tests for each `lambda_handler`) so the test gate passes and actually guards deploys. |
| **P2** | Observability | CloudWatch alarms on Lambda `Errors`/`Throttles` and SFN `ExecutionsFailed` → SNS; a per-stage dashboard; consider an SQS DLQ on async invokes (`agent_runner`, orchestrator background path). |
| **P2** | State integrity | Split `status.json` into append-only/segregated keys (pause token vs. status vs. progress), or guard writes, to remove last-writer-wins races. Raise/parameterize the pause heartbeat and surface "review expired" cleanly. |

## Phased Roadmap

### Phase 0 — Security & durability hotfix (before any real tenant data)
Deploy the API authorizer, add tenant checks to GET/DELETE handlers, tenant-scope `jobs/` keys, pin CORS, and redirect the report deliverable to the non-expiring `reports/` prefix. These are the items that make the difference between "demo" and "safe to handle a client's financials."

### Phase 1 — Close the deploy/code gap ✅ DONE (2026-05-31)
Reconcile the deployed pipeline with the code — deploy or descope Agent 5 and the Market subsystem, wire the `.docx` into the UI, and restore historical/duplicate features by populating `reports/`. Eliminate the silent-failure orchestrator fallback in production.

**Changes shipped:**
- `analysis_workflow.json`: Added `WaitForScorerReview` pause (matches local_server scoring_complete gate) + `RevisorInteligente` task state — deployed workflow now matches 5-agent local pipeline.
- `template.yaml`: Added `RevisorInteligenteFunction` Lambda + IAM; added `MarketDataFunction`, `MarketReadFunction` + API routes + EventBridge 5-min refresh schedule.
- `orchestrator/handler.py`: Silent SFN fallback (`StateMachineDoesNotExist`/`AccessDeniedException`) now only activates when `STAGE=dev`; production raises the error properly.
- `report_generator/handler.py`: After saving the `.docx`, writes `.md` snapshot to `reports/{tenant_id}/{slug}/{YYYY}/{MM}/report_{job_id}.md` — this restores `fetch_historical_reports()` and the duplicate-detection guard.
- `AnalysisPage.tsx`: Agent 4 "done" view now has a **Download Report (.docx)** button that fetches the presigned URL from `/analyses/{id}/report` and opens it.

**Requires AWS redeploy:** Run `sam deploy` to activate the new Lambdas, workflow states, and EventBridge schedule.

### Phase 2 — Operational maturity ✅ DONE (2026-05-31)
Unify local and cloud execution onto one workflow definition, fix CI triggers, add a minimal test suite so the gate is real, and stand up alarms/dashboards/DLQs. Then harden state writes and the analyst-review timeout.

**Changes shipped:**
- `.github/workflows/backend.yml` + `frontend.yml`: Added `master` to push/PR triggers and deploy-dev condition — CI gate now runs on the actual working branch.
- `tests/conftest.py` + `tests/test_smoke_handlers.py`: Real smoke test suite (17 tests) covering all lambda_handler entry points with mocked AWS, shared-model invariants, and progress/job-store helpers. The CI `pytest tests/` step now guards deploys with real coverage.
- `template.yaml`: Added `PauseHeartbeatSeconds` parameter (default 24 h, configurable), `AlertsEmail` parameter, `HasAlertsEmail` condition, `AgentRunnerDLQ` SQS queue (14-day retention), DLQ wired to `AgentRunnerFunction` + IAM `sqs:SendMessage`, `AlertsTopic` SNS + optional email subscription, CloudWatch alarms for Lambda Errors on all 5 agents, Lambda Throttles (aggregate), SFN `ExecutionsFailed`, DLQ depth — all routing to SNS. `CreditIQDashboard` with alarm status row, Lambda errors, SFN executions, Lambda p99 duration, DLQ depth panels.
- `step_functions/analysis_workflow.json`: All three pause states (`WaitForExtractorReview`, `WaitForAnalyzerReview`, `WaitForScorerReview`) now use `${PauseHeartbeatSeconds}` substitution and catch `States.HeartbeatTimeout` → new `ReviewExpired` Fail state (distinct from `WorkflowFailed`).
- `agents/pause/handler.py`: Task token split into a separate `pause_token` S3 artifact; `status.json` now contains only `{status}` — eliminates last-writer-wins race between status polls and token writes.
- `api/continue_analysis/handler.py`: Reads token from `pause_token` artifact (backward-compat fallback to `status_data`); clears token after `SendTaskSuccess`; added `scoring_complete` as a valid SFN resume state (loads `risk_scorer_response` as payload).
- `api/analysis_status/handler.py`: Trusts `scoring_complete` from S3 (was missing, causing pause after Agent 3 to always show "processing"); maps SFN `error == "ReviewExpired"` → `review_expired` status so the frontend can show a distinct message.
- `local_server.py`: Added per-job `threading.Lock` (`_status_write_locks`) wrapping every `_save_status` call — removes last-writer-wins race between concurrent agent threads.

**Requires AWS redeploy:** Run `sam deploy` with `--parameter-overrides AlertsEmail=your@email.com` (optional) to activate DLQ, alarms, and dashboard. Subscribe to the SNS topic confirmation email if provided.

### Phase 3 — Scale & cost
Once correct and observable, revisit Lambda memory/timeout tuning against X-Ray data, evaluate prompt-cost accounting (the `llms_calls_accounting.xlsx` suggests this is already being tracked), and consider provisioned concurrency only where cold-start latency on the review gates actually hurts UX.

---

**Cross-cutting theme:** the deployment artifacts (`template.yaml`, `analysis_workflow.json`) have become the least-maintained part of the system. Every P0/P1 item traces back to these lagging behind `src/` and `local_server.py`. Establishing one source of truth for the pipeline shape — and a CI gate that actually runs — would prevent the whole class of issues from recurring.
