"""Ringmaster CLI — command-line interface for the GPU workstation orchestrator.

All commands are thin wrappers over the Ringmaster REST API.  They call the
server via synchronous httpx requests so that Click's synchronous command model
is not fighting with an async event loop.

Usage example::

    export RINGMASTER_TOKEN=<your-token>
    ringmaster status
    ringmaster queue
    ringmaster submit --model llama3:8b --prompt "Summarise this doc"
    ringmaster pause
    ringmaster resume

Every command accepts ``--host`` and ``--token`` as global options so the CLI
is usable against any Ringmaster instance, not just localhost.
"""

from __future__ import annotations

import sys
from typing import Any

import click
import httpx
import yaml


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _headers(token: str) -> dict[str, str]:
    """Build the HTTP Authorization header dict from a bearer token.

    Having this as a helper keeps individual command implementations short and
    ensures the header name is spelled consistently everywhere.
    """
    return {"Authorization": f"Bearer {token}"}


def _die(message: str) -> None:
    """Print an error message to stderr and exit with a non-zero status.

    Using stderr for errors keeps stdout clean for piping/scripting — a caller
    that pipes ``ringmaster queue | jq`` should only get JSON on stdout.
    """
    click.echo(f"Error: {message}", err=True)
    sys.exit(1)


def _require_ok(response: httpx.Response) -> dict[str, Any]:
    """Assert an HTTP response is 2xx and return the parsed JSON body.

    Raises a user-friendly error (via _die) for non-2xx responses so individual
    commands don't each need to repeat the same status-check boilerplate.
    """
    if not response.is_success:
        _die(f"Server returned {response.status_code}: {response.text}")
    return response.json()  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Top-level group
# ---------------------------------------------------------------------------


@click.group()
@click.option(
    "--host",
    default="http://localhost:8420",
    show_default=True,
    help="Base URL of the Ringmaster server.",
)
@click.option(
    "--token",
    envvar="RINGMASTER_TOKEN",
    required=True,
    help="Bearer token for API authentication (env: RINGMASTER_TOKEN).",
)
@click.pass_context
def cli(ctx: click.Context, host: str, token: str) -> None:
    """Ringmaster — GPU workstation AI task orchestrator."""
    # Store shared state so subcommands can retrieve it without re-declaring
    # the same options.  ctx.ensure_object creates the dict on first use.
    ctx.ensure_object(dict)
    ctx.obj["host"] = host.rstrip("/")
    ctx.obj["token"] = token


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


@cli.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """Show current system state: workload, queue depth, and user presence."""
    host = ctx.obj["host"]
    token = ctx.obj["token"]

    resp = httpx.get(f"{host}/status", headers=_headers(token))
    body = _require_ok(resp)

    click.echo(f"State:        {body['state']}")
    click.echo(f"Queue depth:  {body['queue_depth']}")
    click.echo(f"Current task: {body.get('current_task') or '—'}")
    click.echo(f"User present: {body['user_present']}")
    click.echo(f"Queue paused: {body['queue_paused']}")


# ---------------------------------------------------------------------------
# queue
# ---------------------------------------------------------------------------


@cli.command()
@click.option(
    "--status-filter",
    default=None,
    metavar="STATUS",
    help="Only show tasks with this status (e.g. queued, running, completed).",
)
@click.pass_context
def queue(ctx: click.Context, status_filter: str | None) -> None:
    """List tasks in the queue."""
    host = ctx.obj["host"]
    token = ctx.obj["token"]

    params: dict[str, str] = {}
    if status_filter:
        params["status"] = status_filter

    resp = httpx.get(f"{host}/tasks", headers=_headers(token), params=params)
    tasks = _require_ok(resp)

    if not tasks:
        click.echo("Queue is empty.")
        return

    # Fixed-width columns keep the output readable at a glance.
    header = f"{'ID':<38}  {'TYPE':<12}  {'MODEL':<20}  {'PRI'}  {'STATUS'}"
    click.echo(header)
    click.echo("-" * len(header))
    for task in tasks:
        click.echo(
            f"{task['id']:<38}  "
            f"{task['task_type']:<12}  "
            f"{task['model']:<20}  "
            f"{task['priority']:<3}  "
            f"{task['status']}"
        )


# ---------------------------------------------------------------------------
# submit
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--model", required=True, help="Ollama model tag, e.g. 'llama3:8b'.")
@click.option("--prompt", default=None, help="Input text for the task.")
@click.option("--priority", default=None, type=int, help="Queue priority 1 (high) to 5 (low).")
@click.option("--client-id", default="cli", show_default=True, help="Identifier for this client.")
@click.option("--callback-url", default=None, help="URL to notify on task completion.")
@click.pass_context
def submit(
    ctx: click.Context,
    model: str,
    prompt: str | None,
    priority: int | None,
    client_id: str,
    callback_url: str | None,
) -> None:
    """Submit a new task to the queue."""
    host = ctx.obj["host"]
    token = ctx.obj["token"]

    payload: dict[str, Any] = {
        "task_type": "generate",
        "model": model,
        "client_id": client_id,
    }
    if prompt is not None:
        payload["prompt"] = prompt
    if priority is not None:
        payload["priority"] = priority
    if callback_url is not None:
        payload["callback_url"] = callback_url

    resp = httpx.post(f"{host}/tasks", headers=_headers(token), json=payload)
    body = _require_ok(resp)

    click.echo("Task submitted.")
    click.echo(f"  ID:     {body['id']}")
    click.echo(f"  Status: {body['status']}")


# ---------------------------------------------------------------------------
# pause
# ---------------------------------------------------------------------------


@cli.command()
@click.pass_context
def pause(ctx: click.Context) -> None:
    """Pause the queue — tasks are accepted but not dispatched."""
    host = ctx.obj["host"]
    token = ctx.obj["token"]

    resp = httpx.post(f"{host}/queue/pause", headers=_headers(token))
    _require_ok(resp)

    click.echo("Queue paused.")


# ---------------------------------------------------------------------------
# resume
# ---------------------------------------------------------------------------


@cli.command()
@click.pass_context
def resume(ctx: click.Context) -> None:
    """Resume the queue — dispatch resumes from where it was paused."""
    host = ctx.obj["host"]
    token = ctx.obj["token"]

    resp = httpx.post(f"{host}/queue/resume", headers=_headers(token))
    _require_ok(resp)

    click.echo("Queue resumed.")


# ---------------------------------------------------------------------------
# drain
# ---------------------------------------------------------------------------


@cli.command()
@click.pass_context
def drain(ctx: click.Context) -> None:
    """Drain the queue — finish the current task then stop dispatching.

    Use before a planned shutdown or maintenance window.  Combine with
    ``ringmaster status`` to poll until queue_depth reaches zero.
    """
    host = ctx.obj["host"]
    token = ctx.obj["token"]

    resp = httpx.post(f"{host}/queue/drain", headers=_headers(token))
    body = _require_ok(resp)

    # The server returns {"draining": true, "message": "..."} — surface the
    # message so operators know exactly what to expect.
    msg = body.get("message") or "Queue is draining. No new tasks will be dispatched."
    click.echo(msg)


# ---------------------------------------------------------------------------
# cancel-current
# ---------------------------------------------------------------------------


@cli.command("cancel-current")
@click.pass_context
def cancel_current(ctx: click.Context) -> None:
    """Cancel the task that is currently running."""
    host = ctx.obj["host"]
    token = ctx.obj["token"]

    resp = httpx.post(f"{host}/tasks/current/cancel", headers=_headers(token))

    # 404 means no task is running — that is a normal operational state, not an
    # error the operator needs to act on, so we give a friendly message instead
    # of letting _require_ok exit non-zero.
    if resp.status_code == 404:
        click.echo("No task is currently running.")
        return

    body = _require_ok(resp)
    click.echo(f"Task cancelled: {body.get('id', '(unknown)')}")


# ---------------------------------------------------------------------------
# gpu
# ---------------------------------------------------------------------------


@cli.command()
@click.pass_context
def gpu(ctx: click.Context) -> None:
    """List GPUs known to this Ringmaster instance."""
    host = ctx.obj["host"]
    token = ctx.obj["token"]

    resp = httpx.get(f"{host}/gpus", headers=_headers(token))
    gpus = _require_ok(resp)

    if not gpus:
        click.echo("No GPUs configured.")
        return

    for g in gpus:
        label = g.get("label", "(no label)")
        role = g.get("role", "unknown")
        vram = g.get("vram_mb", 0)
        click.echo(f"  {label}  [{role}]  {vram} MiB VRAM")


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


@cli.command()
def init() -> None:
    """Interactively generate a ringmaster.yaml configuration file.

    Detects GPUs on the local machine and prompts for a human-readable label
    and role for each one.  Writes the result to ringmaster.yaml in the current
    directory.

    This command intentionally does NOT accept --host/--token because it runs
    locally before any server is configured — it is the command you run to
    *create* the config that the server will use.
    """
    from ringmaster.gpu.detect import detect_gpus

    click.echo("Detecting GPUs…")
    detected = detect_gpus()

    if not detected:
        click.echo(
            "No GPUs detected.  Check that rocm-smi (AMD) or nvidia-smi (NVIDIA) "
            "is installed and accessible.",
            err=True,
        )
        sys.exit(1)

    click.echo(f"Found {len(detected)} GPU(s).\n")

    gpu_configs: list[dict[str, Any]] = []
    for i, g in enumerate(detected):
        click.echo(f"GPU {i}: {g.vendor} {g.model}  ({g.vram_mb} MiB VRAM)")

        label = click.prompt(
            "  Label (human-readable name for logs/API)",
            default=f"gpu{i}",
        )
        role = click.prompt(
            "  Role",
            default="compute",
            type=click.Choice(["compute", "display", "compute+display"], case_sensitive=False),
        )

        entry: dict[str, Any] = {
            "label": label,
            "role": role,
            "fingerprint": {
                "vendor": g.vendor,
                "model": g.model,
                "vram_mb": g.vram_mb,
            },
        }
        if g.serial:
            entry["fingerprint"]["serial"] = g.serial
        if g.device_id:
            entry["fingerprint"]["device_id"] = g.device_id

        gpu_configs.append(entry)
        click.echo()

    config: dict[str, Any] = {"gpus": gpu_configs}
    out_path = "ringmaster.yaml"

    with open(out_path, "w") as fh:
        yaml.dump(config, fh, default_flow_style=False, sort_keys=False)

    click.echo(f"Configuration written to {out_path}")
