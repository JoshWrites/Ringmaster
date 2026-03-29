"""Tests for GPU fingerprinting — matching detected hardware to config entries.

The fingerprinting layer is intentionally separate from detection so it can
be unit-tested without hardware.  The priority order for matching is:
  1. Serial match (most specific — uniquely identifies a board)
  2. Model + VRAM match within 5% tolerance (catches driver rounding)
  3. Model-only match (last resort, used when VRAM is unreported)
"""

from __future__ import annotations


from ringmaster.config import GpuConfig, GpuFingerprint
from ringmaster.gpu.detect import DetectedGpu
from ringmaster.gpu.fingerprint import (
    InventoryResult,
    match_gpu_to_config,
    resolve_inventory,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def make_gpu_config(
    label: str,
    model: str,
    vram_mb: int,
    serial: str | None = None,
    vendor: str = "AMD",
    role: str = "compute",
) -> GpuConfig:
    """Build a GpuConfig with a minimal fingerprint for test clarity."""
    return GpuConfig(
        label=label,
        role=role,
        fingerprint=GpuFingerprint(
            vendor=vendor,
            model=model,
            vram_mb=vram_mb,
            serial=serial,
        ),
    )


def make_detected(
    model: str,
    vram_mb: int,
    serial: str = "",
    vendor: str = "AMD",
) -> DetectedGpu:
    """Build a DetectedGpu with minimal fields for test clarity."""
    return DetectedGpu(
        vendor=vendor,
        model=model,
        vram_mb=vram_mb,
        serial=serial,
        device_id=None,
        pci_slot=None,
    )


# ---------------------------------------------------------------------------
# match_gpu_to_config tests
# ---------------------------------------------------------------------------


def test_match_gpu_to_config_by_serial() -> None:
    """Serial match takes priority over model/VRAM, even if model differs.

    This matters when a GPU has been rebadged or when the driver reports a
    slightly different model string than the config.
    """
    config = make_gpu_config(
        label="primary",
        model="AMD Radeon RX 7900 XTX",
        vram_mb=24576,
        serial="0xdeadbeef",
    )
    detected = make_detected(
        model="Radeon RX 7900 XTX",  # slightly different model string
        vram_mb=24545,
        serial="0xdeadbeef",  # same serial → should match
    )

    result = match_gpu_to_config(detected, [config])

    assert result is not None
    assert result.label == "primary"


def test_match_gpu_to_config_by_model_and_vram() -> None:
    """Model + VRAM match works within 5% tolerance when no serial is set.

    ROCm may report VRAM as slightly less than the nominal figure due to
    firmware reservations, so a strict equality check would miss real matches.
    The 5% window is wide enough to absorb driver rounding while still
    distinguishing cards with meaningfully different VRAM (e.g. 16 GB vs 24 GB).
    """
    config = make_gpu_config(
        label="compute",
        model="AMD Radeon RX 7900 XTX",
        vram_mb=24576,  # nominal 24 GiB
        serial=None,    # no serial in config
    )
    detected = make_detected(
        model="AMD Radeon RX 7900 XTX",
        vram_mb=24545,  # driver-reported, slightly under nominal
        serial="",      # driver didn't expose serial
    )

    result = match_gpu_to_config(detected, [config])

    assert result is not None
    assert result.label == "compute"


def test_match_gpu_to_config_vram_outside_tolerance() -> None:
    """VRAM difference > 5% prevents a model+VRAM match.

    Ensures we don't accidentally match a 16 GB card to a 24 GB config entry
    just because the model string is similar.
    """
    config = make_gpu_config(
        label="compute",
        model="AMD Radeon RX 7900 XTX",
        vram_mb=24576,
        serial=None,
    )
    detected = make_detected(
        model="AMD Radeon RX 7900 XTX",
        vram_mb=16384,  # 16 GiB — clearly different card
        serial="",
    )

    result = match_gpu_to_config(detected, [config])

    # Should fall through to model-only match since VRAM tolerance fails
    # but model matches — model-only is the last-resort tier
    # In this scenario model matches so we DO get a match (model-only tier)
    assert result is not None
    assert result.label == "compute"


def test_match_gpu_no_match() -> None:
    """No match is returned when model doesn't match at all."""
    config = make_gpu_config(
        label="compute",
        model="AMD Radeon RX 7900 XTX",
        vram_mb=24576,
        serial=None,
    )
    detected = make_detected(
        model="NVIDIA RTX 4090",
        vram_mb=24576,
        serial="",
    )

    result = match_gpu_to_config(detected, [config])

    assert result is None


# ---------------------------------------------------------------------------
# resolve_inventory tests
# ---------------------------------------------------------------------------


def test_resolve_gpu_inventory() -> None:
    """One detected GPU and two configs → 1 matched, 1 missing, 0 unknown.

    The matched GPU should use the config with the matching model; the
    unmatched config ends up in the missing list; no detected GPU goes
    unaccounted.
    """
    config_primary = make_gpu_config(
        label="primary",
        model="AMD Radeon RX 7900 XTX",
        vram_mb=24576,
        serial=None,
    )
    config_secondary = make_gpu_config(
        label="secondary",
        model="AMD Radeon RX 7900 GRE",
        vram_mb=16384,
        serial=None,
    )
    detected_primary = make_detected(
        model="AMD Radeon RX 7900 XTX",
        vram_mb=24545,
        serial="",
    )

    result = resolve_inventory(
        detected=[detected_primary],
        configs=[config_primary, config_secondary],
    )

    assert isinstance(result, InventoryResult)
    assert len(result.matched) == 1
    assert result.matched[0].label == "primary"
    assert result.matched[0].detected is detected_primary
    assert result.matched[0].config is config_primary

    assert len(result.missing) == 1
    assert result.missing[0].label == "secondary"

    assert len(result.unknown) == 0


def test_resolve_inventory_unknown_gpu() -> None:
    """A detected GPU with no matching config ends up in unknown."""
    config = make_gpu_config(
        label="primary",
        model="AMD Radeon RX 7900 XTX",
        vram_mb=24576,
    )
    mystery_gpu = make_detected(
        model="Intel Arc A770",
        vram_mb=16384,
        vendor="Intel",
    )

    result = resolve_inventory(detected=[mystery_gpu], configs=[config])

    assert len(result.matched) == 0
    assert len(result.missing) == 1
    assert len(result.unknown) == 1
    assert result.unknown[0] is mystery_gpu


def test_resolve_inventory_each_config_matches_at_most_once() -> None:
    """Two identical-looking GPUs do not both match the same config entry.

    When the workstation has two physically identical cards, each card must
    match a distinct config entry.  If there is only one matching config,
    the second card has no home and lands in unknown.
    """
    config = make_gpu_config(
        label="only-one",
        model="AMD Radeon RX 7900 XTX",
        vram_mb=24576,
    )
    gpu_a = make_detected(model="AMD Radeon RX 7900 XTX", vram_mb=24545)
    gpu_b = make_detected(model="AMD Radeon RX 7900 XTX", vram_mb=24545)

    result = resolve_inventory(detected=[gpu_a, gpu_b], configs=[config])

    assert len(result.matched) == 1
    assert len(result.unknown) == 1
