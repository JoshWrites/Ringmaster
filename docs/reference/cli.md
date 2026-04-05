# CLI reference

The `ringmaster` CLI wraps the REST API for shell use. All commands except `init` require a running Ringmaster server and a valid token.

## Global options

These apply to every command:

| Flag | Env var | Default | Description |
|------|---------|---------|-------------|
| `--host` | — | `http://localhost:8420` | Base URL of the Ringmaster server |
| `--token` | `RINGMASTER_TOKEN` | — | Bearer token for API authentication |

```bash
# Set once in your shell
export RINGMASTER_TOKEN=<your-token>

# Or pass per-command
ringmaster --host http://workstation:8420 --token abc123 status
```

---

## `status`

Show current system state.

```bash
ringmaster status
```

```
State:        idle
Queue depth:  0
Current task: —
User present: False
Queue paused: False
```

---

## `queue`

List tasks in the queue.

```bash
ringmaster queue
```

```
ID                                      TYPE          MODEL                 PRI  STATUS
---------------------------------------------------------------------------------------
a1b2c3d4-e5f6-7890-abcd-ef1234567890    generate      llama3:8b             3    completed
```

| Flag | Default | Description |
|------|---------|-------------|
| `--status-filter STATUS` | all | Only show tasks with this status (`queued`, `running`, `completed`, `failed`, etc.) |

```bash
ringmaster queue --status-filter queued
```

---

## `submit`

Submit a new task to the queue.

```bash
ringmaster submit --model llama3:8b --prompt "Summarize this document"
```

```
Task submitted.
  ID:     a1b2c3d4-e5f6-7890-abcd-ef1234567890
  Status: queued
```

| Flag | Required | Default | Description |
|------|----------|---------|-------------|
| `--model` | yes | — | Ollama model tag, e.g. `llama3:8b` |
| `--prompt` | no | `null` | Input text |
| `--priority` | no | config default | 1 (highest) to 5 (lowest) |
| `--client-id` | no | `cli` | Client identifier |
| `--callback-url` | no | `null` | Webhook URL for completion notification |

```bash
ringmaster submit --model llama3:8b \
  --prompt "Urgent work" \
  --priority 1 \
  --callback-url http://my-app:9000/done
```

---

## `pause`

Pause the queue. Tasks are accepted but not dispatched.

```bash
ringmaster pause
```

```
Queue paused.
```

---

## `resume`

Resume dispatch after a pause or drain.

```bash
ringmaster resume
```

```
Queue resumed.
```

---

## `drain`

Finish the current task, then pause. Use before planned shutdowns.

```bash
ringmaster drain
```

```
Queue is draining. No new tasks will be dispatched.
```

!!! tip
    Combine with `ringmaster status` to poll until the current task finishes.

---

## `cancel-current`

Interrupt whatever task is running right now.

```bash
ringmaster cancel-current
```

```
Task cancelled: a1b2c3d4-e5f6-7890-abcd-ef1234567890
```

If nothing is running:

```
No task is currently running.
```

---

## `gpu`

List GPUs known to this Ringmaster instance.

```bash
ringmaster gpu
```

```
  Primary Compute  [compute]  24576 MiB VRAM
```

---

## `init`

Detect GPUs and generate a configuration file. **Does not require a running server or token.**

```bash
ringmaster init
```

```
Detecting GPUs…
Found 1 GPU(s).

GPU 0: NVIDIA RTX 4090  (24576 MiB VRAM)
  Label (human-readable name for logs/API) [gpu0]: Primary Compute
  Role [compute]:

Configuration written to ringmaster.yaml
```

| Flag | Default | Description |
|------|---------|-------------|
| `--config PATH` | `ringmaster.yaml` | Output path for the generated config file |

```bash
ringmaster init --config ~/.config/ringmaster/ringmaster.yaml
```
