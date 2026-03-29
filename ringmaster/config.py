"""Configuration models and loader for Ringmaster.

Configuration is stored in a YAML file (default: ringmaster.yaml) and loaded
into validated Pydantic models at startup.  Every field has a sensible default
so that a minimal config file only needs to override what differs from the
defaults.

Field-level comments explain *why* a default was chosen, not just what it is.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class ServerConfig(BaseModel):
    """HTTP API server binding configuration.

    The default port 8420 was chosen to avoid collisions with common developer
    services (8000, 8080, 8443) while still being memorable.
    """

    host: str = Field(
        default="0.0.0.0",
        description="Interface to bind the HTTP API server to.",
    )
    port: int = Field(
        default=8420,
        description="TCP port for the HTTP API server.",
    )


class GpuFingerprint(BaseModel):
    """Hardware-level identifier for a specific GPU.

    A fingerprint lets Ringmaster confirm it is talking to the expected GPU
    before dispatching tasks.  The serial and device_id fields are optional
    because not all GPU drivers expose them without elevated privileges.
    """

    vendor: str = Field(description="GPU vendor name, e.g. 'NVIDIA' or 'AMD'.")
    model: str = Field(description="GPU model name, e.g. 'RTX 4090'.")
    vram_mb: int = Field(description="Total VRAM in mebibytes.")
    serial: str | None = Field(
        default=None,
        description="Board serial number, if available from the driver.",
    )
    device_id: str | None = Field(
        default=None,
        description="PCI device ID string, e.g. '10de:2684'.",
    )


class GpuConfig(BaseModel):
    """Configuration for a single GPU on the workstation.

    A workstation may have multiple GPUs with different roles — e.g. one card
    dedicated to the display and one for compute-only inference.
    """

    label: str = Field(
        description="Human-readable name used in logs and API responses.",
    )
    role: str = Field(
        default="compute",
        description=(
            "Intended use for this GPU. "
            "One of 'compute', 'display', or 'compute+display'."
        ),
    )
    prefer_for: list[str] = Field(
        default_factory=list,
        description=(
            "Task types this GPU should be preferred for, "
            "e.g. ['embedding', 'image_generation']."
        ),
    )
    fingerprint: GpuFingerprint = Field(
        description="Hardware fingerprint used to identify this GPU at runtime.",
    )


class OllamaConfig(BaseModel):
    """Connection settings for the Ollama inference backend.

    Ollama must be running on the workstation before Ringmaster dispatches
    tasks to it.  The default host assumes Ollama is on the same machine as
    the Ringmaster agent; override this when the agent and Ollama run on
    different hosts.
    """

    host: str = Field(
        default="http://localhost:11434",
        description="Base URL of the Ollama HTTP API.",
    )


class NotificationsConfig(BaseModel):
    """Settings for user-facing notifications sent by Ringmaster.

    Notifications are used to request approval for tasks that arrive while the
    workstation is active.  The fallback backend fires when the primary backend
    fails (e.g. D-Bus is unavailable because the user is logged out).
    """

    backend: str = Field(
        default="desktop",
        description=(
            "Primary notification backend. "
            "One of 'desktop' (libnotify/D-Bus) or 'none'."
        ),
    )
    config: dict[str, Any] = Field(
        default_factory=dict,
        description="Backend-specific configuration key-value pairs.",
    )
    fallback_backend: str = Field(
        default="desktop",
        description="Notification backend to use when the primary backend fails.",
    )


class PowerConfig(BaseModel):
    """Commands and methods used to manage workstation power state.

    All command fields are optional; omitting them disables the corresponding
    power management feature.  Commands are executed as the user that runs the
    Ringmaster agent process.
    """

    wake_method: str = Field(
        default="none",
        description=(
            "How to wake the workstation from sleep. "
            "One of 'none', 'wol' (Wake-on-LAN), or 'command'."
        ),
    )
    sleep_command: str | None = Field(
        default=None,
        description="Shell command to send the workstation to sleep.",
    )
    display_off_command: str | None = Field(
        default=None,
        description="Shell command to turn off the display without sleeping.",
    )
    lock_command: str | None = Field(
        default=None,
        description="Shell command to lock the screen.",
    )
    gpu_compute_profile_command: str | None = Field(
        default=None,
        description=(
            "Shell command to switch the GPU to a power-efficient compute profile "
            "when idle, e.g. setting a lower TDP or clock limit."
        ),
    )


class IdleConfig(BaseModel):
    """Controls when Ringmaster considers the workstation idle.

    Idle detection is used to decide whether task approval notifications should
    be sent or whether tasks should be auto-approved (because the user is away).
    D-Bus is the preferred detection method on modern Linux desktops because it
    integrates with the session manager and handles multi-seat correctly.
    """

    detection_method: str = Field(
        default="dbus",
        description=(
            "How to detect user idle state. "
            "One of 'dbus' (org.freedesktop.ScreenSaver), 'xprintidle', or 'none'."
        ),
    )
    idle_threshold_seconds: int = Field(
        default=300,
        description=(
            "Seconds of inactivity after which the session is considered idle. "
            "Five minutes is a reasonable default that avoids false positives "
            "during short breaks."
        ),
    )
    auto_approve_when_idle: bool = Field(
        default=True,
        description=(
            "If True, tasks are automatically approved when the session is idle, "
            "without sending a notification.  Disable this if you always want "
            "explicit approval regardless of idle state."
        ),
    )
    auto_approve_timeout_seconds: int = Field(
        default=60,
        description=(
            "Seconds to wait for manual approval before auto-approving a task "
            "when the session is active.  Set to 0 to disable timed auto-approval."
        ),
    )


class QueueConfig(BaseModel):
    """Settings that control the task queue behaviour.

    The queue is an in-process priority queue backed by SQLite for durability.
    Tasks are dispatched in priority order (lower number = higher priority);
    within the same priority, tasks are dispatched in FIFO order.
    """

    max_queue_depth: int = Field(
        default=100,
        description=(
            "Maximum number of tasks that can wait in the queue at once. "
            "Submitting a task when the queue is full returns HTTP 429."
        ),
    )
    default_priority: int = Field(
        default=3,
        description=(
            "Priority assigned to tasks that do not specify one. "
            "Matches the mid-point of the 1–5 priority scale."
        ),
    )
    session_idle_timeout_seconds: int = Field(
        default=600,
        description=(
            "Seconds of inactivity after which a client session's reserved "
            "queue slot is released back to the pool."
        ),
    )


class AuthConfig(BaseModel):
    """Authentication settings for the Ringmaster HTTP API.

    Ringmaster uses static bearer tokens stored in a JSON file.  This is
    intentionally simple — the API is only meant to be accessible on a private
    home network, so a full OAuth flow would add complexity without meaningful
    security benefit.
    """

    token_file: str = Field(
        default="tokens.json",
        description=(
            "Path to the JSON file containing API bearer tokens. "
            "Relative paths are resolved from the directory containing the "
            "main config file."
        ),
    )


class RingmasterConfig(BaseModel):
    """Top-level configuration model for Ringmaster.

    All sub-configs have defaults, so a minimal ringmaster.yaml only needs to
    override the fields that differ from the defaults.
    """

    server: ServerConfig = Field(default_factory=ServerConfig)
    gpus: list[GpuConfig] = Field(
        default_factory=list,
        description="List of GPU configurations for the workstation.",
    )
    ollama: OllamaConfig = Field(default_factory=OllamaConfig)
    notifications: NotificationsConfig = Field(default_factory=NotificationsConfig)
    power: PowerConfig = Field(default_factory=PowerConfig)
    idle: IdleConfig = Field(default_factory=IdleConfig)
    queue: QueueConfig = Field(default_factory=QueueConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)


def load_config(path: Path) -> RingmasterConfig:
    """Load and validate a Ringmaster config file from the given path.

    Reads a YAML file, then constructs and validates a RingmasterConfig from
    its contents.  Any keys present in the file override the model defaults;
    keys absent from the file keep their defaults.

    Args:
        path: Absolute or relative path to the YAML config file.

    Returns:
        A fully-validated RingmasterConfig instance.

    Raises:
        FileNotFoundError: If the file does not exist at the given path.
        pydantic.ValidationError: If the file contains invalid configuration.
        yaml.YAMLError: If the file is not valid YAML.
    """
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    return RingmasterConfig.model_validate(raw)
