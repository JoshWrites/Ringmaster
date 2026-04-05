# Sessions

Sessions reserve GPU access for interactive tools — coding assistants, chat interfaces, anything that needs low-latency back-and-forth without the queue stealing the card between requests.

## When to use sessions vs. tasks

- **One-off inference** (summarize a document, classify an email) → submit a [task](tasks.md)
- **Interactive tool** that sends many requests over minutes or hours (coding assistant, chat UI) → open a session

The difference: a task goes through the queue each time. A session holds the GPU reservation open so your model stays loaded and your next request starts immediately.

## Opening a session

```bash
curl -X POST http://localhost:8420/sessions \
  -H "Authorization: Bearer $RINGMASTER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "llama3:8b",
    "client_id": "cursor-ide",
    "session_idle_timeout_seconds": 600
  }'
```

Response:

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

Save the `id` — you'll need it for keepalives and to close the session.

## Keeping a session alive

Sessions expire after `session_idle_timeout_seconds` of inactivity (default: 600 = 10 minutes). Your client is responsible for sending keepalives:

```bash
curl -X POST http://localhost:8420/sessions/sess-abc123/keepalive \
  -H "Authorization: Bearer $RINGMASTER_TOKEN"
```

This resets the idle timer. Call it periodically — every few minutes is fine. If you forget, the session expires and the GPU slot goes back to the queue.

## Checking session status

```bash
curl http://localhost:8420/sessions/sess-abc123 \
  -H "Authorization: Bearer $RINGMASTER_TOKEN"
```

The `status` field will be one of:

| Status | Meaning |
|--------|---------|
| `open` | Active and holding a GPU reservation |
| `closed` | You closed it explicitly |
| `expired` | Idle timeout reached, GPU released |

## Closing a session

When your tool is done:

```bash
curl -X DELETE http://localhost:8420/sessions/sess-abc123 \
  -H "Authorization: Bearer $RINGMASTER_TOKEN"
```

This releases the GPU reservation immediately. If you don't close it, it expires after the idle timeout — but closing explicitly is polite to other clients waiting in the queue.

## Session fields

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `model` | yes | — | Ollama model tag to pre-load |
| `client_id` | yes | — | Identifies your client |
| `priority` | no | `null` | Queue priority for the reservation request |
| `session_idle_timeout_seconds` | no | `600` | Seconds before auto-close on inactivity |
| `callback_url` | no | `null` | URL to notify when the session closes |
| `unattended_policy` | no | `"run"` | Approval behavior when user is present |

!!! note
    Sessions are an API-only feature. The CLI is designed for one-off task submission, not interactive use. If you're building a tool that needs sessions, you'll use the REST API directly.

!!! tip
    Set a reasonable idle timeout. Too short (60s) and your session dies between requests. Too long (3600s) and you hog the GPU while getting coffee. 600 seconds (10 minutes) is a good default for most interactive tools.
