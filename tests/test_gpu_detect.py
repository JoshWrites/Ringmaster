"""Tests for GPU detection via rocm-smi.

These tests mock the subprocess layer so they run without real hardware.
The goal is to verify that our parsing logic correctly handles:
  - Normal rocm-smi output with multiple GPUs
  - An empty GPU list (rocm-smi present but no devices)
  - rocm-smi not installed (FileNotFoundError)
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from ringmaster.gpu.detect import DetectedGpu, detect_gpus, detect_gpus_rocm


# ---------------------------------------------------------------------------
# Sample rocm-smi JSON output
# ---------------------------------------------------------------------------

# Reflects the actual --showproductname --showmeminfo vram --showuniqueid
# output format from rocm-smi 5.x/6.x.  Field names are driver-provided
# strings; we only normalise what we need (vendor, VRAM bytes → MB).
ROCM_SMI_TWO_GPU_OUTPUT = json.dumps(
    {
        "card0": {
            "Card series": "AMD Radeon RX 7900 XTX",
            "Card model": "AMD Radeon RX 7900 XTX",
            "Card vendor": "Advanced Micro Devices, Inc. [AMD/ATI]",
            "Card SKU": "XTX",
            "VRAM Total Memory (B)": "25753026560",
            "VRAM Total Used Memory (B)": "0",
            "Unique ID": "0x1234abcd5678ef01",
        },
        "card1": {
            "Card series": "AMD Radeon RX 7900 GRE",
            "Card model": "AMD Radeon RX 7900 GRE",
            "Card vendor": "Advanced Micro Devices, Inc. [AMD/ATI]",
            "Card SKU": "GRE",
            "VRAM Total Memory (B)": "17179869184",
            "VRAM Total Used Memory (B)": "0",
            "Unique ID": "N/A",
        },
        "system": {
            "Driver version": "6.7.0",
        },
    }
)

ROCM_SMI_EMPTY_OUTPUT = json.dumps({})


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_detect_gpus_parses_rocm_smi_output() -> None:
    """Two GPU entries from rocm-smi are parsed into DetectedGpu objects.

    Verifies:
    - Vendor string normalised to 'AMD'
    - VRAM bytes correctly converted to MB via integer division
    - Serial field uses Unique ID when present, empty string when 'N/A'
    - Non-card keys ('system') are skipped
    """
    completed = MagicMock()
    completed.stdout = ROCM_SMI_TWO_GPU_OUTPUT
    completed.returncode = 0

    with patch("subprocess.run", return_value=completed) as mock_run:
        gpus = detect_gpus_rocm()

    mock_run.assert_called_once()

    assert len(gpus) == 2

    card0 = gpus[0]
    assert card0.vendor == "AMD"
    assert card0.model == "AMD Radeon RX 7900 XTX"
    # 25753026560 bytes // 1048576 = 24545 MB
    assert card0.vram_mb == 25753026560 // (1024 * 1024)
    assert card0.serial == "0x1234abcd5678ef01"

    card1 = gpus[1]
    assert card1.vendor == "AMD"
    assert card1.model == "AMD Radeon RX 7900 GRE"
    assert card1.vram_mb == 17179869184 // (1024 * 1024)
    # 'N/A' unique ID should be normalised to empty string
    assert card1.serial == ""


def test_detect_gpus_handles_no_gpus() -> None:
    """Empty JSON from rocm-smi returns an empty list, not an error.

    rocm-smi exits with {} when no devices are found.  We should treat this
    as 'no GPUs detected' rather than a parse failure.
    """
    completed = MagicMock()
    completed.stdout = ROCM_SMI_EMPTY_OUTPUT
    completed.returncode = 0

    with patch("subprocess.run", return_value=completed):
        gpus = detect_gpus_rocm()

    assert gpus == []


def test_detect_gpus_handles_rocm_smi_missing() -> None:
    """FileNotFoundError from rocm-smi returns an empty list, not an exception.

    The binary might not be installed on NVIDIA or CPU-only machines.  We
    return an empty list so detect_gpus() can fall through to other backends.
    """
    with patch("subprocess.run", side_effect=FileNotFoundError("rocm-smi not found")):
        gpus = detect_gpus_rocm()

    assert gpus == []


def test_detect_gpus_aggregates_backends() -> None:
    """detect_gpus() returns results from rocm detection when GPUs are found.

    This verifies that the top-level aggregator function actually calls through
    to detect_gpus_rocm() and returns its results.
    """
    fake_gpu = DetectedGpu(
        vendor="AMD",
        model="AMD Radeon RX 7900 XTX",
        vram_mb=24545,
        serial="0xdeadbeef",
        device_id=None,
        pci_slot=None,
    )

    with patch("ringmaster.gpu.detect.detect_gpus_rocm", return_value=[fake_gpu]):
        gpus = detect_gpus()

    assert len(gpus) == 1
    assert gpus[0].model == "AMD Radeon RX 7900 XTX"
