# Ringmaster Server + CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Ringmaster server daemon and CLI — a workstation-resident service that accepts AI inference tasks from network clients, manages GPU/Ollama resources, negotiates with the local user, and handles power management.

**Architecture:** FastAPI REST API backed by SQLite for the task queue. GPU discovery via `rocm-smi` (abstracted for future CUDA support). User presence via D-Bus/systemd-logind. Sleep inhibition via systemd inhibitor locks. Desktop notifications via `dbus-next`. HA push notifications via REST. CLI is a thin `click`-based wrapper over the same REST API.

**Tech Stack:** Python 3.11+, FastAPI, uvicorn, SQLite (aiosqlite), click, httpx, pydantic, dbus-next, PyYAML

**Spec:** `docs/specs/2026-03-29-ringmaster-design.md`

**Scope:** Phase 1 server + CLI only. Client app (Ollama proxy, tray, web UI) is a separate plan.

---

## File Structure

```
Ringmaster/
├── pyproject.toml                      # Package metadata, dependencies, entry points
├── ringmaster/
│   ├── __init__.py                     # Version string
│   ├── config.py                       # YAML config loader + defaults + pydantic models
│   ├── db.py                           # SQLite schema, migrations, connection helper
│   ├── models.py                       # Pydantic models for tasks, sessions, GPUs, API responses
│   ├── gpu/
│   │   ├── __init__.py
│   │   ├── detect.py                   # GPU detection abstraction (rocm provider)
│   │   └── fingerprint.py              # Fingerprint matching logic
│   ├── server/
│   │   ├── __init__.py
│   │   ├── app.py                      # FastAPI app factory
│   │   ├── auth.py                     # Token auth middleware + registration
│   │   ├── routes/
│   │   │   ├── __init__.py
│   │   │   ├── tasks.py                # /tasks endpoints
│   │   │   ├── sessions.py             # /sessions endpoints
│   │   │   ├── queue.py                # /queue endpoints (pause/resume/drain)
│   │   │   ├── status.py               # /status, /health, /gpus, /models
│   │   │   └── auth.py                 # /auth endpoints
│   │   └── deps.py                     # FastAPI dependency injection (db, config, scheduler)
│   ├── scheduler.py                    # Queue scheduler: picks next task, manages state machine
│   ├── ollama.py                       # Ollama HTTP client: load model, generate, unload
│   ├── worker.py                       # Background worker: runs tasks, fires webhooks
│   ├── webhooks.py                     # Webhook delivery with retry
│   ├── power/
│   │   ├── __init__.py
│   │   ├── inhibitor.py                # systemd sleep/shutdown inhibitor lock
│   │   ├── presence.py                 # User presence detection (D-Bus idle monitoring)
│   │   └── actions.py                  # Sleep, lock, display off commands
│   ├── notifications/
│   │   ├── __init__.py
│   │   ├── base.py                     # Notification provider interface
│   │   ├── desktop.py                  # D-Bus desktop notifications
│   │   └── homeassistant.py            # HA push notification provider
│   └── cli/
│       ├── __init__.py
│       └── main.py                     # Click CLI commands
├── tests/
│   ├── conftest.py                     # Shared fixtures (test db, test config, test client)
│   ├── test_config.py
│   ├── test_db.py
│   ├── test_models.py
│   ├── test_gpu_detect.py
│   ├── test_gpu_fingerprint.py
│   ├── test_scheduler.py
│   ├── test_ollama.py
│   ├── test_worker.py
│   ├── test_webhooks.py
│   ├── test_power_inhibitor.py
│   ├── test_power_presence.py
│   ├── test_power_actions.py
│   ├── test_notifications_desktop.py
│   ├── test_notifications_ha.py
│   ├── test_auth.py
│   ├── test_routes_tasks.py
│   ├── test_routes_sessions.py
│   ├── test_routes_queue.py
│   ├── test_routes_status.py
│   ├── test_routes_auth.py
│   └── test_cli.py
└── ringmaster.example.yaml             # Example config for users
```

---

### Task 1: Project Scaffold + Config

**Files:**
- Create: `pyproject.toml`
- Create: `ringmaster/__init__.py`
- Create: `ringmaster/config.py`
- Create: `ringmaster.example.yaml`
- Create: `tests/conftest.py`
- Create: `tests/test_config.py`

- [ ] **Step 1: Write the failing test for config loading**

```python
# tests/test_config.py
import pytest
from pathlib import Path


def test_load_config_from_file(tmp_path):
    config_file = tmp_path / "ringmaster.yaml"
    config_file.write_text("""
server:
  host: "127.0.0.1"
  port: 9999
ollama:
  host: "http://localhost:11434"
queue:
  max_queue_depth: 50
  default_priority: 2
  session_idle_timeout_seconds: 300
""")
    from ringmaster.config import load_config

    cfg = load_config(config_file)
    assert cfg.server.host == "127.0.0.1"
    assert cfg.server.port == 9999
    assert cfg.ollama.host == "http://localhost:11434"
    assert cfg.queue.max_queue_depth == 50


def test_load_config_defaults(tmp_path):
    config_file = tmp_path / "ringmaster.yaml"
    config_file.write_text("server:\n  port: 8420\n")
    from ringmaster.config import load_config

    cfg = load_config(config_file)
    assert cfg.server.host == "0.0.0.0"
    assert cfg.server.port == 8420
    assert cfg.queue.max_queue_depth == 100
    assert cfg.queue.default_priority == 3
    assert cfg.queue.session_idle_timeout_seconds == 600
    assert cfg.idle.idle_threshold_seconds == 300
    assert cfg.idle.auto_approve_when_idle is True
    assert cfg.idle.auto_approve_timeout_seconds == 60


def test_load_config_missing_file():
    from ringmaster.config import load_config

    with pytest.raises(FileNotFoundError):
        load_config(Path("/nonexistent/ringmaster.yaml"))


def test_gpu_config_round_trip(tmp_path):
    config_file = tmp_path / "ringmaster.yaml"
    config_file.write_text("""
server:
  port: 8420
gpus:
  - label: "primary"
    role: "compute"
    prefer_for: ["large_models", "default"]
    fingerprint:
      vendor: "AMD"
      model: "Radeon RX 7900 XTX"
      vram_mb: 24576
      device_id: "0x744c"
""")
    from ringmaster.config import load_config

    cfg = load_config(config_file)
    assert len(cfg.gpus) == 1
    assert cfg.gpus[0].label == "primary"
    assert cfg.gpus[0].role == "compute"
    assert cfg.gpus[0].fingerprint.vendor == "AMD"
    assert cfg.gpus[0].fingerprint.vram_mb == 24576
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/levine/Documents/Repos/Ringmaster && python -m pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ringmaster'`

- [ ] **Step 3: Create pyproject.toml**

```toml
# pyproject.toml
[build-system]
requires = ["setuptools>=68.0", "wheel"]
build-backend = "setuptools.backends._legacy:_Backend"

[project]
name = "ringmaster"
version = "0.1.0"
description = "GPU workstation AI task orchestrator for home networks"
readme = "README.md"
requires-python = ">=3.11"
license = {text = "MIT"}
dependencies = [
    "fastapi>=0.110",
    "uvicorn[standard]>=0.29",
    "aiosqlite>=0.20",
    "httpx>=0.27",
    "pydantic>=2.7",
    "pyyaml>=6.0",
    "click>=8.1",
    "dbus-next>=0.2.3",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "pytest-httpx>=0.30",
    "ruff>=0.4",
]

[project.scripts]
ringmaster = "ringmaster.cli.main:cli"

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.ruff]
target-version = "py311"
line-length = 100
```

- [ ] **Step 4: Create ringmaster/__init__.py**

```python
# ringmaster/__init__.py
__version__ = "0.1.0"
```

- [ ] **Step 5: Implement config.py**

```python
# ringmaster/config.py
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8420


class GpuFingerprint(BaseModel):
    vendor: str = ""
    model: str = ""
    vram_mb: int = 0
    serial: str = ""
    device_id: str = ""


class GpuConfig(BaseModel):
    label: str
    role: str = "compute"
    prefer_for: list[str] = Field(default_factory=list)
    fingerprint: GpuFingerprint = Field(default_factory=GpuFingerprint)


class OllamaConfig(BaseModel):
    host: str = "http://localhost:11434"


class NotificationsConfig(BaseModel):
    backend: str = "desktop"
    config: dict[str, Any] = Field(default_factory=dict)
    fallback_backend: str = "desktop"


class PowerConfig(BaseModel):
    wake_method: str = "none"
    sleep_command: str = "systemctl suspend"
    display_off_command: str = "xset dpms force off"
    lock_command: str = "loginctl lock-session"
    gpu_compute_profile_command: str = ""


class IdleConfig(BaseModel):
    detection_method: str = "dbus"
    idle_threshold_seconds: int = 300
    auto_approve_when_idle: bool = True
    auto_approve_timeout_seconds: int = 60


class QueueConfig(BaseModel):
    max_queue_depth: int = 100
    default_priority: int = 3
    session_idle_timeout_seconds: int = 600


class AuthConfig(BaseModel):
    token_file: str = "tokens.json"


class RingmasterConfig(BaseModel):
    server: ServerConfig = Field(default_factory=ServerConfig)
    gpus: list[GpuConfig] = Field(default_factory=list)
    ollama: OllamaConfig = Field(default_factory=OllamaConfig)
    notifications: NotificationsConfig = Field(default_factory=NotificationsConfig)
    power: PowerConfig = Field(default_factory=PowerConfig)
    idle: IdleConfig = Field(default_factory=IdleConfig)
    queue: QueueConfig = Field(default_factory=QueueConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)


def load_config(path: Path) -> RingmasterConfig:
    """Load config from a YAML file. Raises FileNotFoundError if missing."""
    if not path.is_file():
        raise FileNotFoundError(f"Config file not found: {path}")
    with open(path) as f:
        raw = yaml.safe_load(f) or {}
    return RingmasterConfig.model_validate(raw)
```

- [ ] **Step 6: Create conftest.py with shared fixtures**

```python
# tests/conftest.py
from __future__ import annotations

import pytest
from pathlib import Path


@pytest.fixture
def sample_config_path(tmp_path: Path) -> Path:
    config_file = tmp_path / "ringmaster.yaml"
    config_file.write_text("""
server:
  host: "0.0.0.0"
  port: 8420
gpus:
  - label: "primary"
    role: "compute"
    prefer_for: ["large_models", "default"]
    fingerprint:
      vendor: "AMD"
      model: "Radeon RX 7900 XTX"
      vram_mb: 24576
      device_id: "0x744c"
ollama:
  host: "http://localhost:11434"
queue:
  max_queue_depth: 100
  default_priority: 3
  session_idle_timeout_seconds: 600
auth:
  token_file: "{tmp_path}/tokens.json"
""".replace("{tmp_path}", str(tmp_path)))
    return config_file
```

- [ ] **Step 7: Create the example config**

```yaml
# ringmaster.example.yaml
# Ringmaster configuration — copy to ringmaster.yaml and edit.

server:
  host: "0.0.0.0"
  port: 8420

# GPU inventory — run `ringmaster init` to populate fingerprints.
gpus:
  - label: "primary"
    role: "compute"           # compute, gaming, both
    prefer_for: ["large_models", "default"]
    fingerprint: {}           # populated by `ringmaster init`

ollama:
  host: "http://localhost:11434"

notifications:
  backend: "desktop"         # desktop, homeassistant, ntfy, pushover, matrix
  config: {}
  # Example for Home Assistant:
  #   backend: "homeassistant"
  #   config:
  #     ha_url: "http://ha.local:8123"
  #     ha_token_env: "HA_TOKEN"
  fallback_backend: "desktop"

power:
  wake_method: "none"         # wol, ipmi, smart_plug, none
  sleep_command: "systemctl suspend"
  display_off_command: "xset dpms force off"
  lock_command: "loginctl lock-session"
  gpu_compute_profile_command: ""

idle:
  detection_method: "dbus"    # dbus, xprintidle
  idle_threshold_seconds: 300
  auto_approve_when_idle: true
  auto_approve_timeout_seconds: 60

queue:
  max_queue_depth: 100
  default_priority: 3
  session_idle_timeout_seconds: 600

auth:
  token_file: "tokens.json"
```

- [ ] **Step 8: Install package and run tests**

Run: `cd /home/levine/Documents/Repos/Ringmaster && pip install -e ".[dev]" && python -m pytest tests/test_config.py -v`
Expected: All 4 tests PASS

- [ ] **Step 9: Commit**

```bash
cd /home/levine/Documents/Repos/Ringmaster
git add pyproject.toml ringmaster/__init__.py ringmaster/config.py ringmaster.example.yaml tests/conftest.py tests/test_config.py
git commit -m "feat: project scaffold with config loader and pydantic models"
```

---

### Task 2: Database Layer

**Files:**
- Create: `ringmaster/db.py`
- Create: `ringmaster/models.py`
- Create: `tests/test_db.py`
- Create: `tests/test_models.py`

- [ ] **Step 1: Write the failing test for DB schema creation**

```python
# tests/test_db.py
import pytest
import aiosqlite


@pytest.fixture
async def db_conn(tmp_path):
    from ringmaster.db import get_db, init_db

    db_path = tmp_path / "test.db"
    conn = await get_db(str(db_path))
    await init_db(conn)
    yield conn
    await conn.close()


async def test_init_db_creates_tables(db_conn):
    cursor = await db_conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    tables = [row[0] for row in await cursor.fetchall()]
    assert "tasks" in tables
    assert "sessions" in tables
    assert "clients" in tables
    assert "power_events" in tables


async def test_insert_and_fetch_task(db_conn):
    from ringmaster.db import insert_task, get_task

    task_id = await insert_task(
        db_conn,
        task_type="discrete",
        model="mistral-nemo:12b",
        prompt="test prompt",
        priority=1,
        client_id="test-client",
        callback_url="http://localhost:8080/callback",
        unattended_policy="run",
    )
    task = await get_task(db_conn, task_id)
    assert task is not None
    assert task["task_type"] == "discrete"
    assert task["model"] == "mistral-nemo:12b"
    assert task["status"] == "submitted"
    assert task["priority"] == 1


async def test_update_task_status(db_conn):
    from ringmaster.db import insert_task, update_task_status, get_task

    task_id = await insert_task(
        db_conn,
        task_type="discrete",
        model="test:7b",
        prompt="test",
        priority=2,
        client_id="test",
        callback_url=None,
        unattended_policy="run",
    )
    await update_task_status(db_conn, task_id, "queued")
    task = await get_task(db_conn, task_id)
    assert task["status"] == "queued"


async def test_list_tasks_by_status(db_conn):
    from ringmaster.db import insert_task, update_task_status, list_tasks

    id1 = await insert_task(db_conn, "discrete", "m:7b", "p1", 1, "c", None, "run")
    id2 = await insert_task(db_conn, "discrete", "m:7b", "p2", 2, "c", None, "run")
    await update_task_status(db_conn, id1, "queued")

    queued = await list_tasks(db_conn, status="queued")
    assert len(queued) == 1
    assert queued[0]["id"] == id1


async def test_insert_and_fetch_session(db_conn):
    from ringmaster.db import insert_session, get_session

    session_id = await insert_session(
        db_conn,
        client_id="anny-codium",
        model="qwen2.5-coder:14b",
        idle_timeout_seconds=600,
    )
    session = await get_session(db_conn, session_id)
    assert session is not None
    assert session["client_id"] == "anny-codium"
    assert session["status"] == "active"


async def test_queue_ordering(db_conn):
    """Tasks ordered by priority (asc), then deadline (asc nulls last), then submitted_at (asc)."""
    from ringmaster.db import insert_task, update_task_status, get_next_queued_task

    id_low = await insert_task(db_conn, "discrete", "m:7b", "low", 4, "c", None, "run")
    id_high = await insert_task(db_conn, "discrete", "m:7b", "high", 1, "c", None, "run")
    id_mid = await insert_task(db_conn, "discrete", "m:7b", "mid", 2, "c", None, "run",
                                deadline="2026-12-01T00:00:00Z")
    for tid in [id_low, id_high, id_mid]:
        await update_task_status(db_conn, tid, "queued")

    nxt = await get_next_queued_task(db_conn)
    assert nxt["id"] == id_high
```

- [ ] **Step 2: Write the failing test for pydantic models**

```python
# tests/test_models.py
from datetime import datetime, timezone


def test_task_response_model():
    from ringmaster.models import TaskResponse

    t = TaskResponse(
        id="abc-123",
        task_type="discrete",
        model="mistral-nemo:12b",
        priority=1,
        status="queued",
        client_id="netintel",
        submitted_at=datetime.now(timezone.utc),
    )
    assert t.id == "abc-123"
    assert t.task_type == "discrete"


def test_task_submit_request_defaults():
    from ringmaster.models import TaskSubmitRequest

    req = TaskSubmitRequest(
        task_type="discrete",
        model="mistral-nemo:12b",
        prompt="analyze this",
        client_id="netintel",
    )
    assert req.priority is None
    assert req.unattended_policy == "run"


def test_session_response_model():
    from ringmaster.models import SessionResponse

    s = SessionResponse(
        id="sess-1",
        client_id="anny",
        model="qwen:14b",
        status="active",
        opened_at=datetime.now(timezone.utc),
        idle_timeout_seconds=600,
    )
    assert s.status == "active"


def test_status_response_model():
    from ringmaster.models import StatusResponse

    sr = StatusResponse(
        state="idle",
        queue_depth=0,
        current_task=None,
        user_present=False,
        queue_paused=False,
    )
    assert sr.state == "idle"


def test_webhook_payload_model():
    from ringmaster.models import WebhookPayload

    w = WebhookPayload(
        task_id="abc-123",
        status="completed",
        result="analysis complete",
        model="mistral-nemo:12b",
        duration_seconds=142.5,
        completed_at=datetime.now(timezone.utc),
    )
    assert w.status == "completed"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd /home/levine/Documents/Repos/Ringmaster && python -m pytest tests/test_db.py tests/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ringmaster.db'`

- [ ] **Step 4: Implement models.py**

```python
# ringmaster/models.py
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class TaskSubmitRequest(BaseModel):
    task_type: str  # "discrete" or "session"
    model: str
    prompt: str | None = None
    priority: int | None = None
    deadline: str | None = None
    callback_url: str | None = None
    client_id: str
    unattended_policy: str = "run"  # run, wait, skip
    session_idle_timeout_seconds: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class TaskResponse(BaseModel):
    id: str
    task_type: str
    model: str
    priority: int
    status: str
    client_id: str
    submitted_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    deadline: str | None = None
    prompt: str | None = None
    result: str | None = None
    error: str | None = None
    gpu_used: str | None = None
    duration_seconds: float | None = None
    callback_url: str | None = None
    unattended_policy: str = "run"
    metadata: dict[str, Any] = Field(default_factory=dict)


class SessionOpenRequest(BaseModel):
    model: str
    client_id: str
    priority: int | None = None
    session_idle_timeout_seconds: int = 600
    callback_url: str | None = None
    unattended_policy: str = "run"


class SessionResponse(BaseModel):
    id: str
    client_id: str
    model: str
    status: str
    opened_at: datetime
    last_activity_at: datetime | None = None
    idle_timeout_seconds: int
    gpu_label: str | None = None


class SessionGenerateRequest(BaseModel):
    prompt: str
    stream: bool = False


class StatusResponse(BaseModel):
    state: str
    queue_depth: int
    current_task: str | None
    user_present: bool
    queue_paused: bool


class HealthResponse(BaseModel):
    alive: bool = True
    version: str = ""
    uptime_seconds: float = 0.0


class GpuStatusResponse(BaseModel):
    label: str
    role: str
    vram_mb: int
    current_model: str | None = None
    status: str  # available, in_use, missing


class WebhookPayload(BaseModel):
    task_id: str
    status: str
    result: str | None = None
    error: str | None = None
    model: str | None = None
    gpu_used: str | None = None
    duration_seconds: float | None = None
    completed_at: datetime | None = None


class SleepDeferredResponse(BaseModel):
    sleep: str = "deferred"
    reason: str = "task_running"
    est_completion: str | None = None
```

- [ ] **Step 5: Implement db.py**

```python
# ringmaster/db.py
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import aiosqlite

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    task_type TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt TEXT,
    priority INTEGER NOT NULL DEFAULT 3,
    deadline TEXT,
    status TEXT NOT NULL DEFAULT 'submitted',
    client_id TEXT NOT NULL,
    callback_url TEXT,
    unattended_policy TEXT NOT NULL DEFAULT 'run',
    submitted_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    result TEXT,
    error TEXT,
    gpu_used TEXT,
    duration_seconds REAL,
    metadata TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_priority ON tasks(priority);
CREATE INDEX IF NOT EXISTS idx_tasks_client ON tasks(client_id);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,
    model TEXT NOT NULL,
    gpu_label TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    opened_at TEXT NOT NULL,
    last_activity_at TEXT,
    idle_timeout_seconds INTEGER NOT NULL DEFAULT 600
);

CREATE TABLE IF NOT EXISTS clients (
    client_id TEXT PRIMARY KEY,
    token_hash TEXT NOT NULL,
    registered_at TEXT NOT NULL,
    last_seen TEXT
);

CREATE TABLE IF NOT EXISTS power_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    event_type TEXT NOT NULL,
    source TEXT,
    detail TEXT
);
"""


async def get_db(path: str) -> aiosqlite.Connection:
    """Open (or create) the Ringmaster database and return a connection."""
    conn = await aiosqlite.connect(path)
    await conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = aiosqlite.Row
    return conn


async def init_db(conn: aiosqlite.Connection) -> None:
    """Create all tables and indexes."""
    await conn.executescript(_SCHEMA)
    await conn.commit()


async def insert_task(
    conn: aiosqlite.Connection,
    task_type: str,
    model: str,
    prompt: str | None,
    priority: int,
    client_id: str,
    callback_url: str | None,
    unattended_policy: str,
    deadline: str | None = None,
    metadata: dict | None = None,
) -> str:
    """Insert a new task and return its ID."""
    task_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    await conn.execute(
        """INSERT INTO tasks
           (id, task_type, model, prompt, priority, deadline, status,
            client_id, callback_url, unattended_policy, submitted_at, metadata)
           VALUES (?, ?, ?, ?, ?, ?, 'submitted', ?, ?, ?, ?, ?)""",
        (
            task_id, task_type, model, prompt, priority, deadline,
            client_id, callback_url, unattended_policy, now,
            json.dumps(metadata or {}),
        ),
    )
    await conn.commit()
    return task_id


async def get_task(conn: aiosqlite.Connection, task_id: str) -> dict | None:
    """Fetch a single task by ID. Returns dict or None."""
    cursor = await conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
    row = await cursor.fetchone()
    if row is None:
        return None
    return dict(row)


async def update_task_status(
    conn: aiosqlite.Connection, task_id: str, status: str
) -> None:
    """Update a task's status."""
    await conn.execute(
        "UPDATE tasks SET status = ? WHERE id = ?", (status, task_id)
    )
    await conn.commit()


async def update_task_started(conn: aiosqlite.Connection, task_id: str) -> None:
    """Mark a task as running with current timestamp."""
    now = datetime.now(timezone.utc).isoformat()
    await conn.execute(
        "UPDATE tasks SET status = 'running', started_at = ? WHERE id = ?",
        (now, task_id),
    )
    await conn.commit()


async def update_task_completed(
    conn: aiosqlite.Connection,
    task_id: str,
    result: str | None = None,
    error: str | None = None,
    gpu_used: str | None = None,
    duration_seconds: float | None = None,
) -> None:
    """Mark a task as completed or failed."""
    now = datetime.now(timezone.utc).isoformat()
    status = "failed" if error else "completed"
    await conn.execute(
        """UPDATE tasks
           SET status = ?, completed_at = ?, result = ?, error = ?,
               gpu_used = ?, duration_seconds = ?
           WHERE id = ?""",
        (status, now, result, error, gpu_used, duration_seconds, task_id),
    )
    await conn.commit()


async def list_tasks(
    conn: aiosqlite.Connection,
    status: str | None = None,
    client_id: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """List tasks, optionally filtered by status and/or client_id."""
    query = "SELECT * FROM tasks WHERE 1=1"
    params: list = []
    if status:
        query += " AND status = ?"
        params.append(status)
    if client_id:
        query += " AND client_id = ?"
        params.append(client_id)
    query += " ORDER BY priority ASC, deadline ASC, submitted_at ASC LIMIT ?"
    params.append(limit)
    cursor = await conn.execute(query, params)
    return [dict(row) for row in await cursor.fetchall()]


async def get_next_queued_task(conn: aiosqlite.Connection) -> dict | None:
    """Get the highest-priority queued task."""
    cursor = await conn.execute(
        """SELECT * FROM tasks WHERE status = 'queued'
           ORDER BY priority ASC,
                    CASE WHEN deadline IS NULL THEN 1 ELSE 0 END,
                    deadline ASC,
                    submitted_at ASC
           LIMIT 1"""
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


async def insert_session(
    conn: aiosqlite.Connection,
    client_id: str,
    model: str,
    idle_timeout_seconds: int = 600,
    gpu_label: str | None = None,
) -> str:
    """Insert a new session and return its ID."""
    session_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    await conn.execute(
        """INSERT INTO sessions
           (id, client_id, model, gpu_label, status, opened_at,
            last_activity_at, idle_timeout_seconds)
           VALUES (?, ?, ?, ?, 'active', ?, ?, ?)""",
        (session_id, client_id, model, gpu_label, now, now, idle_timeout_seconds),
    )
    await conn.commit()
    return session_id


async def get_session(conn: aiosqlite.Connection, session_id: str) -> dict | None:
    """Fetch a session by ID."""
    cursor = await conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
    row = await cursor.fetchone()
    return dict(row) if row else None


async def update_session_activity(conn: aiosqlite.Connection, session_id: str) -> None:
    """Update last_activity_at for a session."""
    now = datetime.now(timezone.utc).isoformat()
    await conn.execute(
        "UPDATE sessions SET last_activity_at = ? WHERE id = ?",
        (now, session_id),
    )
    await conn.commit()


async def close_session(conn: aiosqlite.Connection, session_id: str) -> None:
    """Close a session."""
    await conn.execute(
        "UPDATE sessions SET status = 'closed' WHERE id = ?", (session_id,)
    )
    await conn.commit()


async def insert_client(
    conn: aiosqlite.Connection, client_id: str, token_hash: str
) -> None:
    """Register a new client."""
    now = datetime.now(timezone.utc).isoformat()
    await conn.execute(
        "INSERT INTO clients (client_id, token_hash, registered_at) VALUES (?, ?, ?)",
        (client_id, token_hash, now),
    )
    await conn.commit()


async def get_client_by_id(conn: aiosqlite.Connection, client_id: str) -> dict | None:
    """Fetch a client by ID."""
    cursor = await conn.execute(
        "SELECT * FROM clients WHERE client_id = ?", (client_id,)
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


async def log_power_event(
    conn: aiosqlite.Connection,
    event_type: str,
    source: str | None = None,
    detail: str | None = None,
) -> None:
    """Log a power management event."""
    now = datetime.now(timezone.utc).isoformat()
    await conn.execute(
        "INSERT INTO power_events (timestamp, event_type, source, detail) VALUES (?, ?, ?, ?)",
        (now, event_type, source, detail),
    )
    await conn.commit()
```

- [ ] **Step 6: Run tests**

Run: `cd /home/levine/Documents/Repos/Ringmaster && python -m pytest tests/test_db.py tests/test_models.py -v`
Expected: All tests PASS

- [ ] **Step 7: Commit**

```bash
cd /home/levine/Documents/Repos/Ringmaster
git add ringmaster/models.py ringmaster/db.py tests/test_db.py tests/test_models.py
git commit -m "feat: database layer and pydantic API models"
```

---

### Task 3: GPU Detection + Fingerprinting

**Files:**
- Create: `ringmaster/gpu/__init__.py`
- Create: `ringmaster/gpu/detect.py`
- Create: `ringmaster/gpu/fingerprint.py`
- Create: `tests/test_gpu_detect.py`
- Create: `tests/test_gpu_fingerprint.py`

- [ ] **Step 1: Write the failing test for GPU detection**

```python
# tests/test_gpu_detect.py
from unittest.mock import patch


def test_detect_gpus_parses_rocm_smi_output():
    from ringmaster.gpu.detect import detect_gpus_rocm

    # Simulated rocm-smi --showproductname --showmeminfo vram --showuniqueid --json output
    fake_output = """{
        "card0": {
            "Card Series": "Radeon RX 7900 XTX",
            "Card Vendor": "Advanced Micro Devices, Inc. [AMD/ATI]",
            "VRAM Total Memory (B)": "25753026560",
            "Unique ID": "0x59f03e0beb4e0a04"
        },
        "card1": {
            "Card Series": "Radeon RX 5700 XT",
            "Card Vendor": "Advanced Micro Devices, Inc. [AMD/ATI]",
            "VRAM Total Memory (B)": "8573157376",
            "Unique ID": "N/A"
        }
    }"""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.stdout = fake_output
        mock_run.return_value.returncode = 0
        gpus = detect_gpus_rocm()

    assert len(gpus) == 2
    assert gpus[0].vendor == "AMD"
    assert gpus[0].model == "Radeon RX 7900 XTX"
    assert gpus[0].vram_mb == 24556  # 25753026560 / 1024 / 1024, rounded
    assert gpus[0].serial == "0x59f03e0beb4e0a04"
    assert gpus[0].pci_slot == "card0"

    assert gpus[1].model == "Radeon RX 5700 XT"
    assert gpus[1].serial == ""  # N/A normalized to empty


def test_detect_gpus_handles_no_gpus():
    from ringmaster.gpu.detect import detect_gpus_rocm

    with patch("subprocess.run") as mock_run:
        mock_run.return_value.stdout = "{}"
        mock_run.return_value.returncode = 0
        gpus = detect_gpus_rocm()

    assert gpus == []


def test_detect_gpus_handles_rocm_smi_missing():
    from ringmaster.gpu.detect import detect_gpus_rocm

    with patch("subprocess.run", side_effect=FileNotFoundError):
        gpus = detect_gpus_rocm()

    assert gpus == []
```

- [ ] **Step 2: Write the failing test for fingerprint matching**

```python
# tests/test_gpu_fingerprint.py
from ringmaster.config import GpuConfig, GpuFingerprint


def test_match_gpu_to_config_by_serial():
    from ringmaster.gpu.fingerprint import match_gpu_to_config
    from ringmaster.gpu.detect import DetectedGpu

    detected = DetectedGpu(
        vendor="AMD",
        model="Radeon RX 7900 XTX",
        vram_mb=24556,
        serial="0x59f03e0beb4e0a04",
        device_id="",
        pci_slot="card0",
    )
    configs = [
        GpuConfig(
            label="primary",
            role="compute",
            prefer_for=["default"],
            fingerprint=GpuFingerprint(
                vendor="AMD",
                model="Radeon RX 7900 XTX",
                vram_mb=24576,
                serial="0x59f03e0beb4e0a04",
            ),
        ),
    ]
    match = match_gpu_to_config(detected, configs)
    assert match is not None
    assert match.label == "primary"


def test_match_gpu_to_config_by_model_and_vram():
    """Falls back to model+vram match when serial is empty."""
    from ringmaster.gpu.fingerprint import match_gpu_to_config
    from ringmaster.gpu.detect import DetectedGpu

    detected = DetectedGpu(
        vendor="AMD",
        model="Radeon RX 5700 XT",
        vram_mb=8173,
        serial="",
        device_id="",
        pci_slot="card1",
    )
    configs = [
        GpuConfig(
            label="secondary",
            role="both",
            fingerprint=GpuFingerprint(
                vendor="AMD",
                model="Radeon RX 5700 XT",
                vram_mb=8192,
            ),
        ),
    ]
    match = match_gpu_to_config(detected, configs)
    assert match is not None
    assert match.label == "secondary"


def test_match_gpu_no_match():
    from ringmaster.gpu.fingerprint import match_gpu_to_config
    from ringmaster.gpu.detect import DetectedGpu

    detected = DetectedGpu(
        vendor="NVIDIA",
        model="RTX 4090",
        vram_mb=24000,
        serial="",
        device_id="",
        pci_slot="card0",
    )
    configs = [
        GpuConfig(
            label="primary",
            fingerprint=GpuFingerprint(vendor="AMD", model="Radeon RX 7900 XTX"),
        ),
    ]
    match = match_gpu_to_config(detected, configs)
    assert match is None


def test_resolve_gpu_inventory():
    """Resolve all detected GPUs against config, reporting matched/missing/unknown."""
    from ringmaster.gpu.fingerprint import resolve_inventory
    from ringmaster.gpu.detect import DetectedGpu

    detected = [
        DetectedGpu(vendor="AMD", model="Radeon RX 7900 XTX", vram_mb=24556,
                    serial="abc", device_id="", pci_slot="card0"),
    ]
    configs = [
        GpuConfig(label="primary", role="compute", prefer_for=["default"],
                  fingerprint=GpuFingerprint(vendor="AMD", model="Radeon RX 7900 XTX",
                                             vram_mb=24576, serial="abc")),
        GpuConfig(label="secondary", role="both",
                  fingerprint=GpuFingerprint(vendor="AMD", model="Radeon RX 5700 XT",
                                             vram_mb=8192)),
    ]
    result = resolve_inventory(detected, configs)
    assert len(result.matched) == 1
    assert result.matched[0].label == "primary"
    assert result.matched[0].pci_slot == "card0"
    assert len(result.missing) == 1
    assert result.missing[0].label == "secondary"
    assert result.unknown == []
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd /home/levine/Documents/Repos/Ringmaster && python -m pytest tests/test_gpu_detect.py tests/test_gpu_fingerprint.py -v`
Expected: FAIL — module not found

- [ ] **Step 4: Implement gpu/detect.py**

```python
# ringmaster/gpu/__init__.py
```

```python
# ringmaster/gpu/detect.py
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass


@dataclass
class DetectedGpu:
    vendor: str
    model: str
    vram_mb: int
    serial: str
    device_id: str
    pci_slot: str


def detect_gpus_rocm() -> list[DetectedGpu]:
    """Detect GPUs using rocm-smi. Returns empty list if rocm-smi is unavailable."""
    try:
        result = subprocess.run(
            ["rocm-smi", "--showproductname", "--showmeminfo", "vram",
             "--showuniqueid", "--json"],
            capture_output=True, text=True, timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []

    if result.returncode != 0:
        return []

    try:
        data = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError):
        return []

    gpus = []
    for slot, info in data.items():
        if not isinstance(info, dict):
            continue
        # Normalize vendor
        vendor_raw = info.get("Card Vendor", "")
        vendor = "AMD" if "AMD" in vendor_raw or "ATI" in vendor_raw else vendor_raw

        model = info.get("Card Series", "")
        if not model:
            continue

        # VRAM in bytes to MB
        vram_bytes_str = info.get("VRAM Total Memory (B)", "0")
        try:
            vram_mb = int(int(vram_bytes_str) / 1024 / 1024)
        except (ValueError, TypeError):
            vram_mb = 0

        serial_raw = info.get("Unique ID", "")
        serial = "" if serial_raw in ("N/A", "", None) else serial_raw

        gpus.append(DetectedGpu(
            vendor=vendor,
            model=model,
            vram_mb=vram_mb,
            serial=serial,
            device_id="",
            pci_slot=slot,
        ))

    return gpus


def detect_gpus() -> list[DetectedGpu]:
    """Detect GPUs using the best available method."""
    gpus = detect_gpus_rocm()
    # Future: try nvidia-smi if rocm returns empty
    return gpus
```

- [ ] **Step 5: Implement gpu/fingerprint.py**

```python
# ringmaster/gpu/fingerprint.py
from __future__ import annotations

from dataclasses import dataclass, field

from ringmaster.config import GpuConfig
from ringmaster.gpu.detect import DetectedGpu


# Allow 5% VRAM variance between detected and configured
_VRAM_TOLERANCE = 0.05


def _vram_close(detected_mb: int, config_mb: int) -> bool:
    """True if VRAM values are within tolerance."""
    if config_mb == 0:
        return True
    return abs(detected_mb - config_mb) / config_mb <= _VRAM_TOLERANCE


def match_gpu_to_config(
    detected: DetectedGpu, configs: list[GpuConfig]
) -> GpuConfig | None:
    """Match a detected GPU to a config entry by fingerprint.

    Priority: serial match > model+vram match > model-only match.
    """
    # Pass 1: exact serial match
    if detected.serial:
        for cfg in configs:
            if cfg.fingerprint.serial and cfg.fingerprint.serial == detected.serial:
                return cfg

    # Pass 2: model + vram match
    for cfg in configs:
        if (cfg.fingerprint.model == detected.model
                and _vram_close(detected.vram_mb, cfg.fingerprint.vram_mb)):
            return cfg

    # Pass 3: model-only match
    for cfg in configs:
        if cfg.fingerprint.model == detected.model:
            return cfg

    return None


@dataclass
class MatchedGpu:
    """A config GPU successfully matched to a detected GPU."""
    label: str
    role: str
    prefer_for: list[str]
    vram_mb: int
    pci_slot: str
    detected: DetectedGpu
    config: GpuConfig


@dataclass
class InventoryResult:
    matched: list[MatchedGpu] = field(default_factory=list)
    missing: list[GpuConfig] = field(default_factory=list)
    unknown: list[DetectedGpu] = field(default_factory=list)


def resolve_inventory(
    detected: list[DetectedGpu], configs: list[GpuConfig]
) -> InventoryResult:
    """Resolve all detected GPUs against config entries."""
    result = InventoryResult()
    matched_configs: set[str] = set()  # labels of matched configs

    for gpu in detected:
        cfg = match_gpu_to_config(gpu, [c for c in configs if c.label not in matched_configs])
        if cfg:
            matched_configs.add(cfg.label)
            result.matched.append(MatchedGpu(
                label=cfg.label,
                role=cfg.role,
                prefer_for=cfg.prefer_for,
                vram_mb=gpu.vram_mb,
                pci_slot=gpu.pci_slot,
                detected=gpu,
                config=cfg,
            ))
        else:
            result.unknown.append(gpu)

    for cfg in configs:
        if cfg.label not in matched_configs:
            result.missing.append(cfg)

    return result
```

- [ ] **Step 6: Run tests**

Run: `cd /home/levine/Documents/Repos/Ringmaster && python -m pytest tests/test_gpu_detect.py tests/test_gpu_fingerprint.py -v`
Expected: All tests PASS

- [ ] **Step 7: Commit**

```bash
cd /home/levine/Documents/Repos/Ringmaster
git add ringmaster/gpu/ tests/test_gpu_detect.py tests/test_gpu_fingerprint.py
git commit -m "feat: GPU detection via rocm-smi and fingerprint matching"
```

---

### Task 4: Ollama Client

**Files:**
- Create: `ringmaster/ollama.py`
- Create: `tests/test_ollama.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ollama.py
import pytest
import httpx


@pytest.fixture
def ollama_client():
    from ringmaster.ollama import OllamaClient
    return OllamaClient(base_url="http://localhost:11434")


async def test_generate_sends_correct_request(ollama_client):
    """Test that generate() sends the right payload and returns the response text."""
    import httpx
    from unittest.mock import AsyncMock, patch

    mock_response = httpx.Response(
        200,
        json={"response": "Analysis complete.", "done": True},
        request=httpx.Request("POST", "http://localhost:11434/api/generate"),
    )

    with patch.object(ollama_client._client, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response
        result = await ollama_client.generate("mistral-nemo:12b", "analyze this")

    assert result == "Analysis complete."
    mock_post.assert_called_once()
    call_kwargs = mock_post.call_args
    assert call_kwargs[1]["json"]["model"] == "mistral-nemo:12b"
    assert call_kwargs[1]["json"]["prompt"] == "analyze this"


async def test_list_models(ollama_client):
    from unittest.mock import AsyncMock, patch

    mock_response = httpx.Response(
        200,
        json={"models": [
            {"name": "mistral-nemo:12b", "size": 12000000000},
            {"name": "llama3.2:3b", "size": 3000000000},
        ]},
        request=httpx.Request("GET", "http://localhost:11434/api/tags"),
    )

    with patch.object(ollama_client._client, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response
        models = await ollama_client.list_models()

    assert len(models) == 2
    assert models[0]["name"] == "mistral-nemo:12b"


async def test_load_model(ollama_client):
    from unittest.mock import AsyncMock, patch

    mock_response = httpx.Response(
        200,
        json={"status": "success"},
        request=httpx.Request("POST", "http://localhost:11434/api/generate"),
    )

    with patch.object(ollama_client._client, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response
        await ollama_client.load_model("mistral-nemo:12b")

    call_kwargs = mock_post.call_args
    assert call_kwargs[1]["json"]["model"] == "mistral-nemo:12b"
    assert call_kwargs[1]["json"]["prompt"] == ""


async def test_unload_model(ollama_client):
    from unittest.mock import AsyncMock, patch

    mock_response = httpx.Response(
        200,
        json={"status": "success"},
        request=httpx.Request("POST", "http://localhost:11434/api/generate"),
    )

    with patch.object(ollama_client._client, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response
        await ollama_client.unload_model("mistral-nemo:12b")

    call_kwargs = mock_post.call_args
    assert call_kwargs[1]["json"]["keep_alive"] == 0


async def test_generate_raises_on_ollama_error(ollama_client):
    from ringmaster.ollama import OllamaError
    from unittest.mock import AsyncMock, patch

    mock_response = httpx.Response(
        500,
        json={"error": "model not found"},
        request=httpx.Request("POST", "http://localhost:11434/api/generate"),
    )

    with patch.object(ollama_client._client, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response
        with pytest.raises(OllamaError, match="model not found"):
            await ollama_client.generate("nonexistent:7b", "test")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/levine/Documents/Repos/Ringmaster && python -m pytest tests/test_ollama.py -v`
Expected: FAIL

- [ ] **Step 3: Implement ollama.py**

```python
# ringmaster/ollama.py
from __future__ import annotations

from typing import Any

import httpx


class OllamaError(Exception):
    """Raised when Ollama returns an error."""


class OllamaClient:
    """Async client for the Ollama HTTP API."""

    def __init__(self, base_url: str = "http://localhost:11434"):
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(base_url=self._base_url, timeout=600.0)

    async def generate(self, model: str, prompt: str) -> str:
        """Run a generate request and return the response text."""
        resp = await self._client.post(
            "/api/generate",
            json={"model": model, "prompt": prompt, "stream": False},
        )
        data = resp.json()
        if resp.status_code != 200:
            raise OllamaError(data.get("error", f"HTTP {resp.status_code}"))
        return data.get("response", "")

    async def load_model(self, model: str) -> None:
        """Pre-load a model into VRAM by sending an empty prompt."""
        resp = await self._client.post(
            "/api/generate",
            json={"model": model, "prompt": "", "stream": False},
        )
        if resp.status_code != 200:
            data = resp.json()
            raise OllamaError(data.get("error", f"HTTP {resp.status_code}"))

    async def unload_model(self, model: str) -> None:
        """Unload a model from VRAM."""
        resp = await self._client.post(
            "/api/generate",
            json={"model": model, "prompt": "", "keep_alive": 0, "stream": False},
        )
        if resp.status_code != 200:
            data = resp.json()
            raise OllamaError(data.get("error", f"HTTP {resp.status_code}"))

    async def list_models(self) -> list[dict[str, Any]]:
        """List all locally available models."""
        resp = await self._client.get("/api/tags")
        if resp.status_code != 200:
            raise OllamaError(f"HTTP {resp.status_code}")
        return resp.json().get("models", [])

    async def list_running(self) -> list[dict[str, Any]]:
        """List currently loaded/running models."""
        resp = await self._client.get("/api/ps")
        if resp.status_code != 200:
            raise OllamaError(f"HTTP {resp.status_code}")
        return resp.json().get("models", [])

    async def close(self) -> None:
        await self._client.aclose()
```

- [ ] **Step 4: Run tests**

Run: `cd /home/levine/Documents/Repos/Ringmaster && python -m pytest tests/test_ollama.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
cd /home/levine/Documents/Repos/Ringmaster
git add ringmaster/ollama.py tests/test_ollama.py
git commit -m "feat: Ollama HTTP client with model load/unload/generate"
```

---

### Task 5: Webhook Delivery

**Files:**
- Create: `ringmaster/webhooks.py`
- Create: `tests/test_webhooks.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_webhooks.py
import pytest
import httpx
from unittest.mock import AsyncMock, patch
from datetime import datetime, timezone


async def test_deliver_webhook_success():
    from ringmaster.webhooks import deliver_webhook
    from ringmaster.models import WebhookPayload

    payload = WebhookPayload(
        task_id="abc-123",
        status="completed",
        result="done",
        model="mistral:12b",
        duration_seconds=42.0,
        completed_at=datetime.now(timezone.utc),
    )

    mock_response = httpx.Response(
        200, request=httpx.Request("POST", "http://example.com/callback")
    )

    with patch("ringmaster.webhooks.httpx.AsyncClient") as MockClient:
        instance = AsyncMock()
        instance.post.return_value = mock_response
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = instance

        success = await deliver_webhook("http://example.com/callback", payload)

    assert success is True
    instance.post.assert_called_once()


async def test_deliver_webhook_retries_on_failure():
    from ringmaster.webhooks import deliver_webhook
    from ringmaster.models import WebhookPayload

    payload = WebhookPayload(task_id="abc", status="failed", error="boom")

    with patch("ringmaster.webhooks.httpx.AsyncClient") as MockClient:
        instance = AsyncMock()
        instance.post.side_effect = httpx.ConnectError("refused")
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = instance

        success = await deliver_webhook(
            "http://unreachable/callback", payload, max_retries=3, base_delay=0.01
        )

    assert success is False
    assert instance.post.call_count == 3


async def test_deliver_webhook_skips_when_no_url():
    from ringmaster.webhooks import deliver_webhook
    from ringmaster.models import WebhookPayload

    payload = WebhookPayload(task_id="abc", status="completed")
    success = await deliver_webhook(None, payload)
    assert success is True  # no-op is success
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/levine/Documents/Repos/Ringmaster && python -m pytest tests/test_webhooks.py -v`
Expected: FAIL

- [ ] **Step 3: Implement webhooks.py**

```python
# ringmaster/webhooks.py
from __future__ import annotations

import asyncio
import logging

import httpx

from ringmaster.models import WebhookPayload

logger = logging.getLogger(__name__)


async def deliver_webhook(
    url: str | None,
    payload: WebhookPayload,
    max_retries: int = 3,
    base_delay: float = 2.0,
) -> bool:
    """Deliver a webhook payload to the callback URL.

    Retries with exponential backoff on failure. Returns True if delivered
    (or no URL to deliver to), False if all retries exhausted.
    """
    if not url:
        return True

    data = payload.model_dump(mode="json", exclude_none=True)

    async with httpx.AsyncClient(timeout=30.0) as client:
        for attempt in range(max_retries):
            try:
                resp = await client.post(url, json=data)
                if 200 <= resp.status_code < 300:
                    logger.info("Webhook delivered to %s (task %s)", url, payload.task_id)
                    return True
                logger.warning(
                    "Webhook to %s returned %d (attempt %d/%d)",
                    url, resp.status_code, attempt + 1, max_retries,
                )
            except (httpx.ConnectError, httpx.TimeoutException, httpx.RemoteProtocolError) as exc:
                logger.warning(
                    "Webhook to %s failed: %s (attempt %d/%d)",
                    url, exc, attempt + 1, max_retries,
                )

            if attempt < max_retries - 1:
                await asyncio.sleep(base_delay * (2 ** attempt))

    logger.error("Webhook delivery to %s exhausted %d retries (task %s)",
                 url, max_retries, payload.task_id)
    return False
```

- [ ] **Step 4: Run tests**

Run: `cd /home/levine/Documents/Repos/Ringmaster && python -m pytest tests/test_webhooks.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
cd /home/levine/Documents/Repos/Ringmaster
git add ringmaster/webhooks.py tests/test_webhooks.py
git commit -m "feat: webhook delivery with exponential backoff retry"
```

---

### Task 6: Scheduler (Queue State Machine)

**Files:**
- Create: `ringmaster/scheduler.py`
- Create: `tests/test_scheduler.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_scheduler.py
import pytest


@pytest.fixture
async def db_conn(tmp_path):
    from ringmaster.db import get_db, init_db
    conn = await get_db(str(tmp_path / "test.db"))
    await init_db(conn)
    yield conn
    await conn.close()


@pytest.fixture
def scheduler_config():
    from ringmaster.config import QueueConfig
    return QueueConfig(max_queue_depth=100, default_priority=3,
                       session_idle_timeout_seconds=600)


async def test_submit_task_goes_to_queued(db_conn, scheduler_config):
    from ringmaster.scheduler import Scheduler

    sched = Scheduler(db_conn, scheduler_config)
    task_id = await sched.submit_task(
        task_type="discrete",
        model="test:7b",
        prompt="test",
        priority=1,
        client_id="test-client",
    )
    from ringmaster.db import get_task
    task = await get_task(db_conn, task_id)
    assert task["status"] == "queued"


async def test_next_task_returns_highest_priority(db_conn, scheduler_config):
    from ringmaster.scheduler import Scheduler

    sched = Scheduler(db_conn, scheduler_config)
    await sched.submit_task("discrete", "m:7b", "low", 4, "c")
    id_high = await sched.submit_task("discrete", "m:7b", "high", 1, "c")

    nxt = await sched.next_task()
    assert nxt is not None
    assert nxt["id"] == id_high


async def test_pause_and_resume(db_conn, scheduler_config):
    from ringmaster.scheduler import Scheduler

    sched = Scheduler(db_conn, scheduler_config)
    assert sched.is_paused is False

    sched.pause()
    assert sched.is_paused is True

    await sched.submit_task("discrete", "m:7b", "test", 1, "c")
    # next_task returns None when paused
    nxt = await sched.next_task()
    assert nxt is None

    sched.resume()
    nxt = await sched.next_task()
    assert nxt is not None


async def test_drain_pauses_after_current(db_conn, scheduler_config):
    from ringmaster.scheduler import Scheduler

    sched = Scheduler(db_conn, scheduler_config)
    sched.drain()
    assert sched.is_draining is True
    assert sched.is_paused is False  # not paused yet — waits for current task

    # Simulate current task completing
    sched.on_task_completed()
    assert sched.is_paused is True
    assert sched.is_draining is False


async def test_queue_depth_limit(db_conn, scheduler_config):
    from ringmaster.scheduler import Scheduler, QueueFullError

    scheduler_config.max_queue_depth = 2
    sched = Scheduler(db_conn, scheduler_config)
    await sched.submit_task("discrete", "m:7b", "t1", 1, "c")
    await sched.submit_task("discrete", "m:7b", "t2", 1, "c")

    with pytest.raises(QueueFullError):
        await sched.submit_task("discrete", "m:7b", "t3", 1, "c")


async def test_cancel_current_task(db_conn, scheduler_config):
    from ringmaster.scheduler import Scheduler
    from ringmaster.db import get_task

    sched = Scheduler(db_conn, scheduler_config)
    task_id = await sched.submit_task("discrete", "m:7b", "test", 1, "c")

    # Simulate task starting
    sched._current_task_id = task_id
    from ringmaster.db import update_task_status
    await update_task_status(db_conn, task_id, "running")

    await sched.cancel_current()
    task = await get_task(db_conn, task_id)
    assert task["status"] == "interrupted"
    assert sched._current_task_id is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/levine/Documents/Repos/Ringmaster && python -m pytest tests/test_scheduler.py -v`
Expected: FAIL

- [ ] **Step 3: Implement scheduler.py**

```python
# ringmaster/scheduler.py
from __future__ import annotations

import logging
from typing import Any

import aiosqlite

from ringmaster.config import QueueConfig
from ringmaster.db import (
    get_next_queued_task,
    get_task,
    insert_task,
    list_tasks,
    update_task_status,
)

logger = logging.getLogger(__name__)


class QueueFullError(Exception):
    """Raised when the queue has reached its maximum depth."""


class Scheduler:
    """Manages task queue state: submit, prioritize, pause, drain, cancel."""

    def __init__(self, conn: aiosqlite.Connection, config: QueueConfig):
        self._conn = conn
        self._config = config
        self._paused = False
        self._draining = False
        self._current_task_id: str | None = None

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def is_draining(self) -> bool:
        return self._draining

    @property
    def current_task_id(self) -> str | None:
        return self._current_task_id

    async def submit_task(
        self,
        task_type: str,
        model: str,
        prompt: str | None,
        priority: int | None,
        client_id: str,
        callback_url: str | None = None,
        unattended_policy: str = "run",
        deadline: str | None = None,
        metadata: dict | None = None,
    ) -> str:
        """Submit a new task. Validates queue depth, assigns priority, sets status to queued."""
        # Check queue depth
        queued = await list_tasks(self._conn, status="queued")
        if len(queued) >= self._config.max_queue_depth:
            raise QueueFullError(
                f"Queue full ({len(queued)}/{self._config.max_queue_depth})"
            )

        effective_priority = priority if priority is not None else self._config.default_priority

        task_id = await insert_task(
            self._conn,
            task_type=task_type,
            model=model,
            prompt=prompt,
            priority=effective_priority,
            client_id=client_id,
            callback_url=callback_url,
            unattended_policy=unattended_policy,
            deadline=deadline,
            metadata=metadata,
        )
        # Move straight to queued
        await update_task_status(self._conn, task_id, "queued")
        logger.info("Task %s submitted (priority=%d, client=%s)",
                     task_id, effective_priority, client_id)
        return task_id

    async def next_task(self) -> dict[str, Any] | None:
        """Get the next task to run, respecting pause state."""
        if self._paused:
            return None
        return await get_next_queued_task(self._conn)

    def set_current(self, task_id: str) -> None:
        """Track the currently running task."""
        self._current_task_id = task_id

    def on_task_completed(self) -> None:
        """Called when the current task finishes. Handles drain logic."""
        self._current_task_id = None
        if self._draining:
            self._paused = True
            self._draining = False
            logger.info("Queue drained — now paused")

    def pause(self) -> None:
        """Pause the queue. Current task keeps running."""
        self._paused = True
        logger.info("Queue paused")

    def resume(self) -> None:
        """Resume the queue."""
        self._paused = False
        self._draining = False
        logger.info("Queue resumed")

    def drain(self) -> None:
        """Finish current task, then pause."""
        if self._current_task_id is None:
            # No task running — pause immediately
            self._paused = True
            logger.info("Queue drained (no current task) — now paused")
        else:
            self._draining = True
            logger.info("Queue draining — will pause after current task")

    async def cancel_current(self) -> str | None:
        """Cancel the currently running task. Returns the cancelled task ID."""
        if self._current_task_id is None:
            return None
        task_id = self._current_task_id
        await update_task_status(self._conn, task_id, "interrupted")
        self._current_task_id = None
        logger.info("Cancelled current task %s", task_id)
        return task_id

    async def queue_depth(self) -> int:
        """Return the number of queued tasks."""
        queued = await list_tasks(self._conn, status="queued")
        return len(queued)

    async def defer_task(self, task_id: str) -> None:
        """Defer a task (move from awaiting_approval to deferred)."""
        await update_task_status(self._conn, task_id, "deferred")
        logger.info("Task %s deferred", task_id)

    async def approve_task(self, task_id: str) -> None:
        """Approve a task (move from awaiting_approval to queued)."""
        await update_task_status(self._conn, task_id, "queued")
        logger.info("Task %s approved", task_id)
```

- [ ] **Step 4: Run tests**

Run: `cd /home/levine/Documents/Repos/Ringmaster && python -m pytest tests/test_scheduler.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
cd /home/levine/Documents/Repos/Ringmaster
git add ringmaster/scheduler.py tests/test_scheduler.py
git commit -m "feat: task queue scheduler with pause/resume/drain/cancel"
```

---

### Task 7: Auth Middleware

**Files:**
- Create: `ringmaster/server/auth.py`
- Create: `tests/test_auth.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_auth.py
import pytest


async def test_register_client_returns_token():
    from ringmaster.server.auth import AuthManager

    auth = AuthManager()
    token = auth.register("netintel")
    assert isinstance(token, str)
    assert len(token) > 20


async def test_verify_valid_token():
    from ringmaster.server.auth import AuthManager

    auth = AuthManager()
    token = auth.register("netintel")
    client_id = auth.verify(token)
    assert client_id == "netintel"


async def test_verify_invalid_token():
    from ringmaster.server.auth import AuthManager

    auth = AuthManager()
    auth.register("netintel")
    client_id = auth.verify("bad-token")
    assert client_id is None


async def test_revoke_token():
    from ringmaster.server.auth import AuthManager

    auth = AuthManager()
    token = auth.register("netintel")
    auth.revoke("netintel")
    assert auth.verify(token) is None


async def test_save_and_load_tokens(tmp_path):
    from ringmaster.server.auth import AuthManager

    path = str(tmp_path / "tokens.json")
    auth1 = AuthManager()
    token = auth1.register("netintel")
    auth1.save(path)

    auth2 = AuthManager()
    auth2.load(path)
    assert auth2.verify(token) == "netintel"


async def test_register_duplicate_client_replaces():
    from ringmaster.server.auth import AuthManager

    auth = AuthManager()
    token1 = auth.register("netintel")
    token2 = auth.register("netintel")
    assert token1 != token2
    assert auth.verify(token1) is None
    assert auth.verify(token2) == "netintel"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/levine/Documents/Repos/Ringmaster && python -m pytest tests/test_auth.py -v`
Expected: FAIL

- [ ] **Step 3: Implement auth.py**

```python
# ringmaster/server/__init__.py
```

```python
# ringmaster/server/auth.py
from __future__ import annotations

import hashlib
import json
import secrets
from pathlib import Path


class AuthManager:
    """Token-based client authentication.

    Stores client_id -> token_hash mapping. Tokens are generated as
    random hex strings. The raw token is returned once at registration;
    only the hash is stored.
    """

    def __init__(self) -> None:
        # client_id -> token_hash
        self._clients: dict[str, str] = {}
        # token_hash -> client_id (reverse index)
        self._tokens: dict[str, str] = {}

    @staticmethod
    def _hash_token(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def register(self, client_id: str) -> str:
        """Register (or re-register) a client. Returns the raw token."""
        # Remove old token if re-registering
        if client_id in self._clients:
            old_hash = self._clients[client_id]
            self._tokens.pop(old_hash, None)

        token = secrets.token_hex(32)
        token_hash = self._hash_token(token)
        self._clients[client_id] = token_hash
        self._tokens[token_hash] = client_id
        return token

    def verify(self, token: str) -> str | None:
        """Verify a token. Returns the client_id or None."""
        token_hash = self._hash_token(token)
        return self._tokens.get(token_hash)

    def revoke(self, client_id: str) -> None:
        """Revoke a client's token."""
        token_hash = self._clients.pop(client_id, None)
        if token_hash:
            self._tokens.pop(token_hash, None)

    def save(self, path: str) -> None:
        """Save token hashes to a JSON file."""
        Path(path).write_text(json.dumps(self._clients, indent=2))

    def load(self, path: str) -> None:
        """Load token hashes from a JSON file."""
        p = Path(path)
        if not p.is_file():
            return
        self._clients = json.loads(p.read_text())
        self._tokens = {h: cid for cid, h in self._clients.items()}
```

- [ ] **Step 4: Run tests**

Run: `cd /home/levine/Documents/Repos/Ringmaster && python -m pytest tests/test_auth.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
cd /home/levine/Documents/Repos/Ringmaster
git add ringmaster/server/__init__.py ringmaster/server/auth.py tests/test_auth.py
git commit -m "feat: token-based client authentication"
```

---

### Task 8: Power Management — Inhibitor + Presence + Actions

**Files:**
- Create: `ringmaster/power/__init__.py`
- Create: `ringmaster/power/inhibitor.py`
- Create: `ringmaster/power/presence.py`
- Create: `ringmaster/power/actions.py`
- Create: `tests/test_power_inhibitor.py`
- Create: `tests/test_power_presence.py`
- Create: `tests/test_power_actions.py`

- [ ] **Step 1: Write the failing tests for power actions**

```python
# tests/test_power_actions.py
from unittest.mock import patch, AsyncMock
import pytest


async def test_sleep_runs_configured_command():
    from ringmaster.power.actions import PowerActions
    from ringmaster.config import PowerConfig

    cfg = PowerConfig(sleep_command="echo sleeping")
    actions = PowerActions(cfg)

    with patch("asyncio.create_subprocess_shell", new_callable=AsyncMock) as mock_proc:
        mock_proc.return_value.wait = AsyncMock(return_value=0)
        await actions.sleep()
    mock_proc.assert_called_once_with("echo sleeping")


async def test_lock_screen():
    from ringmaster.power.actions import PowerActions
    from ringmaster.config import PowerConfig

    cfg = PowerConfig(lock_command="echo locking")
    actions = PowerActions(cfg)

    with patch("asyncio.create_subprocess_shell", new_callable=AsyncMock) as mock_proc:
        mock_proc.return_value.wait = AsyncMock(return_value=0)
        await actions.lock()
    mock_proc.assert_called_once_with("echo locking")


async def test_display_off():
    from ringmaster.power.actions import PowerActions
    from ringmaster.config import PowerConfig

    cfg = PowerConfig(display_off_command="echo dpms off")
    actions = PowerActions(cfg)

    with patch("asyncio.create_subprocess_shell", new_callable=AsyncMock) as mock_proc:
        mock_proc.return_value.wait = AsyncMock(return_value=0)
        await actions.display_off()
    mock_proc.assert_called_once_with("echo dpms off")
```

- [ ] **Step 2: Write the failing tests for inhibitor**

```python
# tests/test_power_inhibitor.py
from unittest.mock import patch, MagicMock


def test_inhibitor_acquire_and_release():
    from ringmaster.power.inhibitor import SleepInhibitor

    inhibitor = SleepInhibitor()

    with patch("ringmaster.power.inhibitor.subprocess.Popen") as mock_popen:
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None  # process is alive
        mock_popen.return_value = mock_proc

        inhibitor.acquire("Running AI task")
        assert inhibitor.is_held is True
        mock_popen.assert_called_once()

        inhibitor.release()
        mock_proc.terminate.assert_called_once()
        assert inhibitor.is_held is False


def test_inhibitor_double_acquire_is_noop():
    from ringmaster.power.inhibitor import SleepInhibitor

    inhibitor = SleepInhibitor()

    with patch("ringmaster.power.inhibitor.subprocess.Popen") as mock_popen:
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_popen.return_value = mock_proc

        inhibitor.acquire("task 1")
        inhibitor.acquire("task 2")
        # Only called once
        assert mock_popen.call_count == 1


def test_inhibitor_release_without_acquire_is_noop():
    from ringmaster.power.inhibitor import SleepInhibitor

    inhibitor = SleepInhibitor()
    inhibitor.release()  # should not raise
    assert inhibitor.is_held is False
```

- [ ] **Step 3: Write the failing test for presence detection**

```python
# tests/test_power_presence.py
from unittest.mock import patch, AsyncMock


async def test_presence_detector_idle_check():
    from ringmaster.power.presence import PresenceDetector
    from ringmaster.config import IdleConfig

    cfg = IdleConfig(detection_method="xprintidle", idle_threshold_seconds=300)
    detector = PresenceDetector(cfg)

    # User idle for 600 seconds (> 300 threshold)
    with patch("asyncio.create_subprocess_shell", new_callable=AsyncMock) as mock_proc:
        mock_proc.return_value.communicate = AsyncMock(return_value=(b"600000\n", b""))
        mock_proc.return_value.returncode = 0
        is_present = await detector.is_user_present()

    assert is_present is False


async def test_presence_detector_active_user():
    from ringmaster.power.presence import PresenceDetector
    from ringmaster.config import IdleConfig

    cfg = IdleConfig(detection_method="xprintidle", idle_threshold_seconds=300)
    detector = PresenceDetector(cfg)

    # User active — 5 seconds idle
    with patch("asyncio.create_subprocess_shell", new_callable=AsyncMock) as mock_proc:
        mock_proc.return_value.communicate = AsyncMock(return_value=(b"5000\n", b""))
        mock_proc.return_value.returncode = 0
        is_present = await detector.is_user_present()

    assert is_present is True


async def test_presence_detector_fallback_on_error():
    from ringmaster.power.presence import PresenceDetector
    from ringmaster.config import IdleConfig

    cfg = IdleConfig(detection_method="xprintidle", idle_threshold_seconds=300)
    detector = PresenceDetector(cfg)

    with patch("asyncio.create_subprocess_shell", new_callable=AsyncMock) as mock_proc:
        mock_proc.return_value.communicate = AsyncMock(return_value=(b"", b"error"))
        mock_proc.return_value.returncode = 1
        # Assume present on error (safe default)
        is_present = await detector.is_user_present()

    assert is_present is True
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `cd /home/levine/Documents/Repos/Ringmaster && python -m pytest tests/test_power_inhibitor.py tests/test_power_presence.py tests/test_power_actions.py -v`
Expected: FAIL

- [ ] **Step 5: Implement power modules**

```python
# ringmaster/power/__init__.py
```

```python
# ringmaster/power/inhibitor.py
from __future__ import annotations

import logging
import subprocess

logger = logging.getLogger(__name__)


class SleepInhibitor:
    """Holds a systemd-inhibit lock to prevent sleep/shutdown."""

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None

    @property
    def is_held(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def acquire(self, reason: str = "Ringmaster AI task running") -> None:
        """Acquire a sleep+shutdown inhibitor lock."""
        if self.is_held:
            return
        try:
            self._proc = subprocess.Popen(
                [
                    "systemd-inhibit",
                    "--what=sleep:shutdown",
                    "--who=Ringmaster",
                    f"--why={reason}",
                    "--mode=block",
                    "sleep", "infinity",
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            logger.info("Sleep inhibitor acquired: %s", reason)
        except FileNotFoundError:
            logger.warning("systemd-inhibit not found — sleep inhibition unavailable")

    def release(self) -> None:
        """Release the inhibitor lock."""
        if self._proc is None:
            return
        try:
            self._proc.terminate()
            self._proc.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            if self._proc:
                self._proc.kill()
        self._proc = None
        logger.info("Sleep inhibitor released")
```

```python
# ringmaster/power/presence.py
from __future__ import annotations

import asyncio
import logging

from ringmaster.config import IdleConfig

logger = logging.getLogger(__name__)


class PresenceDetector:
    """Detects whether a user is actively using the workstation."""

    def __init__(self, config: IdleConfig) -> None:
        self._config = config

    async def is_user_present(self) -> bool:
        """Return True if a user is actively at the workstation.

        Falls back to True (assume present) on error — safe default.
        """
        if self._config.detection_method == "xprintidle":
            return await self._check_xprintidle()
        # dbus method would go here
        return True  # safe default

    async def _check_xprintidle(self) -> bool:
        """Use xprintidle to check idle time in milliseconds."""
        try:
            proc = await asyncio.create_subprocess_shell(
                "xprintidle",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                logger.warning("xprintidle failed: %s", stderr.decode().strip())
                return True  # assume present on error

            idle_ms = int(stdout.decode().strip())
            idle_seconds = idle_ms / 1000
            is_idle = idle_seconds >= self._config.idle_threshold_seconds
            return not is_idle

        except (ValueError, FileNotFoundError, OSError) as exc:
            logger.warning("Presence detection error: %s", exc)
            return True  # assume present on error
```

```python
# ringmaster/power/actions.py
from __future__ import annotations

import asyncio
import logging

from ringmaster.config import PowerConfig

logger = logging.getLogger(__name__)


class PowerActions:
    """Execute power management commands (sleep, lock, display off)."""

    def __init__(self, config: PowerConfig) -> None:
        self._config = config

    async def _run(self, command: str) -> int:
        proc = await asyncio.create_subprocess_shell(command)
        return await proc.wait()

    async def sleep(self) -> None:
        """Suspend the machine."""
        logger.info("Executing sleep: %s", self._config.sleep_command)
        await self._run(self._config.sleep_command)

    async def lock(self) -> None:
        """Lock the screen."""
        logger.info("Executing lock: %s", self._config.lock_command)
        await self._run(self._config.lock_command)

    async def display_off(self) -> None:
        """Blank the displays."""
        logger.info("Executing display off: %s", self._config.display_off_command)
        await self._run(self._config.display_off_command)

    async def lock_and_blank(self) -> None:
        """Lock screen and blank displays."""
        await self.lock()
        await self.display_off()
```

- [ ] **Step 6: Run tests**

Run: `cd /home/levine/Documents/Repos/Ringmaster && python -m pytest tests/test_power_inhibitor.py tests/test_power_presence.py tests/test_power_actions.py -v`
Expected: All tests PASS

- [ ] **Step 7: Commit**

```bash
cd /home/levine/Documents/Repos/Ringmaster
git add ringmaster/power/ tests/test_power_inhibitor.py tests/test_power_presence.py tests/test_power_actions.py
git commit -m "feat: power management — sleep inhibitor, presence detection, actions"
```

---

### Task 9: Notification Providers

**Files:**
- Create: `ringmaster/notifications/__init__.py`
- Create: `ringmaster/notifications/base.py`
- Create: `ringmaster/notifications/desktop.py`
- Create: `ringmaster/notifications/homeassistant.py`
- Create: `tests/test_notifications_desktop.py`
- Create: `tests/test_notifications_ha.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_notifications_desktop.py
from unittest.mock import patch, AsyncMock


async def test_desktop_notification_sends():
    from ringmaster.notifications.desktop import DesktopNotifier

    notifier = DesktopNotifier()

    with patch("ringmaster.notifications.desktop.dbus_notify", new_callable=AsyncMock) as mock:
        await notifier.notify(
            title="Ringmaster",
            message="Task completed",
            actions=None,
        )
    mock.assert_called_once()


async def test_desktop_notification_with_actions():
    from ringmaster.notifications.desktop import DesktopNotifier

    notifier = DesktopNotifier()

    with patch("ringmaster.notifications.desktop.dbus_notify", new_callable=AsyncMock) as mock:
        mock.return_value = "approve"
        result = await notifier.notify(
            title="Ringmaster",
            message="NetIntel wants to run a task. Allow?",
            actions={"approve": "Approve", "defer": "Defer"},
        )
    assert result == "approve"
```

```python
# tests/test_notifications_ha.py
from unittest.mock import patch, AsyncMock
import httpx
import pytest


async def test_ha_notify_sends_request():
    from ringmaster.notifications.homeassistant import HANotifier

    notifier = HANotifier(ha_url="http://ha.local:8123", ha_token="test-token")

    mock_response = httpx.Response(
        200, request=httpx.Request("POST", "http://ha.local:8123/api/services/notify/notify")
    )

    with patch.object(notifier._client, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response
        await notifier.notify(
            title="Ringmaster",
            message="Task completed",
        )

    mock_post.assert_called_once()
    call_kwargs = mock_post.call_args
    assert "Authorization" in call_kwargs[1]["headers"]


async def test_ha_notify_handles_failure():
    from ringmaster.notifications.homeassistant import HANotifier

    notifier = HANotifier(ha_url="http://ha.local:8123", ha_token="test-token")

    with patch.object(notifier._client, "post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = httpx.ConnectError("refused")
        # Should not raise — notifications are best-effort
        await notifier.notify(title="Test", message="Test")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/levine/Documents/Repos/Ringmaster && python -m pytest tests/test_notifications_desktop.py tests/test_notifications_ha.py -v`
Expected: FAIL

- [ ] **Step 3: Implement notification modules**

```python
# ringmaster/notifications/__init__.py
```

```python
# ringmaster/notifications/base.py
from __future__ import annotations

from abc import ABC, abstractmethod


class NotificationProvider(ABC):
    """Base class for notification providers."""

    @abstractmethod
    async def notify(
        self,
        title: str,
        message: str,
        actions: dict[str, str] | None = None,
    ) -> str | None:
        """Send a notification. Returns the action chosen by the user, or None."""
        ...
```

```python
# ringmaster/notifications/desktop.py
from __future__ import annotations

import logging

from ringmaster.notifications.base import NotificationProvider

logger = logging.getLogger(__name__)


async def dbus_notify(
    title: str,
    message: str,
    actions: dict[str, str] | None = None,
) -> str | None:
    """Send a desktop notification via D-Bus org.freedesktop.Notifications.

    If actions are provided, waits for user response and returns the action key.
    Returns None if no actions or user dismissed.
    """
    try:
        from dbus_next.aio import MessageBus
        from dbus_next import Variant

        bus = await MessageBus().connect()
        introspect = await bus.introspect(
            "org.freedesktop.Notifications",
            "/org/freedesktop/Notifications",
        )
        proxy = bus.get_proxy_object(
            "org.freedesktop.Notifications",
            "/org/freedesktop/Notifications",
            introspect,
        )
        iface = proxy.get_interface("org.freedesktop.Notifications")

        # Build actions list: ["key1", "label1", "key2", "label2", ...]
        action_list: list[str] = []
        if actions:
            for key, label in actions.items():
                action_list.extend([key, label])

        notification_id = await iface.call_notify(
            "Ringmaster",        # app_name
            0,                   # replaces_id
            "",                  # app_icon
            title,               # summary
            message,             # body
            action_list,         # actions
            {},                  # hints
            5000 if not actions else 0,  # timeout (-1 = server default, 0 = never)
        )

        if not actions:
            bus.disconnect()
            return None

        # Wait for action response
        import asyncio
        result_action: str | None = None
        event = asyncio.Event()

        def on_action(nid: int, action_key: str) -> None:
            nonlocal result_action
            if nid == notification_id:
                result_action = action_key
                event.set()

        def on_closed(nid: int, reason: int) -> None:
            if nid == notification_id:
                event.set()

        iface.on_action_invoked(on_action)
        iface.on_notification_closed(on_closed)

        try:
            await asyncio.wait_for(event.wait(), timeout=120)
        except asyncio.TimeoutError:
            pass

        bus.disconnect()
        return result_action

    except Exception as exc:
        logger.warning("Desktop notification failed: %s", exc)
        return None


class DesktopNotifier(NotificationProvider):
    """Desktop notification provider via D-Bus."""

    async def notify(
        self,
        title: str,
        message: str,
        actions: dict[str, str] | None = None,
    ) -> str | None:
        return await dbus_notify(title, message, actions)
```

```python
# ringmaster/notifications/homeassistant.py
from __future__ import annotations

import logging

import httpx

from ringmaster.notifications.base import NotificationProvider

logger = logging.getLogger(__name__)


class HANotifier(NotificationProvider):
    """Home Assistant push notification provider."""

    def __init__(self, ha_url: str, ha_token: str) -> None:
        self._ha_url = ha_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=10.0)
        self._token = ha_token

    async def notify(
        self,
        title: str,
        message: str,
        actions: dict[str, str] | None = None,
    ) -> str | None:
        """Send a notification via HA. Actions are included in the message body
        but HA mobile app notifications don't support interactive responses
        back to Ringmaster — this is informational only."""
        url = f"{self._ha_url}/api/services/notify/notify"
        payload = {
            "title": title,
            "message": message,
        }
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }
        try:
            resp = await self._client.post(url, json=payload, headers=headers)
            if resp.status_code != 200:
                logger.warning("HA notification returned %d", resp.status_code)
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            logger.warning("HA notification failed: %s", exc)

        return None  # HA notifications don't return actions

    async def close(self) -> None:
        await self._client.aclose()
```

- [ ] **Step 4: Run tests**

Run: `cd /home/levine/Documents/Repos/Ringmaster && python -m pytest tests/test_notifications_desktop.py tests/test_notifications_ha.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
cd /home/levine/Documents/Repos/Ringmaster
git add ringmaster/notifications/ tests/test_notifications_desktop.py tests/test_notifications_ha.py
git commit -m "feat: notification providers — desktop D-Bus and Home Assistant"
```

---

### Task 10: Worker (Background Task Runner)

**Files:**
- Create: `ringmaster/worker.py`
- Create: `tests/test_worker.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_worker.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.fixture
async def db_conn(tmp_path):
    from ringmaster.db import get_db, init_db
    conn = await get_db(str(tmp_path / "test.db"))
    await init_db(conn)
    yield conn
    await conn.close()


@pytest.fixture
def mock_ollama():
    client = AsyncMock()
    client.generate.return_value = "result text"
    return client


@pytest.fixture
def mock_inhibitor():
    inh = MagicMock()
    inh.is_held = False
    return inh


async def test_worker_runs_discrete_task(db_conn, mock_ollama, mock_inhibitor):
    from ringmaster.worker import Worker
    from ringmaster.scheduler import Scheduler
    from ringmaster.config import QueueConfig
    from ringmaster.db import get_task

    config = QueueConfig()
    sched = Scheduler(db_conn, config)
    task_id = await sched.submit_task("discrete", "test:7b", "analyze this", 1, "test-client")

    worker = Worker(
        conn=db_conn,
        scheduler=sched,
        ollama=mock_ollama,
        inhibitor=mock_inhibitor,
        deliver_webhook=AsyncMock(return_value=True),
    )

    await worker.run_one()

    task = await get_task(db_conn, task_id)
    assert task["status"] == "completed"
    assert task["result"] == "result text"
    assert task["duration_seconds"] is not None
    mock_ollama.generate.assert_called_once_with("test:7b", "analyze this")
    mock_inhibitor.acquire.assert_called_once()
    mock_inhibitor.release.assert_called_once()


async def test_worker_handles_ollama_error(db_conn, mock_ollama, mock_inhibitor):
    from ringmaster.worker import Worker
    from ringmaster.scheduler import Scheduler
    from ringmaster.config import QueueConfig
    from ringmaster.db import get_task
    from ringmaster.ollama import OllamaError

    config = QueueConfig()
    sched = Scheduler(db_conn, config)
    task_id = await sched.submit_task("discrete", "bad:7b", "test", 1, "c")

    mock_ollama.generate.side_effect = OllamaError("model not found")

    worker = Worker(
        conn=db_conn,
        scheduler=sched,
        ollama=mock_ollama,
        inhibitor=mock_inhibitor,
        deliver_webhook=AsyncMock(return_value=True),
    )

    await worker.run_one()

    task = await get_task(db_conn, task_id)
    assert task["status"] == "failed"
    assert "model not found" in task["error"]
    mock_inhibitor.release.assert_called_once()


async def test_worker_fires_webhook(db_conn, mock_ollama, mock_inhibitor):
    from ringmaster.worker import Worker
    from ringmaster.scheduler import Scheduler
    from ringmaster.config import QueueConfig

    config = QueueConfig()
    sched = Scheduler(db_conn, config)
    await sched.submit_task(
        "discrete", "test:7b", "test", 1, "c",
        callback_url="http://localhost:9999/callback",
    )

    mock_deliver = AsyncMock(return_value=True)

    worker = Worker(
        conn=db_conn,
        scheduler=sched,
        ollama=mock_ollama,
        inhibitor=mock_inhibitor,
        deliver_webhook=mock_deliver,
    )

    await worker.run_one()

    mock_deliver.assert_called_once()
    payload = mock_deliver.call_args[0][1]
    assert payload.status == "completed"
    assert payload.result == "result text"


async def test_worker_skips_when_paused(db_conn, mock_ollama, mock_inhibitor):
    from ringmaster.worker import Worker
    from ringmaster.scheduler import Scheduler
    from ringmaster.config import QueueConfig

    config = QueueConfig()
    sched = Scheduler(db_conn, config)
    await sched.submit_task("discrete", "test:7b", "test", 1, "c")
    sched.pause()

    worker = Worker(
        conn=db_conn,
        scheduler=sched,
        ollama=mock_ollama,
        inhibitor=mock_inhibitor,
        deliver_webhook=AsyncMock(),
    )

    ran = await worker.run_one()
    assert ran is False
    mock_ollama.generate.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/levine/Documents/Repos/Ringmaster && python -m pytest tests/test_worker.py -v`
Expected: FAIL

- [ ] **Step 3: Implement worker.py**

```python
# ringmaster/worker.py
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Callable, Awaitable

import aiosqlite

from ringmaster.db import get_task, update_task_completed, update_task_started
from ringmaster.models import WebhookPayload
from ringmaster.ollama import OllamaClient, OllamaError
from ringmaster.power.inhibitor import SleepInhibitor
from ringmaster.scheduler import Scheduler

logger = logging.getLogger(__name__)


class Worker:
    """Runs tasks from the queue: loads model, runs inference, delivers results."""

    def __init__(
        self,
        conn: aiosqlite.Connection,
        scheduler: Scheduler,
        ollama: OllamaClient,
        inhibitor: SleepInhibitor,
        deliver_webhook: Callable[[str | None, WebhookPayload], Awaitable[bool]],
    ) -> None:
        self._conn = conn
        self._scheduler = scheduler
        self._ollama = ollama
        self._inhibitor = inhibitor
        self._deliver_webhook = deliver_webhook

    async def run_one(self) -> bool:
        """Try to run one task from the queue.

        Returns True if a task was run, False if queue was empty or paused.
        """
        task = await self._scheduler.next_task()
        if task is None:
            return False

        task_id = task["id"]
        model = task["model"]
        prompt = task["prompt"] or ""
        callback_url = task["callback_url"]

        logger.info("Starting task %s (model=%s, client=%s)",
                     task_id, model, task["client_id"])

        # Acquire sleep inhibitor
        self._inhibitor.acquire(f"Running task {task_id}")
        self._scheduler.set_current(task_id)
        await update_task_started(self._conn, task_id)

        start = time.monotonic()
        result: str | None = None
        error: str | None = None

        try:
            result = await self._ollama.generate(model, prompt)
        except OllamaError as exc:
            error = str(exc)
            logger.error("Task %s failed: %s", task_id, error)
        except Exception as exc:
            error = f"Unexpected error: {exc}"
            logger.exception("Task %s unexpected error", task_id)

        duration = time.monotonic() - start

        await update_task_completed(
            self._conn,
            task_id,
            result=result,
            error=error,
            duration_seconds=round(duration, 2),
        )

        # Release inhibitor and update scheduler state
        self._inhibitor.release()
        self._scheduler.on_task_completed()

        # Fire webhook
        payload = WebhookPayload(
            task_id=task_id,
            status="failed" if error else "completed",
            result=result,
            error=error,
            model=model,
            duration_seconds=round(duration, 2),
            completed_at=datetime.now(timezone.utc),
        )
        await self._deliver_webhook(callback_url, payload)

        logger.info("Task %s %s (%.1fs)", task_id,
                     "failed" if error else "completed", duration)
        return True
```

- [ ] **Step 4: Run tests**

Run: `cd /home/levine/Documents/Repos/Ringmaster && python -m pytest tests/test_worker.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
cd /home/levine/Documents/Repos/Ringmaster
git add ringmaster/worker.py tests/test_worker.py
git commit -m "feat: background worker — runs tasks, manages inhibitor, fires webhooks"
```

---

### Task 11: FastAPI App + Routes

**Files:**
- Create: `ringmaster/server/app.py`
- Create: `ringmaster/server/deps.py`
- Create: `ringmaster/server/routes/__init__.py`
- Create: `ringmaster/server/routes/tasks.py`
- Create: `ringmaster/server/routes/sessions.py`
- Create: `ringmaster/server/routes/queue.py`
- Create: `ringmaster/server/routes/status.py`
- Create: `ringmaster/server/routes/auth.py`
- Create: `tests/test_routes_tasks.py`
- Create: `tests/test_routes_sessions.py`
- Create: `tests/test_routes_queue.py`
- Create: `tests/test_routes_status.py`
- Create: `tests/test_routes_auth.py`

- [ ] **Step 1: Write the failing test for task routes**

```python
# tests/test_routes_tasks.py
import pytest
from httpx import AsyncClient, ASGITransport


@pytest.fixture
async def app_client(tmp_path):
    from ringmaster.server.app import create_app
    from ringmaster.config import load_config

    config_file = tmp_path / "ringmaster.yaml"
    config_file.write_text(f"""
server:
  port: 8420
ollama:
  host: "http://localhost:11434"
queue:
  max_queue_depth: 100
  default_priority: 3
auth:
  token_file: "{tmp_path}/tokens.json"
""")
    app = await create_app(config_file, db_path=str(tmp_path / "test.db"))

    # Register a test client and get token
    from ringmaster.server.deps import get_auth_manager
    auth = get_auth_manager()
    token = auth.register("test-client")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.headers["Authorization"] = f"Bearer {token}"
        yield client


async def test_submit_task(app_client):
    resp = await app_client.post("/tasks", json={
        "task_type": "discrete",
        "model": "test:7b",
        "prompt": "test prompt",
        "client_id": "test-client",
        "priority": 1,
    })
    assert resp.status_code == 201
    data = resp.json()
    assert "id" in data
    assert data["status"] == "queued"
    assert data["model"] == "test:7b"


async def test_get_task(app_client):
    resp = await app_client.post("/tasks", json={
        "task_type": "discrete",
        "model": "test:7b",
        "prompt": "test",
        "client_id": "test-client",
    })
    task_id = resp.json()["id"]

    resp = await app_client.get(f"/tasks/{task_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == task_id


async def test_list_tasks(app_client):
    await app_client.post("/tasks", json={
        "task_type": "discrete", "model": "m:7b", "prompt": "t1", "client_id": "c"
    })
    await app_client.post("/tasks", json={
        "task_type": "discrete", "model": "m:7b", "prompt": "t2", "client_id": "c"
    })

    resp = await app_client.get("/tasks")
    assert resp.status_code == 200
    assert len(resp.json()) >= 2


async def test_get_nonexistent_task(app_client):
    resp = await app_client.get("/tasks/nonexistent-id")
    assert resp.status_code == 404


async def test_unauthenticated_request(tmp_path):
    from ringmaster.server.app import create_app
    from httpx import AsyncClient, ASGITransport

    config_file = tmp_path / "ringmaster2.yaml"
    config_file.write_text(f"""
server:
  port: 8420
auth:
  token_file: "{tmp_path}/tokens2.json"
""")
    app = await create_app(config_file, db_path=str(tmp_path / "test2.db"))

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/tasks")
    assert resp.status_code == 401
```

- [ ] **Step 2: Write the failing test for queue routes**

```python
# tests/test_routes_queue.py
import pytest
from httpx import AsyncClient, ASGITransport


@pytest.fixture
async def app_client(tmp_path):
    from ringmaster.server.app import create_app
    from ringmaster.server.deps import get_auth_manager

    config_file = tmp_path / "ringmaster.yaml"
    config_file.write_text(f"""
server:
  port: 8420
auth:
  token_file: "{tmp_path}/tokens.json"
""")
    app = await create_app(config_file, db_path=str(tmp_path / "test.db"))
    auth = get_auth_manager()
    token = auth.register("test-client")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.headers["Authorization"] = f"Bearer {token}"
        yield client


async def test_pause_queue(app_client):
    resp = await app_client.post("/queue/pause")
    assert resp.status_code == 200
    assert resp.json()["queue_paused"] is True


async def test_resume_queue(app_client):
    await app_client.post("/queue/pause")
    resp = await app_client.post("/queue/resume")
    assert resp.status_code == 200
    assert resp.json()["queue_paused"] is False


async def test_drain_queue(app_client):
    resp = await app_client.post("/queue/drain")
    assert resp.status_code == 200
```

- [ ] **Step 3: Write the failing test for status routes**

```python
# tests/test_routes_status.py
import pytest
from httpx import AsyncClient, ASGITransport


@pytest.fixture
async def app_client(tmp_path):
    from ringmaster.server.app import create_app
    from ringmaster.server.deps import get_auth_manager

    config_file = tmp_path / "ringmaster.yaml"
    config_file.write_text(f"""
server:
  port: 8420
auth:
  token_file: "{tmp_path}/tokens.json"
""")
    app = await create_app(config_file, db_path=str(tmp_path / "test.db"))
    auth = get_auth_manager()
    token = auth.register("test-client")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.headers["Authorization"] = f"Bearer {token}"
        yield client


async def test_health(app_client):
    # Health should work without auth too, but test with auth
    resp = await app_client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["alive"] is True
    assert "version" in data


async def test_status(app_client):
    resp = await app_client.get("/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "state" in data
    assert "queue_depth" in data
    assert "user_present" in data
    assert "queue_paused" in data
```

- [ ] **Step 4: Write the failing test for auth routes**

```python
# tests/test_routes_auth.py
import pytest
from httpx import AsyncClient, ASGITransport


@pytest.fixture
async def app_client(tmp_path):
    from ringmaster.server.app import create_app
    from ringmaster.server.deps import get_auth_manager

    config_file = tmp_path / "ringmaster.yaml"
    config_file.write_text(f"""
server:
  port: 8420
auth:
  token_file: "{tmp_path}/tokens.json"
""")
    app = await create_app(config_file, db_path=str(tmp_path / "test.db"))
    auth = get_auth_manager()
    token = auth.register("admin")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.headers["Authorization"] = f"Bearer {token}"
        yield client


async def test_register_client(app_client):
    resp = await app_client.post("/auth/register", json={"client_id": "new-client"})
    assert resp.status_code == 200
    data = resp.json()
    assert "token" in data
    assert data["client_id"] == "new-client"


async def test_revoke_client(app_client):
    # Register first
    resp = await app_client.post("/auth/register", json={"client_id": "temp-client"})
    token = resp.json()["token"]

    # Revoke
    resp = await app_client.post("/auth/revoke", json={"client_id": "temp-client"})
    assert resp.status_code == 200
```

- [ ] **Step 5: Write the failing test for session routes**

```python
# tests/test_routes_sessions.py
import pytest
from httpx import AsyncClient, ASGITransport


@pytest.fixture
async def app_client(tmp_path):
    from ringmaster.server.app import create_app
    from ringmaster.server.deps import get_auth_manager

    config_file = tmp_path / "ringmaster.yaml"
    config_file.write_text(f"""
server:
  port: 8420
auth:
  token_file: "{tmp_path}/tokens.json"
""")
    app = await create_app(config_file, db_path=str(tmp_path / "test.db"))
    auth = get_auth_manager()
    token = auth.register("test-client")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.headers["Authorization"] = f"Bearer {token}"
        yield client


async def test_open_session(app_client):
    resp = await app_client.post("/sessions", json={
        "model": "qwen:14b",
        "client_id": "test-client",
        "session_idle_timeout_seconds": 600,
    })
    assert resp.status_code == 201
    data = resp.json()
    assert "id" in data
    assert data["status"] == "active"


async def test_get_session(app_client):
    resp = await app_client.post("/sessions", json={
        "model": "qwen:14b", "client_id": "test-client"
    })
    session_id = resp.json()["id"]

    resp = await app_client.get(f"/sessions/{session_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == session_id


async def test_close_session(app_client):
    resp = await app_client.post("/sessions", json={
        "model": "qwen:14b", "client_id": "test-client"
    })
    session_id = resp.json()["id"]

    resp = await app_client.delete(f"/sessions/{session_id}")
    assert resp.status_code == 200

    resp = await app_client.get(f"/sessions/{session_id}")
    assert resp.json()["status"] == "closed"


async def test_keepalive_session(app_client):
    resp = await app_client.post("/sessions", json={
        "model": "qwen:14b", "client_id": "test-client"
    })
    session_id = resp.json()["id"]

    resp = await app_client.post(f"/sessions/{session_id}/keepalive")
    assert resp.status_code == 200
```

- [ ] **Step 6: Run all route tests to verify they fail**

Run: `cd /home/levine/Documents/Repos/Ringmaster && python -m pytest tests/test_routes_tasks.py tests/test_routes_sessions.py tests/test_routes_queue.py tests/test_routes_status.py tests/test_routes_auth.py -v`
Expected: FAIL

- [ ] **Step 7: Implement deps.py (dependency injection)**

```python
# ringmaster/server/deps.py
from __future__ import annotations

from typing import Any

import aiosqlite

from ringmaster.config import RingmasterConfig
from ringmaster.scheduler import Scheduler
from ringmaster.server.auth import AuthManager

# Module-level singletons set during app creation
_config: RingmasterConfig | None = None
_db: aiosqlite.Connection | None = None
_scheduler: Scheduler | None = None
_auth: AuthManager | None = None


def set_deps(
    config: RingmasterConfig,
    db: aiosqlite.Connection,
    scheduler: Scheduler,
    auth: AuthManager,
) -> None:
    global _config, _db, _scheduler, _auth
    _config = config
    _db = db
    _scheduler = scheduler
    _auth = auth


def get_config() -> RingmasterConfig:
    assert _config is not None
    return _config


def get_db_conn() -> aiosqlite.Connection:
    assert _db is not None
    return _db


def get_scheduler() -> Scheduler:
    assert _scheduler is not None
    return _scheduler


def get_auth_manager() -> AuthManager:
    assert _auth is not None
    return _auth
```

- [ ] **Step 8: Implement route modules**

```python
# ringmaster/server/routes/__init__.py
```

```python
# ringmaster/server/routes/tasks.py
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ringmaster.models import TaskSubmitRequest, TaskResponse
from ringmaster.scheduler import QueueFullError
from ringmaster.server.deps import get_db_conn, get_scheduler
from ringmaster.db import get_task, list_tasks

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.post("", status_code=201)
async def submit_task(req: TaskSubmitRequest) -> dict:
    sched = get_scheduler()
    try:
        task_id = await sched.submit_task(
            task_type=req.task_type,
            model=req.model,
            prompt=req.prompt,
            priority=req.priority,
            client_id=req.client_id,
            callback_url=req.callback_url,
            unattended_policy=req.unattended_policy,
            deadline=req.deadline,
            metadata=req.metadata,
        )
    except QueueFullError as exc:
        raise HTTPException(status_code=429, detail=str(exc))

    conn = get_db_conn()
    task = await get_task(conn, task_id)
    return task


@router.get("")
async def list_all_tasks(
    status: str | None = None,
    client_id: str | None = None,
) -> list[dict]:
    conn = get_db_conn()
    return await list_tasks(conn, status=status, client_id=client_id)


@router.get("/{task_id}")
async def get_task_by_id(task_id: str) -> dict:
    conn = get_db_conn()
    task = await get_task(conn, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.post("/current/cancel")
async def cancel_current_task() -> dict:
    sched = get_scheduler()
    task_id = await sched.cancel_current()
    if task_id is None:
        raise HTTPException(status_code=404, detail="No task currently running")
    return {"cancelled": task_id}


@router.post("/{task_id}/approve")
async def approve_task(task_id: str) -> dict:
    sched = get_scheduler()
    await sched.approve_task(task_id)
    return {"approved": task_id}


@router.post("/{task_id}/defer")
async def defer_task(task_id: str) -> dict:
    sched = get_scheduler()
    await sched.defer_task(task_id)
    return {"deferred": task_id}
```

```python
# ringmaster/server/routes/sessions.py
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ringmaster.models import SessionOpenRequest
from ringmaster.server.deps import get_db_conn
from ringmaster.db import insert_session, get_session, close_session, update_session_activity

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.post("", status_code=201)
async def open_session(req: SessionOpenRequest) -> dict:
    conn = get_db_conn()
    session_id = await insert_session(
        conn,
        client_id=req.client_id,
        model=req.model,
        idle_timeout_seconds=req.session_idle_timeout_seconds,
    )
    session = await get_session(conn, session_id)
    return session


@router.get("/{session_id}")
async def get_session_by_id(session_id: str) -> dict:
    conn = get_db_conn()
    session = await get_session(conn, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@router.post("/{session_id}/keepalive")
async def keepalive(session_id: str) -> dict:
    conn = get_db_conn()
    session = await get_session(conn, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    await update_session_activity(conn, session_id)
    return {"session_id": session_id, "status": "renewed"}


@router.delete("/{session_id}")
async def delete_session(session_id: str) -> dict:
    conn = get_db_conn()
    session = await get_session(conn, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    await close_session(conn, session_id)
    return {"session_id": session_id, "status": "closed"}
```

```python
# ringmaster/server/routes/queue.py
from __future__ import annotations

from fastapi import APIRouter

from ringmaster.server.deps import get_scheduler

router = APIRouter(prefix="/queue", tags=["queue"])


@router.post("/pause")
async def pause_queue() -> dict:
    sched = get_scheduler()
    sched.pause()
    return {"queue_paused": True}


@router.post("/resume")
async def resume_queue() -> dict:
    sched = get_scheduler()
    sched.resume()
    return {"queue_paused": False}


@router.post("/drain")
async def drain_queue() -> dict:
    sched = get_scheduler()
    sched.drain()
    return {"draining": True}
```

```python
# ringmaster/server/routes/status.py
from __future__ import annotations

import time

from fastapi import APIRouter

import ringmaster
from ringmaster.models import HealthResponse, StatusResponse
from ringmaster.server.deps import get_config, get_scheduler

router = APIRouter(tags=["status"])

_start_time = time.monotonic()


@router.get("/health")
async def health() -> HealthResponse:
    return HealthResponse(
        alive=True,
        version=ringmaster.__version__,
        uptime_seconds=round(time.monotonic() - _start_time, 1),
    )


@router.get("/status")
async def status() -> dict:
    sched = get_scheduler()
    depth = await sched.queue_depth()
    current = sched.current_task_id

    # Determine state
    has_task = current is not None
    # User presence detection would go here — for now, default False
    user_present = False

    if has_task and user_present:
        state = "both"
    elif has_task:
        state = "ai_working"
    elif user_present:
        state = "user_active"
    else:
        state = "idle"

    return {
        "state": state,
        "queue_depth": depth,
        "current_task": current,
        "user_present": user_present,
        "queue_paused": sched.is_paused,
    }


@router.get("/gpus")
async def gpus() -> list[dict]:
    config = get_config()
    return [
        {
            "label": gpu.label,
            "role": gpu.role,
            "vram_mb": gpu.fingerprint.vram_mb,
            "current_model": None,  # Phase 1: no live tracking
            "status": "available",
        }
        for gpu in config.gpus
    ]


@router.get("/models")
async def models() -> dict:
    """List available Ollama models. Returns empty list if Ollama is unreachable."""
    from ringmaster.ollama import OllamaClient, OllamaError

    config = get_config()
    client = OllamaClient(config.ollama.host)
    try:
        model_list = await client.list_models()
        return {"models": model_list}
    except OllamaError:
        return {"models": [], "error": "Ollama unreachable"}
    finally:
        await client.close()
```

```python
# ringmaster/server/routes/auth.py
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ringmaster.server.deps import get_auth_manager

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    client_id: str


class RevokeRequest(BaseModel):
    client_id: str


@router.post("/register")
async def register_client(req: RegisterRequest) -> dict:
    auth = get_auth_manager()
    token = auth.register(req.client_id)
    return {"client_id": req.client_id, "token": token}


@router.post("/revoke")
async def revoke_client(req: RevokeRequest) -> dict:
    auth = get_auth_manager()
    auth.revoke(req.client_id)
    return {"client_id": req.client_id, "revoked": True}
```

- [ ] **Step 9: Implement app.py (FastAPI app factory)**

```python
# ringmaster/server/app.py
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ringmaster.config import load_config
from ringmaster.db import get_db, init_db
from ringmaster.scheduler import Scheduler
from ringmaster.server.auth import AuthManager
from ringmaster.server.deps import set_deps, get_auth_manager
from ringmaster.server.routes import tasks, sessions, queue, status, auth


async def create_app(
    config_path: Path | str,
    db_path: str | None = None,
) -> FastAPI:
    """Create and configure the FastAPI application."""
    config_path = Path(config_path)
    config = load_config(config_path)

    # Database
    effective_db_path = db_path or "ringmaster.db"
    conn = await get_db(effective_db_path)
    await init_db(conn)

    # Scheduler
    scheduler = Scheduler(conn, config.queue)

    # Auth
    auth_mgr = AuthManager()
    auth_mgr.load(config.auth.token_file)

    # Set global deps
    set_deps(config, conn, scheduler, auth_mgr)

    # Build app
    app = FastAPI(title="Ringmaster", version="0.1.0")

    # Auth middleware
    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        # Skip auth for health endpoint
        if request.url.path == "/health":
            return await call_next(request)

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse(status_code=401, content={"detail": "Missing token"})

        token = auth_header[7:]
        auth_mgr = get_auth_manager()
        client_id = auth_mgr.verify(token)
        if client_id is None:
            return JSONResponse(status_code=401, content={"detail": "Invalid token"})

        request.state.client_id = client_id
        return await call_next(request)

    # Register routes
    app.include_router(tasks.router)
    app.include_router(sessions.router)
    app.include_router(queue.router)
    app.include_router(status.router)
    app.include_router(auth.router)

    return app
```

- [ ] **Step 10: Run all route tests**

Run: `cd /home/levine/Documents/Repos/Ringmaster && python -m pytest tests/test_routes_tasks.py tests/test_routes_sessions.py tests/test_routes_queue.py tests/test_routes_status.py tests/test_routes_auth.py -v`
Expected: All tests PASS

- [ ] **Step 11: Commit**

```bash
cd /home/levine/Documents/Repos/Ringmaster
git add ringmaster/server/ tests/test_routes_*.py
git commit -m "feat: FastAPI REST API with task, session, queue, status, and auth routes"
```

---

### Task 12: CLI

**Files:**
- Create: `ringmaster/cli/__init__.py`
- Create: `ringmaster/cli/main.py`
- Create: `tests/test_cli.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli.py
import pytest
from click.testing import CliRunner
from unittest.mock import patch, MagicMock
import json


@pytest.fixture
def runner():
    return CliRunner()


def test_cli_status(runner):
    from ringmaster.cli.main import cli

    with patch("ringmaster.cli.main.httpx.get") as mock_get:
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "state": "idle",
                "queue_depth": 0,
                "current_task": None,
                "user_present": False,
                "queue_paused": False,
            },
        )
        result = runner.invoke(cli, ["status", "--host", "http://localhost:8420",
                                      "--token", "test-token"])
    assert result.exit_code == 0
    assert "idle" in result.output


def test_cli_queue(runner):
    from ringmaster.cli.main import cli

    with patch("ringmaster.cli.main.httpx.get") as mock_get:
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: [
                {"id": "abc", "model": "test:7b", "priority": 1, "status": "queued",
                 "client_id": "netintel"},
            ],
        )
        result = runner.invoke(cli, ["queue", "--host", "http://localhost:8420",
                                      "--token", "test-token"])
    assert result.exit_code == 0
    assert "abc" in result.output


def test_cli_submit(runner):
    from ringmaster.cli.main import cli

    with patch("ringmaster.cli.main.httpx.post") as mock_post:
        mock_post.return_value = MagicMock(
            status_code=201,
            json=lambda: {"id": "new-task-id", "status": "queued", "model": "test:7b"},
        )
        result = runner.invoke(cli, [
            "submit", "--host", "http://localhost:8420", "--token", "test-token",
            "--model", "test:7b", "--prompt", "analyze this", "--priority", "1",
            "--client-id", "test",
        ])
    assert result.exit_code == 0
    assert "new-task-id" in result.output


def test_cli_pause(runner):
    from ringmaster.cli.main import cli

    with patch("ringmaster.cli.main.httpx.post") as mock_post:
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"queue_paused": True},
        )
        result = runner.invoke(cli, ["pause", "--host", "http://localhost:8420",
                                      "--token", "test-token"])
    assert result.exit_code == 0
    assert "paused" in result.output.lower()


def test_cli_resume(runner):
    from ringmaster.cli.main import cli

    with patch("ringmaster.cli.main.httpx.post") as mock_post:
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"queue_paused": False},
        )
        result = runner.invoke(cli, ["resume", "--host", "http://localhost:8420",
                                      "--token", "test-token"])
    assert result.exit_code == 0
    assert "resumed" in result.output.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/levine/Documents/Repos/Ringmaster && python -m pytest tests/test_cli.py -v`
Expected: FAIL

- [ ] **Step 3: Implement cli/main.py**

```python
# ringmaster/cli/__init__.py
```

```python
# ringmaster/cli/main.py
from __future__ import annotations

import json

import click
import httpx


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@click.group()
def cli() -> None:
    """Ringmaster — GPU workstation AI task orchestrator."""


@cli.command()
@click.option("--host", default="http://localhost:8420", help="Ringmaster server URL")
@click.option("--token", envvar="RINGMASTER_TOKEN", required=True, help="Auth token")
def status(host: str, token: str) -> None:
    """Show workstation and queue status."""
    resp = httpx.get(f"{host}/status", headers=_headers(token))
    data = resp.json()
    click.echo(f"State:        {data['state']}")
    click.echo(f"Queue depth:  {data['queue_depth']}")
    click.echo(f"Current task: {data['current_task'] or 'none'}")
    click.echo(f"User present: {data['user_present']}")
    click.echo(f"Queue paused: {data['queue_paused']}")


@cli.command()
@click.option("--host", default="http://localhost:8420")
@click.option("--token", envvar="RINGMASTER_TOKEN", required=True)
@click.option("--status-filter", "status_filter", default=None, help="Filter by status")
def queue(host: str, token: str, status_filter: str | None) -> None:
    """List queued tasks."""
    params = {}
    if status_filter:
        params["status"] = status_filter
    resp = httpx.get(f"{host}/tasks", headers=_headers(token), params=params)
    tasks = resp.json()
    if not tasks:
        click.echo("Queue is empty.")
        return
    for t in tasks:
        click.echo(f"  {t['id'][:8]}  pri={t['priority']}  {t['status']:12s}  "
                    f"{t['model']:20s}  client={t['client_id']}")


@cli.command()
@click.option("--host", default="http://localhost:8420")
@click.option("--token", envvar="RINGMASTER_TOKEN", required=True)
@click.option("--model", required=True, help="Ollama model name")
@click.option("--prompt", required=True, help="Prompt text")
@click.option("--priority", type=int, default=None, help="Priority tier (1=highest)")
@click.option("--client-id", required=True, help="Client identifier")
@click.option("--callback-url", default=None, help="Webhook callback URL")
def submit(host: str, token: str, model: str, prompt: str, priority: int | None,
           client_id: str, callback_url: str | None) -> None:
    """Submit a discrete task."""
    payload = {
        "task_type": "discrete",
        "model": model,
        "prompt": prompt,
        "client_id": client_id,
    }
    if priority is not None:
        payload["priority"] = priority
    if callback_url:
        payload["callback_url"] = callback_url

    resp = httpx.post(f"{host}/tasks", headers=_headers(token), json=payload)
    data = resp.json()
    click.echo(f"Task submitted: {data['id']}")
    click.echo(f"Status: {data['status']}")


@cli.command()
@click.option("--host", default="http://localhost:8420")
@click.option("--token", envvar="RINGMASTER_TOKEN", required=True)
def pause(host: str, token: str) -> None:
    """Pause the task queue."""
    resp = httpx.post(f"{host}/queue/pause", headers=_headers(token))
    click.echo("Queue paused.")


@cli.command()
@click.option("--host", default="http://localhost:8420")
@click.option("--token", envvar="RINGMASTER_TOKEN", required=True)
def resume(host: str, token: str) -> None:
    """Resume the task queue."""
    resp = httpx.post(f"{host}/queue/resume", headers=_headers(token))
    click.echo("Queue resumed.")


@cli.command()
@click.option("--host", default="http://localhost:8420")
@click.option("--token", envvar="RINGMASTER_TOKEN", required=True)
def drain(host: str, token: str) -> None:
    """Finish current task, then pause."""
    resp = httpx.post(f"{host}/queue/drain", headers=_headers(token))
    click.echo("Queue draining — will pause after current task.")


@cli.command("cancel-current")
@click.option("--host", default="http://localhost:8420")
@click.option("--token", envvar="RINGMASTER_TOKEN", required=True)
def cancel_current(host: str, token: str) -> None:
    """Cancel the currently running task."""
    resp = httpx.post(f"{host}/tasks/current/cancel", headers=_headers(token))
    if resp.status_code == 404:
        click.echo("No task currently running.")
    else:
        data = resp.json()
        click.echo(f"Cancelled task: {data['cancelled']}")


@cli.command()
@click.option("--host", default="http://localhost:8420")
@click.option("--token", envvar="RINGMASTER_TOKEN", required=True)
def gpu(host: str, token: str) -> None:
    """Show GPU inventory."""
    resp = httpx.get(f"{host}/gpus", headers=_headers(token))
    gpus = resp.json()
    if not gpus:
        click.echo("No GPUs configured.")
        return
    for g in gpus:
        model_info = f"  loaded: {g['current_model']}" if g.get("current_model") else ""
        click.echo(f"  {g['label']:12s}  {g['role']:8s}  {g['vram_mb']}MB  "
                    f"{g['status']}{model_info}")


@cli.command("init")
@click.option("--config", "config_path", default="ringmaster.yaml",
              help="Path to write config file")
def init_gpus(config_path: str) -> None:
    """Detect GPUs and create initial configuration."""
    from ringmaster.gpu.detect import detect_gpus
    import yaml

    gpus = detect_gpus()
    if not gpus:
        click.echo("No GPUs detected. Is ROCm/CUDA installed?")
        return

    click.echo(f"Found {len(gpus)} GPU(s):")
    gpu_configs = []
    for i, gpu in enumerate(gpus):
        click.echo(f"  {i + 1}. {gpu.vendor} {gpu.model} "
                    f"({gpu.vram_mb}MB, PCI {gpu.pci_slot})")
        label = click.prompt(f"  Label for GPU {i + 1}", default=f"gpu-{i}")
        role = click.prompt(f"  Role for '{label}'",
                           type=click.Choice(["compute", "gaming", "both"]),
                           default="compute")
        prefer = ["default"] if i == 0 else ["fallback"]
        gpu_configs.append({
            "label": label,
            "role": role,
            "prefer_for": prefer,
            "fingerprint": {
                "vendor": gpu.vendor,
                "model": gpu.model,
                "vram_mb": gpu.vram_mb,
                "serial": gpu.serial,
                "device_id": gpu.device_id,
            },
        })

    config = {
        "server": {"host": "0.0.0.0", "port": 8420},
        "gpus": gpu_configs,
        "ollama": {"host": "http://localhost:11434"},
        "notifications": {"backend": "desktop"},
        "power": {
            "sleep_command": "systemctl suspend",
            "display_off_command": "xset dpms force off",
            "lock_command": "loginctl lock-session",
        },
        "queue": {"max_queue_depth": 100, "default_priority": 3},
        "auth": {"token_file": "tokens.json"},
    }

    from pathlib import Path
    Path(config_path).write_text(yaml.dump(config, default_flow_style=False))
    click.echo(f"\nConfig written to {config_path}")
    click.echo("Review it, then start the server with: ringmaster-server -c " + config_path)


if __name__ == "__main__":
    cli()
```

- [ ] **Step 4: Run tests**

Run: `cd /home/levine/Documents/Repos/Ringmaster && python -m pytest tests/test_cli.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
cd /home/levine/Documents/Repos/Ringmaster
git add ringmaster/cli/ tests/test_cli.py
git commit -m "feat: CLI — submit, queue, status, pause, resume, drain, cancel, gpu"
```

---

### Task 13: Server Entry Point + systemd Unit

**Files:**
- Create: `ringmaster/server/run.py`
- Create: `ringmaster.service`

- [ ] **Step 1: Implement server entry point**

```python
# ringmaster/server/run.py
from __future__ import annotations

import asyncio
import logging
import signal
import time
from pathlib import Path

import uvicorn

from ringmaster.config import load_config
from ringmaster.db import get_db, init_db
from ringmaster.ollama import OllamaClient
from ringmaster.power.inhibitor import SleepInhibitor
from ringmaster.scheduler import Scheduler
from ringmaster.server.app import create_app
from ringmaster.server.auth import AuthManager
from ringmaster.server.deps import set_deps, get_scheduler
from ringmaster.webhooks import deliver_webhook
from ringmaster.worker import Worker

logger = logging.getLogger("ringmaster")


async def worker_loop(worker: Worker, interval: float = 2.0) -> None:
    """Continuously poll for and run tasks."""
    while True:
        try:
            ran = await worker.run_one()
            if not ran:
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("Worker loop error")
            await asyncio.sleep(interval)


def main() -> None:
    """Start the Ringmaster server."""
    import argparse

    parser = argparse.ArgumentParser(description="Ringmaster server")
    parser.add_argument(
        "-c", "--config",
        default="ringmaster.yaml",
        help="Path to config file (default: ringmaster.yaml)",
    )
    args = parser.parse_args()

    config_path = Path(args.config)
    config = load_config(config_path)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    async def run() -> None:
        app = await create_app(config_path)

        # Create worker components
        ollama = OllamaClient(config.ollama.host)
        inhibitor = SleepInhibitor()
        scheduler = get_scheduler()

        worker = Worker(
            conn=app.state.db if hasattr(app, "state") else None,
            scheduler=scheduler,
            ollama=ollama,
            inhibitor=inhibitor,
            deliver_webhook=deliver_webhook,
        )

        # Start worker loop as background task
        worker_task = asyncio.create_task(worker_loop(worker))

        # Run uvicorn
        server_config = uvicorn.Config(
            app, host=config.server.host, port=config.server.port,
            log_level="info",
        )
        server = uvicorn.Server(server_config)

        try:
            await server.serve()
        finally:
            worker_task.cancel()
            await worker_task
            inhibitor.release()
            await ollama.close()

    asyncio.run(run())


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Create systemd service unit**

```ini
# ringmaster.service
# Install to: ~/.config/systemd/user/ringmaster.service
# Enable with: systemctl --user enable ringmaster
# Start with:  systemctl --user start ringmaster

[Unit]
Description=Ringmaster AI Task Orchestrator
After=network-online.target ollama.service
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 -m ringmaster.server.run -c %h/.config/ringmaster/ringmaster.yaml
Restart=on-failure
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
```

- [ ] **Step 3: Commit**

```bash
cd /home/levine/Documents/Repos/Ringmaster
git add ringmaster/server/run.py ringmaster.service
git commit -m "feat: server entry point and systemd user service unit"
```

---

### Task 14: Run Full Test Suite + Fix Issues

- [ ] **Step 1: Run the complete test suite**

Run: `cd /home/levine/Documents/Repos/Ringmaster && python -m pytest tests/ -v --tb=short`
Expected: All tests PASS

- [ ] **Step 2: Fix any failures found in step 1**

Address each failure, re-run affected tests to confirm the fix.

- [ ] **Step 3: Run ruff lint**

Run: `cd /home/levine/Documents/Repos/Ringmaster && python -m ruff check ringmaster/ tests/`
Expected: No errors (or fix any that appear)

- [ ] **Step 4: Commit any fixes**

```bash
cd /home/levine/Documents/Repos/Ringmaster
git add -A
git commit -m "fix: resolve test suite and lint issues"
```

---

### Task 15: Integration Smoke Test

- [ ] **Step 1: Verify the server starts**

Run: `cd /home/levine/Documents/Repos/Ringmaster && timeout 5 python -m ringmaster.server.run -c ringmaster.example.yaml 2>&1 || true`
Expected: Server starts listening (may fail to connect to Ollama — that's OK, it should start the HTTP server)

- [ ] **Step 2: Verify the CLI connects**

Run (in a separate terminal while server is running): `ringmaster status --host http://localhost:8420 --token <test-token>`
Expected: Shows status output

- [ ] **Step 3: Verify GPU init works**

Run: `ringmaster init` (if the workstation has GPUs with ROCm)
Expected: Detects GPUs and prompts for labels

- [ ] **Step 4: Commit final state**

```bash
cd /home/levine/Documents/Repos/Ringmaster
git add -A
git commit -m "chore: integration smoke test verified"
```
