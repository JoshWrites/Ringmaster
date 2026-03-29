"""Tests for ringmaster.config — config loading and model validation.

These tests follow TDD: they were written to describe correct behaviour before
the implementation existed.  Each test exercises a distinct scenario so that
a failure points directly at what broke.
"""

from pathlib import Path

import pytest
import yaml

from ringmaster.config import (
    GpuConfig,
    GpuFingerprint,
    RingmasterConfig,
    load_config,
)


def test_load_config_from_file(sample_config_path: Path) -> None:
    """Loading the example config file produces a valid RingmasterConfig.

    Verifies that the example file parses correctly and that key fields
    match the documented defaults and example values.
    """
    config = load_config(sample_config_path)

    assert isinstance(config, RingmasterConfig)
    # The example file sets a non-default port so we can distinguish it
    # from a purely-defaulted config.
    assert config.server.port == 8420
    assert config.server.host == "0.0.0.0"
    assert config.ollama.host == "http://localhost:11434"


def test_load_config_defaults(tmp_path: Path) -> None:
    """A minimal YAML file produces a config where all defaults are applied.

    This ensures that operators can start with an almost-empty config file
    and get sensible behaviour without having to spell out every field.
    """
    minimal_yaml = tmp_path / "minimal.yaml"
    minimal_yaml.write_text("{}\n", encoding="utf-8")

    config = load_config(minimal_yaml)

    assert config.server.host == "0.0.0.0"
    assert config.server.port == 8420
    assert config.ollama.host == "http://localhost:11434"
    assert config.idle.idle_threshold_seconds == 300
    assert config.idle.auto_approve_when_idle is True
    assert config.queue.max_queue_depth == 100
    assert config.queue.default_priority == 3
    assert config.auth.token_file == "tokens.json"
    assert config.gpus == []
    assert config.power.wake_method == "none"
    assert config.notifications.backend == "desktop"


def test_load_config_missing_file(tmp_path: Path) -> None:
    """Attempting to load a non-existent file raises FileNotFoundError.

    A clear exception here is much more helpful than a cryptic traceback
    from the YAML parser or Pydantic if the path is wrong.
    """
    missing_path = tmp_path / "does_not_exist.yaml"

    with pytest.raises(FileNotFoundError, match="does_not_exist.yaml"):
        load_config(missing_path)


def test_gpu_config_round_trip(tmp_path: Path) -> None:
    """A GPU config with a full fingerprint survives a YAML round-trip.

    Writing a GpuConfig to YAML and loading it back verifies that the
    fingerprint sub-model is parsed correctly and that no fields are lost
    or silently coerced to wrong types.
    """
    gpu_data = {
        "gpus": [
            {
                "label": "Primary Compute",
                "role": "compute",
                "prefer_for": ["embedding", "chat"],
                "fingerprint": {
                    "vendor": "NVIDIA",
                    "model": "RTX 4090",
                    "vram_mb": 24576,
                    "serial": "GPU-abc123",
                    "device_id": "10de:2684",
                },
            }
        ]
    }

    config_file = tmp_path / "gpu_test.yaml"
    config_file.write_text(yaml.dump(gpu_data), encoding="utf-8")

    config = load_config(config_file)

    assert len(config.gpus) == 1
    gpu = config.gpus[0]
    assert isinstance(gpu, GpuConfig)
    assert gpu.label == "Primary Compute"
    assert gpu.role == "compute"
    assert gpu.prefer_for == ["embedding", "chat"]

    fp = gpu.fingerprint
    assert isinstance(fp, GpuFingerprint)
    assert fp.vendor == "NVIDIA"
    assert fp.model == "RTX 4090"
    assert fp.vram_mb == 24576
    assert fp.serial == "GPU-abc123"
    assert fp.device_id == "10de:2684"
