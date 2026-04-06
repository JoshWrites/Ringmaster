# Ringmaster Client App — Design Spec

**Date:** 2026-04-06
**Branch:** client-app
**Status:** Draft

---

## 1. Overview

### Problem

AI tools (opencode, Msty, Continue, custom scripts) expect a local Ollama instance at `localhost:11434`. Today, users either talk to Ollama directly (bypassing Ringmaster's GPU queue) or can't use these tools with Ringmaster at all. Meanwhile, multiple users and automated tasks compete for the same GPU with no coordination.

Ringmaster needs a client component that makes every Ollama-consuming tool a managed participant in the GPU queue — without modifying those tools.

### Users and Workloads

- **Two live users** on the network, running interactive AI tools (coding assistants, chat)
- **Automated tasks** running from Proxmox VMs/containers (file analysis, weekly network review)
- **One workstation** with the GPU (AMD RX 7900 XTX, 24GB VRAM) running the Ringmaster server and Ollama
- A second, smaller GPU on the workstation for lighter tasks (future multi-GPU scheduling)

### Architecture

```
 Machine A (user)          Machine B (user)          Proxmox VM (automation)
 ┌──────────────────┐      ┌──────────────────┐      ┌──────────────────┐
 │ TUI Launcher     │      │ TUI Launcher     │      │ CLI Wrapper      │
 │ ┌──────────────┐ │      │ ┌──────────────┐ │      │ ringmaster-client│
 │ │ Client Daemon│ │      │ │ Client Daemon│ │      │   run --profile  │
 │ │ :11434       │ │      │ │ :11434       │ │      │   batch-analysis │
 │ └──────┬───────┘ │      │ └──────┬───────┘ │      │ ┌──────────────┐ │
 │   opencode, etc  │      │   Msty, etc      │      │ │ Client Daemon│ │
 └────────┼─────────┘      └────────┼─────────┘      │ │ :11434       │ │
          │                         │                 │ └──────┬───────┘ │
          │                         │                 │   python scripts │
          └─────────────┬───────────┘                 └────────┼─────────┘
                        │                                      │
                        ▼                                      │
              ┌─────────────────────┐                          │
              │  Ringmaster Server  │◄─────────────────────────┘
              │  :8420              │
              │  ┌───────────────┐  │
              │  │ Scheduler     │  │
              │  │ Policy Engine │  │
              │  │ Session Mgr   │  │
              │  └───────┬───────┘  │
              │          │          │
              │  ┌───────▼───────┐  │
              │  │ Ollama :11435 │  │
              │  │ (internal)    │  │
              │  └───────────────┘  │
              │  GPU: 7900 XTX     │
              │  GPU: (smaller)    │
              └─────────────────────┘
```

On the workstation, Ollama is reconfigured to a non-default port (`:11435`) via `OLLAMA_HOST=127.0.0.1:11435` in its systemd unit. The client daemon occupies `:11434` so all local tools go through the queue. No requests bypass Ringmaster.

### Packaging

- `pip install ringmaster` — installs the **client only** (daemon, TUI, CLI wrapper, Ollama proxy)
- `pip install ringmaster[server]` — adds the **server** (scheduler, GPU management, SQLite, worker)

The base package cannot run a server. The workstation installs with `[server]` and gets both components — its own tasks are queued like any other client.

### Three Modes of Operation

**1. TUI Launcher (Interactive)** — For humans at a keyboard. The user opens the Ringmaster TUI, selects an app, and the client queues a session request. The TUI shows queue position and estimated wait. When the session is granted, the client starts the Ollama proxy and launches the app. When the app exits, the session is released.

**2. CLI Wrapper (Headless Automation Needing Ollama)** — For scripts/tools in Proxmox VMs that expect a local Ollama instance:

```bash
ringmaster-client run --class batch --model mistral-nemo:12b -- python analyze.py
```

Sends a session request (including app manifest), blocks until granted, starts the proxy, runs the command, tears down on exit.

**3. Direct API (Prompt-to-Result)** — For automation that just needs a prompt processed, no Ollama proxy needed. Already exists today via `POST /tasks`. No changes needed.

---

## 2. Client

### Daemon

The client daemon is a long-running process (systemd user service) that always binds to `localhost:11434`.

**When no session is active:**

- Returns an Ollama-shaped error to connecting tools: `{"error": "Ollama is not available. Please launch via Ringmaster."}`
- **Logs the connection attempt** with process identification:
  - Uses `/proc/net/tcp` inode lookup + `/proc/[pid]/fd/` scan to find the peer PID (via `psutil`)
  - Reads `/proc/{pid}/comm` and `/proc/{pid}/cmdline` for process name and full command line
  - Records: timestamp, process name, command line, user, attempt count
  - This identification only works for local (same-machine) connections from the **same user** as the daemon (without root). Cross-user local processes and remote connections log source IP/port only.

**When a session is active:**

- Proxies Ollama API requests to the Ringmaster server
- Full streaming support (newline-delimited JSON, matching Ollama's wire format)
- Sends keepalive pings to maintain the session

**Control interface:**

- Listens on a Unix domain socket for commands from the TUI/CLI
- Commands: start session, stop session, get status, get attempt log, reload config

**Wake-on-LAN:**

When a session is requested and the server is unreachable, the daemon sends a WoL magic packet to the workstation's MAC address, then polls `/health` with backoff until the server comes online.

### TUI Launcher

An interactive terminal UI that connects to the local daemon via the control socket.

**App registry** — configured in the client config:

```yaml
server:
  host: http://workstation:8420
  mac: "AA:BB:CC:DD:EE:FF"
  token_env: RINGMASTER_TOKEN

client:
  name: joshdesktop
  user: josh

apps:
  opencode:
    command: opencode
    class: interactive
    model: mistral-nemo:12b
  msty:
    command: msty
    class: interactive
    model: llama3:8b
  file-analysis:
    command: python /opt/scripts/analyze.py
    class: batch
    model: mistral-nemo:12b
```

**Features:**

- **App menu** — list configured apps, select one to launch
- **Queue status** — real-time display of queue position and estimated wait
- **Session status** — shows active session, model loaded, time remaining
- **Missed connection log** — on launch, shows apps that tried to reach Ollama while no session was active:
  ```
  Missed Ollama requests:
    opencode          — 3 attempts, last 2 min ago    [Add app]
    python analyze.py — 1 attempt, 5 min ago          [Add app]
  ```
- **Add app** — interactively add a new app to the config from the TUI
- **Model requests** — request the admin install a model not yet available
- **Notifications** — desktop notification when a queued session is granted ("GPU ready — opencode is starting")

### Ollama API Compatibility

The proxy implements the full Ollama API surface that tools expect.

**Metadata — always available, no session required:**

| Endpoint | Method | Behavior |
|---|---|---|
| `/api/tags` | GET | List available models (proxied to server) |
| `/api/show` | POST | Model details (proxied to server) |
| `/api/ps` | GET | Currently loaded models (proxied to server; empty if no active session) |

These allow tools to discover models and configure themselves without holding a GPU session.

**Inference — require an active session:**

| Endpoint | Method | Behavior |
|---|---|---|
| `/api/chat` | POST | Proxy to server, full streaming support |
| `/api/generate` | POST | Proxy to server, full streaming support |
| `/api/embeddings` | POST | Proxy to server |

Without an active session, these return an Ollama-shaped error: `{"error": "No active session. Launch via Ringmaster."}` and the attempt is logged for the TUI's missed-connection display.

Streaming responses use Ollama's wire format: newline-delimited JSON objects, with a final object containing `"done": true`.

**Admin-only — rejected at the client, never forwarded:**

| Endpoint | Method | Behavior |
|---|---|---|
| `/api/pull` | POST | Rejected: "Model installation is admin-only. Use 'ringmaster-client request-model \<name\>' to request it." |
| `/api/delete` | DELETE | Rejected: "Model deletion is admin-only." |
| `/api/copy` | POST | Rejected |
| `/api/create` | POST | Rejected |

### Crash Recovery

If the client daemon crashes mid-session:

- The server's session idle timeout eventually closes the orphaned session and releases the GPU
- On restart, the daemon checks the server for any open sessions belonging to this client and either resumes or closes them
- The TUI shows the recovered state on next launch

If the server crashes:

- Client daemon detects lost connection, stops proxying, returns error responses to tools
- Client retries connection on a backoff schedule

---

## 3. Server

### Session Lifecycle

Sessions are the fundamental unit of GPU scheduling. Every request goes through a session.

```
requested → queued → granted → active → closed
```

- **requested** — client has asked for GPU time
- **queued** — server has accepted the request, waiting for GPU availability
- **granted** — GPU is available, client is notified
- **active** — client has started using the session (proxy is forwarding requests)
- **closed** — session ended (app exited, idle timeout, or explicit close)

Each session has a stable UUID that identifies it throughout its lifecycle, tags all proxied requests, and serves as the key for all scheduling decisions. In the future, this UUID will map to a GPU state snapshot.

### VRAM Cleanup

When a session closes, the server checks the queue. If no sessions are queued or active, the server unloads the model from VRAM (`keep_alive=0`). This frees the GPU entirely for non-AI use (gaming, rendering, etc.) rather than leaving a model parked in VRAM.

If another session is queued, the model stays loaded (or the next session's model is loaded immediately).

### Scheduling

**Task classes** — the client declares what kind of work it's doing:

- **interactive** — human at keyboard, latency-sensitive
- **batch** — automated, no human waiting, throughput-oriented
- **scheduled** — cron-triggered, has a time window, flexible

**The client does not set its own priority.** It provides identity and intent. The server decides priority based on:

- Task class
- Full queue state (who else is waiting, what's running)
- App manifest (what tools this client has configured)
- Historical patterns
- Workstation user presence

### App Manifest

On each session request, the client sends its full app manifest — the list of all configured apps, their task classes, and models. The server:

- Stores the manifest per client
- Compares against previous reports to detect changes (new app added, one removed)
- Uses the cross-network view of all clients' manifests to inform scheduling

This gives the server a complete picture: which machines exist, what tools they run, what kind of workloads to expect.

### Session Request Payload

```json
{
  "client_id": "joshdesktop",
  "user": "josh",
  "model": "mistral-nemo:12b",
  "task_class": "interactive",
  "app": "opencode",
  "manifest": {
    "apps": [
      {"name": "opencode", "class": "interactive", "model": "mistral-nemo:12b"},
      {"name": "msty", "class": "interactive", "model": "llama3:8b"},
      {"name": "file-analysis", "class": "batch", "model": "mistral-nemo:12b"}
    ]
  }
}
```

### Model Management

Clients cannot install or delete models. They can request them:

1. Client sends `POST /models/requests` with model name, client ID, and optional reason
2. Server logs the request for the admin digest
3. Admin reviews and approves/denies
4. Approval triggers a pull on the server's Ollama instance
5. Requesting client is notified when the model becomes available

### Admin Digest

The server collects admin-facing events and sends them as a periodic digest rather than individual notifications:

- **Model requests** — clients requesting models not yet installed
- **New app registrations** — previously unseen apps appearing in client manifests, needing priority/class guidance
- **Scheduling anomalies** — e.g., a client consistently starved of GPU time, sessions timing out before grant

```yaml
admin:
  email: josh@example.com
  digest_frequency: daily    # hourly | daily | weekly
  digest_time: "08:00"       # for daily/weekly: when to send (local time)
  digest_day: monday         # for weekly: which day
```

If no events accumulated since the last digest, no email is sent. Urgent events (server errors, GPU hardware faults) bypass the digest and notify immediately.

### Power Management

**Boot:** The server starts on boot via a system-level systemd service (`ringmaster-server.service` with `WantedBy=multi-user.target`), ensuring availability after reboots, power outages, or WoL wakes.

**Auto-sleep:** The server triggers sleep when ALL of the following are true:

- No sessions are active or queued
- No tasks are queued or running
- No user is present at the workstation (keyboard/mouse idle, no active desktop session)
- VRAM has been purged (last model unloaded)
- A configurable cooldown period has elapsed since the last activity (prevents sleep-wake thrashing)

```yaml
power:
  auto_sleep: true
  idle_cooldown_seconds: 300
  sleep_command: "systemctl suspend"
```

The server inhibits sleep (via systemd-logind inhibitor) while any session or task is active, and releases the inhibitor when idle conditions are met.

### Crash Recovery

Sessions are persisted in SQLite — on server restart, the scheduler recovers state from the database.

### Workstation Ollama Port

Ollama is reconfigured to `:11435` via its systemd unit:

```ini
Environment=OLLAMA_HOST=127.0.0.1:11435
```

The Ringmaster server's `OllamaClient` points at this internal port. The Ollama CLI on the workstation also needs this env var set to function. External tools never see this port — they go through the client daemon on `:11434`.

### Server Readiness

The current server needs evaluation for:

- **Concurrency** — async handling, thread safety, connection pooling under concurrent client load
- **CPU pinning / multi-threading** — the server will manage more state and I/O with multiple clients
- **New API endpoints** — session request/grant/status protocol, manifest ingestion, model requests, streaming proxy to Ollama

---

## 4. Future Plans

These are NOT being implemented now but the architecture must support them.

### Soft Unload (Precursor to VRAM Hotswap)

When an interactive session is idle (no Ollama calls for a configurable duration), the server:

1. Unloads the model from VRAM — GPU is now free
2. Session remains **open** from the client's perspective (proxy stays up, session is not closed)

When the session receives a new request:

1. Session is moved to the **front of the queue** (interactive user returned to keyboard)
2. If a batch task is currently running on the GPU, it is **terminated** and re-queued as next-in-line
3. The model is reloaded and the request processed

Batch tasks accept that they may be preempted — that's the contract of running at lower priority.

```yaml
sessions:
  soft_unload_idle_seconds: 300
```

### VRAM Context Switching

The server will manage multiple Ollama instances (one per session, each on a unique internal port). Only one holds VRAM at a time.

#### Reload-Based Swapping (First Implementation)

1. Finish current inference on session A
2. Evict session A's model (`keep_alive=0`)
3. Load session B's model (from Linux page cache)
4. Run session B's request

**Performance:** 1-3 seconds for an 8GB model (Ollama reinitializes from scratch: GGUF parsing, tensor setup, mmap, KV cache allocation).

**Limitation:** KV cache (conversation context) is lost on unload. Prior conversation must be re-processed from message history.

To guarantee fast reloads, active models' GGUF blobs should be pinned in RAM using `vmtouch -l` or `mlock()`, with `RLIMIT_MEMLOCK` raised to ~36GB. With 64GB system RAM, 2-3 models can be pinned simultaneously.

#### Arena-Based VRAM Hotswap (Research)

A faster approach that preserves full session state including KV cache, bypassing Ollama's reinitialization entirely.

**Concept:** An LD_PRELOAD shim intercepts `hipMalloc` and corrals each Ollama instance's VRAM allocations into a single fixed arena. All VRAM state (weights, KV cache, scratch buffers) lives in one contiguous block with stable pointers.

**Swap flow:**

1. Finish current inference on session A's Ollama instance
2. SIGSTOP session A's process (freeze — microseconds)
3. `hipMemcpy` session A's arena: VRAM → host buffer in system RAM (~300-400ms for 8GB at PCIe 4.0)
4. `hipMemcpy` session B's host buffer → VRAM arena (~300-400ms)
5. SIGCONT session B's process (thaw — microseconds)
6. Run session B's request

**Total swap time: ~600-800ms** — 2-4x faster than reload-based swapping.

**Key advantage:** KV cache is preserved. Full conversation context survives the swap. The Ollama process has no idea anything happened.

**Implementation requires:**

- **LD_PRELOAD HIP allocator shim** — intercepts `hipMalloc`/`hipFree`, sub-allocates from a pre-allocated arena
- **Host-side buffer management** — one RAM buffer per session (~8-16GB each). With 64GB RAM, 3-4 sessions can be stashed.
- **Process lifecycle management** — SIGSTOP/SIGCONT for freezing/thawing instances
- **Arena sizing** — configurable per session based on model + max context length

**Open questions:**

- Does `hipMemcpy` device→host work on a SIGSTOP'd process's allocations, or must the copy complete before the stop?
- How does ROCm handle a SIGSTOP'd process that holds GPU allocations? Does the driver reclaim anything?
- Can the shim reliably intercept all of llama.cpp's VRAM allocations, including ROCm internals?

This is a research project. The reload-based approach is the safe fallback.

### Multi-GPU Scheduling

The workstation has two GPUs of different specs. The scheduler will:

- Match tasks to GPUs based on model size vs. available VRAM
- Run light tasks (small models) on the smaller GPU concurrently with heavy tasks on the 7900 XTX
- Each GPU independently manages its own Ollama instance pool and VRAM swap cycle

The data model already carries GPU assignment per session. The current single-GPU sequential scheduler is the first implementation of an interface that will grow to support multi-GPU concurrent scheduling.

### Steam / Gaming Coexistence

When an AI task holds the primary GPU (7900 XTX), the server should integrate with Steam to:

- Detect when a **game** is launched (Steam itself runs persistently — only game process starts matter)
- Force the game onto the secondary GPU (via `DRI_PRIME`, `DXVK_FILTER_DEVICE_NAME`, or Steam per-game launch options)
- Optionally: if the game needs the primary GPU, notify the user and offer to preempt the AI session or defer the game launch
- When the AI session ends and no game is running, the primary GPU returns to the AI pool

The workstation runs X11, where multi-GPU via `DRI_PRIME` and `DXVK_FILTER_DEVICE_NAME` is well-tested and reliable. Per-game configuration is more reliable than automatic injection. Some games hard-select GPU 0 regardless of environment variables.

### Multiple Ollama Instances

Each session maps to its own Ollama process on a dynamic port. The server's Ollama Instance Manager:

- Spawns/destroys Ollama processes
- Maps session UUID → Ollama port
- Tracks which instance currently holds VRAM on which GPU
- Handles the evict/load cycle
