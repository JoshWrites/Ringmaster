# Ringmaster Design Specification

**Date:** 2026-03-29
**Status:** Draft
**Repo:** ~/Documents/Repos/Ringmaster

## Overview

Ringmaster turns a personal workstation into a shared AI compute node for a home
network — while respecting that someone might be using it to game, browse, or work.

It is a two-component system: a server daemon on the workstation and a client app
on any machine that needs AI compute. A CLI provides direct access for scripts and
power users. All three interfaces consume the same REST API.

Ringmaster is designed for public release. It is configuration-driven with no
hardcoded hardware, services, or network topology.

## Problem Statement

A home network may have one powerful GPU workstation and many devices that need AI
inference: security scanners, code companions, financial tools, storage indexers.
Today there is no way to:

- Queue and prioritize inference requests from multiple clients
- Negotiate GPU access with a human user at the keyboard
- Prevent sleep/shutdown from killing a running inference task
- Wake the machine for scheduled AI work and sleep it when done
- Give interactive users (IDE coding assistants) session-based GPU reservation
- Do all of this without modifying every individual AI tool

## Architecture

### Components

**1. Ringmaster Server** (workstation daemon)

Always-on systemd service. Responsibilities:

- FastAPI REST API for task submission, queue management, status
- SQLite task queue with priority ordering
- Ollama instance management (model loading, swapping, GPU targeting)
- GPU inventory discovery and fingerprint-based identification
- User presence detection via D-Bus/systemd-logind
- Desktop notifications with action buttons via D-Bus
- Sleep/shutdown inhibition via systemd inhibitor locks
- Display blanking and screen lock when deferring sleep
- Webhook delivery for task completion
- Push notifications via configurable backend (HA in phase 1)
- Queue pause/resume controls

**2. Ringmaster Client App** (any OS)

Lightweight system tray daemon for interactive users. Responsibilities:

- Local Ollama-compatible HTTP proxy (tools point at localhost, client handles the rest)
- Session management against the Ringmaster server
- Desktop notifications from Ringmaster (queue position, delays, errors)
- Web UI on a configurable local port for queue status and settings
- Authentication against the Ringmaster server

Cross-platform: Python with `pystray` (tray), `desktop-notifier` (notifications),
FastAPI (proxy + web UI). Packaged via pip or PyInstaller.

**3. CLI**

Thin wrapper over the REST API. Ships with both server and client installs.
Available on any machine that can reach the server over the network.

```
ringmaster submit --model mistral-nemo:12b --prompt "..." --priority 1
ringmaster queue
ringmaster status
ringmaster gpu
ringmaster pause
ringmaster resume
ringmaster cancel-current
ringmaster --host 10.100.102.181 queue
```

### Communication Flow

```
[AI tool on client] → localhost:11434 (client app Ollama proxy)
    → Ringmaster REST API (workstation, authenticated)
        → Ollama (actual inference)
            → result back through the chain
            → webhook to callback URL on completion

[Server script] → Ringmaster REST API directly (no client app needed)
    → same path from there

[Human at terminal] → ringmaster CLI → REST API → same path
```

## Configuration

All configuration is file-based. No hardcoded values.

### Server Configuration

```yaml
# ringmaster.yaml

server:
  host: "0.0.0.0"
  port: 8420

gpus:
  - label: "primary"
    role: "compute"           # compute, gaming, both
    prefer_for: ["large_models", "default"]
    fingerprint: {}           # populated by `ringmaster init`
  - label: "secondary"
    role: "both"
    prefer_for: ["small_models", "fallback"]
    fingerprint: {}

ollama:
  host: "http://localhost:11434"

notifications:
  backend: "homeassistant"    # desktop, homeassistant, ntfy, pushover, matrix
  config:
    ha_url: "http://ha.local:8123"
    ha_token_env: "HA_TOKEN"
  fallback_backend: "desktop" # used when primary is unreachable

power:
  wake_method: "wol"          # wol, ipmi, smart_plug, none
  sleep_command: "systemctl suspend"
  display_off_command: "xset dpms force off"
  lock_command: "loginctl lock-session"
  gpu_compute_profile_command: ""  # optional: force GPU to compute mode while tasks run

idle:
  detection_method: "dbus"    # dbus, xprintidle
  idle_threshold_seconds: 300
  auto_approve_when_idle: true
  auto_approve_timeout_seconds: 60  # seconds before auto-approving when user is present

queue:
  max_queue_depth: 100
  default_priority: 3
  session_idle_timeout_seconds: 600

auth:
  token_file: "/etc/ringmaster/tokens.json"
```

### Client Configuration

```yaml
# ringmaster-client.yaml

server:
  host: "10.100.102.181"
  port: 8420
  token_env: "RINGMASTER_TOKEN"

proxy:
  port: 11434               # local Ollama-compatible endpoint
  bind: "127.0.0.1"

webui:
  port: 8421
  bind: "127.0.0.1"

notifications:
  enabled: true
```

## GPU Management

### Discovery and Fingerprinting

`ringmaster init` scans available GPUs and presents them to the user for labeling:

```
$ ringmaster init
Found 2 GPUs:
  1. AMD Radeon RX 7900 XTX (24GB VRAM, PCI 03:00.0, serial: ABC123)
  2. AMD Radeon RX 5700 XT (8GB VRAM, PCI 06:00.0, serial: DEF456)

Label for GPU 1: primary
Role for 'primary' [compute/gaming/both]: compute

Label for GPU 2: secondary
Role for 'secondary' [compute/gaming/both]: both
```

The fingerprint is stored using immutable hardware traits:

```yaml
fingerprint:
  vendor: "AMD"
  model: "Radeon RX 7900 XTX"
  vram_mb: 24576
  serial: "ABC123"           # if available
  device_id: "0x744c"        # PCI device ID
```

**At startup**, Ringmaster re-scans GPUs and matches each physical device to its
config entry by fingerprint. The PCI bus address and device index may change
between boots — Ringmaster follows the card, not the slot.

- **Card missing at boot:** Warning notification. Tasks requiring it queue until
  it reappears.
- **Unknown card appears:** Info log. User runs `ringmaster gpu add` to configure it.

### GPU Allocation (Phase 1)

Phase 1 uses the GPU whose `prefer_for` list includes `"default"`. If no GPU
is marked as default, the first compute-role GPU in config order is used. Tasks
do not specify GPU preferences. The scheduler is a simple FIFO against this GPU.

### GPU Allocation (Phase 2 — designed for, not implemented)

Tasks declare requirements:

```json
{
  "preferred_gpu": "primary",
  "minimum_vram_mb": 16000,
  "fallback_model": "llama3.2:3b",
  "allow_secondary_gpu": true
}
```

The scheduler matches task requirements against GPU inventory. A task that
*needs* the big card waits for it. A task that *prefers* it can fall back to a
smaller card with a smaller model. When a user is gaming on the primary GPU, the
scheduler automatically checks whether queued tasks can run on the secondary.

A model-to-GPU capability map tracks which models fit on which cards:

| Model              | Min VRAM | Preferred GPU |
|--------------------|----------|---------------|
| mistral-nemo:12b   | 12GB     | primary       |
| qwen2.5-coder:14b  | 14GB     | primary       |
| llama3.2:3b        | 4GB      | either        |

### GPU Allocation (Future — designed for, not implemented)

N GPUs, M concurrent tasks. The scheduler becomes a resource allocator matching
task requirements against a pool of available GPUs. No schema or API changes
required — the task request format and GPU inventory model already support this.

## Task System

### Task Types

**Discrete tasks** — defined start and end. Client submits a prompt, gets a
result, task completes. Queue moves on.

```json
{
  "task_type": "discrete",
  "model": "mistral-nemo:12b",
  "prompt": "Analyze these network findings...",
  "priority": 1,
  "deadline": "2026-03-29T08:00:00Z",
  "callback_url": "http://10.100.102.50:8080/ringmaster/callback",
  "client_id": "netintel",
  "unattended_policy": "run",
  "metadata": {}
}
```

**Session tasks** — client opens a session, makes many requests over time. The
GPU is reserved for the session holder until the session closes or idle-times-out.

```json
{
  "task_type": "session",
  "model": "qwen2.5-coder:14b",
  "priority": 2,
  "session_idle_timeout_seconds": 600,
  "callback_url": "http://10.100.102.193:8421/ringmaster/callback",
  "client_id": "anny-codium",
  "unattended_policy": "run",
  "metadata": {}
}
```

Session lifecycle:
- Client opens session via the client app (or direct API call)
- Model loads, GPU is reserved
- Client sends queries within the session — processed immediately
- After `session_idle_timeout_seconds` with no queries, Ringmaster notifies the
  client that the session is expiring
- Client can renew (keepalive) or let it close
- If no keepalive within 60 seconds of the expiry notice, session auto-closes
- Session closes, GPU released, queue moves on

### Task Lifecycle

```
submitted → queued → awaiting_approval → running → completed
                         ↓                  ↓
                      deferred           failed
                         ↓                  ↓
                      queued            interrupted
                                            ↓
                                        queued (re-submitted by client)
```

- **submitted** — request validated and persisted to SQLite
- **queued** — ordered by priority tier, then deadline, then submission time
- **awaiting_approval** — user is present at the workstation, desktop notification
  shown with options: approve, defer (with duration or "until idle"), or let it
  auto-proceed after configurable timeout
- **running** — model loaded on GPU, inference active, systemd sleep inhibitor held
- **completed** — result stored, webhook fired to callback URL
- **failed** — error stored, webhook fired with error detail
- **deferred** — user delayed it. Re-enters queue after specified duration or when
  idle is detected
- **interrupted** — user forced sleep/shutdown, or user cancelled via pause
  controls. Client can resubmit.

When no user is present, tasks skip `awaiting_approval` and go straight from
`queued` to `running`.

### Priority

Configurable priority tiers (defaults):

| Tier | Category     | Examples                      |
|------|-------------|-------------------------------|
| 1    | Security    | NetIntel, threat analysis      |
| 2    | Interactive | Code companion, chat           |
| 3    | Analytical  | Financial analysis, reporting  |
| 4    | Housekeeping| Storage indexing, maintenance  |

Within a tier, tasks are ordered by deadline (soonest first), then submission
time (oldest first).

**No preemption in phase 1.** A running task finishes before the next starts,
regardless of priority. Priority only affects queue ordering.

**Phase 2** may add preemption: a tier-1 task can interrupt a tier-4 task if the
tier-4 task supports checkpointing.

### Queue Controls

The user is always in control. Ringmaster never locks someone out.

**Phase 1:**
- **Pause queue** — no new tasks start, current task finishes to completion.
  `ringmaster pause` / `POST /queue/pause` / HA switch.
- **Resume queue** — processing resumes. `ringmaster resume` / `POST /queue/resume`.
- **Cancel current task** — kills the running task, marks it `interrupted`, moves
  to next in queue. `ringmaster cancel-current` / `POST /tasks/current/cancel`.
- **Drain and stop** — finish current task, then pause. For "I want the machine
  back soon." `ringmaster drain` / `POST /queue/drain`.

**Phase 2:**
- **Pause task** — checkpoint if supported, free the GPU, resume later.
- **Migrate task** — move a running task to a secondary GPU with a smaller model,
  freeing the primary card for the user.

**HA integration:** Pause/resume exposed as an HA switch entity. User toggles
"AI Queue" in the HA dashboard — off means the machine is theirs.

## Client Communication

### Webhook Callbacks (primary)

When a task completes, fails, or is interrupted, Ringmaster POSTs to the
client's `callback_url`:

```json
{
  "task_id": "abc-123",
  "status": "completed",
  "result": "...",
  "model": "mistral-nemo:12b",
  "gpu_used": "primary",
  "duration_seconds": 142,
  "completed_at": "2026-03-29T14:35:00Z"
}
```

### Polling (fallback)

If the client was asleep when the webhook was sent (delivery failed), the client
polls `GET /tasks/{id}` on wake. If the task is still running, the client
registers for the webhook again and waits.

Webhook delivery retries 3 times with exponential backoff before giving up. The
result remains available via polling indefinitely (until the client retrieves it
or the task is cleaned up).

### Session Queries

For session tasks, the client sends inference requests within the session:

```
POST /sessions/{session_id}/generate
{
  "prompt": "...",
  "stream": true
}
```

Responses stream back (SSE) or return as a single JSON body, matching Ollama's
API format so the client app proxy can transparently forward them.

## REST API

```
# Task management
POST   /tasks                     Submit a task
GET    /tasks                     List queue (filterable: status, priority, client_id)
GET    /tasks/{id}                Task detail + result
PATCH  /tasks/{id}                Update (cancel, reprioritize)
DELETE /tasks/{id}                Cancel and remove

# Session management
POST   /sessions                  Open a session
GET    /sessions/{id}             Session status
POST   /sessions/{id}/generate    Send inference request within session
POST   /sessions/{id}/keepalive   Extend session timeout
DELETE /sessions/{id}             Close session, release GPU

# Queue controls
POST   /queue/pause               Pause queue (current task finishes)
POST   /queue/resume              Resume queue
POST   /queue/drain               Finish current task, then pause
POST   /tasks/current/cancel      Cancel running task

# Task approval (from desktop notification actions)
POST   /tasks/{id}/approve        User approves a queued task
POST   /tasks/{id}/defer          User defers a task (body: duration or "until_idle")

# System status
GET    /status                    Machine state, queue depth, current task, user presence
GET    /health                    Heartbeat (for HA/clients to check before sleep/WoL)
GET    /gpus                      GPU inventory: labels, roles, VRAM, current model, utilization
GET    /models                    Available Ollama models + GPU fit info

# Authentication
POST   /auth/register             Register a new client (returns token)
POST   /auth/revoke               Revoke a client token
```

## Power Management

### Workstation States

```
sleeping ←→ idle ←→ user_active
              ↕          ↕
          ai_working ← both
```

- **sleeping** — powered off, WoL-able
- **idle** — awake, no user activity, no AI tasks. Safe to sleep.
- **user_active** — human at the keyboard. Not safe to sleep.
- **ai_working** — running task(s), no user. Not safe to sleep.
- **both** — user active AND AI task running.

### Wake

- Any client can wake the workstation via the configured wake method (WoL, IPMI,
  smart plug). The client app and CLI include a wake helper.
- After waking, the client polls `GET /health` until the server is up, then
  submits work.
- Ringmaster tracks `woken_for_work = true` when the first task arrives on a
  boot with no user login.

### Sleep Inhibition

Ringmaster holds a `systemd-inhibit` lock (blocking `sleep` and `shutdown`)
while any task is running. This prevents all sources of suspend/shutdown:
systemctl, GUI, HA automations, power button, lid close, unattended-upgrades.

**When sleep is requested while tasks are running:**

*User is at the display:*
Desktop notification: "Ringmaster is running a task for [client]. The machine
will sleep when it finishes (est. ~N min). [Sleep Now] [OK]"
- **Sleep Now** — Ringmaster gracefully stops the task (marks `interrupted`,
  re-queues), releases the inhibitor, sleeps the machine.
- **OK** — Ringmaster sets `sleep_when_done = true`, blanks displays, locks the
  screen. Machine sleeps after last task.

*User is NOT at the display (sleep from HA/phone):*
- Ringmaster holds the sleep via inhibitor.
- The requesting system (HA) receives: `{"sleep": "deferred", "reason":
  "task_running", "est_completion": "2026-03-29T14:35:00Z"}`
- HA can display workstation state as "finishing work" rather than "on" or "off".
- Machine sleeps automatically when last task finishes.

**When shutdown/reboot is requested while tasks are running:**

Same pattern. Desktop notification: "Ringmaster is running tasks. [Shut down
anyway] [Shut down when done]"

For unattended-upgrades: configure `Automatic-Reboot "false"` in apt config.
Ringmaster checks for `reboot_pending` when the queue drains and reboots then.

### Display and Lock

When sleep is deferred (user said OK, or sleep came from HA while AI is working):
- Displays blank immediately (`display_off_command`)
- Screen locks (`lock_command`)
- GPU stays in compute mode for inference
- If the user moves the mouse, displays wake — Ringmaster does not fight this

### Sleep After Work

When the last task completes:
- If `sleep_when_done` is set (deferred sleep request pending): sleep.
- If `woken_for_work` is true and no user has logged in: sleep.
- If a user is active: stay awake, clear flags.

### GPU Power State

While tasks are running, Ringmaster optionally sets the GPU to a compute
performance profile (via `gpu_compute_profile_command`) to prevent power
management from downclocking during inference. Profile is restored after the
last task finishes.

### Network Continuity

The workstation must remain network-reachable while locked/display-off. The
server config should document: disable WiFi power saving, ensure ethernet is
not affected by screen lock, verify network manager does not drop connections
on lock.

## Notifications

### Desktop (user at the workstation)

D-Bus desktop notifications with action buttons. Used for:
- Task needing approval
- Sleep/shutdown intercepted
- Task failure
- Queue completed ("work done, sleeping now")

### Push (user away from the workstation)

Configurable backend. Phase 1: Home Assistant companion app push notifications.

Used when:
- Machine is locked or displays are off
- Sleep/shutdown was requested remotely
- A notification has no one to show it to on the local display

### Fallback Behavior

If no notification channel is reachable (desktop locked, HA down), Ringmaster
proceeds based on the task's `unattended_policy`:
- `run` — proceed without approval (default for scheduled tasks)
- `wait` — hold in queue until a human can approve (default for interactive)
- `skip` — drop the task and notify the client via webhook

## Authentication

Clients register with `ringmaster register` or `POST /auth/register`. The server
issues a bearer token stored in a local token file. All API requests require
the token in the `Authorization` header.

Clients must be on the same network or connected via VPN. Ringmaster does not
expose itself to the public internet.

Token revocation: `ringmaster revoke <client_id>` or `POST /auth/revoke`.

## Data Storage

SQLite database on the workstation. Tables:

- **tasks** — id, task_type, model, prompt, priority, deadline, status,
  client_id, callback_url, unattended_policy, submitted_at, started_at,
  completed_at, result, error, gpu_used, duration_seconds, metadata
- **sessions** — id, client_id, model, gpu_label, status, opened_at,
  last_activity_at, idle_timeout_seconds
- **gpus** — label, role, fingerprint (JSON), current_model, status
- **clients** — client_id, token_hash, registered_at, last_seen
- **power_events** — timestamp, event_type, source, detail (audit log)

## Phase Boundaries

### Phase 1 (MVP)

- Server daemon: FastAPI, SQLite queue, single-GPU scheduling
- GPU init and fingerprinting
- Discrete and session task types
- Priority queue ordering (no preemption)
- Queue pause/resume/cancel/drain
- Sleep/shutdown inhibition with user feedback
- Display blanking + lock on deferred sleep
- Desktop notifications (D-Bus)
- HA push notifications as fallback
- Webhook callbacks with poll fallback
- CLI (submit, queue, status, gpu, pause, resume, cancel-current, drain)
- Client app: Ollama proxy, session management, tray icon, web UI, notifications
- Authentication (token-based)
- `ringmaster init` for GPU setup

### Phase 2

- Multi-GPU scheduling with task-to-GPU matching
- Model-to-GPU capability map
- Task GPU preferences and fallback models
- Task preemption (higher tier interrupts lower)
- Task pause/checkpoint and resume
- Task migration between GPUs
- User gaming on one GPU while AI runs on another
- Additional notification backends (ntfy, Pushover, Matrix)

### Future

- N GPUs, M concurrent tasks (resource pool scheduling)
- Remote coding assistant proxy (Codium/Continue backend)
- Dashboard web UI on the server for monitoring
- Metrics export (Prometheus)
- Task history and analytics
- Plugin system for custom task types beyond Ollama

## Technical Decisions

- **Language:** Python. Same ecosystem as Ollama clients, cross-platform for
  client app, team familiarity.
- **API framework:** FastAPI. Async, OpenAPI docs for free, lightweight.
- **Database:** SQLite. Single-machine, no external dependencies, WAL mode for
  concurrent reads.
- **GPU detection:** ROCm tools (`rocm-smi`) for AMD. Abstracted behind a
  provider interface for future CUDA/NVIDIA support.
- **Notifications:** `desktop-notifier` (cross-platform), `pystray` (tray icon),
  HA REST API (push).
- **Process management:** systemd service on the workstation. Client app runs
  as user-level systemd service or manual launch.
- **Ollama interaction:** HTTP API at `localhost:11434`. No special client
  library. Model load/unload via the standard API.

## Non-Goals

- Ringmaster is not an Ollama replacement. It manages the workstation around
  Ollama, not inference itself.
- Ringmaster is not a cluster scheduler. It is single-machine by design.
  Multiple machines would each run their own Ringmaster instance (federation
  is out of scope).
- Ringmaster does not modify AI tools (Codium, Msty, etc.). The client app's
  Ollama proxy provides transparent compatibility.
- Ringmaster does not manage model downloads or fine-tuning.
