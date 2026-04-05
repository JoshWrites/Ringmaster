# Tasks

Submitting inference tasks, controlling the queue, and managing the approval workflow.

## Submitting a task

### From the CLI

```bash
ringmaster submit --model llama3:8b --prompt "Summarize this document"
```

With priority and a webhook callback:

```bash
ringmaster submit --model llama3:8b \
  --prompt "Urgent: classify this email" \
  --priority 1 \
  --callback-url http://my-app:9000/done
```

### From the API

```bash
curl -X POST http://localhost:8420/tasks \
  -H "Authorization: Bearer $RINGMASTER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "task_type": "generate",
    "model": "llama3:8b",
    "client_id": "my-laptop",
    "prompt": "Summarize this document",
    "priority": 3
  }'
```

The API gives you more control than the CLI. Fields you can set on submission:

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `task_type` | yes | — | Kind of task: `generate`, `embed`, etc. |
| `model` | yes | — | Ollama model tag, e.g. `llama3:8b` |
| `client_id` | yes | — | Identifies your client |
| `prompt` | no | `null` | Input text |
| `priority` | no | `3` | 1 (highest) to 5 (lowest) |
| `deadline` | no | `null` | ISO 8601 UTC — tasks with deadlines jump the queue |
| `callback_url` | no | `null` | Webhook URL for completion notification |
| `unattended_policy` | no | `"run"` | What to do when you're at the keyboard (see below) |
| `metadata` | no | `{}` | Arbitrary key-value pairs, stored and echoed back |

For the full request/response schema, see the [API Reference](../reference/api.md).

## Task lifecycle

Every task moves through these states:

```
queued → running → completed
                 → failed
                 → cancelled
```

There's also a **deferred** state for tasks held back by the approval workflow (see below).

You can check a task's status with the CLI:

```bash
ringmaster queue
```

Or the API:

```bash
curl http://localhost:8420/tasks \
  -H "Authorization: Bearer $RINGMASTER_TOKEN"
```

## Priority and deadlines

Tasks are dispatched in this order:

1. **Priority** — lower number goes first (1 is highest, 5 is lowest)
2. **Deadline** — tasks with a deadline are dequeued ahead of tasks without one, nearest deadline first
3. **Submission time** — within the same priority and deadline, first in, first out

If you don't set a priority, the task gets the default from your config (`queue.default_priority`, which is `3` unless you changed it).

## Queue control

Three distinct operations, each with a different purpose:

### Pause

```bash
ringmaster pause
```

The server keeps accepting new tasks, but stops dispatching them. Nothing runs until you resume. Use this when you need the GPU for something else temporarily.

### Resume

```bash
ringmaster resume
```

Normal dispatch resumes. The next queued task starts running.

### Drain

```bash
ringmaster drain
```

The server finishes whatever task is currently running, then stops dispatching. New tasks are still accepted into the queue. Use this before a planned shutdown — it lets the current work complete gracefully instead of interrupting it.

!!! info "Drain vs. pause"
    **Pause** stops immediately (the current task keeps running but nothing new starts). **Drain** waits for the current task to finish, then pauses automatically. Use drain when you're about to sleep or shut down the workstation.

### Cancel the current task

```bash
ringmaster cancel-current
```

Interrupts whatever is running right now. The task moves to `cancelled` status.

## Task approval workflow

When you're sitting at the workstation, you might not want background tasks grabbing the GPU without asking. The `unattended_policy` field controls this:

| Policy | Behavior |
|--------|----------|
| `run` | Start immediately, no questions asked. |
| `defer` | Hold the task until the session goes idle or you approve it manually. |
| `notify` | Send a desktop notification and wait for approval. Auto-approve after `auto_approve_timeout_seconds` if you don't respond. |

When a task is deferred, it sits in the `deferred` state. You can approve it manually:

```bash
curl -X POST http://localhost:8420/tasks/<task-id>/approve \
  -H "Authorization: Bearer $RINGMASTER_TOKEN"
```

Or push it back to deferred if you're not ready:

```bash
curl -X POST http://localhost:8420/tasks/<task-id>/defer \
  -H "Authorization: Bearer $RINGMASTER_TOKEN"
```

!!! tip
    If you set `auto_approve_when_idle: true` in the [idle config](configuration.md#idle), tasks auto-approve when you step away from the keyboard. This is the default — you only need to think about the approval workflow if you've turned it off.

## Webhooks

If you set `callback_url` on a task, Ringmaster POSTs a notification to that URL when the task completes or fails:

```json
{
  "task_id": "abc123",
  "status": "completed",
  "result": "The document discusses...",
  "model": "llama3:8b",
  "gpu_used": "Primary Compute",
  "duration_seconds": 4.2,
  "completed_at": "2026-04-05T12:00:00Z"
}
```

Delivery retries 3 times with exponential backoff if the callback URL doesn't respond with a 2xx status.

!!! note
    Webhooks are fire-and-forget from the client's perspective. If you don't set a `callback_url`, you can always poll `GET /tasks/<task-id>` instead.
