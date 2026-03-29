"""Route modules for the Ringmaster HTTP API.

Each sub-module owns a single APIRouter with a cohesive set of endpoints.
The routers are registered onto the main FastAPI app in ``app.py``.

Route modules:
  - tasks.py   — submit, list, retrieve, approve, defer, cancel tasks
  - sessions.py — open, retrieve, keepalive, close interactive sessions
  - queue.py   — pause, resume, drain the scheduler
  - status.py  — health probe, system status, GPU list, model list
  - auth.py    — register and revoke API client tokens
"""
