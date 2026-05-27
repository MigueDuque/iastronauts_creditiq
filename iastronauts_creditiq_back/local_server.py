"""
Local dev server — wraps Lambda handlers in FastAPI.
Calls real AWS (S3, Step Functions) using your local credentials.

Usage:
    pip install fastapi uvicorn python-dotenv
    uvicorn local_server:app --reload --reload-dir src --port 8000
"""
import json
import os
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
except ImportError:
    pass

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

app = FastAPI(title="CreditIQ Local Dev")

# Job IDs that the user has requested to cancel.
# Background threads check this before doing significant work.
_cancelled_jobs: set[str] = set()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _event(request: Request, body: str | None, path_params: dict | None = None) -> dict:
    return {
        "version": "2.0",
        "rawPath": str(request.url.path),
        "rawQueryString": str(request.url.query),
        "headers": {k.lower(): v for k, v in request.headers.items()},
        "pathParameters": path_params or {},
        "queryStringParameters": dict(request.query_params) or None,
        "body": body,
        "isBase64Encoded": False,
        "requestContext": {
            "http": {
                "method": request.method,
                "path": str(request.url.path),
                "sourceIp": "127.0.0.1",
            }
        },
    }


def _resp(result: dict) -> JSONResponse:
    body = result.get("body", "{}")
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except Exception:
            body = {"message": body}
    return JSONResponse(content=body, status_code=result.get("statusCode", 200))


@app.post("/upload-url")
async def upload_url(request: Request):
    from api.presigned_url.handler import lambda_handler
    body = (await request.body()).decode()
    return _resp(lambda_handler(_event(request, body), None))


@app.post("/analyses")
async def create_analysis(request: Request):
    from api.orchestrator.handler import lambda_handler
    body = (await request.body()).decode()
    return _resp(lambda_handler(_event(request, body), None))


@app.get("/analyses/{analysis_id}")
async def get_status(analysis_id: str, request: Request):
    from api.analysis_status.handler import lambda_handler
    return _resp(lambda_handler(_event(request, None, {"analysis_id": analysis_id}), None))


@app.get("/analyses/{analysis_id}/report")
async def get_report(analysis_id: str, request: Request):
    from api.report_url.handler import lambda_handler
    return _resp(lambda_handler(_event(request, None, {"analysis_id": analysis_id}), None))


# ── Progress helpers ───────────────────────────────────────────────────────────

def _save_status(job_id: str, status: str, error: str | None = None, progress: dict | None = None) -> None:
    from shared.job_store import save as job_save, STATUS
    body: dict = {"status": status}
    if error:
        body["error"] = error
    if progress is not None:
        body["progress"] = progress
    job_save(job_id, STATUS, body)


def _extractor_summary(job_id: str) -> str:
    """Return a human-readable Agent 1 completion label from the extractor output."""
    try:
        from shared.job_store import load as job_load, EXTRACTOR
        ext = job_load(job_id, EXTRACTOR)
        n = len(ext.get("accounts", []))
        c = ext.get("extraction_confidence", 0)
        return f"{n} accounts extracted · {c * 100:.1f}% confidence"
    except Exception:
        return "Accounts extracted"


# ── Agent runners ──────────────────────────────────────────────────────────────

def _run_agent2(job_id: str) -> None:
    """Run FinancialAnalyzer (Agent 2) and pause at analysis_complete."""
    from shared.job_store import load as job_load, EXTRACTOR
    from shared.progress_store import build_progress, register as reg_cb, unregister as unreg_cb

    if job_id in _cancelled_jobs:
        return

    agent1_step = _extractor_summary(job_id)
    done1 = {1: agent1_step}

    def _step(label: str) -> None:
        if job_id not in _cancelled_jobs:
            _save_status(job_id, "processing", progress=build_progress(
                running_agent=2, current_step=label,
                done_agents=[1], done_steps=done1,
            ))

    try:
        _step("Loading historical data")
        if job_id in _cancelled_jobs:
            return

        payload = job_load(job_id, EXTRACTOR)

        from agents.financial_analyzer.handler import lambda_handler as analyzer
        if job_id in _cancelled_jobs:
            return

        reg_cb(job_id, _step)
        try:
            analyzer(payload, None)  # saves financial_analyzer_response.json to S3 internally
        finally:
            unreg_cb(job_id)

        if job_id not in _cancelled_jobs:
            _save_status(job_id, "analysis_complete", progress=build_progress(
                running_agent=None, current_step=None,
                done_agents=[1, 2], done_steps={**done1, 2: "Analysis complete"},
            ))
    except Exception as exc:
        import traceback
        traceback.print_exc()
        _save_status(job_id, "failed", str(exc))


def _run_agents3_4(job_id: str) -> None:
    """Run RiskScorer + ReportGenerator (Agents 3-4) through to completed."""
    from shared.job_store import load as job_load, save as job_save, FINANCIAL_ANALYZER, REPORT_GENERATOR
    from shared.progress_store import build_progress

    if job_id in _cancelled_jobs:
        return

    agent1_step = _extractor_summary(job_id)
    done_12 = {1: agent1_step, 2: "Analysis complete"}

    try:
        _save_status(job_id, "processing", progress=build_progress(
            running_agent=3, current_step="Validating math & compliance",
            done_agents=[1, 2], done_steps=done_12,
        ))
        if job_id in _cancelled_jobs:
            return

        payload = job_load(job_id, FINANCIAL_ANALYZER)
        from agents.risk_scorer.handler import lambda_handler as scorer
        if job_id in _cancelled_jobs:
            return
        payload = scorer(payload, None)

        _save_status(job_id, "processing", progress=build_progress(
            running_agent=4, current_step="Generating report narrative",
            done_agents=[1, 2, 3], done_steps={**done_12, 3: "Risk validated"},
        ))
        from agents.report_generator.handler import lambda_handler as report_gen
        if job_id in _cancelled_jobs:
            return
        result = report_gen(payload, None)
        job_save(job_id, REPORT_GENERATOR, result)

        if job_id not in _cancelled_jobs:
            _save_status(job_id, "completed", progress=build_progress(
                running_agent=None, current_step=None,
                done_agents=[1, 2, 3, 4],
                done_steps={**done_12, 3: "Risk validated", 4: "Report generated"},
            ))
    except Exception as exc:
        import traceback
        traceback.print_exc()
        _save_status(job_id, "failed", str(exc))


# ── Job listing ────────────────────────────────────────────────────────────────

@app.get("/jobs")
async def list_jobs():
    """List all jobs from S3 sorted newest first. Used by the GUI job picker."""
    import boto3

    s3 = boto3.client("s3")
    bucket = os.environ.get("MAIN_BUCKET", "")
    if not bucket:
        return JSONResponse(content={"jobs": []})

    from shared.job_store import job_key

    status_keys: list[str] = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix="jobs/"):
        for obj in page.get("Contents", []):
            key: str = obj["Key"]
            if key.endswith("/status.json"):
                status_keys.append(key)

    jobs: list[dict] = []
    for key in status_keys:
        # key shape: jobs/YYYY-MM-DD/job_id/status.json
        parts = key.split("/")
        if len(parts) != 4:
            continue
        date_folder, job_id = parts[1], parts[2]

        try:
            obj = s3.get_object(Bucket=bucket, Key=key)
            status = json.loads(obj["Body"].read()).get("status", "unknown")
        except Exception:
            status = "unknown"

        company_name: str | None = None
        periods: list[str] = []
        try:
            ext_obj = s3.get_object(Bucket=bucket, Key=job_key(job_id, "extractor_response"))
            ext = json.loads(ext_obj["Body"].read())
            company_name = ext.get("company_name")
            periods = ext.get("periods", [])
        except Exception:
            pass

        jobs.append({
            "job_id": job_id,
            "date": date_folder,
            "status": status,
            "company_name": company_name,
            "periods": periods,
        })

    jobs.sort(key=lambda j: j["date"], reverse=True)
    return JSONResponse(content={"jobs": jobs})


@app.delete("/analyses/{analysis_id}")
async def cancel_analysis(analysis_id: str):
    """Stop any running background thread and mark the job as cancelled in S3."""
    _cancelled_jobs.add(analysis_id)
    _save_status(analysis_id, "cancelled")
    return JSONResponse(content={"analysis_id": analysis_id, "status": "cancelled"}, status_code=200)


@app.post("/analyses/{analysis_id}/reanalyze")
async def reanalyze(analysis_id: str):
    """Force-run Agent 2 regardless of current pipeline status."""
    _cancelled_jobs.discard(analysis_id)
    thread = threading.Thread(target=_run_agent2, args=(analysis_id,), daemon=True)
    thread.start()
    return JSONResponse(content={"analysis_id": analysis_id, "status": "processing"}, status_code=202)


@app.post("/analyses/{analysis_id}/continue")
async def continue_analysis(analysis_id: str):
    """
    Smart continue: reads current status from S3 and routes to the right stage.
      extraction_complete  → run Agent 2 → analysis_complete
      analysis_complete    → run Agents 3-4 → completed
    """
    from shared.job_store import load as job_load, STATUS
    try:
        current_status = job_load(analysis_id, STATUS).get("status", "")
    except Exception:
        current_status = ""

    fn = _run_agents3_4 if current_status == "analysis_complete" else _run_agent2
    thread = threading.Thread(target=fn, args=(analysis_id,), daemon=True)
    thread.start()
    return JSONResponse(content={"analysis_id": analysis_id, "status": "processing"}, status_code=202)
