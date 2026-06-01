"""
Shared pytest configuration: sys.path setup + baseline env vars.

Setting env vars here (module-level, not in a fixture) ensures they are present
before any test module is imported and before handler modules initialize their
module-level boto3 clients and os.environ lookups.
"""
import os
import sys

# Add src/ to the path so tests can import handler modules directly.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# Minimum env vars required by all handlers.  Values are test-safe (no real AWS).
_TEST_ENV = {
    "MAIN_BUCKET": "test-bucket",
    "STAGE": "test",
    "WORKFLOW_ARN": "arn:aws:states:us-east-1:000000000000:stateMachine:test",
    "AWS_REGION": "us-east-1",
    "AWS_ACCOUNT_ID": "000000000000",
    "LLM_PROVIDER": "anthropic_api",
    "ANTHROPIC_API_KEY": "sk-ant-test",
    "ANTHROPIC_API_KEY_SECRET_ARN": "arn:aws:secretsmanager:us-east-1:000000000000:secret:test",
    "RUNNER_FUNCTION_NAME": "test-runner",
    "ANALYZER_FUNCTION_NAME": "test-analyzer",
    "RISK_SCORER_FUNCTION_NAME": "test-scorer",
    "REPORT_GENERATOR_FUNCTION_NAME": "test-report-gen",
    "GNEWS_API_KEY": "",
}
for key, value in _TEST_ENV.items():
    os.environ.setdefault(key, value)
