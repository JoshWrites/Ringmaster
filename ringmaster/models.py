"""Pydantic models for the Ringmaster REST API.

These models serve three purposes:
  1. Request validation — FastAPI uses them to parse and reject bad input before
     it reaches any business logic.
  2. Response serialisation — FastAPI serialises them to JSON for all API
     responses, so the schema is always consistent with the model definition.
  3. Documentation — FastAPI generates OpenAPI schemas from these models
     automatically, so every field description appears in the /docs UI.

Design notes:
  - All timestamp fields are ISO 8601 strings rather than datetime objects.
    This keeps the models storage-agnostic (SQLite stores ISO strings natively)
    and avoids timezone-awareness surprises when serialising to JSON.
  - Mutable defaults (dict, list) always use default_factory to avoid the
    classic Python shared-mutable-default bug.
  - Optional fields that are genuinely absent are represented as None, not as
    empty strings, so callers can use a simple truthiness check.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Task models
# ---------------------------------------------------------------------------


class TaskSubmitRequest(BaseModel):
    """Body of POST /tasks — submit a new background task to the queue.

    Only task_type, model, and client_id are required.  Everything else has a
    sensible default that callers can override for fine-grained control.
    """

    task_type: str = Field(
        description=(
            "Kind of task to run, e.g. 'generate', 'embed', 'image_generation'. "
            "Must match a task type understood by the worker."
        ),
    )
    model: str = Field(
        description="Ollama model tag to use for this task, e.g. 'llama3:8b'.",
    )
    client_id: str = Field(
        description="Opaque identifier for the API client submitting this task.",
    )
    prompt: str | None = Field(
        default=None,
        description="Input text for generation/embedding tasks.",
    )
    priority: int | None = Field(
        default=None,
        description=(
            "Queue priority, 1 (highest) to 5 (lowest). "
            "Defaults to the value of queue.default_priority in config."
        ),
    )
    deadline: str | None = Field(
        default=None,
        description=(
            "ISO 8601 UTC timestamp by which the task should start running. "
            "Tasks with a deadline are dequeued ahead of tasks without one."
        ),
    )
    callback_url: str | None = Field(
        default=None,
        description=(
            "URL to POST a WebhookPayload to when the task completes or fails. "
            "Omit if the caller will poll GET /tasks/{id} instead."
        ),
    )
    unattended_policy: str = Field(
        default="run",
        description=(
            "What to do when the task is ready to run but the user is present. "
            "One of 'run' (start immediately), 'defer' (wait for idle), "
            "or 'notify' (send a desktop notification and wait for approval)."
        ),
    )
    session_idle_timeout_seconds: int | None = Field(
        default=None,
        description=(
            "Override the default session idle timeout for this task's session. "
            "Only relevant for session-backed tasks."
        ),
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Arbitrary key-value pairs the client wants stored alongside the task. "
            "Ringmaster does not interpret these — they are echoed back in responses."
        ),
    )


class TaskResponse(BaseModel):
    """Representation of a task returned by GET /tasks/{id} and related endpoints.

    Fields that are not yet applicable (e.g. result before completion) are
    returned as null rather than being omitted, so callers can always rely on
    the same schema regardless of task state.
    """

    id: str = Field(description="Unique task identifier (UUID4).")
    task_type: str = Field(description="Kind of task, e.g. 'generate'.")
    model: str = Field(description="Ollama model tag used for this task.")
    priority: int = Field(description="Queue priority, 1–5.")
    status: str = Field(
        description=(
            "Current lifecycle state. One of: 'queued', 'running', "
            "'completed', 'failed', 'cancelled'."
        ),
    )
    client_id: str = Field(description="Client that submitted this task.")
    submitted_at: str = Field(description="ISO 8601 UTC timestamp when the task was submitted.")
    started_at: str | None = Field(
        default=None,
        description="ISO 8601 UTC timestamp when the worker picked up the task.",
    )
    completed_at: str | None = Field(
        default=None,
        description="ISO 8601 UTC timestamp when the task finished (success or failure).",
    )
    deadline: str | None = Field(
        default=None,
        description="ISO 8601 UTC deadline requested by the client.",
    )
    prompt: str | None = Field(
        default=None,
        description="Input prompt, echoed back for traceability.",
    )
    result: str | None = Field(
        default=None,
        description="Task output text on success; null otherwise.",
    )
    error: str | None = Field(
        default=None,
        description="Error message on failure; null otherwise.",
    )
    gpu_used: str | None = Field(
        default=None,
        description="Label of the GPU that executed this task.",
    )
    duration_seconds: float | None = Field(
        default=None,
        description="Wall-clock seconds from task start to completion.",
    )
    callback_url: str | None = Field(
        default=None,
        description="Webhook URL that was notified on completion.",
    )
    unattended_policy: str = Field(
        default="run",
        description="Unattended policy that was applied to this task.",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Client-supplied metadata, stored and echoed back unchanged.",
    )


# ---------------------------------------------------------------------------
# Session models
# ---------------------------------------------------------------------------


class SessionOpenRequest(BaseModel):
    """Body of POST /sessions — open a new interactive inference session.

    Sessions let a client reserve a GPU for a sequence of generate calls
    without paying the model-load latency on each request.
    """

    model: str = Field(description="Ollama model tag to pre-load for this session.")
    client_id: str = Field(description="Opaque identifier for the client opening this session.")
    priority: int | None = Field(
        default=None,
        description="Queue priority for the session reservation request.",
    )
    session_idle_timeout_seconds: int = Field(
        default=600,
        description=(
            "Seconds of inactivity after which the session is automatically closed "
            "and the GPU reservation released.  Ten minutes is the default because "
            "it balances resource utilisation against interactive responsiveness."
        ),
    )
    callback_url: str | None = Field(
        default=None,
        description="URL to notify when the session is closed.",
    )
    unattended_policy: str = Field(
        default="run",
        description="Unattended policy applied to generate requests on this session.",
    )


class SessionResponse(BaseModel):
    """Representation of a session returned by GET /sessions/{id}."""

    id: str = Field(description="Unique session identifier (UUID4).")
    client_id: str = Field(description="Client that owns this session.")
    model: str = Field(description="Model loaded for this session.")
    status: str = Field(
        description="Session lifecycle state. One of: 'open', 'closed', 'expired'.",
    )
    opened_at: str = Field(description="ISO 8601 UTC timestamp when the session was created.")
    last_activity_at: str | None = Field(
        default=None,
        description="ISO 8601 UTC timestamp of the last generate request on this session.",
    )
    idle_timeout_seconds: int = Field(
        description="Inactivity timeout in seconds after which the session auto-closes.",
    )
    gpu_label: str | None = Field(
        default=None,
        description="Label of the GPU reserved for this session; null if not yet assigned.",
    )


class SessionGenerateRequest(BaseModel):
    """Body of POST /sessions/{id}/generate — run a single generate within a session."""

    prompt: str = Field(description="Input text to pass to the model.")
    stream: bool = Field(
        default=False,
        description=(
            "If True, the response is streamed as Server-Sent Events. "
            "If False, the full response is returned in one JSON body."
        ),
    )


# ---------------------------------------------------------------------------
# System status models
# ---------------------------------------------------------------------------


class StatusResponse(BaseModel):
    """Response body for GET /status — current system state at a glance."""

    state: str = Field(
        description=(
            "High-level workstation state. "
            "One of: 'idle', 'busy', 'paused', 'sleeping'."
        ),
    )
    queue_depth: int = Field(description="Number of tasks currently waiting in the queue.")
    current_task: str | None = Field(
        default=None,
        description="ID of the task currently being executed; null when idle.",
    )
    user_present: bool = Field(
        description="True if the user is actively using the workstation right now.",
    )
    queue_paused: bool = Field(
        description="True if new tasks are being accepted but not dispatched.",
    )


class HealthResponse(BaseModel):
    """Response body for GET /health — liveness probe for external monitors."""

    alive: bool = Field(
        default=True,
        description="Always True while the process is running and accepting requests.",
    )
    version: str = Field(
        default="",
        description="Ringmaster version string, e.g. '0.1.0'.",
    )
    uptime_seconds: float = Field(
        default=0.0,
        description="Seconds since the Ringmaster process started.",
    )


class GpuStatusResponse(BaseModel):
    """Status of a single GPU as reported by GET /gpus or GET /gpus/{label}."""

    label: str = Field(description="Human-readable GPU label from config.")
    role: str = Field(
        description="Configured role for this GPU: 'compute', 'gaming', or 'both'.",
    )
    vram_mb: int = Field(description="Total VRAM in mebibytes as detected at runtime.")
    current_model: str | None = Field(
        default=None,
        description="Model currently loaded in this GPU's VRAM; null when idle.",
    )
    status: str = Field(
        description="GPU availability state. One of: 'idle', 'busy', 'unavailable'.",
    )


# ---------------------------------------------------------------------------
# Webhook model
# ---------------------------------------------------------------------------


class WebhookPayload(BaseModel):
    """Payload POSTed to a task's callback_url when the task finishes.

    Clients should treat unknown fields as forwards-compatible additions and
    ignore them rather than failing.
    """

    task_id: str = Field(description="ID of the task this notification is for.")
    status: str = Field(
        description="Terminal status of the task: 'completed' or 'failed'.",
    )
    result: str | None = Field(
        default=None,
        description="Task output on success; null on failure.",
    )
    error: str | None = Field(
        default=None,
        description="Error message on failure; null on success.",
    )
    model: str | None = Field(
        default=None,
        description="Ollama model that executed the task.",
    )
    gpu_used: str | None = Field(
        default=None,
        description="GPU label that executed the task.",
    )
    duration_seconds: float | None = Field(
        default=None,
        description="Wall-clock seconds from task start to completion.",
    )
    completed_at: str | None = Field(
        default=None,
        description="ISO 8601 UTC timestamp when the task finished.",
    )


# ---------------------------------------------------------------------------
# Power management models
# ---------------------------------------------------------------------------


class SleepDeferredResponse(BaseModel):
    """Response to a sleep request when a task is currently running.

    When the scheduler receives a sleep request but cannot honour it (because
    a task is mid-flight), it returns this model with an optional ETA so the
    caller knows when to retry.
    """

    sleep: str = Field(
        default="deferred",
        description=(
            "Always 'deferred' for this response type, "
            "so callers can distinguish it from a success response."
        ),
    )
    reason: str = Field(
        default="task_running",
        description="Machine-readable reason the sleep was deferred.",
    )
    est_completion: str | None = Field(
        default=None,
        description=(
            "ISO 8601 UTC timestamp when the blocking task is expected to finish. "
            "Null if no estimate is available."
        ),
    )
