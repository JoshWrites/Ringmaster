"""GPU fingerprinting — matching detected hardware to operator config entries.

The fingerprinting layer answers the question: "Which GPU in my config is
this physical card?"  This is necessary because the same workstation may
have multiple GPUs with different intended roles (e.g. one for display,
one for pure compute), and we need to know which is which before dispatching
tasks.

Match priority (highest to lowest):
  1. **Serial match** — the board serial number uniquely identifies a GPU,
     so this is the most reliable match.  Useful when two cards of the same
     model are installed.
  2. **Model + VRAM match** — when no serial is available, match on model
     name plus VRAM within a 5% tolerance.  The tolerance absorbs firmware
     reservations that cause the driver to report slightly less than nominal.
  3. **Model-only match** — last resort when VRAM is unreported or wildly
     wrong.  Less reliable but better than no match.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ringmaster.config import GpuConfig
from ringmaster.gpu.detect import DetectedGpu


# How far the detected VRAM can deviate from the config value before the
# model+VRAM match tier is skipped.  5% is wide enough to absorb firmware
# reservations (~1.2 GB on a 24 GB card) while still distinguishing
# meaningfully different capacities (e.g. 16 GB vs 24 GB = 50% difference).
_VRAM_TOLERANCE = 0.05


@dataclass
class MatchedGpu:
    """A detected GPU that has been successfully linked to a config entry.

    Carries both sides of the match so callers can access hardware facts
    (pci_slot, actual VRAM) alongside config intent (role, prefer_for).
    """

    label: str
    """Human-readable name from the config, used in logs and API responses."""

    role: str
    """Intended use from the config: 'compute', 'display', or 'compute+display'."""

    prefer_for: list[str]
    """Task types this GPU should be preferred for (from config)."""

    vram_mb: int
    """Detected VRAM in MiB — authoritative at runtime, config value is nominal."""

    pci_slot: str | None
    """PCI slot address from detection, e.g. '0000:03:00.0'."""

    detected: DetectedGpu
    """The raw detection result that was matched."""

    config: GpuConfig
    """The config entry that was matched."""


@dataclass
class InventoryResult:
    """The outcome of matching all detected GPUs against the full config.

    Three mutually exclusive buckets:
    - ``matched``: every detected GPU that maps to a config entry.
    - ``missing``: config entries for which no GPU was detected (card removed?).
    - ``unknown``: detected GPUs that don't match any config entry (new card?).
    """

    matched: list[MatchedGpu] = field(default_factory=list)
    missing: list[GpuConfig] = field(default_factory=list)
    unknown: list[DetectedGpu] = field(default_factory=list)


def _vram_within_tolerance(detected_mb: int, config_mb: int) -> bool:
    """Return True if detected VRAM is within _VRAM_TOLERANCE of the config value.

    Uses the config value as the reference because it represents the nominal
    (advertised) capacity, which is what the operator typed.
    """
    if config_mb == 0:
        # Avoid division by zero; treat 0 as unknown → skip VRAM check.
        return True
    ratio = abs(detected_mb - config_mb) / config_mb
    return ratio <= _VRAM_TOLERANCE


def match_gpu_to_config(detected: DetectedGpu, configs: list[GpuConfig]) -> GpuConfig | None:
    """Find the best-matching config entry for a single detected GPU.

    Applies the three-tier priority hierarchy described in the module docstring.
    Returns the first (and ideally only) match at the highest-priority tier
    that produces a result.

    Args:
        detected: The GPU as reported by the driver.
        configs: All operator-configured GPU entries to search through.

    Returns:
        The matching GpuConfig, or None if no config entry fits.
    """
    # --- Tier 1: Serial match ---
    # Serial is unique to a board, so a serial match overrides everything else.
    # We only attempt this when both sides have a non-empty serial.
    if detected.serial:
        for cfg in configs:
            if cfg.fingerprint.serial and cfg.fingerprint.serial == detected.serial:
                return cfg

    # --- Tier 2: Model + VRAM match (within tolerance) ---
    for cfg in configs:
        if (
            cfg.fingerprint.model == detected.model
            and _vram_within_tolerance(detected.vram_mb, cfg.fingerprint.vram_mb)
        ):
            return cfg

    # --- Tier 3: Model-only match ---
    # Last resort: model string matches but VRAM is outside tolerance or
    # unreported.  Still better than marking the card as unknown.
    for cfg in configs:
        if cfg.fingerprint.model == detected.model:
            return cfg

    return None


def resolve_inventory(
    detected: list[DetectedGpu],
    configs: list[GpuConfig],
) -> InventoryResult:
    """Match all detected GPUs against the full config, tracking every outcome.

    Each config entry can only absorb one detected GPU — if two identical
    physical cards are present, the second one lands in `unknown` unless a
    second matching config entry exists.  This prevents double-counting and
    forces the operator to explicitly configure duplicate cards.

    Args:
        detected: All GPUs found on the system by detection backends.
        configs: All operator-configured GPU entries.

    Returns:
        An InventoryResult with matched/missing/unknown buckets populated.
    """
    result = InventoryResult()

    # Track which config entries have already been claimed so a single config
    # entry cannot match more than one physical card.
    claimed_configs: set[int] = set()  # set of id(config) values

    for gpu in detected:
        # Only offer unclaimed configs to match_gpu_to_config.
        available = [c for c in configs if id(c) not in claimed_configs]
        matched_cfg = match_gpu_to_config(gpu, available)

        if matched_cfg is not None:
            claimed_configs.add(id(matched_cfg))
            result.matched.append(
                MatchedGpu(
                    label=matched_cfg.label,
                    role=matched_cfg.role,
                    prefer_for=list(matched_cfg.prefer_for),
                    vram_mb=gpu.vram_mb,
                    pci_slot=gpu.pci_slot,
                    detected=gpu,
                    config=matched_cfg,
                )
            )
        else:
            result.unknown.append(gpu)

    # Config entries that were not claimed by any detected GPU are missing.
    for cfg in configs:
        if id(cfg) not in claimed_configs:
            result.missing.append(cfg)

    return result
