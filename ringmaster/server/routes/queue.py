"""Queue lifecycle route handlers — pause, resume, and drain.

These endpoints give operators direct control over the scheduler's dispatch
loop without restarting the process.  They are useful for:
  - Maintenance windows (pause → do work → resume)
  - Planned power events (drain → wait for in-flight task → workstation sleeps)

All three are synchronous because the Scheduler's flag operations are
in-memory and complete instantaneously.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ringmaster.scheduler import Scheduler
from ringmaster.server.deps import get_scheduler

router = APIRouter(prefix="/queue", tags=["queue"])


@router.post("/pause")
def pause_queue(scheduler: Scheduler = Depends(get_scheduler)) -> dict:
    """Stop dispatching new tasks immediately.

    Any task currently running continues to completion; only new dispatch is
    halted.  Call POST /queue/resume to restart dispatch.
    """
    scheduler.pause()
    return {"queue_paused": True}


@router.post("/resume")
def resume_queue(scheduler: Scheduler = Depends(get_scheduler)) -> dict:
    """Resume task dispatch after a pause or drain.

    Clears both the paused and draining flags so the scheduler returns to
    normal operation.
    """
    scheduler.resume()
    return {"queue_paused": False}


@router.post("/drain")
def drain_queue(scheduler: Scheduler = Depends(get_scheduler)) -> dict:
    """Gracefully quiesce the scheduler before a planned power event.

    If no task is running, the scheduler pauses immediately.  If a task is
    running, the drain is deferred until that task completes — on_task_completed()
    will trigger the pause so no task is interrupted mid-execution.
    """
    scheduler.drain()
    return {"draining": True}
