# Configuration reference

Every field in `ringmaster.yaml`, with type, default value, and description. Generate a starter config with `ringmaster init`.

For a guided walkthrough, see the [Configuration guide](../guide/configuration.md).

---

## `server`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `host` | string | `"0.0.0.0"` | Interface to bind the HTTP API server to. |
| `port` | integer | `8420` | TCP port for the HTTP API server. |

---

## `gpus`

A list of GPU configurations. Each entry:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `label` | string | *(required)* | Human-readable name used in logs and API responses. |
| `role` | string | `"compute"` | Intended use: `compute`, `gaming`, or `both`. |
| `prefer_for` | list of strings | `[]` | Task types this GPU should be preferred for, e.g. `["embedding", "image_generation"]`. |
| `fingerprint` | object | *(required)* | Hardware fingerprint (see below). |

### `fingerprint`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `vendor` | string | *(required)* | GPU vendor: `"NVIDIA"`, `"AMD"`, or `"Intel"`. |
| `model` | string | *(required)* | Model name as reported by `nvidia-smi` or `rocm-smi`. |
| `vram_mb` | integer | *(required)* | Total VRAM in mebibytes. |
| `serial` | string | `null` | Board serial number, if available from the driver. |
| `device_id` | string | `null` | PCI device ID string, e.g. `"10de:2684"`. |

---

## `ollama`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `host` | string | `"http://localhost:11434"` | Base URL of the Ollama HTTP API. |

---

## `notifications`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `backend` | string | `"desktop"` | Primary notification backend: `"desktop"` (libnotify/D-Bus) or `"none"`. |
| `config` | object | `{}` | Backend-specific configuration key-value pairs. |
| `fallback_backend` | string | `"desktop"` | Backend to use when the primary fails (e.g. D-Bus unavailable). |

---

## `power`

All command fields are optional. Omitting a command disables that feature. Commands run as the Ringmaster process user.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `wake_method` | string | `"none"` | How to wake from sleep: `"none"`, `"wol"` (Wake-on-LAN), or `"command"`. |
| `sleep_command` | string | `null` | Shell command to put the workstation to sleep. |
| `display_off_command` | string | `null` | Shell command to turn off the display without sleeping. |
| `lock_command` | string | `null` | Shell command to lock the screen. |
| `gpu_compute_profile_command` | string | `null` | Shell command to switch the GPU to a power-efficient compute profile when idle. |

---

## `idle`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `detection_method` | string | `"dbus"` | How to detect user idle state: `"dbus"` (org.freedesktop.ScreenSaver), `"xprintidle"`, or `"none"`. |
| `idle_threshold_seconds` | integer | `300` | Seconds of inactivity before the session is considered idle. |
| `auto_approve_when_idle` | boolean | `true` | Auto-approve tasks without notification when the session is idle. |
| `auto_approve_timeout_seconds` | integer | `60` | Seconds to wait for manual approval before auto-approving when the session is active. Set `0` to disable. |

---

## `queue`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `max_queue_depth` | integer | `100` | Maximum tasks that can wait in the queue. Submitting when full returns HTTP 429. |
| `default_priority` | integer | `3` | Priority assigned to tasks that don't specify one (1 = highest, 5 = lowest). |
| `session_idle_timeout_seconds` | integer | `600` | Seconds of inactivity before a session's reserved GPU slot is released. |

---

## `auth`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `token_file` | string | `"tokens.json"` | Path to the JSON file containing API bearer tokens. Relative paths resolve from the directory containing `ringmaster.yaml`. |
