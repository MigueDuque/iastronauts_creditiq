"""
agent_handoff.py — claim-check helpers for the Step Functions pipeline.

Step Functions caps state I/O (and SendTaskSuccess output) at 256 KB. A real
analysis carries 80+ accounts plus risk/ratio/concentration detail, so the full
agent outputs (AnalyzerOutput → ScorerOutput → FinalReportOutput → RevisorOutput)
blow past that limit. When a Lambda *returns* more than 256 KB, Step Functions
fails the task with ``States.DataLimitExceeded`` — caught by the workflow's
``States.ALL`` handler and surfaced as the generic ``WorkflowExecutionFailed``.
This never reproduces locally (``local_server.py`` passes dicts in memory with no
cap), which is why the pipeline "works in local, fails in the cloud".

The fix is the standard claim-check pattern: every agent already persists its full
output to S3 via ``job_store``, so the agents pass only a thin pointer through the
state machine and the next agent rehydrates the full payload from S3.

  • ``slim_envelope(output, ...)`` — build the pointer an agent returns to SFN.
  • ``hydrate_input(event, artifact, Model, discriminator=...)`` — load the
    predecessor's full output for validation, transparently falling back to the
    event itself when it already carries the full payload (direct/local
    invocation, agent_runner, or any legacy fat-payload caller).
"""
from __future__ import annotations

from typing import Type, TypeVar

from pydantic import BaseModel

from .job_store import load as job_load

T = TypeVar("T", bound=BaseModel)

# Fields that are NOT persisted in the S3 artifact but must survive the handoff
# (e.g. the analyst-chosen LLM model the ReportGenerator reads off its event).
_TRANSIENT_KEYS = ("llm_model",)


def slim_envelope(output, *, status: str | None = None, extra: dict | None = None) -> dict:
    """Build the thin Step Functions handoff payload from a full agent output.

    Carries only what downstream needs without re-reading S3: the job_id (which the
    pause states extract as ``$.job_id`` and the next agent uses to rehydrate),
    tenant_id, an optional status label, any transient fields, plus any explicit
    ``extra`` scalars (small summary values used by status-label builders).
    """
    data = output if isinstance(output, dict) else output.model_dump(mode="json")
    env: dict = {"job_id": data.get("job_id"), "tenant_id": data.get("tenant_id")}
    if status:
        env["status"] = status
    for k in _TRANSIENT_KEYS:
        if data.get(k) is not None:
            env[k] = data[k]
    if extra:
        env.update(extra)
    return env


def hydrate_input(
    event: dict, artifact: str, model: Type[T], *, discriminator: str
) -> T:
    """Validate the predecessor agent's full output as ``model``.

    If ``event`` already carries the full payload (it contains ``discriminator``),
    validate it directly — this keeps direct/local invocation, agent_runner, and
    any legacy fat-payload caller working unchanged. Otherwise treat ``event`` as a
    slim envelope and load the full output from S3 by ``job_id``, re-attaching any
    transient fields the envelope carried.
    """
    if discriminator in event:
        return model.model_validate(event)

    job_id = event.get("job_id")
    if not job_id:
        # No pointer and no payload — let pydantic raise a clear validation error.
        return model.model_validate(event)

    data = job_load(job_id, artifact)
    for k in _TRANSIENT_KEYS:
        if event.get(k) is not None:
            data[k] = event[k]
    return model.model_validate(data)
