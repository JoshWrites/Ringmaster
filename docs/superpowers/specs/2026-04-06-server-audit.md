# Ringmaster Server Readiness Audit

**Date:** 2026-04-06
**Purpose:** Evaluate the current server codebase for readiness to support the client app as described in the client-app design spec.

## Summary

The server is well-structured, well-tested (150 tests, all passing), and follows clean architectural patterns. However, it was designed for single-client, single-GPU, non-streaming, task-only workloads. Supporting the client app requires significant additions and some refactoring. There are no fundamental blockers — the codebase is a solid foundation.

---

## 1. Concurrency Model

### Current State

- **Single shared SQLite connection** (`deps._db_conn`) used by both the HTTP request handlers and the background worker loop. Accessed concurrently from the uvicorn async workers and the worker task.
- SQLite is opened with `check_same_thread=False` and WAL mode. The `deps.py` docstring claims this is safe because "SQLite WAL mode serialises writes internally."
- **The Scheduler holds in-memory state** (`_paused`, `_draining`, `_current_task_id`) with no locking. These are read/written from both the HTTP handlers (via route calls) and the worker loop.
- FastAPI route handlers are synchronous (`def`, not `async def`). Uvicorn runs sync handlers in a threadpool. This means multiple HTTP requests can execute route handler code concurrently in different threads.

### Gaps

**GAP-1: Thread safety of Scheduler state.** The Scheduler's `_paused`, `_draining`, and `_current_task_id` are plain Python attributes. When sync route handlers run in uvicorn's threadpool, multiple threads can read/write these simultaneously. Python's GIL makes single-attribute reads/writes atomic for simple types (bool, str), so this is *currently safe by accident*. But it's fragile — any future change that adds compound state transitions (read-then-write) will introduce races without explicit locking.

**GAP-2: SQLite connection sharing across threads.** The single `sqlite3.Connection` is shared between the threadpool (sync route handlers) and the async worker task. While `check_same_thread=False` disables Python's thread check, SQLite itself is not fully thread-safe for concurrent writes on the same connection. WAL mode helps (concurrent reads are safe, writes are serialized at the SQLite level), but concurrent `conn.execute()` + `conn.commit()` from different threads on the same connection object can corrupt the connection state. The current low concurrency (one worker + a few API requests) makes this unlikely to manifest, but under the load of multiple clients making concurrent session requests, it becomes a real risk.

**GAP-3: No connection pooling.** For higher concurrency, each request should get its own connection (or use a connection pool). The current single-connection model doesn't scale.

### Recommendations

- Add a threading lock to the Scheduler for state mutations (pause, resume, drain, set_current, on_task_completed, cancel_current).
- Switch to a connection-per-request model using a connection pool or FastAPI's dependency injection to create/close connections per request.
- Alternatively, convert all route handlers to `async def` and use `aiosqlite` (already a dependency) so all DB access happens on the event loop, eliminating the threadpool concurrency issue entirely.

---

## 2. Session Model

### Current State

- Sessions exist in the DB with states: `open`, `closed`, `expired`.
- Sessions are created immediately in `open` status — there is no concept of requesting a session and waiting for GPU availability.
- No `POST /sessions/{id}/generate` endpoint exists despite being mentioned in the README's API table. Sessions can be opened, retrieved, keepalived, and closed, but there's no way to actually run inference through a session.
- Sessions have no `task_class`, `app`, `user`, or `manifest` fields.
- No session-to-GPU binding logic — `gpu_label` is a field but is never populated.

### Gaps

**GAP-4: No session lifecycle for queued grants.** The spec requires `requested → queued → granted → active → closed`. Currently sessions go straight to `open`. The entire grant-queue mechanism needs to be built.

**GAP-5: No session generate endpoint.** The session concept is incomplete — you can open a session but can't use it for inference. Need `POST /sessions/{id}/generate` that forwards to Ollama.

**GAP-6: No streaming support.** The `OllamaClient` is hardcoded to `stream=False`. The client app requires full end-to-end streaming for `/api/chat` and `/api/generate`. The server needs streaming proxy endpoints that relay Ollama's NDJSON stream to the client.

**GAP-7: No task class support.** Sessions and tasks have no concept of `interactive`, `batch`, or `scheduled` task classes. This field needs to be added to both the DB schema and the Pydantic models.

**GAP-8: No app manifest support.** No endpoint, storage, or logic for receiving and processing client app manifests.

**GAP-9: No model request endpoint.** No `POST /models/requests` for clients to request model installation.

---

## 3. Scheduling / Policy Engine

### Current State

- The Scheduler is a simple priority-FIFO queue. Lower priority number = dispatched first. Deadline-bearing tasks go before non-deadline tasks at the same priority.
- Priority is set by the client at submission time (1-5 integer).
- No concept of task classes or policy-based priority assignment.
- The scheduler has no awareness of sessions — it only manages tasks.

### Gaps

**GAP-10: No session-aware scheduling.** The scheduler manages tasks only. Sessions need to participate in the scheduling queue so that session requests wait for GPU availability alongside tasks.

**GAP-11: No policy engine.** The spec requires the server to decide priority based on task class, queue state, manifests, and user presence. Currently the client sets priority directly. A policy layer that maps (task_class, client_id, manifest, queue_state) → effective_priority needs to be built.

**GAP-12: No preemption support.** The spec's soft-unload feature (future) requires the ability to preempt a batch task when an interactive session resumes. The current scheduler has no preemption concept.

---

## 4. Power Management

### Current State

- `SleepInhibitor` works (acquires/releases systemd inhibitor locks).
- `PresenceDetector` exists with xprintidle support.
- Power event logging exists in the DB.
- `PowerConfig` has sleep_command, display_off, lock, etc.

### Gaps

**GAP-13: No auto-sleep logic.** The spec requires the server to auto-sleep when: no sessions, no tasks, no user present, VRAM purged, cooldown elapsed. None of this orchestration exists — the individual primitives (presence detection, sleep command, inhibitor) are there but not wired together.

**GAP-14: No VRAM cleanup trigger.** When the last session closes and the queue is empty, the server should unload models from VRAM. Currently nothing triggers `keep_alive=0` on session close.

**GAP-15: User presence not wired into HTTP layer.** The `/status` endpoint always returns `user_present=False`. The PresenceDetector exists but isn't connected to anything.

---

## 5. Ollama Integration

### Current State

- `OllamaClient` supports: `generate()` (non-streaming), `load_model()`, `unload_model()`, `list_models()`, `list_running()`.
- No `chat()` method.
- No streaming support.
- The `/models` status route makes a synchronous `httpx.get()` call from inside a sync route handler — this blocks a threadpool worker.

### Gaps

**GAP-16: No chat endpoint.** `OllamaClient` has no `chat()` method and the server has no chat endpoint. The client app needs to proxy `/api/chat` requests.

**GAP-17: No streaming generate/chat.** Both need streaming variants that yield NDJSON chunks.

**GAP-18: Ollama port configuration.** `OllamaConfig.host` defaults to `http://localhost:11434`. For the client app, Ollama needs to run on `:11435`. This is just a config change, not a code change, but the migration (reconfiguring Ollama's systemd unit) needs to be documented and possibly automated by `ringmaster init`.

---

## 6. Auth Model

### Current State

- Bearer token auth with SHA-256 hashing.
- Localhost connections skip auth entirely.
- AuthManager is in-memory with JSON file persistence.
- No concept of roles or permissions — all authenticated clients can do everything.

### Gaps

**GAP-19: No admin vs. user roles.** The spec requires that only admins can install/delete models. Currently all authenticated clients have equal access. A simple role field (`admin` vs `client`) on the client registration would suffice.

**GAP-20: Localhost auth bypass may be too permissive.** With the client daemon running on the workstation, localhost requests come from the client daemon, not directly from the user. This is probably fine (the daemon is trusted), but it means any local process can hit the API without auth. Worth noting for the security model.

---

## 7. Admin Digest

### Current State

Nothing exists. No email support, no event collection, no digest scheduling.

### Gaps

**GAP-21: Full admin digest system.** Needs: event collection (model requests, new app registrations, scheduling anomalies), digest formatting, email delivery, configurable frequency (hourly/daily/weekly). This is a significant new feature.

---

## 8. Test Infrastructure

### Current State

- 150 tests, all passing in 5.4s.
- Good coverage of existing functionality: scheduler, worker, DB, routes, auth, config, power, GPU, notifications, webhooks, Ollama client, CLI.
- Tests use in-memory SQLite and `ASGITransport` for integration tests — fast and isolated.
- `pytest-asyncio` with `asyncio_mode = "auto"`.
- `pytest-httpx` for mocking HTTP calls.

### Strengths

- Existing test patterns are clean and well-documented.
- Test helper functions (`make_conn`, `make_scheduler`) are reusable for new tests.
- Integration test pattern (create app → register client → make requests) is solid.

### Gaps

- No tests for concurrent access (multiple simultaneous requests).
- No tests for streaming responses (because none exist yet).
- No session generate tests (because the endpoint doesn't exist).

---

## 9. Code Quality and Patterns

### Strengths

- Excellent docstrings throughout. Every module, class, and method has clear documentation explaining *why*, not just *what*.
- Clean separation: routes are thin HTTP adapters, business logic lives in scheduler/worker, data layer in db.py.
- Dependency injection via `deps.py` makes testing easy.
- Pydantic models for all request/response schemas.
- Config is well-structured with sensible defaults.

### Minor Issues

- `list_models()` in `status.py` (line 100-115) makes a synchronous `httpx.get()` inside a sync route handler. Should be async.
- The `aiosqlite` dependency is listed in pyproject.toml but never used — all DB access is synchronous `sqlite3`.

---

## Gap Priority for Implementation

### Must-Have (blocks client app from working at all)

| Gap | Description | Effort |
|-----|-------------|--------|
| GAP-4 | Session lifecycle (requested→queued→granted→active→closed) | Large |
| GAP-5 | Session generate endpoint | Medium |
| GAP-6 | Streaming support (generate + chat) | Large |
| GAP-7 | Task class support (interactive/batch/scheduled) | Small |
| GAP-10 | Session-aware scheduling | Large |
| GAP-16 | Chat endpoint on OllamaClient | Small |
| GAP-17 | Streaming generate/chat on OllamaClient | Medium |

### Should-Have (needed for correct multi-user operation)

| Gap | Description | Effort |
|-----|-------------|--------|
| GAP-1 | Thread safety for Scheduler | Small |
| GAP-2 | SQLite connection safety | Medium |
| GAP-8 | App manifest endpoint and storage | Medium |
| GAP-11 | Policy engine for priority assignment | Medium |
| GAP-14 | VRAM cleanup on empty queue | Small |
| GAP-15 | Wire user presence into status | Small |
| GAP-19 | Admin vs client roles | Small |

### Nice-to-Have (can follow after initial client app launch)

| Gap | Description | Effort |
|-----|-------------|--------|
| GAP-3 | Connection pooling | Medium |
| GAP-9 | Model request endpoint | Small |
| GAP-12 | Preemption support | Large |
| GAP-13 | Auto-sleep orchestration | Medium |
| GAP-18 | Ollama port migration tooling | Small |
| GAP-20 | Localhost auth review | Small |
| GAP-21 | Admin digest system | Large |
