"""GPU detection via vendor-specific CLI tools.

This module queries the host system for installed GPUs by calling external
tools (rocm-smi, nvidia-smi).  Each backend is tried in isolation so that a
missing or failing tool does not prevent detection from other backends.

All detection functions are intentionally synchronous — GPU enumeration is a
startup-time operation that runs once before the async event loop starts, so
the added complexity of async subprocess calls is not justified.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass


@dataclass
class DetectedGpu:
    """Raw GPU facts gathered from the system at runtime.

    This is the driver's view of the hardware — not the operator's config.
    Fields are kept close to what the tools actually report so that the
    fingerprinting layer can make informed match decisions.
    """

    vendor: str
    """Normalised vendor name: 'AMD', 'NVIDIA', or the raw string if unknown."""

    model: str
    """Full model name as reported by the driver."""

    vram_mb: int
    """Total VRAM in mebibytes (1 MiB = 1 048 576 bytes)."""

    serial: str
    """Board serial / unique ID.  Empty string when unavailable or not exposed."""

    device_id: str | None
    """PCI device ID string, e.g. '1002:744c'.  None if not determined."""

    pci_slot: str | None
    """PCI slot address, e.g. '0000:03:00.0'.  None if not determined."""


def _normalise_vendor(raw: str) -> str:
    """Collapse vendor string variants into a single canonical form.

    ROCm and various system utilities report AMD GPUs with many different
    strings ('Advanced Micro Devices, Inc. [AMD/ATI]', 'ATI Technologies',
    etc.).  We normalise to the short form so callers can do simple equality
    checks.
    """
    upper = raw.upper()
    if "AMD" in upper or "ATI" in upper:
        return "AMD"
    if "NVIDIA" in upper:
        return "NVIDIA"
    if "INTEL" in upper:
        return "Intel"
    return raw


def detect_gpus_rocm() -> list[DetectedGpu]:
    """Detect AMD GPUs by parsing output from rocm-smi.

    Calls::

        rocm-smi --showproductname --showmeminfo vram --showuniqueid --json

    Returns an empty list (rather than raising) if:
    - rocm-smi is not installed (FileNotFoundError)
    - rocm-smi exits with a non-zero status
    - The JSON output is empty or unparseable

    This defensive posture lets detect_gpus() fall through to other backends
    without propagating errors from a missing ROCm installation.
    """
    try:
        result = subprocess.run(
            ["rocm-smi", "--showproductname", "--showmeminfo", "vram", "--showuniqueid", "--json"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError:
        # rocm-smi is not installed — not an error on non-AMD machines.
        return []
    except subprocess.TimeoutExpired:
        return []

    if result.returncode != 0:
        return []

    try:
        data = json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError):
        return []

    if not data:
        return []

    gpus: list[DetectedGpu] = []
    for key, card in data.items():
        # rocm-smi includes a top-level 'system' key with driver metadata;
        # GPU entries are named 'card0', 'card1', etc.
        if not key.startswith("card"):
            continue

        if not isinstance(card, dict):
            continue

        raw_vendor = card.get("Card Vendor", card.get("Card vendor", ""))
        raw_model = card.get("Card Series") or card.get("Card series") or card.get("Card Model") or card.get("Card model") or ""
        # Some drivers prefix the model with the vendor name (e.g. "AMD Radeon
        # RX 5700 XT") while others don't ("Radeon RX 7900 XTX").  Strip the
        # prefix when present so the model field is consistent.
        vendor = _normalise_vendor(raw_vendor)
        model = raw_model.removeprefix(f"{vendor} ").strip() if raw_model else ""
        vram_bytes_str = card.get("VRAM Total Memory (B)", "0")
        unique_id = card.get("Unique ID", "")

        try:
            vram_mb = int(vram_bytes_str) // (1024 * 1024)
        except (ValueError, TypeError):
            vram_mb = 0

        # rocm-smi emits "N/A" when the driver cannot retrieve the serial.
        # Normalise to empty string so callers can test with `if gpu.serial`.
        serial = "" if unique_id in ("N/A", "N/A\n", None) else unique_id

        gpus.append(
            DetectedGpu(
                vendor=vendor,
                model=model,
                vram_mb=vram_mb,
                serial=serial,
                device_id=None,
                pci_slot=None,
            )
        )

    return gpus


def detect_gpus() -> list[DetectedGpu]:
    """Detect all GPUs on the system by trying each supported backend.

    Currently supports AMD via rocm-smi.  NVIDIA support via nvidia-smi will
    be added when the first NVIDIA workstation is added to the fleet.

    Returns a flat list of all detected GPUs across all backends.  Backends
    are tried in order; failures are silently ignored so a machine without
    ROCm installed still works if NVIDIA tools are present.
    """
    gpus: list[DetectedGpu] = []

    # AMD / ROCm
    gpus.extend(detect_gpus_rocm())

    # Future: NVIDIA
    # gpus.extend(detect_gpus_nvidia())

    return gpus
