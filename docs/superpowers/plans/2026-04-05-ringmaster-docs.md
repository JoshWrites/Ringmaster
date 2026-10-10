# Ringmaster Documentation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a 10-page MkDocs Material documentation site for Ringmaster and deploy it to GitHub Pages.

**Architecture:** MkDocs Material with explicit nav, GitHub Actions for auto-deploy. Docs source lives in `docs/` at repo root, replacing `docs/Anny/`. Existing installation guide is migrated; other pages are written fresh. Portfolio card added to LevineLabs website.

**Tech Stack:** MkDocs, mkdocs-material, GitHub Actions, GitHub Pages

---

### Task 1: MkDocs scaffolding

**Files:**
- Create: `mkdocs.yml`
- Create: `.github/workflows/docs.yml`
- Modify: `.gitignore`

- [ ] **Step 1: Install MkDocs Material**

```bash
pip install mkdocs-material
```

Verify: `mkdocs --version` should print version info.

- [ ] **Step 2: Create mkdocs.yml**

Create `mkdocs.yml` in the repo root:

```yaml
site_name: Ringmaster
site_description: GPU workstation AI task orchestrator for home networks
site_url: https://joshwrites.github.io/Ringmaster/
repo_url: https://github.com/JoshWrites/Ringmaster
repo_name: JoshWrites/Ringmaster

theme:
  name: material
  palette:
    - scheme: default
      primary: deep purple
      accent: amber
      toggle:
        icon: material/brightness-7
        name: Switch to dark mode
    - scheme: slate
      primary: deep purple
      accent: amber
      toggle:
        icon: material/brightness-4
        name: Switch to light mode
  features:
    - navigation.tabs
    - navigation.sections
    - navigation.top
    - search.suggest
    - content.code.copy

markdown_extensions:
  - admonition
  - pymdownx.details
  - pymdownx.superfences
  - pymdownx.highlight:
      anchor_linenums: true
  - pymdownx.inlinehilite
  - pymdownx.tabbed:
      alternate_style: true
  - tables
  - attr_list

nav:
  - Home: index.md
  - Quick Start: quickstart.md
  - User Guide:
    - Installation: guide/installation.md
    - Configuration: guide/configuration.md
    - Tasks: guide/tasks.md
    - Sessions: guide/sessions.md
  - Architecture: architecture/overview.md
  - Reference:
    - REST API: reference/api.md
    - CLI: reference/cli.md
    - Configuration: reference/config.md
```

- [ ] **Step 3: Add site/ to .gitignore**

Append to `.gitignore`:

```
# MkDocs build output
site/
```

- [ ] **Step 4: Create GitHub Actions workflow**

Create `.github/workflows/docs.yml`:

```yaml
name: Deploy docs

on:
  push:
    branches: [main]
    paths:
      - 'docs/**'
      - 'mkdocs.yml'

permissions:
  contents: write

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: pip install mkdocs-material
      - run: mkdocs gh-deploy --force
```

- [ ] **Step 5: Create placeholder docs structure**

```bash
mkdir -p docs/guide docs/architecture docs/reference
touch docs/index.md docs/quickstart.md
touch docs/guide/installation.md docs/guide/configuration.md docs/guide/tasks.md docs/guide/sessions.md
touch docs/architecture/overview.md
touch docs/reference/api.md docs/reference/cli.md docs/reference/config.md
```

- [ ] **Step 6: Verify local build**

```bash
mkdocs serve
```

Open `http://127.0.0.1:8000` in a browser. Should see empty Material theme site with nav structure. Stop the server with Ctrl+C.

- [ ] **Step 7: Commit**

```bash
git add mkdocs.yml .gitignore .github/workflows/docs.yml docs/
git commit -m "scaffold: MkDocs Material site with GitHub Actions deploy"
```

---

### Task 2: Landing page (index.md)

**Files:**
- Write: `docs/index.md`
- Reference: `docs/Anny/README.md` (source material)

- [ ] **Step 1: Write docs/index.md**

```markdown
# Ringmaster

Ringmaster turns your GPU workstation into a shared AI compute node for your home network. It queues inference tasks from any device, prioritizes them, and dispatches them to [Ollama](https://ollama.com) — while respecting that someone might be using the workstation to game, browse, or work.

## The problem

You have one powerful GPU and many devices that need AI inference: code companions, security scanners, document indexers, financial tools. Without Ringmaster, each tool talks directly to Ollama and competes for the GPU. With Ringmaster, every request goes through a single queue that handles priority, scheduling, and conflict with the person at the keyboard.

## What Ringmaster does

- **Priority queue** — Tasks ordered by priority (1–5), deadline, and submission time. The most urgent work runs first.
- **User-aware scheduling** — Detects whether you're at the keyboard. Can ask for approval, auto-approve when idle, or defer work until later.
- **Sleep protection** — A running task prevents your workstation from sleeping or shutting down. When it finishes, normal power management resumes.
- **GPU inventory** — Detects your GPUs at startup and matches them by hardware fingerprint (serial number, model, VRAM), not PCI bus index — so they stay consistent across reboots.
- **Interactive sessions** — Reserve GPU access for tools that need low-latency back-and-forth (coding assistants, chat interfaces) without the queue stealing the card between keystrokes.
- **Notifications** — Desktop popups or Home Assistant push notifications let you approve, defer, or cancel tasks without switching windows.
- **Webhooks** — Clients receive a callback when their task completes, with automatic retry on failure.
- **REST API + CLI** — Submit and manage tasks from any language or shell script. The CLI wraps the same API.

## What you need

- Python 3.11 or later
- [Ollama](https://ollama.com) installed and running on the workstation
- Linux with systemd (for sleep inhibition and service management)
- `rocm-smi` (AMD) or `nvidia-smi` (NVIDIA) for GPU detection

## Current status

Ringmaster is **v0.1.0** (Phase 1). Server, CLI, task queue, GPU detection, notifications, sessions, and authentication are implemented and tested. Multi-GPU task routing and a client proxy daemon are planned for Phase 2.

## Next steps

New here? Start with the [Quick Start](quickstart.md) — you'll have Ringmaster running and a task submitted in under five minutes.
```

- [ ] **Step 2: Verify locally**

```bash
mkdocs serve
```

Check that the landing page renders with all sections. Stop server.

- [ ] **Step 3: Commit**

```bash
git add docs/index.md
git commit -m "docs: landing page"
```

---

### Task 3: Quick start (quickstart.md)

**Files:**
- Write: `docs/quickstart.md`

- [ ] **Step 1: Write docs/quickstart.md**

```markdown
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
```

- [ ] **Step 2: Verify locally**

```bash
mkdocs serve
```

Check quickstart renders, all code blocks are highlighted, links resolve.

- [ ] **Step 3: Commit**

```bash
git add docs/quickstart.md
git commit -m "docs: quick start guide"
```

---

### Task 4: Installation guide (guide/installation.md)

**Files:**
- Write: `docs/guide/installation.md`
- Reference: `docs/Anny/installation.md` (source — migrate and clean up)

- [ ] **Step 1: Migrate and clean up the existing installation guide**

Copy `docs/Anny/installation.md` to `docs/guide/installation.md`. Then edit it:

1. Add MkDocs admonitions where appropriate (convert plain text warnings to `!!! warning` blocks, tips to `!!! tip`)
2. Ensure all code blocks have language specifiers (`bash`, `yaml`, `json`)
3. Verify all paths and commands are accurate against current codebase
4. Add a brief intro sentence at the top: "Full installation walkthrough — virtual environments, GPU detection, systemd service setup, and remote access."
5. Keep the existing content structure intact — it's well-written

Do NOT rewrite the guide. The existing content is good. This is a formatting migration, not a rewrite.

- [ ] **Step 2: Verify locally**

```bash
mkdocs serve
```

Check installation page renders, admonitions display correctly, code blocks are highlighted.

- [ ] **Step 3: Commit**

```bash
git add docs/guide/installation.md
git commit -m "docs: migrate installation guide to MkDocs"
```

---

### Task 5: Configuration guide (guide/configuration.md)

**Files:**
- Write: `docs/guide/configuration.md`
- Reference: `ringmaster.example.yaml`, `ringmaster/config.py`

- [ ] **Step 1: Write docs/guide/configuration.md**

Structure the page as a walkthrough of `ringmaster.yaml`, section by section. For each section:

1. Show the YAML with the default values
2. Explain what it controls in plain language
3. Call out the most common thing you'd change

Sections to cover (in this order, matching `ringmaster.example.yaml`):
- `server` (host, port)
- `gpus` (label, role, prefer_for, fingerprint)
- `ollama` (host)
- `notifications` (backend, fallback_backend, config)
- `power` (wake_method, sleep_command, display_off_command, lock_command, gpu_compute_profile_command)
- `idle` (detection_method, idle_threshold_seconds, auto_approve_when_idle, auto_approve_timeout_seconds)
- `queue` (max_queue_depth, default_priority, session_idle_timeout_seconds)
- `auth` (token_file)

Open with: "Ringmaster is configured through a YAML file, typically `ringmaster.yaml` in your project root. Generate a starter config with `ringmaster init`, then edit it to match your setup."

End with a tip: "For a full list of every field, default value, and validation rule, see the [Configuration Reference](../reference/config.md)."

Use `!!! tip` admonitions for common scenarios (e.g., "If Ringmaster and Ollama run on the same machine, you don't need to change the `ollama` section").

- [ ] **Step 2: Verify locally**

```bash
mkdocs serve
```

- [ ] **Step 3: Commit**

```bash
git add docs/guide/configuration.md
git commit -m "docs: configuration guide"
```

---

### Task 6: Tasks guide (guide/tasks.md)

**Files:**
- Write: `docs/guide/tasks.md`
- Reference: `ringmaster/server/routes/tasks.py`, `ringmaster/server/routes/queue.py`, `ringmaster/cli/main.py`, `ringmaster/models.py`

- [ ] **Step 1: Write docs/guide/tasks.md**

This page merges three related topics: submitting tasks, queue control, and the approval workflow. Structure:

**Submitting tasks** — CLI and API examples:
```bash
# Basic submission
ringmaster submit --model llama3:8b --prompt "Hello"

# With priority and callback
ringmaster submit --model llama3:8b --prompt "Urgent work" \
  --priority 1 --callback-url http://my-app:9000/done
```

```bash
# API equivalent
curl -X POST http://localhost:8420/tasks \
  -H "Authorization: Bearer $RINGMASTER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"model": "llama3:8b", "prompt": "Hello", "priority": 3}'
```

**Task lifecycle** — explain the states: queued → running → completed/failed/cancelled. Also: deferred (when `unattended_policy` is "defer" and user is present).

**Priority and deadlines** — priority 1 (highest) to 5 (lowest). Tasks with deadlines are dequeued ahead of those without.

**Queue control** — three distinct operations:

- `ringmaster pause` — accept new tasks but don't dispatch them
- `ringmaster resume` — resume normal dispatch
- `ringmaster drain` — finish the current task then stop (for planned shutdowns)

**Task approval workflow** — when the user is at the keyboard:

- `unattended_policy: run` — run immediately
- `unattended_policy: defer` — hold for manual approval
- `unattended_policy: notify` — notify and auto-approve after timeout

Deferred tasks are approved via `POST /tasks/{id}/approve` or deferred further via `POST /tasks/{id}/defer`.

**Webhooks** — briefly explain `callback_url`: Ringmaster POSTs to it when the task completes, with 3 retries and exponential backoff. Link to API reference for payload format.

- [ ] **Step 2: Verify locally**

```bash
mkdocs serve
```

- [ ] **Step 3: Commit**

```bash
git add docs/guide/tasks.md
git commit -m "docs: tasks guide (submission, queue control, approval, webhooks)"
```

---

### Task 7: Sessions guide (guide/sessions.md)

**Files:**
- Write: `docs/guide/sessions.md`
- Reference: `ringmaster/server/routes/sessions.py`, `ringmaster/models.py`

- [ ] **Step 1: Write docs/guide/sessions.md**

Open with: "Sessions reserve GPU access for interactive tools — coding assistants, chat interfaces, anything that needs low-latency back-and-forth without the queue stealing the card between requests."

Cover:

**When to use sessions vs. tasks:**

- One-off inference → submit a task
- Interactive tool that sends many requests over minutes/hours → open a session

**Session lifecycle:**

1. `POST /sessions` with `client_id` and `model` → opens session, returns session ID
2. `POST /sessions/{id}/keepalive` → resets idle timer (client should call this periodically)
3. `DELETE /sessions/{id}` → closes session, releases GPU slot

**Idle timeout:** Sessions expire after `session_idle_timeout_seconds` (default: 600 = 10 minutes) of inactivity. The client is responsible for sending keepalives.

Show curl examples for each operation. Explain that sessions are an API-only feature — the CLI doesn't have session commands (it's designed for one-off tasks).

- [ ] **Step 2: Verify locally**

```bash
mkdocs serve
```

- [ ] **Step 3: Commit**

```bash
git add docs/guide/sessions.md
git commit -m "docs: sessions guide"
```

---

### Task 8: Architecture overview (architecture/overview.md)

**Files:**
- Write: `docs/architecture/overview.md`
- Reference: `ringmaster/scheduler.py`, `ringmaster/worker.py`, `ringmaster/gpu/detect.py`, `ringmaster/server/auth.py`, code docstrings throughout

- [ ] **Step 1: Write docs/architecture/overview.md**

Open with: "How Ringmaster works under the hood — the components, how they connect, and why they're designed this way."

Sections:

**System overview** — describe the component relationships in prose (a Mermaid diagram is a nice-to-have but not required; if adding one, use a simple flowchart: Client → API → Scheduler → Worker → Ollama, with Presence Detector and Sleep Inhibitor as side components).

**Scheduler** — the queue state machine:
- 6 task states: queued, running, completed, failed, deferred, cancelled
- Priority ordering: by priority (1–5), then deadline (nearest first), then submission time (FIFO)
- Pause/resume/drain: explain each and when you'd use it
- Why in-memory state works: the scheduler reads from SQLite on startup and keeps state in memory during operation

**Worker** — the 13-step task execution lifecycle (read from `worker.py` docstring). Summarize as a numbered list: acquire inhibitor → check presence → notify if needed → wait for approval → load model → run inference → store result → fire webhook → release inhibitor → update DB.

**GPU detection** — why fingerprinting by vendor/model/VRAM/serial instead of PCI bus index. Bus indices can change across reboots; fingerprints don't.

**Authentication** — bearer tokens stored in a JSON file. Simple, appropriate for a home network behind a firewall. `POST /auth/register` issues a token, `POST /auth/revoke` removes one. No expiration (tokens are long-lived).

For each section, include a "Design decision" callout (`!!! info "Design decision"`) explaining *why* this approach was chosen over alternatives.

- [ ] **Step 2: Verify locally**

```bash
mkdocs serve
```

- [ ] **Step 3: Commit**

```bash
git add docs/architecture/overview.md
git commit -m "docs: architecture overview"
```

---

### Task 9: API reference (reference/api.md)

**Files:**
- Write: `docs/reference/api.md`
- Reference: `ringmaster/server/routes/*.py`, `ringmaster/models.py`

- [ ] **Step 1: Write docs/reference/api.md**

Open with: "All Ringmaster REST API endpoints. Every endpoint except `/health` requires a `Authorization: Bearer <token>` header."

For each endpoint, use this format:

```markdown
### `POST /tasks`

Submit a new inference task.

**Request body:**

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `model` | string | yes | — | Ollama model name |
| `prompt` | string | yes | — | Input text |
| `priority` | integer | no | 3 | 1 (highest) to 5 (lowest) |
| `deadline` | string | no | null | ISO 8601 UTC timestamp |
| `client_id` | string | no | null | Client identifier |
| `callback_url` | string | no | null | Webhook URL for completion |
| `unattended_policy` | string | no | "run" | run, defer, or notify |
| `metadata` | object | no | {} | Arbitrary key-value pairs |

**Response:** `201 Created`

​```json
{
  "task_id": "abc123",
  "status": "queued",
  "model": "llama3:8b",
  "priority": 3,
  "created_at": "2026-04-05T12:00:00Z"
}
​```

**Example:**

​```bash
curl -X POST http://localhost:8420/tasks \
  -H "Authorization: Bearer $RINGMASTER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"model": "llama3:8b", "prompt": "Hello"}'
​```
```

Document all 19 endpoints in this format, grouped by section:

**Authentication** (2): POST /auth/register, POST /auth/revoke
**Tasks** (6): POST /tasks, GET /tasks, GET /tasks/{id}, POST /tasks/current/cancel, POST /tasks/{id}/approve, POST /tasks/{id}/defer
**Queue** (3): POST /queue/pause, POST /queue/resume, POST /queue/drain
**Sessions** (4): POST /sessions, GET /sessions/{id}, POST /sessions/{id}/keepalive, DELETE /sessions/{id}
**Status** (4): GET /health, GET /status, GET /gpus, GET /models

Read the Pydantic models in `ringmaster/models.py` and the route handlers in `ringmaster/server/routes/*.py` to get exact field names, types, defaults, and response shapes.

- [ ] **Step 2: Verify locally**

```bash
mkdocs serve
```

Check tables render, code blocks are highlighted, all 19 endpoints are present.

- [ ] **Step 3: Commit**

```bash
git add docs/reference/api.md
git commit -m "docs: REST API reference (19 endpoints)"
```

---

### Task 10: CLI reference (reference/cli.md)

**Files:**
- Write: `docs/reference/cli.md`
- Reference: `ringmaster/cli/main.py`

- [ ] **Step 1: Write docs/reference/cli.md**

Open with: "The `ringmaster` CLI wraps the REST API for shell use. All commands require a running Ringmaster server."

**Global options** (apply to all commands):

| Flag | Env var | Default | Description |
|------|---------|---------|-------------|
| `--host` | — | `http://localhost:8420` | Server URL |
| `--token` | `RINGMASTER_TOKEN` | — | Bearer token |

Then document each of the 9 commands:

1. `ringmaster status` — show current system state
2. `ringmaster queue [--status-filter STATUS]` — list tasks
3. `ringmaster submit --model MODEL [--prompt TEXT] [--priority N] [--client-id ID] [--callback-url URL]` — submit task
4. `ringmaster pause` — pause queue dispatch
5. `ringmaster resume` — resume queue dispatch
6. `ringmaster drain` — finish current task then stop
7. `ringmaster cancel-current` — interrupt running task
8. `ringmaster gpu` — list configured GPUs
9. `ringmaster init [--config PATH]` — detect GPUs and generate config

For each command: description, flags table, example usage with expected output.

Read `ringmaster/cli/main.py` for exact flag names, defaults, and help text.

- [ ] **Step 2: Verify locally**

```bash
mkdocs serve
```

- [ ] **Step 3: Commit**

```bash
git add docs/reference/cli.md
git commit -m "docs: CLI reference (9 commands)"
```

---

### Task 11: Configuration reference (reference/config.md)

**Files:**
- Write: `docs/reference/config.md`
- Reference: `ringmaster/config.py`, `ringmaster.example.yaml`

- [ ] **Step 1: Write docs/reference/config.md**

Open with: "Every configuration field in `ringmaster.yaml`, with type, default value, and description. Generate a starter config with `ringmaster init`."

For each config section, use a table:

```markdown
## `server`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `host` | string | `"0.0.0.0"` | Bind address |
| `port` | integer | `8420` | TCP port |
```

Sections: `server`, `gpus` (with nested `fingerprint`), `ollama`, `notifications`, `power`, `idle`, `queue`, `auth`.

Read every field from the Pydantic models in `ringmaster/config.py`. Include the validation rules where they exist (e.g., port range, enum values for `role`, `detection_method`).

- [ ] **Step 2: Verify locally**

```bash
mkdocs serve
```

- [ ] **Step 3: Commit**

```bash
git add docs/reference/config.md
git commit -m "docs: configuration reference"
```

---

### Task 12: Clean up old docs and deploy

**Files:**
- Remove: `docs/Anny/` (content migrated)
- Modify: `README.md` (update docs links)

- [ ] **Step 1: Remove old docs/Anny/ directory**

```bash
git rm -r docs/Anny/
```

The content has been migrated to the new `docs/` structure. The `docs/plans/`, `docs/specs/`, and `docs/superpowers/` directories stay — they're internal dev docs, not part of the user-facing site.

- [ ] **Step 2: Update root README.md**

Update the Documentation table in the root `README.md` to point to the new docs site:

Replace the existing docs table with:

```markdown
## Documentation

Full documentation at [joshwrites.github.io/Ringmaster](https://joshwrites.github.io/Ringmaster/).

| Guide | Description |
|-------|-------------|
| [Quick Start](https://joshwrites.github.io/Ringmaster/quickstart/) | Zero to running in five minutes |
| [Installation](https://joshwrites.github.io/Ringmaster/guide/installation/) | Full setup with systemd and remote access |
| [Configuration](https://joshwrites.github.io/Ringmaster/guide/configuration/) | Complete config reference |
| [API Reference](https://joshwrites.github.io/Ringmaster/reference/api/) | All 19 REST endpoints |
| [CLI Reference](https://joshwrites.github.io/Ringmaster/reference/cli/) | All 9 CLI commands |
```

- [ ] **Step 3: Build and deploy**

```bash
mkdocs gh-deploy --force
```

This pushes the built site to the `gh-pages` branch. Verify at `https://joshwrites.github.io/Ringmaster/`.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "docs: remove old docs/Anny, update README links, deploy to GitHub Pages"
git push
```

---

### Task 13: Portfolio integration (LevineLabs website)

**Files:**
- Modify: `/home/anny/Projects/Repos/levinelabs-website/src/anny/techwriting/index.html`
- Modify: `/home/anny/Projects/Repos/levinelabs-website/src/anny/about/cv/longform/index.html`

- [ ] **Step 1: Add tech writing portfolio card**

In `src/anny/techwriting/index.html`, add a new card after the huddle notes card:

```html
<div class="card">
    <span class="sample-type">Docs-as-code &middot; Developer documentation &middot; MkDocs</span>
    <div class="card-title">Ringmaster Documentation</div>
    <div class="card-meta">2026 &middot; Open-source project</div>
    <div class="card-excerpt">Developer documentation for a GPU workstation orchestrator daemon. Full docs site built with MkDocs Material &mdash; quick start, user guides, architecture deep dive, and API/CLI/config reference. Written for sysadmins and homelabbers running local AI inference.</div>
    <a href="https://joshwrites.github.io/Ringmaster/" target="_blank" rel="noopener noreferrer" class="card-link">View docs site &nearr;</a>
    <a href="https://github.com/JoshWrites/Ringmaster/tree/main/docs" target="_blank" rel="noopener noreferrer" class="card-link">View source &nearr;</a>
</div>
```

- [ ] **Step 2: Add to longform CV**

In `src/anny/about/cv/longform/index.html`, add after the CALMe entry in the Projects section:

```html
<p><strong><a href="https://joshwrites.github.io/Ringmaster/" target="_blank" rel="noopener noreferrer">Ringmaster Documentation</a></strong> (2026) &mdash; Docs-as-code documentation site for a GPU workstation orchestrator. 10-page MkDocs Material site covering quick start, user guides, architecture, and API/CLI reference. Written for an audience of sysadmins and homelabbers. Demonstrates information architecture, plain-language technical writing, and docs-as-code toolchain (MkDocs, GitHub Actions, GitHub Pages).</p>
```

- [ ] **Step 3: Commit and push LevineLabs changes**

```bash
cd /home/anny/Projects/Repos/levinelabs-website
git add src/anny/techwriting/index.html src/anny/about/cv/longform/index.html
git commit -m "Add Ringmaster docs to tech writing portfolio and CV"
git push
```
