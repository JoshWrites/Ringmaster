# Configuration

Ringmaster is configured through a YAML file, typically `ringmaster.yaml` in your project root. Generate a starter config with `ringmaster init`, then edit it to match your setup.

Every section is optional. Fields you don't include keep their defaults.

## `server`

Where the HTTP API listens.

```yaml
server:
  host: "0.0.0.0"
  port: 8420
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `host` | string | `"0.0.0.0"` | Bind address. Use `0.0.0.0` to accept connections from the network, `127.0.0.1` for local only. |
| `port` | integer | `8420` | TCP port. Change only if you have a conflict. |

!!! tip
    If you only run tasks from the same machine, set `host: "127.0.0.1"` to prevent network access entirely.

## `gpus`

Declare your GPU hardware. Ringmaster uses fingerprints to verify it's talking to the expected card — not PCI bus indices, which can change across reboots.

```yaml
gpus:
  - label: "Primary Compute"
    role: compute
    prefer_for:
      - chat
      - embedding
    fingerprint:
      vendor: "NVIDIA"
      model: "RTX 4090"
      vram_mb: 24576
      serial: "GPU-abc123def456"
      device_id: "10de:2684"
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `label` | string | *(required)* | Human-readable name for logs and API responses. |
| `role` | string | `"compute"` | How this card is used: `compute`, `gaming`, or `both`. |
| `prefer_for` | list | `[]` | Task types this GPU should handle first when multiple GPUs are available. |

### `fingerprint` (nested)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `vendor` | string | *(required)* | GPU vendor: `"NVIDIA"`, `"AMD"`, or `"Intel"`. |
| `model` | string | *(required)* | Model name as reported by `nvidia-smi` or `rocm-smi`. |
| `vram_mb` | integer | *(required)* | Total VRAM in mebibytes (24 GiB = 24576 MiB). |
| `serial` | string | `null` | Board serial number, if available. |
| `device_id` | string | `null` | PCI device ID string, e.g. `"10de:2684"`. |

!!! tip
    If you have one GPU, you can omit the `gpus` section entirely. `ringmaster init` fills it in for you.

## `ollama`

Connection to the Ollama inference backend.

```yaml
ollama:
  host: "http://localhost:11434"
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `host` | string | `"http://localhost:11434"` | Base URL of the Ollama HTTP API. |

!!! tip
    If Ringmaster and Ollama run on the same machine, you don't need to change this section.

## `notifications`

How Ringmaster asks for your approval when a task arrives while you're at the keyboard.

```yaml
notifications:
  backend: desktop
  fallback_backend: desktop
  config: {}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `backend` | string | `"desktop"` | Primary notification backend: `"desktop"` (libnotify/D-Bus) or `"none"`. |
| `config` | object | `{}` | Backend-specific key-value pairs (none needed for desktop). |
| `fallback_backend` | string | `"desktop"` | Backend to use when the primary fails (e.g., display server unavailable). |

## `power`

Commands for managing workstation power state. All are optional — omitting a command disables that feature. Commands run as the user that runs the Ringmaster process.

```yaml
power:
  wake_method: none
  # sleep_command: "systemctl suspend"
  # display_off_command: "xset dpms force off"
  # lock_command: "loginctl lock-session"
  # gpu_compute_profile_command: "nvidia-smi -pl 200"
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `wake_method` | string | `"none"` | How to wake from sleep: `"none"`, `"wol"` (Wake-on-LAN), or `"command"`. |
| `sleep_command` | string | `null` | Shell command to put the workstation to sleep. |
| `display_off_command` | string | `null` | Shell command to blank the display without sleeping. |
| `lock_command` | string | `null` | Shell command to lock the screen. |
| `gpu_compute_profile_command` | string | `null` | Shell command to switch the GPU to a power-efficient profile when idle. |

## `idle`

When Ringmaster considers you "away" and can auto-approve tasks without asking.

```yaml
idle:
  detection_method: dbus
  idle_threshold_seconds: 300
  auto_approve_when_idle: true
  auto_approve_timeout_seconds: 60
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `detection_method` | string | `"dbus"` | How to detect idle state: `"dbus"` (org.freedesktop.ScreenSaver), `"xprintidle"`, or `"none"`. |
| `idle_threshold_seconds` | integer | `300` | Seconds of inactivity before the session is considered idle. |
| `auto_approve_when_idle` | boolean | `true` | Auto-approve tasks without notification when idle. Set `false` to always require explicit approval. |
| `auto_approve_timeout_seconds` | integer | `60` | Seconds to wait for manual approval before auto-approving when the session is active. Set `0` to disable. |

!!! tip
    On a workstation you share with others, set `auto_approve_when_idle: false` so tasks never run without someone explicitly saying yes.

## `queue`

Controls the task queue.

```yaml
queue:
  max_queue_depth: 100
  default_priority: 3
  session_idle_timeout_seconds: 600
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `max_queue_depth` | integer | `100` | Maximum tasks waiting in the queue. Submitting when full returns HTTP 429. |
| `default_priority` | integer | `3` | Priority for tasks that don't specify one (1 = highest, 5 = lowest). |
| `session_idle_timeout_seconds` | integer | `600` | Seconds before an inactive session's GPU slot is released. |

## `auth`

Token storage.

```yaml
auth:
  token_file: "tokens.json"
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `token_file` | string | `"tokens.json"` | Path to the JSON file containing bearer tokens. Relative paths resolve from the directory containing `ringmaster.yaml`. |

!!! info
    For a complete walkthrough of setting up tokens, see [Bootstrap authentication](installation.md#bootstrap-authentication) in the installation guide.

---

For the full field-level reference with validation rules, see [Configuration Reference](../reference/config.md).
