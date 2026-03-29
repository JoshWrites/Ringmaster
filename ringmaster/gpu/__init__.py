"""GPU detection and fingerprinting for Ringmaster.

This subpackage is responsible for two distinct concerns:

  1. **Detection** (`detect.py`): Query the host system for installed GPUs
     using vendor-specific tools (rocm-smi for AMD, nvidia-smi for NVIDIA).
     Detection is hardware-level and returns raw facts about what is present.

  2. **Fingerprinting** (`fingerprint.py`): Match detected GPUs against the
     operator-supplied config entries so that Ringmaster knows which card to
     use for which task type.  This separation means fingerprinting can be
     unit-tested without real hardware.
"""
