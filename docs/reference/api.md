# REST API reference

All endpoints except `GET /health` require an `Authorization: Bearer <token>` header.

The server listens on port 8420 by default. All request and response bodies are JSON.

---

## Authentication

### `POST /auth/register`

Issue a new bearer token for a client. If the client already has a token, the old one is revoked and a new one is issued (token rotation).

**Request body:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `client_id` | string | yes | Stable identifier for the client, e.g. `"my-laptop"` |

**Response:** `200 OK`

```json
{
  "client_id": "my-laptop",
  "token": "a1b2c3d4e5f6..."
}
```

!!! warning
    The raw token is returned once. It is never stored or recoverable. Save it securely.

---

### `POST /auth/revoke`

Revoke all tokens for a client. Revoking an unknown client is a no-op.

**Request body:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `client_id` | string | yes | Client to revoke |

**Response:** `200 OK`

```json
{
  "client_id": "my-laptop",
  "revoked": true
}
```

---

## Tasks

### `POST /tasks`

Submit a new inference task to the queue.

**Request body:**

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `task_type` | string | yes | — | Task kind: `generate`, `embed`, etc. |
| `model` | string | yes | — | Ollama model tag, e.g. `llama3:8b` |
| `client_id` | string | yes | — | Client identifier |
| `prompt` | string | no | `null` | Input text |
| `priority` | integer | no | config default (3) | 1 (highest) to 5 (lowest) |
| `deadline` | string | no | `null` | ISO 8601 UTC — tasks with deadlines jump the queue |
| `callback_url` | string | no | `null` | Webhook URL for completion notification |
| `unattended_policy` | string | no | `"run"` | `run`, `defer`, or `notify` |
| `metadata` | object | no | `{}` | Arbitrary key-value pairs, echoed back |

**Response:** `201 Created`

```json
{
  "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "task_type": "generate",
  "model": "llama3:8b",
  "priority": 3,
  "status": "queued",
  "client_id": "my-laptop",
  "submitted_at": "2026-04-05T12:00:00Z",
  "started_at": null,
  "completed_at": null,
  "deadline": null,
  "prompt": "Hello",
  "result": null,
  "error": null,
  "gpu_used": null,
  "duration_seconds": null,
  "callback_url": null,
  "unattended_policy": "run",
  "metadata": {}
}
```

**Example:**

```bash
curl -X POST http://localhost:8420/tasks \
  -H "Authorization: Bearer $RINGMASTER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"task_type": "generate", "model": "llama3:8b", "client_id": "my-laptop", "prompt": "Hello"}'
```

---

### `GET /tasks`

List tasks, optionally filtered by status or client.

**Query parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `status` | string | Filter by status: `queued`, `running`, `completed`, `failed`, `deferred`, `interrupted` |
| `client_id` | string | Filter by client |

**Response:** `200 OK` — array of task objects (same schema as `POST /tasks` response)

**Example:**

```bash
curl "http://localhost:8420/tasks?status=queued" \
  -H "Authorization: Bearer $RINGMASTER_TOKEN"
```

---

### `GET /tasks/{task_id}`

Retrieve a single task by ID.

**Response:** `200 OK` — task object

**Response:** `404 Not Found` — if the task ID doesn't exist

---

### `POST /tasks/current/cancel`

Interrupt the currently running task. Best-effort — the worker handles the actual stop.

**Response:** `200 OK`

```json
{
  "cancelled_task_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
}
```

Returns `null` for `cancelled_task_id` if nothing was running.

---

### `POST /tasks/{task_id}/approve`

Move a deferred task back to `queued` so it re-enters dispatch.

**Response:** `200 OK`

```json
{
  "task_id": "a1b2c3d4-...",
  "status": "queued"
}
```

---

### `POST /tasks/{task_id}/defer`

Move a task to `deferred` status, removing it from active dispatch.

**Response:** `200 OK`

```json
{
  "task_id": "a1b2c3d4-...",
  "status": "deferred"
}
```

---

## Queue control

### `POST /queue/pause`

Stop dispatching tasks. New tasks are still accepted into the queue.

**Response:** `200 OK`

```json
{
  "queue_paused": true
}
```

---

### `POST /queue/resume`

Resume normal dispatch after a pause or drain.

**Response:** `200 OK`

```json
{
  "queue_paused": false
}
```

---

### `POST /queue/drain`

Finish the current task, then pause. If nothing is running, pause immediately.

**Response:** `200 OK`

```json
{
  "draining": true
}
```

---

## Sessions

### `POST /sessions`

Open an interactive inference session.

**Request body:**

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `model` | string | yes | — | Ollama model tag to pre-load |
| `client_id` | string | yes | — | Client identifier |
| `priority` | integer | no | `null` | Queue priority for the reservation |
| `session_idle_timeout_seconds` | integer | no | `600` | Seconds before auto-close |
| `callback_url` | string | no | `null` | URL to notify on session close |
| `unattended_policy` | string | no | `"run"` | Approval behavior |

**Response:** `201 Created`

```json
{
  "id": "sess-abc123",
  "client_id": "cursor-ide",
  "model": "llama3:8b",
  "status": "open",
  "opened_at": "2026-04-05T12:00:00Z",
  "last_activity_at": null,
  "idle_timeout_seconds": 600,
  "gpu_label": "Primary Compute"
}
```

---

### `GET /sessions/{session_id}`

Retrieve session status.

**Response:** `200 OK` — session object (same schema as above)

---

### `POST /sessions/{session_id}/keepalive`

Reset the session's idle timer.

**Response:** `200 OK`

```json
{
  "session_id": "sess-abc123",
  "status": "open",
  "idle_timeout_reset": true
}
```

---

### `DELETE /sessions/{session_id}`

Close a session and release the GPU reservation.

**Response:** `200 OK`

```json
{
  "session_id": "sess-abc123",
  "status": "closed"
}
```

---

## System status

### `GET /health`

Liveness probe. **No authentication required.**

**Response:** `200 OK`

```json
{
  "alive": true,
  "version": "0.1.0",
  "uptime_seconds": 3600.5
}
```

---

### `GET /status`

System state snapshot.

**Response:** `200 OK`

```json
{
  "state": "idle",
  "queue_depth": 0,
  "current_task": null,
  "user_present": false,
  "queue_paused": false
}
```

| Field | Type | Description |
|-------|------|-------------|
| `state` | string | `idle`, `busy`, `paused`, or `sleeping` |
| `queue_depth` | integer | Tasks waiting in the queue |
| `current_task` | string or null | ID of the running task |
| `user_present` | boolean | Whether the user is at the keyboard |
| `queue_paused` | boolean | Whether dispatch is paused |

---

### `GET /gpus`

List configured GPUs.

**Response:** `200 OK` — array of GPU objects from config

---

### `GET /models`

List models available in Ollama.

**Response:** `200 OK` — model list from Ollama's API

---

## Webhook payload

When a task with `callback_url` completes or fails, Ringmaster POSTs this payload to the URL:

```json
{
  "task_id": "a1b2c3d4-...",
  "status": "completed",
  "result": "The output text...",
  "error": null,
  "model": "llama3:8b",
  "gpu_used": "Primary Compute",
  "duration_seconds": 4.2,
  "completed_at": "2026-04-05T12:00:04Z"
}
```

Delivery retries 3 times with exponential backoff on non-2xx responses.
