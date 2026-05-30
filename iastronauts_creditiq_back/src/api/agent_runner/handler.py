"""
agent_runner — deployed equivalent of local_server.py's per-agent runners
(_run_agent2 / _run_agent3 / _run_agent4).

Re-runs a SINGLE pipeline agent *outside* Step Functions by directly invoking
that agent's Lambda with the upstream artifact as input, then recording the
resulting status in jobs/{date}/{id}/status.json with source="reanalyze".

Backs:
  POST /analyses/{id}/reanalyze         → re-run Agent 2 (FinancialAnalyzer)
  POST /analyses/{id}/reanalyze-scorer  → re-run Agent 3 (RiskScorer)
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

import boto3

from shared.job_store import (
    load as job_load,
    save as job_save,
    EXTRACTOR,
    FINANCIAL_ANALYZER,
    RISK_SCORER,
    REPORT_GENERATOR,
    STATUS,
)

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# agent number → (upstream artifact fed in, env var holding the target Lambda name,
#                 terminal status to record, response artifact to persist or None
#                 when the agent already persists its own output).
# Agents 2 and 3 save their own *_response.json internally; Agent 4 (stub) does not.
_AGENTS: dict[int, tuple[str, str, str, str | None]] = {
    2: (EXTRACTOR,          "ANALYZER_FUNCTION_NAME",         "analysis_complete", None),
    3: (FINANCIAL_ANALYZER, "RISK_SCORER_FUNCTION_NAME",      "scoring_complete",  None),
    4: (RISK_SCORER,        "REPORT_GENERATOR_FUNCTION_NAME", "completed",         REPORT_GENERATOR),
}


def _lambda():
    return boto3.client("lambda")


def _save_status(job_id: str, status: str, error: str | None = None) -> None:
    body: dict = {"status": status, "source": "reanalyze"}
    if error:
        body["error"] = error
    job_save(job_id, STATUS, body)


def _run_agent(analysis_id: str, agent: int) -> dict:
    """Worker mode: synchronously invoke the target agent Lambda, record status."""
    if agent not in _AGENTS:
        logger.error("agent_runner | job=%s unknown agent=%s", analysis_id, agent)
        return {"analysis_id": analysis_id, "agent": agent, "error": "unknown agent"}

    upstream, fn_env, done_status, save_as = _AGENTS[agent]
    target = os.environ[fn_env]
    try:
        payload = job_load(analysis_id, upstream)
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


def _dispatch_async(analysis_id: str, agent: int) -> dict:
    """API mode: mark processing synchronously, fire the worker, return 202."""
    # Write processing BEFORE returning so the frontend's first status poll never
    # races against the stale SFN terminal state.
    _save_status(analysis_id, "processing")
    _lambda().invoke(
        FunctionName=os.environ["AWS_LAMBDA_FUNCTION_NAME"],
        InvocationType="Event",
        Payload=json.dumps({"analysis_id": analysis_id, "agent": agent}).encode("utf-8"),
    )
    return _response(202, {"analysis_id": analysis_id, "status": "processing"})


def _agent_for_path(path: str) -> int | None:
    p = (path or "").rstrip("/")
    if p.endswith("/reanalyze"):
        return 2
    if p.endswith("/reanalyze-scorer"):
        return 3
    return None


def lambda_handler(event: dict, context) -> dict:
    # Worker mode: plain async payload, no HTTP envelope.
    if "agent" in event and "requestContext" not in event:
        return _run_agent(event["analysis_id"], int(event["agent"]))

    # API mode.
    analysis_id = (event.get("pathParameters") or {}).get("analysis_id")
    path = (
        event.get("rawPath")
        or event.get("requestContext", {}).get("http", {}).get("path", "")
    )
    agent = _agent_for_path(path)
    if not analysis_id or not agent:
        return _response(400, {"error": "Ruta inválida o analysis_id faltante"})
    return _dispatch_async(analysis_id, agent)


def _response(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body, ensure_ascii=False, default=str),
    }
