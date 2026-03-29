"""Tests for Ringmaster Pydantic API models.

These tests verify that models enforce their field contracts, apply correct
defaults, and accept/reject the right inputs.  We test defaults separately
from field presence so that accidental default-value changes surface clearly.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from ringmaster.models import (
    GpuStatusResponse,
    HealthResponse,
    SessionGenerateRequest,
    SessionOpenRequest,
    SessionResponse,
    SleepDeferredResponse,
    StatusResponse,
    TaskResponse,
    TaskSubmitRequest,
    WebhookPayload,
)


# ---------------------------------------------------------------------------
# TaskSubmitRequest
# ---------------------------------------------------------------------------


class TestTaskSubmitRequest:
    def test_minimal_required_fields(self):
        """A request with only the required fields should succeed."""
        req = TaskSubmitRequest(task_type="generate", model="llama3", client_id="client-1")
        assert req.task_type == "generate"
        assert req.model == "llama3"
        assert req.client_id == "client-1"

    def test_task_submit_request_defaults(self):
        """Verify that unset optional fields have the documented defaults."""
        req = TaskSubmitRequest(task_type="generate", model="llama3", client_id="client-1")
        assert req.priority is None
        assert req.deadline is None
        assert req.prompt is None
        assert req.callback_url is None
        assert req.unattended_policy == "run"
        assert req.session_idle_timeout_seconds is None
        assert req.metadata == {}

    def test_explicit_priority_and_metadata(self):
        """Explicitly provided optional fields should be stored as given."""
        req = TaskSubmitRequest(
            task_type="embed",
            model="nomic-embed-text",
            client_id="client-2",
            priority=1,
            metadata={"source": "ci"},
        )
        assert req.priority == 1
        assert req.metadata == {"source": "ci"}

    def test_missing_required_field_raises(self):
        """Omitting a required field must raise a ValidationError."""
        with pytest.raises(ValidationError):
            TaskSubmitRequest(task_type="generate", model="llama3")  # missing client_id


# ---------------------------------------------------------------------------
# TaskResponse
# ---------------------------------------------------------------------------


class TestTaskResponse:
    def test_task_response_model(self):
        """TaskResponse should accept a full set of fields without error."""
        now = datetime.now(timezone.utc).isoformat()
        resp = TaskResponse(
            id="task-abc",
            task_type="generate",
            model="llama3",
            priority=3,
            status="queued",
            client_id="client-1",
            submitted_at=now,
        )
        assert resp.id == "task-abc"
        assert resp.status == "queued"
        # Optional fields default to None
        assert resp.started_at is None
        assert resp.completed_at is None
        assert resp.result is None
        assert resp.error is None
        assert resp.gpu_used is None
        assert resp.duration_seconds is None
        assert resp.callback_url is None
        assert resp.prompt is None
        assert resp.deadline is None
        assert resp.unattended_policy == "run"
        assert resp.metadata == {}

    def test_task_response_with_result(self):
        """A completed task response should carry result and timing fields."""
        now = datetime.now(timezone.utc).isoformat()
        resp = TaskResponse(
            id="task-xyz",
            task_type="generate",
            model="llama3",
            priority=2,
            status="completed",
            client_id="client-1",
            submitted_at=now,
            started_at=now,
            completed_at=now,
            result="Hello, world!",
            gpu_used="rtx4090",
            duration_seconds=1.23,
        )
        assert resp.result == "Hello, world!"
        assert resp.duration_seconds == 1.23
        assert resp.gpu_used == "rtx4090"


# ---------------------------------------------------------------------------
# SessionOpenRequest
# ---------------------------------------------------------------------------


class TestSessionOpenRequest:
    def test_minimal_session_open_request(self):
        """Only model and client_id are required for opening a session."""
        req = SessionOpenRequest(model="llama3", client_id="client-1")
        assert req.model == "llama3"
        assert req.client_id == "client-1"

    def test_session_open_request_defaults(self):
        """Check that session defaults match the API contract."""
        req = SessionOpenRequest(model="llama3", client_id="client-1")
        assert req.priority is None
        assert req.session_idle_timeout_seconds == 600
        assert req.callback_url is None
        assert req.unattended_policy == "run"


# ---------------------------------------------------------------------------
# SessionResponse
# ---------------------------------------------------------------------------


class TestSessionResponse:
    def test_session_response_model(self):
        """SessionResponse should accept all documented fields."""
        now = datetime.now(timezone.utc).isoformat()
        resp = SessionResponse(
            id="sess-1",
            client_id="client-1",
            model="llama3",
            status="open",
            opened_at=now,
            idle_timeout_seconds=600,
        )
        assert resp.id == "sess-1"
        assert resp.status == "open"
        assert resp.last_activity_at is None
        assert resp.gpu_label is None

    def test_session_response_with_gpu(self):
        """A session assigned to a GPU should carry the gpu_label."""
        now = datetime.now(timezone.utc).isoformat()
        resp = SessionResponse(
            id="sess-2",
            client_id="client-1",
            model="llama3",
            status="open",
            opened_at=now,
            idle_timeout_seconds=600,
            gpu_label="rtx4090",
            last_activity_at=now,
        )
        assert resp.gpu_label == "rtx4090"


# ---------------------------------------------------------------------------
# SessionGenerateRequest
# ---------------------------------------------------------------------------


class TestSessionGenerateRequest:
    def test_minimal_generate_request(self):
        """prompt is the only required field; stream defaults to False."""
        req = SessionGenerateRequest(prompt="Hello")
        assert req.prompt == "Hello"
        assert req.stream is False

    def test_stream_flag(self):
        """stream=True should be accepted."""
        req = SessionGenerateRequest(prompt="Hello", stream=True)
        assert req.stream is True


# ---------------------------------------------------------------------------
# StatusResponse
# ---------------------------------------------------------------------------


class TestStatusResponse:
    def test_status_response_model(self):
        """StatusResponse carries system state and queue depth."""
        resp = StatusResponse(
            state="idle",
            queue_depth=0,
            current_task=None,
            user_present=True,
            queue_paused=False,
        )
        assert resp.state == "idle"
        assert resp.queue_depth == 0
        assert resp.user_present is True
        assert resp.queue_paused is False
        assert resp.current_task is None

    def test_status_response_with_task(self):
        """current_task accepts a string task ID."""
        resp = StatusResponse(
            state="busy",
            queue_depth=3,
            current_task="task-abc",
            user_present=False,
            queue_paused=False,
        )
        assert resp.current_task == "task-abc"


# ---------------------------------------------------------------------------
# HealthResponse
# ---------------------------------------------------------------------------


class TestHealthResponse:
    def test_health_response_defaults(self):
        """HealthResponse should work with all defaults (zero-arg construction)."""
        resp = HealthResponse()
        assert resp.alive is True
        assert resp.version == ""
        assert resp.uptime_seconds == 0.0

    def test_health_response_with_values(self):
        """Explicit values override defaults correctly."""
        resp = HealthResponse(alive=True, version="0.1.0", uptime_seconds=3600.5)
        assert resp.version == "0.1.0"
        assert resp.uptime_seconds == 3600.5


# ---------------------------------------------------------------------------
# GpuStatusResponse
# ---------------------------------------------------------------------------


class TestGpuStatusResponse:
    def test_gpu_status_response(self):
        """GpuStatusResponse requires label, role, vram_mb, and status."""
        resp = GpuStatusResponse(
            label="rtx4090",
            role="compute",
            vram_mb=24576,
            status="idle",
        )
        assert resp.label == "rtx4090"
        assert resp.vram_mb == 24576
        assert resp.current_model is None

    def test_gpu_status_response_with_loaded_model(self):
        """current_model is populated when a model is loaded on the GPU."""
        resp = GpuStatusResponse(
            label="rtx4090",
            role="compute",
            vram_mb=24576,
            current_model="llama3",
            status="busy",
        )
        assert resp.current_model == "llama3"
        assert resp.status == "busy"


# ---------------------------------------------------------------------------
# WebhookPayload
# ---------------------------------------------------------------------------


class TestWebhookPayload:
    def test_webhook_payload_model(self):
        """A minimal webhook payload needs only task_id and status."""
        now = datetime.now(timezone.utc).isoformat()
        payload = WebhookPayload(task_id="task-1", status="completed", completed_at=now)
        assert payload.task_id == "task-1"
        assert payload.status == "completed"
        assert payload.result is None
        assert payload.error is None

    def test_webhook_payload_with_error(self):
        """A failed task payload should carry the error string."""
        payload = WebhookPayload(task_id="task-2", status="failed", error="OOM")
        assert payload.status == "failed"
        assert payload.error == "OOM"

    def test_webhook_payload_optional_fields(self):
        """All optional webhook fields can be omitted."""
        payload = WebhookPayload(task_id="task-3", status="queued")
        assert payload.result is None
        assert payload.model is None
        assert payload.gpu_used is None
        assert payload.duration_seconds is None
        assert payload.completed_at is None


# ---------------------------------------------------------------------------
# SleepDeferredResponse
# ---------------------------------------------------------------------------


class TestSleepDeferredResponse:
    def test_sleep_deferred_defaults(self):
        """Default fields should match the documented API contract."""
        resp = SleepDeferredResponse()
        assert resp.sleep == "deferred"
        assert resp.reason == "task_running"
        assert resp.est_completion is None

    def test_sleep_deferred_with_estimate(self):
        """est_completion accepts an ISO 8601 timestamp string."""
        now = datetime.now(timezone.utc).isoformat()
        resp = SleepDeferredResponse(est_completion=now)
        assert resp.est_completion == now
