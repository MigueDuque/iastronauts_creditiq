"""
agent_runner — deployed equivalent of local_server.py's per-agent runners
(_run_agent2 / _run_agent3 / _run_agent4).

Re-runs a SINGLE pipeline agent *outside* Step Functions by directly invoking
that agent's Lambda with the upstream artifact as input, then recording the
resulting status in jobs/{date}/{id}/status.json with source="reanalyze".

Backs:
  POST /analyses/{id}/reanalyze         → re-run Agent 2 (FinancialAnalyzer)
  POST /analyses/{id}/reanalyze-scorer  → re-run Agent 3 (RiskScorer)
  POST /analyses/{id}/reanalyze-report  → re-run Agent 4 (ReportGenerator)
and is also invoked by continue_analysis as a fallback when a job is being driven
manually (no Step Functions task token left to resume).

Two modes, distinguished by event shape:
  • API mode  — invoked by API Gateway. Writes a "processing" status, fires an
                async self-invoke to do the slow work, returns 202 immediately so
                we stay under API Gateway's 30s integration timeout.
  • Worker mode — invoked asynchronously with {"analysis_id","agent"}. Synchronously
                invokes the target agent Lambda (minutes of work) and writes the
                terminal status.

Why the status override (source="reanalyze")? A re-run happens after the Step
Functions execution already finished, so analysis_status would otherwise keep
reporting the SFN terminal state ("completed"). The override makes status polling
reflect the manual re-run. See analysis_status.handler for the read side.
"""
import json
import logging
import os
import time

import boto3
from botocore.config import Config

from shared.job_store import (
    load as job_load,
    save as job_save,
    EXTRACTOR,
    FINANCIAL_ANALYZER,
    RISK_SCORER,
    REPORT_GENERATOR,
    STATUS,
)
from agents.pause.handler import PAUSE_TOKEN

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# agent number → (upstream artifact fed in, env var holding the target Lambda name,
#                 terminal status to record, response artifact to persist or None
#                 when the agent already persists its own output).
# Agents 2, 3 and 5 save their own *_response.json internally; Agent 4 does not.
# Agent 4 pauses at report_complete (mirrors the SFN report-review gate) so the
# intelligent reviewer (Agent 5) still runs afterwards on the manual path.
_AGENTS: dict[int, tuple[str, str, str, str | None]] = {
    2: (EXTRACTOR,          "ANALYZER_FUNCTION_NAME",         "analysis_complete", None),
    3: (FINANCIAL_ANALYZER, "RISK_SCORER_FUNCTION_NAME",      "scoring_complete",  None),
    4: (RISK_SCORER,        "REPORT_GENERATOR_FUNCTION_NAME", "report_complete",   REPORT_GENERATOR),
    5: (REPORT_GENERATOR,   "REVISOR_FUNCTION_NAME",          "completed",         None),
}


# The worker synchronously invokes a target agent (RequestResponse) that can run
# for minutes. botocore's DEFAULT read_timeout is 60s, which fires long before the
# Analyzer's 300s Lambda budget — surfacing as
#   "Read timeout on endpoint URL: .../creditiq-analyzer-dev/invocations".
# Set read_timeout above the longest agent timeout (Analyzer = 300s) and disable
# retries so a slow-but-succeeding agent is never invoked twice.
_INVOKE_CONFIG = Config(
    connect_timeout=10,
    read_timeout=320,
    retries={"max_attempts": 1, "mode": "standard"},
)


def _lambda():
    return boto3.client("lambda", config=_INVOKE_CONFIG)


def _sfn_client():
    kwargs = {}
    if url := os.environ.get("SFN_ENDPOINT_URL"):
        kwargs["endpoint_url"] = url
    return boto3.client("stepfunctions", **kwargs)


def _abandon_sfn_execution(job_id: str) -> None:
    """Tidy up the Step Functions execution before driving the job manually.

    A manual re-run (/reanalyze*) runs the agent OUTSIDE Step Functions, but the
    execution is usually still parked at a waitForTaskToken gate holding a live
    token. If left alone:
      • a later /continue would read that stale token and resume the WRONG gate
        (re-running the already-rerun agent instead of advancing), and
      • the execution would hang until the 24 h review heartbeat expires, then fail
        with ReviewExpired — a false positive on the ExecutionsFailed alarm.

    So we clear the parked token artifact (forcing /continue onto the manual
    fallback path) and abort the execution. Both steps are best-effort and never
    raise — a missing token or already-terminal execution is the normal case when
    continue_analysis falls back here with no token to begin with.
    """
    try:
        token_data = job_load(job_id, PAUSE_TOKEN)
        if token_data.get("task_token"):
            job_save(job_id, PAUSE_TOKEN, {})
    except Exception:
        pass  # no token artifact (continue fallback path) — nothing to clear

    account_id = os.environ.get("AWS_ACCOUNT_ID", "")
    if not account_id:
        return
    region = os.environ.get("AWS_REGION", "us-east-1")
    stage = os.environ.get("STAGE", "dev")
    execution_arn = (
        f"arn:aws:states:{region}:{account_id}:"
        f"execution:creditiq-analysis-workflow-{stage}:{job_id}"
    )
    try:
        _sfn_client().stop_execution(
            executionArn=execution_arn,
            cause="Job switched to manual agent re-run via agent_runner",
        )
    except Exception:
        pass  # execution may already be terminal — fine


def _save_status(job_id: str, status: str, error: str | None = None) -> None:
    body: dict = {"status": status, "source": "reanalyze"}
    # Stamp a heartbeat on every non-terminal status so analysis_status can detect a
    # worker that died hard (OOM / Lambda timeout) — those crashes skip the except
    # below, leaving "processing" written forever. A stale heartbeat surfaces as
    # "failed" instead of an eternal spinner. See analysis_status._is_stale_processing.
    if status in ("processing", "pending"):
        body["updated_at"] = int(time.time())
    if error:
        body["error"] = error
    job_save(job_id, STATUS, body)


def _run_agent(analysis_id: str, agent: int, llm_model: str | None = None) -> dict:
    """Worker mode: synchronously invoke the target agent Lambda, record status."""
    # Committing to the manual path: clear any parked SFN token and abort the
    # abandoned execution so a later /continue doesn't resume the wrong gate.
    _abandon_sfn_execution(analysis_id)
    # Everything that can raise lives inside the try so any failure — including an
    # unknown agent or a missing *_FUNCTION_NAME env var — writes a terminal "failed"
    # status. Previously the _AGENTS lookup and os.environ[fn_env] sat before the try,
    # so a config gap crashed the worker silently and the job hung on "processing".
    try:
        if agent not in _AGENTS:
            raise ValueError(f"unknown agent {agent}")
        upstream, fn_env, done_status, save_as = _AGENTS[agent]
        target = os.environ[fn_env]
        payload = job_load(analysis_id, upstream)
        # Agent 4 (ReportGenerator) reads llm_model off its event to pick the LLM.
        # Inject the analyst-chosen model so /reanalyze-report honours the dialog.
        if agent == 4 and llm_model:
            payload = dict(payload)
            payload["llm_model"] = llm_model
        resp = _lambda().invoke(
            FunctionName=target,
            InvocationType="RequestResponse",
            Payload=json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"),
        )
        raw = resp["Payload"].read()
        if resp.get("FunctionError"):
            raise RuntimeError(f"{target} returned {resp['FunctionError']}: {raw[:500]!r}")
        result = json.loads(raw) if raw else {}
        if save_as:  # Agent 4 stub doesn't persist itself — store its output here.
            job_save(analysis_id, save_as, result)
        _save_status(analysis_id, done_status)
        logger.info("agent_runner | job=%s agent=%d -> %s", analysis_id, agent, done_status)
    except Exception as exc:
        logger.error("agent_runner | job=%s agent=%d failed: %s", analysis_id, agent, exc)
        _save_status(analysis_id, "failed", str(exc))
    return {"analysis_id": analysis_id, "agent": agent}


def _dispatch_async(analysis_id: str, agent: int, llm_model: str | None = None) -> dict:
    """API mode: mark processing synchronously, fire the worker, return 202."""
    # Write processing BEFORE returning so the frontend's first status poll never
    # races against the stale SFN terminal state.
    _save_status(analysis_id, "processing")
    worker_payload: dict = {"analysis_id": analysis_id, "agent": agent}
    if agent == 4 and llm_model:
        worker_payload["llm_model"] = llm_model
    _lambda().invoke(
        FunctionName=os.environ["AWS_LAMBDA_FUNCTION_NAME"],
        InvocationType="Event",
        Payload=json.dumps(worker_payload).encode("utf-8"),
    )
    return _response(202, {"analysis_id": analysis_id, "status": "processing"})


def _agent_for_path(path: str) -> int | None:
    # Check the more specific suffixes first: "…/reanalyze-report".endswith("/reanalyze")
    # is False, but ordering specific-first keeps the intent obvious.
    p = (path or "").rstrip("/")
    if p.endswith("/reanalyze-report"):
        return 4
    if p.endswith("/reanalyze-scorer"):
        return 3
    if p.endswith("/reanalyze"):
        return 2
    return None


def lambda_handler(event: dict, context) -> dict:
    # Worker mode: plain async payload, no HTTP envelope.
    if "agent" in event and "requestContext" not in event:
        return _run_agent(event["analysis_id"], int(event["agent"]), event.get("llm_model"))

    # API mode.
    analysis_id = (event.get("pathParameters") or {}).get("analysis_id")
    path = (
        event.get("rawPath")
        or event.get("requestContext", {}).get("http", {}).get("path", "")
    )
    agent = _agent_for_path(path)
    if not analysis_id or not agent:
        return _response(400, {"error": "Ruta inválida o analysis_id faltante"})

    # Agent 4 (ReportGenerator) accepts an optional analyst-chosen llm_model in the body.
    llm_model: str | None = None
    if agent == 4:
        try:
            llm_model = json.loads(event.get("body") or "{}").get("llm_model") or None
        except Exception:
            llm_model = None
    return _dispatch_async(analysis_id, agent, llm_model=llm_model)


def _response(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body, ensure_ascii=False, default=str),
    }
