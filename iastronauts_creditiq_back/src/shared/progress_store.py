"""
progress_store.py — Local-dev step-progress registry.

Agents call emit() to report their current internal step.
local_server.py registers callbacks that write the enriched progress to status.json.

In production Lambda, no callback is ever registered so emit() is always a no-op.
"""
import threading

_CALLBACKS: dict[str, callable] = {}
_LOCK = threading.Lock()

# ── Agent metadata (single source of truth for both backend and frontend) ──────

AGENT_DEFS = [
    {"index": 1, "label": "Agent 1", "title": "Document Ingestion & OCR",  "detail": "Textract PDF · pandas Excel"},
    {"index": 2, "label": "Agent 2", "title": "Financial Analysis & NIIF", "detail": "Account classification · variance"},
    {"index": 3, "label": "Agent 3", "title": "Risk Scoring",              "detail": "Materiality · anomaly detection"},
    {"index": 4, "label": "Agent 4", "title": "Report Generation",         "detail": "LLM narrative · .docx fill"},
    {"index": 5, "label": "Agent 5", "title": "Intelligent Review",         "detail": "6-category validation · QA score"},
]


def build_progress(
    running_agent: int | None,
    current_step: str | None,
    done_agents: list[int],
    done_steps: dict[int, str] | None = None,
    failed_agent: int | None = None,
) -> dict:
    """
    Build the `progress` block stored inside status.json.

    Args:
        running_agent:  1-based index of the currently active agent, or None.
        current_step:   Human-readable sub-step label for the running agent.
        done_agents:    List of 1-based indices of completed agents.
        done_steps:     Optional summary step label per done agent (keyed by index).
        failed_agent:   1-based index of a failed agent, or None.
    """
    agents = []
    for defn in AGENT_DEFS:
        idx = defn["index"]
        if idx == failed_agent:
            state, step = "failed", None
        elif idx in done_agents:
            state = "done"
            step = (done_steps or {}).get(idx)
        elif idx == running_agent:
            state, step = "running", current_step
        else:
            state, step = "pending", None
        agents.append({**defn, "state": state, "step": step})

    return {
        "current_agent": running_agent,
        "current_step": current_step,
        "agents": agents,
    }


# ── Callback registry ──────────────────────────────────────────────────────────

def register(job_id: str, callback) -> None:
    """Register a step callback for a running job (call before starting the agent)."""
    with _LOCK:
        _CALLBACKS[job_id] = callback


def unregister(job_id: str) -> None:
    """Remove the callback when the agent finishes or fails."""
    with _LOCK:
        _CALLBACKS.pop(job_id, None)


def emit(job_id: str, step: str) -> None:
    """
    Emit a step update from inside an agent.
    Safe to call in any context — silently no-ops if no callback is registered.
    """
    with _LOCK:
        cb = _CALLBACKS.get(job_id)
    if cb:
        try:
            cb(step)
        except Exception:
            pass
