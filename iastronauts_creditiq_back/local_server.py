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


def _save_status(job_id: str, status: str, error: str | None = None) -> None:
    import boto3
    s3 = boto3.client("s3")
    bucket = os.environ["MAIN_BUCKET"]
    body: dict = {"status": status}
    if error:
        body["error"] = error
    s3.put_object(Bucket=bucket, Key=f"jobs/{job_id}/status.json", Body=json.dumps(body))


def _run_agent2(job_id: str) -> None:
    """Run FinancialAnalyzer (Agent 2) and pause at analysis_complete."""
    import boto3
    s3 = boto3.client("s3")
    bucket = os.environ["MAIN_BUCKET"]
    try:
        _save_status(job_id, "processing")
        obj = s3.get_object(Bucket=bucket, Key=f"jobs/{job_id}/extractor_output.json")
        payload = json.loads(obj["Body"].read())
        from agents.financial_analyzer.handler import lambda_handler as analyzer
        analyzer(payload, None)  # saves analyzer_output.json to S3 internally
        _save_status(job_id, "analysis_complete")
    except Exception as exc:
        import traceback
        traceback.print_exc()
        _save_status(job_id, "failed", str(exc))


def _run_agents3_4(job_id: str) -> None:
    """Run RiskScorer + ReportGenerator (Agents 3-4) through to completed."""
    import boto3
    s3 = boto3.client("s3")
    bucket = os.environ["MAIN_BUCKET"]
    try:
        _save_status(job_id, "processing")
        obj = s3.get_object(Bucket=bucket, Key=f"jobs/{job_id}/analyzer_output.json")
        payload = json.loads(obj["Body"].read())
        from agents.risk_scorer.handler import lambda_handler as scorer
        payload = scorer(payload, None)
        from agents.report_generator.handler import lambda_handler as report_gen
        result = report_gen(payload, None)
        s3.put_object(
            Bucket=bucket,
            Key=f"jobs/{job_id}/final_report.json",
            Body=json.dumps(result),
        )
        _save_status(job_id, "completed")
    except Exception as exc:
        import traceback
        traceback.print_exc()
        _save_status(job_id, "failed", str(exc))


@app.post("/analyses/{analysis_id}/continue")
async def continue_analysis(analysis_id: str):
    """
    Smart continue: reads current status from S3 and routes to the right stage.
      extraction_complete  → run Agent 2 → analysis_complete
      analysis_complete    → run Agents 3-4 → completed
    """
    import boto3
    s3 = boto3.client("s3")
    bucket = os.environ["MAIN_BUCKET"]
    try:
        obj = s3.get_object(Bucket=bucket, Key=f"jobs/{analysis_id}/status.json")
        current_status = json.loads(obj["Body"].read()).get("status", "")
    except Exception:
        current_status = ""

    fn = _run_agents3_4 if current_status == "analysis_complete" else _run_agent2
    thread = threading.Thread(target=fn, args=(analysis_id,), daemon=True)
    thread.start()
    return JSONResponse(content={"analysis_id": analysis_id, "status": "processing"}, status_code=202)
