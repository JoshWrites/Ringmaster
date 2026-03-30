# Ringmaster

A workstation-resident daemon that turns a personal computer into a shared AI compute node for a home network — while respecting that someone might be using it to game, browse, or work.

## What it does

Ringmaster sits between your AI tools and [Ollama](https://ollama.com), managing access to your GPU(s) across the network:

- **Task queue** — Accepts inference requests from any device on your network, prioritizes them, and runs them one at a time against your local Ollama instance.
- **GPU management** — Discovers your GPUs at install time, tracks them by hardware fingerprint (not bus ID), and targets the right card even if device indices shuffle between reboots.
- **User coexistence** — Detects when you're at the keyboard and asks before stealing the GPU. You can approve, defer, or let tasks auto-proceed when you're idle.
- **Power management** — Prevents sleep/shutdown from killing a running inference task. Wakes the machine for scheduled AI work and sleeps it when done.
- **Session support** — Interactive tools (coding assistants, chat) get session-based GPU reservations so the queue doesn't steal the card between keystrokes.

## Architecture

**Server daemon** (runs on the workstation) — FastAPI REST API, SQLite task queue, Ollama management, sleep/shutdown inhibition, desktop and push notifications.

**CLI** — Thin wrapper over the REST API. Submit tasks, check queue status, pause/resume, manage GPUs. Works from any machine that can reach the server.

**Client app** (planned) — System tray daemon for other machines. Runs a local Ollama-compatible proxy so your tools (Codium, Msty, etc.) just point at localhost — the client handles session management and notifications transparently.

## Quick start

```bash
# Install in a virtual environment
git clone https://github.com/JoshWrites/Ringmaster.git
cd Ringmaster
python3 -m venv .venv
source .venv/bin/activate
pip install .

# Detect GPUs and create config
ringmaster init

# Bootstrap your first API token
python3 -c "
from ringmaster.server.auth import AuthManager
mgr = AuthManager()
token = mgr.register('my-workstation')
mgr.save('tokens.json')
print(f'Your token: {token}')
"

# Start the server
python3 -m ringmaster.server.run

# In another terminal (with the venv activated)
export RINGMASTER_TOKEN=<your-token>
ringmaster status
```

For the full setup walkthrough — including manual GPU config, systemd service, remote access, and troubleshooting — see [docs/Anny/installation.md](docs/Anny/installation.md).

## Configuration

Copy `ringmaster.example.yaml` to `ringmaster.yaml` and edit. All values have sensible defaults. Key sections:

- **gpus** — Label and role for each GPU (populated by `ringmaster init`)
- **notifications** — Desktop (D-Bus) or Home Assistant push
- **power** — Sleep/lock/display-off commands
- **queue** — Max depth, default priority, session timeout
- **auth** — Token file path

## CLI usage

```bash
# Check workstation status
ringmaster status --host http://workstation:8420 --token $TOKEN

# Submit a task
ringmaster submit --model mistral-nemo:12b --prompt "Analyze this data" --priority 1

# View the queue
ringmaster queue

# Pause the queue (current task finishes, then stops)
ringmaster pause

# Resume
ringmaster resume

# Cancel whatever's running right now
ringmaster cancel-current
```

Set `RINGMASTER_TOKEN` in your environment to skip `--token` on every call.

## API

Full REST API at `http://workstation:8420`:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/tasks` | POST | Submit a task |
| `/tasks` | GET | List queue |
| `/tasks/{id}` | GET | Task detail + result |
| `/sessions` | POST | Open interactive session |
| `/sessions/{id}/generate` | POST | Query within session |
| `/sessions/{id}/keepalive` | POST | Extend session |
| `/queue/pause` | POST | Pause queue |
| `/queue/resume` | POST | Resume queue |
| `/queue/drain` | POST | Finish current, then pause |
| `/tasks/current/cancel` | POST | Cancel running task |
| `/status` | GET | Machine state + queue info |
| `/health` | GET | Heartbeat (no auth required) |
| `/gpus` | GET | GPU inventory |
| `/models` | GET | Available Ollama models |
| `/auth/register` | POST | Register a client |

All endpoints except `/health` require a bearer token.

## Task types

**Discrete** — Submit a prompt, get a result. Task completes, queue moves on.

**Session** — Reserve the GPU for an interactive tool. Send multiple queries without re-queuing. Auto-closes after idle timeout.

## Phase roadmap

- **Phase 1** (current) — Server daemon, CLI, single-GPU scheduling, sleep/shutdown protection, notifications
- **Phase 2** — Multi-GPU scheduling, task-to-GPU matching, preemption, task pause/migrate
- **Client app** — Cross-platform tray daemon with Ollama proxy for transparent tool integration

## Requirements

- Python 3.11+
- [Ollama](https://ollama.com) running on the workstation
- `rocm-smi` (AMD) or `nvidia-smi` (NVIDIA) for GPU detection
- Linux (systemd for sleep inhibition, D-Bus for notifications)

## License

MIT
