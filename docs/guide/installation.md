# Installation

Full installation walkthrough — virtual environments, GPU detection, systemd service setup, and remote access.

Ringmaster has two pieces: a **server** that runs on the GPU workstation, and a **CLI** that talks to it from anywhere on your network. Both install from the same package.

## What you need

- **Git** and **Python 3.11 or later** — this is all you need if you're installing the CLI on a laptop to submit tasks remotely

For running the server on the workstation, you also need:

- **Ollama** installed and running — Ringmaster dispatches tasks to Ollama but doesn't replace it
- **Linux with systemd** — sleep inhibition, service management, and D-Bus idle detection depend on it
- **GPU tools** — `rocm-smi` for AMD or `nvidia-smi` for NVIDIA, so that Ringmaster can detect your hardware

## Install

### Virtual environment (recommended)

Don't install into your system Python. Instead, use a virtual environment:

```bash
git clone https://github.com/JoshWrites/Ringmaster.git
cd Ringmaster
python3 -m venv .venv
source .venv/bin/activate
pip install .
```

You'll need to activate the venv (`source .venv/bin/activate`) each time you open a new terminal, or add it to your shell profile.

!!! tip "Pinning a version"
    `pip install .` installs from whatever commit you cloned. Once tagged releases exist, you can pin to a specific version: `pip install git+https://github.com/JoshWrites/Ringmaster.git@v0.1.0`

### pipx (CLI-only use)

If you only need the `ringmaster` CLI and don't plan to run the server from this machine, pipx keeps it isolated without managing a venv yourself:

```bash
pipx install git+https://github.com/JoshWrites/Ringmaster.git
```

!!! note
    pipx is designed for CLI tools, not long-running daemons. It gives you the `ringmaster` command, but if you're running the server (especially under systemd), use the virtual environment approach described above.

### Verify

```bash
ringmaster --help
```

You should see the list of commands: `status`, `queue`, `submit`, `gpu`, `pause`, `resume`, `drain`, `cancel-current`, and `init`.

## Configure

### GPU detection (machines with a discrete GPU)

If your workstation has an AMD or NVIDIA GPU with the appropriate tools installed:

```bash
ringmaster init
```

This command scans your hardware, asks you to label each GPU, and writes `ringmaster.yaml` in the current directory. To write to a different path:

```bash
ringmaster init --config <path-to-your-config>/ringmaster.yaml
```

### Manual configuration (no discrete GPU, or custom setup)

If `ringmaster init` doesn't detect your hardware — you're on Intel integrated graphics, your GPU tools aren't in PATH, or you want to configure things by hand — create `ringmaster.yaml` yourself:

```yaml
gpus:
  - label: primary
    role: compute              # compute, gaming, or both
    fingerprint:
      vendor: NVIDIA           # NVIDIA, AMD, or Intel
      model: RTX 4090          # as reported by nvidia-smi or rocm-smi
      vram_mb: 24576           # total VRAM in MiB
```

Replace the values with your actual hardware. The `vendor` and `model` values should match what your GPU tools report. The `vram_mb` value is the total VRAM in mebibytes. The `role` value tells Ringmaster how the card is used: `compute` for inference only, `gaming` for the display or gaming card, or `both`.

Every other setting has a sensible default. The full configuration reference is in [Configuration](configuration.md), but to get running, you only need the `gpus` block.

### Ollama

Ringmaster expects Ollama at `http://localhost:11434` by default. If yours is elsewhere:

```yaml
ollama:
  host: "http://<ollama-host>:11434"
```

Make sure you've pulled at least one model:

```bash
ollama pull llama3:8b
```

## Bootstrap authentication

Ringmaster uses bearer tokens for API auth. Here's the catch: the token registration endpoint *itself* requires a token. So you need to create the first one out-of-band.

From your Ringmaster project directory (this doesn't need `ringmaster.yaml` to exist yet — it just writes `tokens.json` to the current directory):

```python
python3 -c "
from ringmaster.server.auth import AuthManager
mgr = AuthManager()
token = mgr.register('<your-client-id>')
mgr.save('tokens.json')
print(f'Your token: {token}')
"
```

!!! warning
    Save that token. It's shown once and never stored in plaintext. Only the SHA-256 hash goes into `tokens.json`.

The `token_file` setting in `ringmaster.yaml` defaults to `"tokens.json"`. Relative paths resolve from the directory that contains `ringmaster.yaml`. For example, if your config is at `~/.config/ringmaster/ringmaster.yaml`, the server looks for `~/.config/ringmaster/tokens.json`. Make sure the token file is next to your config, or use an absolute path in the config.

Set it in your environment:

```bash
export RINGMASTER_TOKEN=<your-token>
```

Or pass it per-command:

```bash
ringmaster --token <your-token> status
```

Once the server is running and you have a valid token, you can register additional clients through the API:

```bash
curl -X POST http://localhost:8420/auth/register \
  -H "Authorization: Bearer $RINGMASTER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"client_id": "<new-client-id>"}'
```

The response contains the new client's token:

```json
{"client_id": "<new-client-id>", "token": "a1b2c3d4..."}
```

Distribute that token to the client securely. It won't be shown again.

## Start the server

From the directory containing `ringmaster.yaml`:

```bash
python3 -m ringmaster.server.run
```

Or point it at a config file elsewhere:

```bash
python3 -m ringmaster.server.run -c <path-to-your-config>/ringmaster.yaml
```

Verify it's running (make sure you've set `RINGMASTER_TOKEN` first — see [Bootstrap authentication](#bootstrap-authentication)):

```bash
ringmaster status
```

You should see:

```
State:        idle
Queue depth:  0
Current task: —
User present: False
Queue paused: False
```

### Run as a systemd service

The included `ringmaster.service` unit uses `/usr/bin/python3`, the system Python. If you installed Ringmaster in a virtual environment (which you should have), the system Python doesn't know about it. You need to update the service unit to point at your virtual environment.

```bash
# Copy your config and tokens to the standard location
mkdir -p ~/.config/ringmaster
cp ringmaster.yaml ~/.config/ringmaster/
cp tokens.json ~/.config/ringmaster/

# Install the service unit
mkdir -p ~/.config/systemd/user
cp ringmaster.service ~/.config/systemd/user/
```

Edit `~/.config/systemd/user/ringmaster.service` to point `ExecStart` at your venv's Python:

```ini
[Unit]
Description=Ringmaster AI Task Orchestrator
After=network-online.target ollama.service
Wants=network-online.target

[Service]
Type=simple
ExecStart=<path-to-your-clone>/.venv/bin/python3 -m ringmaster.server.run -c %h/.config/ringmaster/ringmaster.yaml
Restart=on-failure
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
```

Replace `<path-to-your-clone>` with the absolute path to your Ringmaster repo (for example, `/home/anny/Projects/Repos/Ringmaster`).

!!! warning
    If you have a Ringmaster server already running manually (for example, from an earlier `python3 -m ringmaster.server.run`), stop it first by pressing Ctrl+C in that terminal. Otherwise, the service fails because port 8420 is already in use.

Then enable and start it:

```bash
systemctl --user daemon-reload
systemctl --user enable --now ringmaster
```

Verify that the service is running:

```bash
systemctl --user status ringmaster
journalctl --user -u ringmaster -f
```

## Submit your first task

```bash
ringmaster submit --model llama3:8b --prompt "Hello from the network"
```

You should see:

```
Task submitted.
  ID:     <task-id>
  Status: queued
```

Check the queue:

```bash
ringmaster queue
```

```
ID                                      TYPE          MODEL                 PRI  STATUS
---------------------------------------------------------------------------------------
a1b2c3d4-e5f6-7890-abcd-ef1234567890    generate      llama3:8b             3    completed
```

The task should move from `queued` to `completed` within a few seconds (longer on CPU-only machines). If it shows `failed`, see [Troubleshooting](#troubleshooting).

## Remote access

The CLI defaults to `http://localhost:8420`. To talk to a Ringmaster server on another machine, make sure port 8420 is reachable on the workstation (no firewall blocking it). Then:

```bash
ringmaster --host http://<workstation-ip>:8420 --token $RINGMASTER_TOKEN status
```

Or set it once:

```bash
export RINGMASTER_HOST=http://<workstation-ip>:8420
export RINGMASTER_TOKEN=<your-token>
ringmaster status
```

!!! note
    The CLI reads `RINGMASTER_TOKEN` from the environment, but `RINGMASTER_HOST` isn't wired up as an `envvar` in Click yet. The `--host` flag works. The environment variable is aspirational until someone adds `envvar="RINGMASTER_HOST"` to the Click option.

## Troubleshooting

**"No GPUs detected" during `ringmaster init`**

Ringmaster looks for `rocm-smi` (AMD) and `nvidia-smi` (NVIDIA). If you have a GPU but the tool isn't in your PATH, install the appropriate driver package. For Intel integrated graphics, skip `init` and write `ringmaster.yaml` by hand. See [Manual configuration](#manual-configuration-no-discrete-gpu-or-custom-setup).

---

**"Missing or malformed Authorization header"**

You're hitting the server without a token. Set `RINGMASTER_TOKEN` or pass `--token`.

---

**Task immediately goes to `failed`**

Check that Ollama is running (`curl http://localhost:11434`) and that you've pulled the model your task requests (`ollama list`).

---

**"externally-managed-environment" when running `pip install`**

Your system Python is managed by your OS package manager. Use a virtual environment. See [Virtual environment (recommended)](#virtual-environment-recommended).

---

**Server can't find `ringmaster.yaml`**

The server looks in the current directory by default. Either change to the directory that contains it, or pass `-c <path-to-your-config>/ringmaster.yaml`.

## Uninstall

### venv install

Delete the cloned repo (which includes the venv):

```bash
rm -rf <path-to-your-clone>
```

If you set up the systemd service, stop it first, and then remove the config directory. The `~/.config/ringmaster/` directory contains the database (`ringmaster.db`) with your task history. Deleting it removes that history as well.

```bash
systemctl --user disable --now ringmaster
rm ~/.config/systemd/user/ringmaster.service
systemctl --user daemon-reload
rm -rf ~/.config/ringmaster
```

### pipx install

```bash
pipx uninstall ringmaster
```

This command removes the CLI. Any config files or token files you created locally aren't affected. Delete those manually if you don't need them.
