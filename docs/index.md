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
