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
_cancelled_jobs: set[str] = set()
# Job IDs with an actively-running background thread.
_running_jobs: set[str] = set()
# Per-job write locks — prevents last-writer-wins races on status.json when
# multiple agent threads progress at the same time (e.g. reanalyze then continue).
_status_write_locks: dict[str, threading.Lock] = {}
_status_write_locks_meta = threading.Lock()


def _get_status_lock(job_id: str) -> threading.Lock:
    with _status_write_locks_meta:
        if job_id not in _status_write_locks:
            _status_write_locks[job_id] = threading.Lock()
        return _status_write_locks[job_id]

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
    result = lambda_handler(_event(request, None, {"analysis_id": analysis_id}), None)

    body = result.get("body", "{}")
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except Exception:
            body = {"message": body}

    # Correct stale 'processing'/'pending' when no thread is running for this job.
    # Happens when the server was restarted mid-run: status.json still says 'processing'
    # but artifacts from a prior run exist in S3.
    current_status = body.get("status", "") if isinstance(body, dict) else ""
    if current_status in ("processing", "pending", "unknown") and analysis_id not in _running_jobs:
        bucket = os.environ.get("MAIN_BUCKET", "")
        if bucket:
            # exists() resolves the job's real date folder, so this works for jobs
            # created on a previous day (not just today).
            from shared.job_store import exists, EXTRACTOR, FINANCIAL_ANALYZER, RISK_SCORER

            if exists(analysis_id, RISK_SCORER):
                body["status"] = "scoring_complete"
            elif exists(analysis_id, FINANCIAL_ANALYZER):
                body["status"] = "analysis_complete"
            elif exists(analysis_id, EXTRACTOR):
                body["status"] = "extraction_complete"

    return JSONResponse(content=body, status_code=result.get("statusCode", 200))


@app.get("/analyses/{analysis_id}/report")
async def get_report(analysis_id: str, request: Request):
    from api.report_url.handler import lambda_handler
    return _resp(lambda_handler(_event(request, None, {"analysis_id": analysis_id}), None))


@app.get("/analyses/{analysis_id}/extractor")
async def get_extractor(analysis_id: str):
    """Return the raw DocumentExtractor output JSON for Agent 1 results view."""
    from shared.job_store import load as job_load, EXTRACTOR
    try:
        data = job_load(analysis_id, EXTRACTOR)
        return JSONResponse(content=data)
    except Exception:
        return JSONResponse(content={}, status_code=404)


@app.get("/analyses/{analysis_id}/analyzer")
async def get_analyzer(analysis_id: str):
    """Return the raw FinancialAnalyzer output JSON for the Agent 2 results view.

    Dedicated endpoint so the frontend stops reading Agent 2 data off /report
    (which is overloaded to also serve the final report's presigned URL)."""
    from shared.job_store import load as job_load, FINANCIAL_ANALYZER
    try:
        data = job_load(analysis_id, FINANCIAL_ANALYZER)
        return JSONResponse(content=data)
    except Exception:
        return JSONResponse(content={}, status_code=404)


@app.get("/analyses/{analysis_id}/scorer")
async def get_scorer(analysis_id: str):
    """Return the raw RiskScorer output JSON for Agent 3 results dashboard."""
    from shared.job_store import load as job_load, RISK_SCORER
    try:
        data = job_load(analysis_id, RISK_SCORER)
        return JSONResponse(content=data)
    except Exception:
        return JSONResponse(content={}, status_code=404)


@app.get("/analyses/{analysis_id}/revisor")
async def get_revisor(analysis_id: str):
    """Return the raw RevisorInteligente output JSON for Agent 5 quality review view."""
    from shared.job_store import load as job_load, REVISOR
    try:
        data = job_load(analysis_id, REVISOR)
        return JSONResponse(content=data)
    except Exception:
        return JSONResponse(content={}, status_code=404)


# ── Progress helpers ───────────────────────────────────────────────────────────

def _save_status(job_id: str, status: str, error: str | None = None, progress: dict | None = None) -> None:
    from shared.job_store import save as job_save, STATUS
    body: dict = {"status": status}
    if error:
        body["error"] = error
    if progress is not None:
        body["progress"] = progress
    with _get_status_lock(job_id):
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
    _running_jobs.add(job_id)

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
    finally:
        _running_jobs.discard(job_id)


def _run_agent3(job_id: str) -> None:
    """Run RiskScorer (Agent 3) and pause at scoring_complete."""
    from shared.job_store import load as job_load, FINANCIAL_ANALYZER
    from shared.progress_store import build_progress

    if job_id in _cancelled_jobs:
        return
    _running_jobs.add(job_id)

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
        scorer(payload, None)  # saves risk_scorer_response.json internally

        if job_id not in _cancelled_jobs:
            _save_status(job_id, "scoring_complete", progress=build_progress(
                running_agent=None, current_step=None,
                done_agents=[1, 2, 3], done_steps={**done_12, 3: "Risk scored"},
            ))
    except Exception as exc:
        import traceback
        traceback.print_exc()
        _save_status(job_id, "failed", str(exc))
    finally:
        _running_jobs.discard(job_id)


def _run_agent4(job_id: str, llm_model: str | None = None) -> None:
    """Run ReportGenerator (Agent 4) and pause at report_complete."""
    from shared.job_store import (
        load as job_load, save as job_save,
        RISK_SCORER, REPORT_GENERATOR,
    )
    from shared.progress_store import build_progress

    if job_id in _cancelled_jobs:
        return
    _running_jobs.add(job_id)

    agent1_step = _extractor_summary(job_id)
    done_123 = {1: agent1_step, 2: "Analysis complete", 3: "Risk scored"}

    try:
        model_label = f" ({llm_model})" if llm_model else ""
        _save_status(job_id, "processing", progress=build_progress(
            running_agent=4, current_step=f"Generating intelligence report{model_label}",
            done_agents=[1, 2, 3], done_steps=done_123,
        ))
        if job_id in _cancelled_jobs:
            return

        payload = job_load(job_id, RISK_SCORER)
        if llm_model:
            payload["llm_model"] = llm_model

        from agents.report_generator.handler import lambda_handler as report_gen
        if job_id in _cancelled_jobs:
            return
        result = report_gen(payload, None)
        job_save(job_id, REPORT_GENERATOR, result)

        if job_id not in _cancelled_jobs:
            _save_status(job_id, "report_complete", progress=build_progress(
                running_agent=None, current_step=None,
                done_agents=[1, 2, 3, 4], done_steps={**done_123, 4: "Report generated"},
            ))
    except Exception as exc:
        import traceback
        traceback.print_exc()
        _save_status(job_id, "failed", str(exc))
    finally:
        _running_jobs.discard(job_id)


def _run_agent5(job_id: str) -> None:
    """Run RevisorInteligente (Agent 5) — quality review gate."""
    from shared.job_store import (
        load as job_load, save as job_save,
        REPORT_GENERATOR, REVISOR,
    )
    from shared.progress_store import build_progress

    if job_id in _cancelled_jobs:
        return
    _running_jobs.add(job_id)

    agent1_step = _extractor_summary(job_id)
    done_1234 = {1: agent1_step, 2: "Analysis complete", 3: "Risk scored", 4: "Report generated"}

    try:
        _save_status(job_id, "processing", progress=build_progress(
            running_agent=5, current_step="Reviewing report quality",
            done_agents=[1, 2, 3, 4], done_steps=done_1234,
        ))
        if job_id in _cancelled_jobs:
            return

        payload = job_load(job_id, REPORT_GENERATOR)
        from agents.revisor_inteligente.handler import lambda_handler as revisor
        if job_id in _cancelled_jobs:
            return
        revised = revisor(payload, None)
        job_save(job_id, REVISOR, revised)

        if job_id not in _cancelled_jobs:
            review_step = (
                f"QA {revised.get('validation_score')}/100 · "
                f"{revised.get('errors_count', 0)}E/{revised.get('warnings_count', 0)}W"
            )
            _save_status(job_id, "completed", progress=build_progress(
                running_agent=None, current_step=None,
                done_agents=[1, 2, 3, 4, 5],
                done_steps={**done_1234, 5: review_step},
            ))
    except Exception as exc:
        import traceback
        traceback.print_exc()
        _save_status(job_id, "failed", str(exc))
    finally:
        _running_jobs.discard(job_id)


# ── Job listing ────────────────────────────────────────────────────────────────

@app.get("/jobs")
async def list_jobs():
    """List all jobs from S3 sorted newest first. Used by the GUI job picker."""
    import boto3

    s3 = boto3.client("s3")
    bucket = os.environ.get("MAIN_BUCKET", "")
    if not bucket:
        return JSONResponse(content={"jobs": []})

    # Single paginated listing — collect all keys under jobs/
    all_keys: set[str] = set()
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix="jobs/"):
        for obj in page.get("Contents", []):
            all_keys.add(obj["Key"])

    # Discover jobs from status.json files  (jobs/{date}/{job_id}/status.json)
    job_dates: dict[str, str] = {}
    for key in all_keys:
        parts = key.split("/")
        if len(parts) == 4 and parts[3] == "status.json":
            job_dates[parts[2]] = parts[1]  # job_id → date_folder

    jobs: list[dict] = []
    for job_id, date_folder in job_dates.items():
        base = f"jobs/{date_folder}/{job_id}"
        has_extractor = f"{base}/extractor_response.json"          in all_keys
        has_analyzer  = f"{base}/financial_analyzer_response.json" in all_keys
        has_scorer    = f"{base}/risk_scorer_response.json"        in all_keys
        has_report    = f"{base}/report_generator_response.json"   in all_keys

        # Read status.json for exact disposition (failed / cancelled / completed)
        status = "unknown"
        try:
            obj = s3.get_object(Bucket=bucket, Key=f"{base}/status.json")
            status = json.loads(obj["Body"].read()).get("status", "unknown")
        except Exception:
            pass

        # If status is ambiguous, derive from artifact presence
        if status in ("unknown", "processing", "pending"):
            if has_report:
                status = "completed"
            elif has_scorer:
                status = "scoring_complete"
            elif has_analyzer:
                status = "analysis_complete"
            elif has_extractor:
                status = "extraction_complete"

        company_name: str | None = None
        periods: list[str] = []
        if has_extractor:
            try:
                ext_obj = s3.get_object(Bucket=bucket, Key=f"{base}/extractor_response.json")
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


# ── AI Market Pulse ─────────────────────────────────────────────────────────────
# Real-time dashboard data. Distinct from the per-job analysis pipeline: a global,
# tenant-shared snapshot refreshed on a schedule. Read routes only ever serve the
# cached pulse (never hit GNews/yfinance/TE on the request path).

@app.get("/market/data")
async def market_data(request: Request):
    from api.market_data.handler import lambda_handler
    return _resp(lambda_handler(_event(request, None), None))


@app.get("/market/pulse")
@app.get("/market/news")
@app.get("/market/overview")
@app.get("/market/signals")
async def market_read(request: Request):
    from api.market_read.handler import lambda_handler
    return _resp(lambda_handler(_event(request, None), None))


@app.post("/market/refresh")
async def market_refresh():
    """Force an immediate ingest → interpret cycle (manual / debugging)."""
    from api.market_read.handler import refresh
    try:
        pulse = refresh()
        return JSONResponse(content={"status": "refreshed", "as_of": pulse.get("as_of")})
    except Exception as exc:
        import traceback
        traceback.print_exc()
        return JSONResponse(content={"status": "error", "error": str(exc)}, status_code=500)


def _market_refresh_loop() -> None:
    """Background refresher: ingest+interpret every MARKET_REFRESH_SECONDS.

    Each cycle is fully isolated — a failed source or a thrown refresh never kills
    the loop, it just logs and waits for the next tick. The read endpoints keep
    serving the last-good pulse meanwhile.
    """
    import time
    from api.market_read.handler import refresh

    interval = int(os.environ.get("MARKET_REFRESH_SECONDS", "300"))
    while True:
        try:
            refresh()
        except Exception as exc:
            print(f"[market] refresh failed: {exc}")
        time.sleep(interval)


@app.on_event("startup")
async def _start_market_refresh() -> None:
    # Opt-out via MARKET_AUTO_REFRESH=false (e.g. when running without API keys).
    if os.environ.get("MARKET_AUTO_REFRESH", "true").lower() != "true":
        print("[market] auto-refresh disabled (MARKET_AUTO_REFRESH=false)")
        return
    threading.Thread(target=_market_refresh_loop, daemon=True).start()
    print("[market] background refresh started")


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


@app.post("/analyses/{analysis_id}/reanalyze-scorer")
async def reanalyze_scorer(analysis_id: str):
    """Force-run Agent 3 (RiskScorer) regardless of current pipeline status."""
    _cancelled_jobs.discard(analysis_id)
    thread = threading.Thread(target=_run_agent3, args=(analysis_id,), daemon=True)
    thread.start()
    return JSONResponse(content={"analysis_id": analysis_id, "status": "processing"}, status_code=202)


@app.post("/analyses/{analysis_id}/reanalyze-report")
async def reanalyze_report(analysis_id: str, request: Request):
    """Force-run Agent 4 (ReportGenerator) regardless of current pipeline status."""
    llm_model: str | None = None
    try:
        body_bytes = await request.body()
        if body_bytes:
            llm_model = json.loads(body_bytes).get("llm_model") or None
    except Exception:
        pass
    _cancelled_jobs.discard(analysis_id)
    thread = threading.Thread(
        target=_run_agent4, args=(analysis_id,), kwargs={"llm_model": llm_model}, daemon=True
    )
    thread.start()
    return JSONResponse(content={"analysis_id": analysis_id, "status": "processing"}, status_code=202)


@app.post("/analyses/{analysis_id}/continue")
async def continue_analysis(analysis_id: str, request: Request):
    """
    Smart continue: reads current status from S3 and routes to the right stage.
      extraction_complete  → run Agent 2 → analysis_complete
      analysis_complete    → run Agent 3 → scoring_complete
      scoring_complete     → run Agent 4 → completed

    Optional body: { "llm_model": "claude-opus-4-7" }  (Agent 4 only)
    """
    from shared.job_store import load as job_load, STATUS

    llm_model: str | None = None
    try:
        body_bytes = await request.body()
        if body_bytes:
            body = json.loads(body_bytes)
            llm_model = body.get("llm_model") or None
    except Exception:
        pass

    try:
        current_status = job_load(analysis_id, STATUS).get("status", "")
    except Exception:
        current_status = ""

    _cancelled_jobs.discard(analysis_id)

    if current_status == "scoring_complete":
        thread = threading.Thread(
            target=_run_agent4, args=(analysis_id,), kwargs={"llm_model": llm_model}, daemon=True
        )
    elif current_status == "report_complete":
        thread = threading.Thread(target=_run_agent5, args=(analysis_id,), daemon=True)
    elif current_status == "analysis_complete":
        thread = threading.Thread(target=_run_agent3, args=(analysis_id,), daemon=True)
    else:
        thread = threading.Thread(target=_run_agent2, args=(analysis_id,), daemon=True)

    thread.start()
    return JSONResponse(content={"analysis_id": analysis_id, "status": "processing"}, status_code=202)
