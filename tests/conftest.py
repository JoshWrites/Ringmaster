"""Shared pytest fixtures for the Ringmaster test suite."""

from pathlib import Path

import pytest


@pytest.fixture
def sample_config_path() -> Path:
    """Return the path to the example config file shipped with the repo.

    Using the real example file as a test fixture verifies that the example
    stays in sync with the actual config schema — if ringmaster.example.yaml
    ever drifts out of date, the tests will catch it.
    """
    return Path(__file__).parent.parent / "ringmaster.example.yaml"
