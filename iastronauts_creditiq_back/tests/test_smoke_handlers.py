"""
Smoke tests — one test per lambda_handler entry point.

Goals:
  1. Verify each handler module imports without errors.
  2. Verify each handler returns a dict with a numeric `statusCode` key
     when given a valid minimal event (happy path with AWS calls mocked).
  3. Catch obvious wiring issues (wrong env var names, bad import paths,
     missing model fields) before a deploy reaches AWS.

AWS calls are mocked via unittest.mock.patch so no real credentials are needed.
"""
import json
from unittest.mock import MagicMock, patch

import pytest


# ── helpers ────────────────────────────────────────────────────────────────────

def _api_event(method: str = "GET", path: str = "/", path_params: dict | None = None,
               body: dict | str | None = None, tenant_id: str = "tenant-test") -> dict:
    return {
        "version": "2.0",
        "rawPath": path,
        "rawQueryString": "",
        "headers": {"x-tenant-id": tenant_id, "content-type": "application/json"},
        "pathParameters": path_params or {},
        "queryStringParameters": None,
        "body": json.dumps(body) if isinstance(body, dict) else body,
        "isBase64Encoded": False,
        "requestContext": {"http": {"method": method, "path": path, "sourceIp": "127.0.0.1"}},
    }


def _assert_response(result: dict, expected_status: int | None = None) -> None:
    assert isinstance(result, dict), f"Handler returned {type(result)}, expected dict"
    assert "statusCode" in result, f"Missing 'statusCode' key in response: {result}"
    assert isinstance(result["statusCode"], int), f"statusCode is not int: {result['statusCode']}"
    if expected_status is not None:
        assert result["statusCode"] == expected_status, (
            f"Expected {expected_status}, got {result['statusCode']}. Body: {result.get('body')}"
        )


# ── API handlers ───────────────────────────────────────────────────────────────

class TestPresignedUrl:
    def test_returns_200_with_valid_request(self):
        from api.presigned_url import handler as mod

        mock_s3 = MagicMock()
        mock_s3.generate_presigned_url.return_value = "https://s3.example.com/presigned"
        with patch.object(mod, "s3", mock_s3):
            result = mod.lambda_handler(
                _api_event("POST", "/upload-url", body={"file_name": "test.pdf", "file_type": "pdf"}),
                None,
            )

        _assert_response(result, 200)
        body = json.loads(result["body"])
        assert "upload_url" in body
        assert "s3_key" in body

    def test_missing_file_name_returns_400(self):
        from api.presigned_url import handler as mod

        mock_s3 = MagicMock()
        with patch.object(mod, "s3", mock_s3):
            result = mod.lambda_handler(
                _api_event("POST", "/upload-url", body={"file_type": "pdf"}),
                None,
            )

        _assert_response(result, 400)

    def test_no_tenant_returns_401(self):
        from api.presigned_url import handler as mod

        event = _api_event("POST", "/upload-url", body={"file_name": "test.pdf"})
        del event["headers"]["x-tenant-id"]
        result = mod.lambda_handler(event, None)

        _assert_response(result, 401)


class TestOrchestrator:
    def test_returns_202_when_sfn_starts(self):
        from api.orchestrator import handler as mod

        mock_s3 = MagicMock()
        mock_s3.head_object.return_value = {}
        mock_s3.list_objects_v2.return_value = {"Contents": []}

        mock_sfn = MagicMock()
        mock_sfn.start_execution.return_value = {
            "executionArn": "arn:aws:states:us-east-1:000000000000:execution:test:job-123"
        }

        with patch.object(mod, "s3", mock_s3), \
             patch("api.orchestrator.handler._sfn_client", return_value=mock_sfn), \
             patch("api.orchestrator.handler._save_job_status"), \
             patch("shared.job_store.boto3") as mock_job_boto:
            mock_job_boto.client.return_value = MagicMock()
            result = mod.lambda_handler(
                _api_event("POST", "/analyses", body={
                    "files_to_process": [
                        {"file_name": "test.pdf", "s3_location": "uploads/tenant-test/test.pdf", "file_type": "pdf"}
                    ],
                    "company_name": "Acme Corp",
                    "reporting_period": "2024-12",
                    "output_formats": ["markdown"],
                }),
                None,
            )

        _assert_response(result)
        assert result["statusCode"] in (200, 202, 409)

    def test_missing_s3_key_returns_400(self):
        from api.orchestrator import handler as mod

        result = mod.lambda_handler(
            _api_event("POST", "/analyses", body={"file_type": "pdf"}),
            None,
        )
        _assert_response(result, 400)


class TestAnalysisStatus:
    def test_sfn_running_returns_processing(self):
        from api.analysis_status import handler as mod
        from botocore.exceptions import ClientError

        mock_sfn = MagicMock()
        mock_sfn.describe_execution.return_value = {"status": "RUNNING"}

        mock_s3_client = MagicMock()
        mock_s3_client.get_object.side_effect = ClientError(
            {"Error": {"Code": "NoSuchKey", "Message": ""}}, "get_object"
        )

        with patch("api.analysis_status.handler._sfn_client", return_value=mock_sfn), \
             patch.object(mod, "s3", mock_s3_client), \
             patch("shared.job_store.boto3") as mock_job_boto:
            mock_job_boto.client.return_value = mock_s3_client
            result = mod.lambda_handler(
                _api_event("GET", "/analyses/job-123", path_params={"analysis_id": "job-123"}),
                None,
            )

        _assert_response(result, 200)
        body = json.loads(result["body"])
        assert body["status"] in ("processing", "pending")

    def test_sfn_succeeded_returns_completed(self):
        from api.analysis_status import handler as mod
        from botocore.exceptions import ClientError

        mock_sfn = MagicMock()
        mock_sfn.describe_execution.return_value = {"status": "SUCCEEDED"}

        mock_s3_client = MagicMock()
        mock_s3_client.get_object.side_effect = ClientError(
            {"Error": {"Code": "NoSuchKey", "Message": ""}}, "get_object"
        )

        with patch("api.analysis_status.handler._sfn_client", return_value=mock_sfn), \
             patch.object(mod, "s3", mock_s3_client), \
             patch("shared.job_store.boto3") as mock_job_boto:
            mock_job_boto.client.return_value = mock_s3_client
            result = mod.lambda_handler(
                _api_event("GET", "/analyses/job-123", path_params={"analysis_id": "job-123"}),
                None,
            )

        _assert_response(result, 200)
        body = json.loads(result["body"])
        assert body["status"] == "completed"

    def test_sfn_failed_with_review_expired_error(self):
        from api.analysis_status import handler as mod
        from botocore.exceptions import ClientError

        mock_sfn = MagicMock()
        mock_sfn.describe_execution.return_value = {
            "status": "FAILED",
            "error": "ReviewExpired",
            "cause": "Analyst review window expired.",
        }

        mock_s3_client = MagicMock()
        mock_s3_client.get_object.side_effect = ClientError(
            {"Error": {"Code": "NoSuchKey", "Message": ""}}, "get_object"
        )

        with patch("api.analysis_status.handler._sfn_client", return_value=mock_sfn), \
             patch.object(mod, "s3", mock_s3_client), \
             patch("shared.job_store.boto3") as mock_job_boto:
            mock_job_boto.client.return_value = mock_s3_client
            result = mod.lambda_handler(
                _api_event("GET", "/analyses/job-123", path_params={"analysis_id": "job-123"}),
                None,
            )

        _assert_response(result, 200)
        body = json.loads(result["body"])
        assert body["status"] == "review_expired"

    def test_missing_analysis_id_returns_400(self):
        from api.analysis_status import handler as mod

        result = mod.lambda_handler(_api_event("GET", "/analyses/"), None)
        _assert_response(result, 400)


class TestReportUrl:
    def test_returns_url_when_docx_exists(self):
        from api.report_url import handler as mod

        mock_s3 = MagicMock()
        mock_s3.generate_presigned_url.return_value = "https://s3.example.com/report.docx"

        mock_s3_for_job = MagicMock()
        mock_s3_for_job.get_object.return_value = {
            "Body": MagicMock(read=lambda: json.dumps({
                "docx_key": "jobs/2024-01-01/job-123/final_report.docx"
            }).encode())
        }

        with patch.object(mod, "s3", mock_s3), \
             patch("shared.job_store.boto3") as mock_job_boto:
            mock_job_boto.client.return_value = mock_s3_for_job
            result = mod.lambda_handler(
                _api_event("GET", "/analyses/job-123/report",
                           path_params={"analysis_id": "job-123"}),
                None,
            )

        _assert_response(result)
        assert result["statusCode"] in (200, 404)

    def test_missing_analysis_id_returns_400(self):
        from api.report_url import handler as mod

        result = mod.lambda_handler(_api_event("GET", "/analyses//report"), None)
        _assert_response(result, 400)


class TestContinueAnalysis:
    def test_sfn_path_sends_task_success(self):
        from api.continue_analysis import handler as mod
        from botocore.exceptions import ClientError

        mock_sfn = MagicMock()

        status_payload = {"status": "extraction_complete"}
        token_payload = {"task_token": "test-token-abc"}
        extractor_payload = {"job_id": "job-123", "accounts": [], "company_name": "Acme"}

        call_index = {"n": 0}
        def fake_get_object(**kwargs):
            call_index["n"] += 1
            key = kwargs.get("Key", "")
            if "pause_token" in key:
                return {"Body": MagicMock(read=lambda: json.dumps(token_payload).encode())}
            if "extractor_response" in key:
                return {"Body": MagicMock(read=lambda: json.dumps(extractor_payload).encode())}
            return {"Body": MagicMock(read=lambda: json.dumps(status_payload).encode())}

        mock_s3_client = MagicMock()
        mock_s3_client.get_object.side_effect = fake_get_object

        with patch("api.continue_analysis.handler._sfn_client", return_value=mock_sfn), \
             patch("shared.job_store.boto3") as mock_job_boto:
            mock_job_boto.client.return_value = mock_s3_client
            result = mod.lambda_handler(
                _api_event("POST", "/analyses/job-123/continue",
                           path_params={"analysis_id": "job-123"}),
                None,
            )

        _assert_response(result, 202)

    def test_missing_analysis_id_returns_400(self):
        from api.continue_analysis import handler as mod

        result = mod.lambda_handler(_api_event("POST", "/analyses//continue"), None)
        _assert_response(result, 400)


class TestCancelAnalysis:
    def test_cancel_returns_200(self):
        from api.cancel_analysis import handler as mod
        from botocore.exceptions import ClientError

        mock_sfn = MagicMock()
        mock_sfn.stop_execution.return_value = {}

        mock_s3_client = MagicMock()
        # Simulate tenant.json not present (legacy job) so the allow-through branch runs.
        mock_s3_client.get_object.side_effect = ClientError(
            {"Error": {"Code": "NoSuchKey", "Message": ""}}, "get_object"
        )

        with patch("api.cancel_analysis.handler._sfn_client", return_value=mock_sfn), \
             patch("shared.job_store.boto3") as mock_job_boto:
            mock_job_boto.client.return_value = mock_s3_client
            result = mod.lambda_handler(
                _api_event("DELETE", "/analyses/job-123",
                           path_params={"analysis_id": "job-123"}),
                None,
            )

        _assert_response(result)
        assert result["statusCode"] in (200, 404)


# ── Pause agent ────────────────────────────────────────────────────────────────

class TestPauseHandler:
    def test_saves_status_and_token_separately(self):
        from agents.pause import handler as mod

        saved: dict[str, dict] = {}

        def fake_save(job_id, artifact, payload, s3_client=None):
            saved[artifact] = payload

        with patch("agents.pause.handler.job_save", side_effect=fake_save):
            mod.lambda_handler(
                {
                    "job_id": "job-abc",
                    "task_token": "token-xyz",
                    "pause_status": "extraction_complete",
                },
                None,
            )

        assert "status" in saved, "Expected a STATUS artifact to be saved"
        assert saved["status"]["status"] == "extraction_complete"
        assert "task_token" not in saved["status"], (
            "task_token must NOT be mixed into status.json — use pause_token artifact"
        )
        assert "pause_token" in saved, "Expected a separate pause_token artifact"
        assert saved["pause_token"]["task_token"] == "token-xyz"


# ── Agent handler imports ──────────────────────────────────────────────────────
# These are import-only smoke tests: we just verify the module loads cleanly.
# Full integration testing of agent logic requires real LLM calls and is out of
# scope for the CI gate.

class TestAgentImports:
    def test_document_extractor_imports(self):
        import agents.document_extractor.handler  # noqa: F401

    def test_financial_analyzer_imports(self):
        import agents.financial_analyzer.handler  # noqa: F401

    def test_risk_scorer_imports(self):
        import agents.risk_scorer.handler  # noqa: F401

    def test_report_generator_imports(self):
        import agents.report_generator.handler  # noqa: F401

    def test_revisor_inteligente_imports(self):
        import agents.revisor_inteligente.handler  # noqa: F401

    def test_management_report_imports(self):
        import agents.management_report.handler  # noqa: F401


# ── Shared model integrity ─────────────────────────────────────────────────────

class TestSharedModels:
    def test_orchestrator_output_requires_tenant_id(self):
        from shared.models import OrchestratorOutput, OutputFormat
        import pytest as pt

        with pt.raises(Exception):
            OrchestratorOutput(
                job_id="j",
                s3_key="uploads/t/file.pdf",
                file_type="pdf",
                output_format=OutputFormat.markdown,
                # tenant_id intentionally omitted
            )

    def test_progress_store_builds_all_agents(self):
        from shared.progress_store import build_progress, AGENT_DEFS

        progress = build_progress(running_agent=2, current_step="step", done_agents=[1])

        assert progress["current_agent"] == 2
        agents = {a["index"]: a for a in progress["agents"]}
        assert agents[1]["state"] == "done"
        assert agents[2]["state"] == "running"
        assert agents[3]["state"] == "pending"
        assert len(progress["agents"]) == len(AGENT_DEFS)

    def test_job_store_key_format(self):
        from shared.job_store import job_key

        key = job_key("job-abc", "status", date="2024-05-31")
        assert key == "jobs/2024-05-31/job-abc/status.json"
