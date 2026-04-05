# Quick start

Get Ringmaster running and submit your first task. This takes about five minutes.

## 1. Install

```bash
git clone https://github.com/JoshWrites/Ringmaster.git
cd Ringmaster
python3 -m venv .venv
source .venv/bin/activate
pip install .
```

## 2. Detect your GPU and generate config

```bash
ringmaster init
```

This creates `ringmaster.yaml` with your GPU hardware fingerprint. If you have Ollama running on the default port, no further configuration is needed.

## 3. Start the server

```bash
python -m ringmaster.server.run -c ringmaster.yaml
```

You should see:

```
INFO:     Uvicorn running on http://0.0.0.0:8420
```

Leave this terminal open.

## 4. Register a client

In a new terminal (with the venv activated):

```bash
curl -s -X POST http://localhost:8420/auth/register \
  -H "Content-Type: application/json" \
  -d '{"client_id": "my-laptop"}' | python3 -m json.tool
```

Copy the `token` value from the response and export it:

```bash
export RINGMASTER_TOKEN=<paste-token-here>
```

## 5. Submit a task

```bash
ringmaster submit --model llama3:8b --prompt "Explain what a GPU orchestrator does in one sentence."
```

## 6. Check the result

```bash
ringmaster status
```

You should see the task in the queue (or already completed, if Ollama is fast). To see the full task list:

```bash
ringmaster queue
```

## What's next

- [Installation guide](guide/installation.md) — systemd service setup, remote access, pipx install
- [Configuration](guide/configuration.md) — customize queue depth, idle detection, notifications, power management
- [Tasks guide](guide/tasks.md) — priority, deadlines, approval workflow, queue control
