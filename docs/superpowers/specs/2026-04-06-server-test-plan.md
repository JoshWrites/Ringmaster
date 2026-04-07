# Ringmaster Server Test Plan

**Date:** 2026-04-06
**Purpose:** Comprehensive test plan for the server changes needed to support the client app. Organized by feature area, with tests for both new functionality and regressions on existing functionality.

---

## Test Categories

- **Unit tests** — test individual functions/classes in isolation with mocks
- **Integration tests** — test HTTP endpoints via `ASGITransport` with real SQLite
- **Concurrency tests** — test thread safety under concurrent access

All tests use in-memory SQLite and follow existing patterns in the test suite.

---

## 1. Session Lifecycle (GAP-4)

The session state machine changes from `open → closed` to `requested → queued → granted → active → closed`.

### DB Layer (`tests/test_db.py` additions)

- `test_insert_session_with_requested_status` — new sessions start in `requested` status
- `test_update_session_status` — can transition between all valid states
- `test_session_has_task_class_field` — sessions store task_class (interactive/batch/scheduled)
- `test_session_has_app_field` — sessions store the app name
- `test_session_has_user_field` — sessions store the requesting user

### Scheduler Layer (`tests/test_scheduler.py` additions)

- `test_request_session_creates_queued_session` — requesting a session places it in the queue
- `test_grant_session_when_gpu_available` — if GPU is idle, session goes directly to `granted`
- `test_session_queued_when_gpu_busy` — if GPU is in use, session stays `queued`
- `test_session_queue_respects_task_class_ordering` — interactive sessions granted before batch
- `test_activate_session` — moving a granted session to `active` marks it in use
- `test_close_session_frees_gpu` — closing an active session makes GPU available for next in queue
- `test_session_idle_timeout_closes_session` — expired idle sessions are auto-closed
- `test_multiple_sessions_queued_fifo_within_class` — same-class sessions are FIFO
- `test_session_request_with_manifest` — manifest data is accepted and stored

### Route Layer (`tests/test_routes_sessions.py` additions)

- `test_request_session_returns_201_with_requested_status` — POST /sessions with new payload format
- `test_request_session_requires_task_class` — missing task_class returns 422
- `test_request_session_requires_model` — missing model returns 422
- `test_get_session_shows_queue_position` — GET /sessions/{id} includes position in queue
- `test_session_grant_notification` — when session is granted, response reflects new status
- `test_activate_session_endpoint` — POST /sessions/{id}/activate moves to active
- `test_close_session_triggers_next_grant` — closing a session causes the next queued session to be granted
- `test_session_request_with_full_payload` — all fields (client_id, user, model, task_class, app, manifest)

### Regression Tests

- `test_existing_session_open_close_still_works` — backward compat for basic open/close flow
- `test_session_keepalive_still_works` — keepalive on active sessions
- `test_session_404_on_missing_id` — error handling preserved

---

## 2. Session Generate / Chat (GAP-5, GAP-16)

### OllamaClient (`tests/test_ollama.py` additions)

- `test_chat_sends_messages_array` — `chat()` method sends correct payload to `/api/chat`
- `test_chat_returns_response_message` — parses Ollama's chat response format
- `test_chat_raises_on_error` — OllamaError on non-200
- `test_generate_with_system_prompt` — generate supports optional system prompt

### Route Layer (`tests/test_routes_sessions.py` additions)

- `test_generate_on_active_session` — POST /sessions/{id}/generate returns inference result
- `test_generate_on_non_active_session_returns_403` — can't generate on queued/closed sessions
- `test_generate_updates_last_activity` — generate call resets idle timer
- `test_chat_on_active_session` — POST /sessions/{id}/chat returns chat response
- `test_chat_with_message_history` — multiple messages passed through correctly
- `test_generate_on_nonexistent_session_returns_404`

---

## 3. Streaming Support (GAP-6, GAP-17)

### OllamaClient (`tests/test_ollama.py` additions)

- `test_stream_generate_yields_chunks` — `stream_generate()` yields NDJSON lines
- `test_stream_generate_final_chunk_has_done_true` — last chunk has `"done": true`
- `test_stream_chat_yields_chunks` — `stream_chat()` yields NDJSON lines
- `test_stream_chat_final_chunk_has_done_true`
- `test_stream_generate_raises_on_error` — error mid-stream is handled
- `test_stream_generate_timeout` — long inference doesn't prematurely timeout

### Route Layer (new `tests/test_routes_streaming.py`)

- `test_streaming_generate_returns_ndjson` — response has correct content-type and format
- `test_streaming_chat_returns_ndjson`
- `test_streaming_generate_each_line_is_valid_json` — every line parses as JSON
- `test_streaming_generate_last_line_has_done` — final line has `"done": true`
- `test_non_streaming_generate_returns_single_json` — `stream: false` returns one object
- `test_non_streaming_chat_returns_single_json`

---

## 4. Task Class Support (GAP-7)

### DB Layer

- `test_task_with_task_class_field` — tasks can store task_class
- `test_session_with_task_class_field` — sessions can store task_class
- `test_task_class_values_validated` — only interactive/batch/scheduled accepted

### Models

- `test_session_open_request_includes_task_class` — Pydantic model validates task_class
- `test_task_submit_request_includes_task_class` — Pydantic model validates task_class

---

## 5. App Manifest (GAP-8)

### DB Layer

- `test_store_manifest_for_client` — manifest JSON is persisted per client_id
- `test_update_manifest_replaces_previous` — new manifest overwrites old
- `test_get_manifest_for_client` — retrieve stored manifest
- `test_manifest_stores_app_list` — manifest contains apps with name, class, model

### Route Layer (new `tests/test_routes_manifest.py`)

- `test_session_request_stores_manifest` — manifest from session request is persisted
- `test_manifest_delta_detected` — server detects new/removed apps vs previous manifest
- `test_get_manifests_endpoint` — admin can list all client manifests

---

## 6. Policy Engine (GAP-11)

### Unit Tests (new `tests/test_policy.py`)

- `test_interactive_gets_higher_priority_than_batch` — policy maps interactive to lower priority number
- `test_batch_gets_higher_priority_than_scheduled`
- `test_same_class_same_priority` — two interactive requests get equal priority
- `test_policy_considers_queue_state` — with many interactive sessions queued, batch gets deprioritized further
- `test_policy_considers_user_presence` — when workstation user is present, remote batch is deprioritized
- `test_priority_is_server_assigned` — client-supplied priority is ignored; server computes it

---

## 7. VRAM Cleanup (GAP-14)

### Unit Tests

- `test_vram_purge_on_last_session_close` — when last active session closes and queue is empty, `unload_model()` is called
- `test_no_vram_purge_when_queue_has_pending` — if another session is queued, model stays loaded
- `test_no_vram_purge_when_session_still_active` — don't purge if other sessions are active
- `test_vram_purge_after_last_task_completes` — task queue path also triggers purge check

---

## 8. Thread Safety (GAP-1, GAP-2)

### Concurrency Tests (new `tests/test_concurrency.py`)

- `test_concurrent_task_submissions` — submit 20 tasks from 20 threads simultaneously, all succeed with unique IDs
- `test_concurrent_session_requests` — request 10 sessions concurrently, no crashes or data corruption
- `test_pause_during_concurrent_submissions` — pause while tasks are being submitted, no race
- `test_cancel_during_task_execution` — cancel while worker is running, clean state transition
- `test_scheduler_state_consistent_under_load` — stress test: mix of submits, pauses, resumes, cancels from multiple threads

---

## 9. Model Requests (GAP-9)

### Route Layer (new `tests/test_routes_models.py`)

- `test_request_model_creates_pending_request` — POST /models/requests stores the request
- `test_request_model_requires_model_name` — missing model returns 422
- `test_list_model_requests` — GET /models/requests returns all pending requests
- `test_approve_model_request` — admin approval triggers (mocked) model pull
- `test_deny_model_request` — denial removes from pending
- `test_duplicate_model_request` — requesting an already-installed model returns info message

---

## 10. Admin Roles (GAP-19)

### Auth Tests (`tests/test_auth.py` additions)

- `test_register_client_with_role` — clients can be registered with a role (admin/client)
- `test_default_role_is_client` — unspecified role defaults to client
- `test_admin_can_access_admin_endpoints` — admin role grants access to model management
- `test_client_cannot_access_admin_endpoints` — client role is rejected from admin endpoints

---

## 11. Power Management (GAP-13, GAP-15)

### User Presence Integration

- `test_status_reports_actual_user_presence` — /status uses PresenceDetector, not hardcoded False
- `test_presence_detection_failure_assumes_present` — detector failure = safe default

### Auto-Sleep (future, but test the primitives)

- `test_idle_check_returns_true_when_all_conditions_met` — no sessions, no tasks, no user, cooldown elapsed
- `test_idle_check_returns_false_with_active_session`
- `test_idle_check_returns_false_with_queued_task`
- `test_idle_check_returns_false_within_cooldown`

---

## 12. Ollama Port Configuration (GAP-18)

### Config Tests (`tests/test_config.py` additions)

- `test_ollama_host_configurable` — setting `ollama.host` to `http://localhost:11435` works
- `test_ollama_host_default_is_11434` — default preserved for backward compat

---

## 13. Regression Suite

All existing 150 tests must continue to pass. Additionally:

- `test_existing_task_submit_still_works` — POST /tasks with current payload format
- `test_existing_queue_operations` — pause/resume/drain unchanged
- `test_existing_auth_flow` — register/revoke unchanged
- `test_health_endpoint_unchanged` — /health still works without auth
- `test_localhost_auth_bypass_unchanged` — local requests still skip auth

---

## Execution Strategy

### Phase 1: Foundation (before implementing features)

Write tests for GAP-1, GAP-2, GAP-7 first. These are small changes that de-risk the rest.

### Phase 2: Session lifecycle

Write tests for GAP-4, GAP-5, GAP-10. This is the core of the client app integration.

### Phase 3: Streaming

Write tests for GAP-6, GAP-17. Streaming is complex and should be tested independently.

### Phase 4: Policy and management

Write tests for GAP-8, GAP-11, GAP-9, GAP-14, GAP-19. These are medium-effort features that build on the session foundation.

### Phase 5: Power and polish

Write tests for GAP-13, GAP-15. These are nice-to-haves that complete the picture.

---

## Test Naming Convention

Follow existing pattern: `test_{what_is_being_tested}`. Group related tests in classes named `Test{Feature}`.

## Test Count Estimate

- New unit tests: ~45
- New integration tests: ~35
- New concurrency tests: ~5
- **Total new tests: ~85**
- Plus 150 existing regression tests
- **Total: ~235 tests**
