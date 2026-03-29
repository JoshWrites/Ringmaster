"""Status and observability route handlers — health, system state, GPUs, models.

These endpoints provide different levels of observability:
  - /health: a minimal liveness probe that external monitors can call without
    authentication (load balancers, uptime monitors, etc. don't have tokens).
  - /status: a richer snapshot of current system state for dashboards.
  - /gpus: the statically configured GPU list (dynamic runtime status is out of
    scope for this module; that requires GPU detection which is handled elsewhere).
  - /models: the list of models currently loaded in Ollama.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends

from ringmaster.config import RingmasterConfig
from ringmaster.models import HealthResponse, StatusResponse
from ringmaster.scheduler import Scheduler
from ringmaster.server.deps import get_config, get_scheduler

router = APIRouter(tags=["status"])

# Record the module import time as the process start reference.
# This is a reasonable proxy for uptime — it is set when the app module loads,
# which happens at startup, not at request time.
_start_time: float = time.monotonic()

# Package version — read once at import time to avoid repeated importlib calls.
try:
    from importlib.metadata import version as _pkg_version
    _VERSION: str = _pkg_version("ringmaster")
except Exception:
    _VERSION = "unknown"


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Liveness probe: return alive=True plus version and uptime.

    This endpoint intentionally skips authentication (see app.py middleware)
    so external monitors (e.g. uptime-kuma, load balancers) can probe it
    without needing a bearer token.
    """
    uptime = time.monotonic() - _start_time
    return HealthResponse(alive=True, version=_VERSION, uptime_seconds=uptime)


@router.get("/status", response_model=StatusResponse)
def status(
    scheduler: Scheduler = Depends(get_scheduler),
) -> StatusResponse:
    """Return a high-level snapshot of current system state.

    The 'state' field summarises what the system is doing right now:
      - 'idle': no tasks running, no user activity, queue dispatch active.
      - 'user_active': user is at the workstation (best-effort; always False
        in the current implementation — full idle detection is in power_presence).
      - 'ai_working': a task is currently being dispatched to Ollama.
      - 'paused': the scheduler is paused or draining.

    User presence detection (via D-Bus / xprintidle) is handled by the
    power_presence module and is not wired into the HTTP API yet.  Until that
    integration exists, user_present is always False.
    """
    current_task_id = scheduler.current_task_id
    queue_depth = scheduler.queue_depth()
    is_paused = scheduler.is_paused

    if is_paused:
        state = "paused"
    elif current_task_id is not None:
        state = "ai_working"
    else:
        state = "idle"

    return StatusResponse(
        state=state,
        queue_depth=queue_depth,
        current_task=current_task_id,
        user_present=False,  # Full idle detection not yet integrated into HTTP layer.
        queue_paused=is_paused,
    )


@router.get("/gpus")
def list_gpus(config: RingmasterConfig = Depends(get_config)) -> list[dict[str, Any]]:
    """Return the list of GPUs defined in the static configuration.

    This returns config-level GPU metadata (label, role, VRAM, fingerprint),
    not real-time runtime status.  Runtime GPU monitoring is out of scope for
    this endpoint.
    """
    return [gpu.model_dump() for gpu in config.gpus]


@router.get("/models")
def list_models(config: RingmasterConfig = Depends(get_config)) -> dict[str, Any]:
    """Return the list of models available in the configured Ollama instance.

    Makes a synchronous HTTP call to Ollama's /api/tags endpoint.  If Ollama
    is unreachable (e.g. the GPU workstation is sleeping), returns an empty list
    with an error field rather than raising a 500 — callers should treat this
    endpoint as best-effort.
    """
    import httpx

    try:
        resp = httpx.get(f"{config.ollama.host}/api/tags", timeout=5)
        resp.raise_for_status()
        return {"models": resp.json().get("models", []), "error": None}
    except Exception as exc:
        return {"models": [], "error": str(exc)}
