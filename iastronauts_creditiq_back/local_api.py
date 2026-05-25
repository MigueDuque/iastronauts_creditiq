"""
Local API server — simulates the CreditIQ backend for frontend testing.
Only runs Agent 1 (DocumentExtractor). Agents 2-4 are not built yet.

Usage:
    cd iastronauts_creditiq_back
    python local_api.py

Endpoints:
    POST /analyses          → starts extractor in background, returns {job_id}
    GET  /analyses/{id}     → returns {status: pending|processing|completed|failed}
    GET  /analyses/{id}/report → returns ExtractorOutput JSON
"""
import os
import sys
import uuid
import threading
import logging
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

if not os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_ACCESS_KEY"):
    os.environ["AWS_ACCESS_KEY_ID"] = os.environ["AWS_ACCESS_KEY"]

sys.path.insert(0, str(Path(__file__).parent / "src"))

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("local_api")

app = FastAPI(title="CreditIQ Local API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── in-memory job store ───────────────────────────────────────────────────────
# { job_id: { status, started_at, finished_at, output, error } }
jobs: dict[str, dict] = {}


# ── request models ────────────────────────────────────────────────────────────

class FileToProcess(BaseModel):
    file_name: str
    s3_location: str
    file_type: str

class BusinessContext(BaseModel):
    company_name: str | None = None
    reporting_period: str | None = None
    raw_context: str = ""

class AnalysisRequest(BaseModel):
    tenant_id: str = "local-test"
    business_context: BusinessContext
    files_to_process: list[FileToProcess]
    niif_standards: list[str] = ["NIIF 7", "NIIF 9", "NIIF 13"]


# ── background worker ─────────────────────────────────────────────────────────

def run_extractor(job_id: str, request: AnalysisRequest):
    log.info("job=%s  START  files=%d", job_id, len(request.files_to_process))
    jobs[job_id]["status"] = "processing"

    try:
        from shared.models.orchestrator import (
            BusinessContext as BC, FileToProcess as FTP, OrchestratorOutput
        )
        from shared.models.base import OutputFormat
        from agents.document_extractor.handler import lambda_handler

        event = OrchestratorOutput(
            job_id=job_id,
            tenant_id=request.tenant_id,
            business_context=BC(
                company_name=request.business_context.company_name,
                reporting_period=request.business_context.reporting_period,
                raw_context=request.business_context.raw_context,
            ),
            files_to_process=[
                FTP(
                    file_name=f.file_name,
                    s3_location=f.s3_location,
                    file_type=f.file_type,
                )
                for f in request.files_to_process
            ],
            niif_standards=request.niif_standards,
            output_formats=[OutputFormat.MARKDOWN],
        ).model_dump(mode="json")

        class MockContext:
            def get_remaining_time_in_millis(self):
                return 290_000

        output = lambda_handler(event, MockContext())

        jobs[job_id]["status"]      = "completed"
        jobs[job_id]["output"]      = output
        jobs[job_id]["finished_at"] = datetime.utcnow().isoformat()

        accounts = output.get("accounts", [])
        log.info(
            "job=%s  DONE  accounts=%d  confidence=%.3f  warnings=%d",
            job_id, len(accounts),
            output.get("extraction_confidence", 0),
            len(output.get("extraction_warnings", [])),
        )

    except Exception as e:
        jobs[job_id]["status"]      = "failed"
        jobs[job_id]["error"]       = str(e)
        jobs[job_id]["finished_at"] = datetime.utcnow().isoformat()
        log.exception("job=%s  FAILED  error=%s", job_id, e)


# ── routes ────────────────────────────────────────────────────────────────────

@app.post("/analyses", status_code=202)
def start_analysis(body: AnalysisRequest):
    job_id = str(uuid.uuid4())
    jobs[job_id] = {
        "job_id":     job_id,
        "status":     "pending",
        "started_at": datetime.utcnow().isoformat(),
        "finished_at": None,
        "output":     None,
        "error":      None,
    }
    log.info("job=%s  QUEUED  company=%s  files=%s",
             job_id,
             body.business_context.company_name,
             [f.file_name for f in body.files_to_process])

    t = threading.Thread(target=run_extractor, args=(job_id, body), daemon=True)
    t.start()

    return {"job_id": job_id, "status": "pending"}


@app.get("/analyses/{job_id}")
def get_status(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        "job_id":      job["job_id"],
        "status":      job["status"],
        "started_at":  job["started_at"],
        "finished_at": job["finished_at"],
        "error":       job["error"],
        "accounts_extracted": len(job["output"].get("accounts", [])) if job["output"] else None,
    }


@app.get("/analyses/{job_id}/report")
def get_report(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] != "completed":
        raise HTTPException(status_code=409, detail=f"Job is {job['status']}")
    return job["output"]


@app.get("/health")
def health():
    return {"status": "ok", "jobs": len(jobs)}


# ── main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    log.info("Starting CreditIQ local API on http://localhost:8000")
    log.info("Bucket : %s", os.environ.get("MAIN_BUCKET"))
    log.info("LLM    : %s / %s", os.environ.get("LLM_PROVIDER"), os.environ.get("LLM_MODEL", "default"))
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning")
